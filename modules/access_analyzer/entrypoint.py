"""access_analyzer 공개 실행 창구: run(operation, input_paths, output_dir, context)와 CLI.

지원 operation:
- prepare_queries: 입력 JSON 없음 → graph_query.json 공개(KG에 보낼 질의 계획).
- analyze: graph_query_result.json 하나 입력 → vulnerability_candidates.json 공개(후보. 규칙은 다음 PR이라 빈 배열).

    .venv/bin/python -m modules.access_analyzer.entrypoint prepare_queries --mode development --graph-id GID [--expected-graph-revision N]
    .venv/bin/python -m modules.access_analyzer.entrypoint analyze --mode development --graph-id GID --expected-graph-revision N --input-path <graph_query_result.json>

처리 순서: 실행 값 검증 → (prepare: 질의 계획 / analyze: 입력 검증·판정) → envelope → 자기 출력 검증 → 원자적 공개.
반환값·CLI stdout 한 줄: {status, artifact_path, artifact_id, sha256, errors}. 파일을 못 썼으면 경로·ID·sha256이 null.

context 키는 명세 03 실행 값 + 필요한 graph 상태 여섯 개: run_id·iteration·mode·run_root·graph_id·expected_graph_revision.
알 수 없는 operation은 산출물 타입을 모르므로 파일 없이 반환값으로만 알린다(종료코드 3).
"""

import argparse
import hashlib
import json
import logging
import re
import secrets
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from modules.access_analyzer import service
from modules.access_analyzer.utils.envelope import (
    ARTIFACT_TYPE_CANDIDATES,
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
    CANDIDATES_FILE_NAME,
    GRAPH_QUERY_FILE_NAME,
    ValidationIssue,
    validate_graph_query_bytes,
    validate_graph_query_result_file,
    validate_vulnerability_candidates_bytes,
)

logger = logging.getLogger(__name__)

PREPARE_QUERIES_OPERATION = "prepare_queries"
ANALYZE_OPERATION = "analyze"
GRAPH_QUERY_RESULT_TYPE = "graph_query_result"
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

# operation별 산출물 종류·파일명·자기 출력 검증기.
ARTIFACT_TYPE_BY_OPERATION = {
    PREPARE_QUERIES_OPERATION: ARTIFACT_TYPE_GRAPH_QUERY,
    ANALYZE_OPERATION: ARTIFACT_TYPE_CANDIDATES,
}
FILE_NAME_BY_OPERATION = {
    PREPARE_QUERIES_OPERATION: GRAPH_QUERY_FILE_NAME,
    ANALYZE_OPERATION: CANDIDATES_FILE_NAME,
}
VALIDATOR_BY_OPERATION: dict[str, Callable[[bytes], list[ValidationIssue]]] = {
    PREPARE_QUERIES_OPERATION: validate_graph_query_bytes,
    ANALYZE_OPERATION: validate_vulnerability_candidates_bytes,
}


class ContextError(ValueError):
    """context가 명세 실행 값 규칙에 맞지 않는다. 메시지에는 키 이름만 넣는다."""


class InputPathError(ValueError):
    """analyze 입력 경로가 run_root 안의 읽을 수 있는 파일이 아니다."""


def run(
    operation: str, input_paths: Sequence[str | Path], output_dir: str | Path, context: Mapping[str, Any]
) -> dict[str, Any]:
    """operation을 실행하고 산출물을 output_dir에 공개한다. 예외 대신 결과 객체로 알린다."""
    started = time.monotonic()
    # 알 수 없는 operation은 어떤 산출물을 쓸지 모른다. 파일을 만들지 않고 반환값으로만 알린다.
    if operation not in ARTIFACT_TYPE_BY_OPERATION:
        supported = ", ".join(sorted(ARTIFACT_TYPE_BY_OPERATION))
        return _make_unwritten_result(ErrorCode.OPERATION_UNSUPPORTED, f"지원 operation은 {supported}")
    try:
        run_context = parse_context(context)
        artifact_dir = prepare_output_dir(run_context.run_root, run_context.iteration, Path(output_dir))
    except ContextError as error:
        return _make_unwritten_result(ErrorCode.CONTEXT_INVALID, str(error))
    except OutputPathError as error:
        return _make_unwritten_result(ErrorCode.OUTPUT_PATH_INVALID, str(error))
    except OSError as error:
        return _make_unwritten_result(ErrorCode.OUTPUT_WRITE_FAILED, f"출력 폴더를 만들지 못함: {type(error).__name__}")
    file_name = FILE_NAME_BY_OPERATION[operation]
    # 완료 파일은 한 번만 공개한다. 작업 전에 먼저 거절한다(공개 때 os.link가 한 번 더 막는다).
    if (artifact_dir / file_name).exists():
        return _make_unwritten_result(ErrorCode.ARTIFACT_EXISTS, f"이미 공개된 파일이 있음: {file_name}")
    if operation == PREPARE_QUERIES_OPERATION:
        work, input_refs = _prepare_queries_work(run_context, input_paths, started)
    else:
        work, input_refs = _analyze_work(run_context, input_paths, started)
    return _publish(run_context, artifact_dir, operation, work, input_refs)


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


def _prepare_queries_work(
    run_context: RunContext, input_paths: Sequence[str | Path], started: float
) -> tuple[WorkResult, list[dict[str, Any]]]:
    """질의 계획을 만든다. 입력 JSON을 받으면 거절한다."""
    if input_paths:
        error = make_error_item(ErrorCode.INPUT_UNEXPECTED, "prepare_queries는 입력 JSON을 받지 않음")
        return WorkResult(Status.FAILED, [error], None, _elapsed_ms(started)), []
    data = service.build_graph_query_data(run_context.graph_id, run_context.expected_graph_revision)
    return WorkResult(Status.COMPLETED, [], data, _elapsed_ms(started)), []


def _analyze_work(
    run_context: RunContext, input_paths: Sequence[str | Path], started: float
) -> tuple[WorkResult, list[dict[str, Any]]]:
    """graph_query_result 하나를 읽어 검증하고 후보를 판정한다. 입력을 못 읽으면 input_refs 없이 failed."""
    if len(input_paths) != 1:
        code = ErrorCode.INPUT_MISSING if not input_paths else ErrorCode.INPUT_UNEXPECTED
        message = "analyze는 graph_query_result.json 하나를 입력으로 받는다"
        return WorkResult(Status.FAILED, [make_error_item(code, message)], None, _elapsed_ms(started)), []
    try:
        input_path = _resolve_input_path(run_context.run_root, input_paths[0])
    except InputPathError as error:
        return WorkResult(Status.FAILED, [make_error_item(ErrorCode.INPUT_PATH_INVALID, str(error))], None, _elapsed_ms(started)), []
    issues = validate_graph_query_result_file(input_path)
    if issues:
        errors = _describe_issues(ErrorCode.INPUT_CONTRACT_INVALID, issues)
        return WorkResult(Status.FAILED, errors, None, _elapsed_ms(started)), []
    raw = input_path.read_bytes()
    input_artifact = json.loads(raw)
    input_ref = _build_input_ref(run_context.run_root, input_path, raw, input_artifact)
    outcome = service.build_candidates_result(
        input_artifact,
        run_context.graph_id,
        run_context.expected_graph_revision,
        run_context.run_id,
    )
    return WorkResult(outcome.status, outcome.errors, outcome.data, _elapsed_ms(started)), [input_ref]


def _resolve_input_path(run_root: Path, raw_path: str | Path) -> Path:
    """입력 경로를 run_root 안으로 제한한다. 상대 경로는 run_root 기준, 바깥을 가리키면 거절한다."""
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = run_root / candidate
    resolved = candidate.resolve()
    root = run_root.resolve()
    if not resolved.is_relative_to(root):
        raise InputPathError("입력 경로가 run_root 밖을 가리킴")
    if not resolved.is_file():
        raise InputPathError("입력 파일이 없음")
    return resolved


def _build_input_ref(run_root: Path, input_path: Path, raw: bytes, artifact: Mapping[str, Any]) -> dict[str, Any]:
    relative = input_path.resolve().relative_to(run_root.resolve()).as_posix()
    return {
        "artifact_id": artifact["artifact_id"],
        "artifact_type": GRAPH_QUERY_RESULT_TYPE,
        "iteration": artifact["iteration"],
        "path": relative,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _publish(
    run_context: RunContext,
    artifact_dir: Path,
    operation: str,
    work: WorkResult,
    input_refs: list[dict[str, Any]],
) -> dict[str, Any]:
    """자기 출력 검증을 통과한 바이트만 공개한다. 통과 못 하면 데이터 없이 failed로 공개한다."""
    artifact_type = ARTIFACT_TYPE_BY_OPERATION[operation]
    file_name = FILE_NAME_BY_OPERATION[operation]
    validate = VALIDATOR_BY_OPERATION[operation]
    document = build_envelope(artifact_type, run_context, work, input_refs=input_refs)
    raw = serialize_json(document)
    issues = validate(raw)
    if issues:
        logger.error("자기 출력 검증 실패 %d건: 데이터 없이 failed로 공개", len(issues))
        failed_work = WorkResult(Status.FAILED, _describe_issues(ErrorCode.OUTPUT_CONTRACT_INVALID, issues), None, work.duration_ms)
        document = build_envelope(artifact_type, run_context, failed_work, input_refs=input_refs)
        raw = serialize_json(document)
        if validate(raw):
            return _make_unwritten_result(ErrorCode.OUTPUT_CONTRACT_INVALID, "failed 결과도 계약 검증을 통과하지 못함")
    try:
        path = publish_file(artifact_dir, file_name, raw)
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


def _describe_issues(code: ErrorCode, issues: Sequence[Any]) -> list[dict[str, Any]]:
    described = [
        make_error_item(code, f"{issue.code} {issue.location}: {issue.message}")
        for issue in issues[:MAX_CONTRACT_ISSUES]
    ]
    if len(issues) > MAX_CONTRACT_ISSUES:
        omitted = len(issues) - MAX_CONTRACT_ISSUES
        described.append(make_error_item(code, f"그 밖의 문제 {omitted}건 생략"))
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
    detail = (
        f"질의 {len(data['queries'])}"
        if "queries" in data
        else f"후보 {len(data.get('candidates', []))}"
    )
    logger.info("%s %s: %s·errors %d", document["status"], path, detail, len(document["errors"]))


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
        description="KG 질의 계획(prepare_queries) 또는 취약점 후보(analyze)를 공개",
    )
    parser.add_argument("operation", help=f"{PREPARE_QUERIES_OPERATION} 또는 {ANALYZE_OPERATION}")
    parser.add_argument("--mode", required=True, choices=[mode.value for mode in Mode])
    parser.add_argument("--graph-id", required=True, help="조회 대상 KG ID (KG ingest 제어 응답에서 받음)")
    parser.add_argument(
        "--expected-graph-revision",
        type=int,
        default=None,
        help="기대 KG revision. 생략하면 현재 revision을 조회하고 결과에서 실제 값을 받는다",
    )
    parser.add_argument("--input-path", help=f"{ANALYZE_OPERATION}의 graph_query_result.json (run_root 기준 상대 또는 절대)")
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
    input_paths = [args.input_path] if args.input_path else []
    result = run(args.operation, input_paths, output_dir, context)
    # stdout은 호출자가 읽는 결과 한 줄이다. 로그는 stderr로 간다.
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    return _exit_code(result)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.exit(main())
