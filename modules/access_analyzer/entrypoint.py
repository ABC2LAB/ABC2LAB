"""access_analyzer 공개 실행 창구: run(operation, input_paths, output_dir, context)와 CLI.

    .venv/bin/python -m modules.access_analyzer.entrypoint prepare_queries --mode development \
        --graph-id GID [--expected-graph-revision N] [--run-id ID] [--iteration 0] [--runs-dir runs]

이 PR은 prepare_queries만 연다(analyze는 다음 PR). 처리 순서: 실행 값 검증 → 질의 계획(service)
→ 공개 형식(envelope) → 자기 출력 검증(validation) → 원자적 공개(storage). 반환값과 CLI stdout 한 줄은
{status, artifact_path, artifact_id, sha256, errors}다. 파일을 못 썼으면 경로·ID·sha256이 null이고 이유는 errors에.

context 키는 명세 03 실행 값 + 필요한 graph 상태다: run_id·iteration·mode·run_root·graph_id·expected_graph_revision.
graph_id·expected_graph_revision은 KG ingest 제어 응답에서 온다(명세 03 "필요한 graph 상태"). 그 밖의 키는 거절한다.
"""

import argparse
import hashlib
import json
import logging
import re
import secrets
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from modules.access_analyzer import service
from modules.access_analyzer.utils.envelope import (
    ARTIFACT_TYPE_GRAPH_QUERY,
    ErrorCode,
    Mode,
    RunContext,
    Status,
    WorkResult,
    build_envelope,
    make_error_item,
)
from modules.access_analyzer.utils.storage import (
    ARTIFACTS_DIR_NAME,
    ITERATION_DIR_FORMAT,
    PRODUCER,
    ArtifactExistsError,
    OutputPathError,
    prepare_output_dir,
    publish_file,
    serialize_json,
)
from modules.access_analyzer.utils.validation import (
    GRAPH_QUERY_FILE_NAME,
    validate_graph_query_bytes,
)

logger = logging.getLogger(__name__)

PREPARE_QUERIES_OPERATION = "prepare_queries"
REQUIRED_CONTEXT_KEYS = frozenset(
    {"run_id", "iteration", "mode", "run_root", "graph_id", "expected_graph_revision"}
)
DEFAULT_RUNS_DIR = Path("runs")
# 폴더 이름으로 쓰이므로 경로 구분자·..가 들어갈 수 없는 모양만 받는다.
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
RUN_ID_TIME_FORMAT = "%Y%m%d-%H%M%S"
RUN_ID_RANDOM_BYTES = 2
MILLISECONDS_PER_SECOND = 1000
# 자기 출력 검증에 실패했을 때 errors로 옮길 문제 수 상한.
MAX_CONTRACT_ISSUES = 20
EXIT_COMPLETED = 0
EXIT_PARTIAL = 1
EXIT_FAILED_WITH_FILE = 2
EXIT_NO_FILE = 3


class ContextError(ValueError):
    """context가 명세 실행 값 규칙에 맞지 않는다. 메시지에는 키 이름만 넣는다."""


def run(
    operation: str, input_paths: Sequence[str | Path], output_dir: str | Path, context: Mapping[str, Any]
) -> dict[str, Any]:
    """prepare_queries를 실행하고 graph_query.json을 output_dir에 공개한다. 예외 대신 결과 객체로 알린다."""
    started = time.monotonic()
    try:
        run_context = parse_context(context)
        artifact_dir = prepare_output_dir(run_context.run_root, run_context.iteration, Path(output_dir))
    except ContextError as error:
        return _make_unwritten_result(ErrorCode.CONTEXT_INVALID, str(error))
    except OutputPathError as error:
        return _make_unwritten_result(ErrorCode.OUTPUT_PATH_INVALID, str(error))
    except OSError as error:
        return _make_unwritten_result(ErrorCode.OUTPUT_WRITE_FAILED, f"출력 폴더를 만들지 못함: {type(error).__name__}")
    # 완료 파일은 한 번만 공개한다. 탐색하기 전에 먼저 거절한다(공개 때 os.link가 한 번 더 막는다).
    if (artifact_dir / GRAPH_QUERY_FILE_NAME).exists():
        return _make_unwritten_result(ErrorCode.ARTIFACT_EXISTS, f"이미 공개된 파일이 있음: {GRAPH_QUERY_FILE_NAME}")
    call_errors = _check_call(operation, input_paths)
    if call_errors:
        work = WorkResult(Status.FAILED, call_errors, None, _elapsed_ms(started))
        return _publish(run_context, artifact_dir, work)
    data = service.build_graph_query_data(run_context.graph_id, run_context.expected_graph_revision)
    work = WorkResult(Status.COMPLETED, [], data, _elapsed_ms(started))
    return _publish(run_context, artifact_dir, work)


def parse_context(context: Mapping[str, Any]) -> RunContext:
    unknown_keys = set(context) - REQUIRED_CONTEXT_KEYS
    missing_keys = REQUIRED_CONTEXT_KEYS - set(context)
    if unknown_keys or missing_keys:
        raise ContextError(f"context 키 오류 (빠짐: {sorted(missing_keys)}, 정의 안 됨: {sorted(unknown_keys)})")
    run_id = context["run_id"]
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
        raise ContextError("run_id는 영문·숫자로 시작하고 영문·숫자·._-만 쓸 수 있음")
    iteration = context["iteration"]
    # bool은 int의 하위 클래스라 따로 막는다.
    if isinstance(iteration, bool) or not isinstance(iteration, int) or iteration < 0:
        raise ContextError("iteration은 0 이상의 정수여야 함")
    if context["mode"] not in set(Mode):
        raise ContextError(f"mode는 {', '.join(Mode)} 중 하나여야 함")
    run_root = Path(context["run_root"])
    if run_root.name != run_id:
        raise ContextError("run_root 폴더 이름이 run_id와 같아야 함 (runs/<run_id>)")
    graph_id = context["graph_id"]
    if not isinstance(graph_id, str) or not graph_id:
        raise ContextError("graph_id는 비어 있지 않은 문자열이어야 함")
    expected_graph_revision = _parse_expected_revision(context["expected_graph_revision"])
    return RunContext(run_id, iteration, Mode(context["mode"]), run_root, graph_id, expected_graph_revision)


def _parse_expected_revision(value: Any) -> int | None:
    """null이면 현재 revision 조회(KG가 실제 값을 돌려줌). 값이 있으면 0 이상 정수."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContextError("expected_graph_revision은 0 이상의 정수이거나 null이어야 함")
    return value


def make_run_id(now: datetime) -> str:
    """시각 + 짧은 난수. 같은 초에 두 번 돌려도 실행이 갈린다."""
    return f"{now.strftime(RUN_ID_TIME_FORMAT)}-{secrets.token_hex(RUN_ID_RANDOM_BYTES)}"


def _check_call(operation: str, input_paths: Sequence[str | Path]) -> list[dict[str, Any]]:
    errors = []
    if operation != PREPARE_QUERIES_OPERATION:
        errors.append(
            make_error_item(ErrorCode.OPERATION_UNSUPPORTED, f"이 모듈이 지원하는 operation은 {PREPARE_QUERIES_OPERATION}")
        )
    # prepare_queries는 입력 JSON을 읽지 않는다. graph 상태는 context로 받는다.
    if input_paths:
        errors.append(make_error_item(ErrorCode.INPUT_UNEXPECTED, "prepare_queries는 입력 JSON을 받지 않음"))
    return errors


def _publish(run_context: RunContext, artifact_dir: Path, work: WorkResult) -> dict[str, Any]:
    """자기 출력 검증을 통과한 바이트만 공개한다. 통과 못 하면 데이터 없이 failed로 공개한다."""
    document = build_envelope(ARTIFACT_TYPE_GRAPH_QUERY, run_context, work)
    raw = serialize_json(document)
    issues = validate_graph_query_bytes(raw)
    if issues:
        logger.error("자기 출력 검증 실패 %d건: 데이터 없이 failed로 공개", len(issues))
        failed_work = WorkResult(Status.FAILED, _describe_issues(issues), None, work.duration_ms)
        document = build_envelope(ARTIFACT_TYPE_GRAPH_QUERY, run_context, failed_work)
        raw = serialize_json(document)
        if validate_graph_query_bytes(raw):
            return _make_unwritten_result(ErrorCode.OUTPUT_CONTRACT_INVALID, "failed 결과도 계약 검증을 통과하지 못함")
    try:
        path = publish_file(artifact_dir, GRAPH_QUERY_FILE_NAME, raw)
    except ArtifactExistsError as error:
        return _make_unwritten_result(ErrorCode.ARTIFACT_EXISTS, str(error))
    except OSError as error:
        return _make_unwritten_result(ErrorCode.OUTPUT_WRITE_FAILED, f"파일을 쓰지 못함: {type(error).__name__}")
    _log_summary(document, path)
    return {
        "status": document["status"],
        "artifact_path": str(path),
        "artifact_id": document["artifact_id"],
        "sha256": hashlib.sha256(raw).hexdigest(),
        "errors": document["errors"],
    }


def _describe_issues(issues: Sequence[Any]) -> list[dict[str, Any]]:
    described = [
        make_error_item(ErrorCode.OUTPUT_CONTRACT_INVALID, f"{issue.code} {issue.location}: {issue.message}")
        for issue in issues[:MAX_CONTRACT_ISSUES]
    ]
    if len(issues) > MAX_CONTRACT_ISSUES:
        omitted = len(issues) - MAX_CONTRACT_ISSUES
        described.append(make_error_item(ErrorCode.OUTPUT_CONTRACT_INVALID, f"그 밖의 문제 {omitted}건 생략"))
    return described


def _make_unwritten_result(code: ErrorCode, message: str) -> dict[str, Any]:
    logger.error("결과 파일을 쓰지 않음: %s %s", code, message)
    error = make_error_item(code, message)
    return {"status": Status.FAILED.value, "artifact_path": None, "artifact_id": None, "sha256": None, "errors": [error]}


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * MILLISECONDS_PER_SECOND)


def _log_summary(document: Mapping[str, Any], path: Path) -> None:
    """runs/는 직접 열어보지 않으므로 건수로 결과를 확인한다. 값은 남기지 않는다."""
    data = document["data"] or {}
    logger.info(
        "%s %s: 질의 %d·errors %d",
        document["status"],
        path,
        len(data.get("queries", [])),
        len(document["errors"]),
    )


def _exit_code(result: Mapping[str, Any]) -> int:
    if result["artifact_path"] is None:
        return EXIT_NO_FILE
    exit_code_by_status = {
        Status.COMPLETED.value: EXIT_COMPLETED,
        Status.PARTIAL.value: EXIT_PARTIAL,
        Status.FAILED.value: EXIT_FAILED_WITH_FILE,
    }
    return exit_code_by_status[result["status"]]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m modules.access_analyzer.entrypoint",
        description="KG에 보낼 읽기 전용 질의 계획 graph_query.json을 공개",
    )
    parser.add_argument("operation", help=f"공개 operation ({PREPARE_QUERIES_OPERATION})")
    parser.add_argument("--mode", required=True, choices=[mode.value for mode in Mode])
    parser.add_argument("--graph-id", required=True, help="조회 대상 KG ID (KG ingest 제어 응답에서 받음)")
    parser.add_argument(
        "--expected-graph-revision",
        type=int,
        default=None,
        help="기대 KG revision. 생략하면 현재 revision을 조회하고 결과에서 실제 값을 받는다",
    )
    parser.add_argument("--run-id", help="없으면 시각+난수로 만든다")
    parser.add_argument("--iteration", type=int, default=0)
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR, help=f"기본 {DEFAULT_RUNS_DIR}")
    args = parser.parse_args(argv)

    run_id = args.run_id or make_run_id(datetime.now(UTC))
    run_root = args.runs_dir / run_id
    output_dir = run_root / ARTIFACTS_DIR_NAME / ITERATION_DIR_FORMAT.format(args.iteration) / PRODUCER
    context = {
        "run_id": run_id,
        "iteration": args.iteration,
        "mode": args.mode,
        "run_root": str(run_root),
        "graph_id": args.graph_id,
        "expected_graph_revision": args.expected_graph_revision,
    }
    result = run(args.operation, [], output_dir, context)
    # stdout은 호출자가 읽는 결과 한 줄이다. 로그는 stderr로 간다.
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    return _exit_code(result)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.exit(main())
