"""verifier 공개 실행 창구: run(operation, input_paths, output_dir, context[, session_executor])와 CLI.

이 PR(1)은 안전 게이트까지. 입력 3종(test_scenarios·safety_decisions·crawl_result)을 검증하고, 계획 해시 == Policy
해시를 확인한 뒤 시나리오별 판정에 따라 block·require_approval은 요청 없이 blocked, allow는 계정·세션 대응만 확인하고
실행은 보류(indeterminate+not_executed+EXECUTION_PENDING). 실제 HTTP 전송은 PR2.

    .venv/bin/python -m modules.verifier.entrypoint verify --mode development --source-graph-revision 1 \
        --input-path <test_scenarios.json> --input-path <safety_decisions.json> --input-path <crawl_result.json>

context 키: run_id·iteration·mode·run_root·source_graph_revision(기준 KG revision, 명세 m7 실행 인자). 그 밖의 키는 거절.
세션 공개 창구(session_executor)는 런너가 주입하는 선택 인자다(명세 03). 이 PR은 게이트만이라 호출하지 않는다.
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

from modules.verifier import service
from modules.verifier.execution import ExecutionContext
from modules.verifier.executor import SessionExecutor
from modules.verifier.utils.config import load_replay_config
from modules.verifier.utils.evidence import EvidenceWriter
from modules.verifier.utils.envelope import (
    ErrorCode,
    Mode,
    RunContext,
    Status,
    WorkResult,
    build_envelope,
    make_error_item,
)
from modules.verifier.utils.storage import (
    ARTIFACTS_DIR_NAME,
    ITERATION_DIR_FORMAT,
    PRODUCER,
    ArtifactExistsError,
    OutputPathError,
    prepare_output_dir,
    publish_file,
    serialize_json,
)
from modules.verifier.utils.validation import (
    VERIFICATION_RESULTS_FILE_NAME,
    ValidationIssue,
    validate_crawl_result_file,
    validate_safety_decisions_file,
    validate_test_scenarios_file,
    validate_verification_results_bytes,
)

logger = logging.getLogger(__name__)

VERIFY_OPERATION = "verify"
REQUIRED_CONTEXT_KEYS = frozenset({"run_id", "iteration", "mode", "run_root", "source_graph_revision"})
# 입력 artifact_type → (검증기, 필수 여부). 정확히 이 세 종류가 하나씩 와야 한다.
INPUT_VALIDATORS = {
    "test_scenarios": validate_test_scenarios_file,
    "safety_decisions": validate_safety_decisions_file,
    "crawl_result": validate_crawl_result_file,
}
DEFAULT_RUNS_DIR = Path("runs")
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
RUN_ID_TIME_FORMAT = "%Y%m%d-%H%M%S"
RUN_ID_RANDOM_BYTES = 2
MILLISECONDS_PER_SECOND = 1000
MAX_CONTRACT_ISSUES = 20
EXIT_COMPLETED = 0
EXIT_PARTIAL = 1
EXIT_FAILED_WITH_FILE = 2
EXIT_NO_FILE = 3


class ContextError(ValueError):
    """context가 명세 실행 값 규칙에 맞지 않는다. 메시지에는 키 이름만 넣는다."""


class InputSetError(ValueError):
    """입력 3종 구성이 올바르지 않다(누락·중복·여분·계약 위반). code를 함께 전달한다."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


def run(
    operation: str,
    input_paths: Sequence[str | Path],
    output_dir: str | Path,
    context: Mapping[str, Any],
    session_executor: SessionExecutor | None = None,
) -> dict[str, Any]:
    """verify를 실행하고 verification_results.json을 공개한다. 예외 대신 결과 객체로 알린다."""
    started = time.monotonic()
    # 출력 타입이 verification_results 하나뿐이라 경로를 신뢰할 수 있으면 실패도 파일로 공개한다.
    # 경로 자체가 불확실할 때만(context·output_dir) 파일 없이 반환값으로만 알린다.
    try:
        run_context = parse_context(context)
        artifact_dir = prepare_output_dir(run_context.run_root, run_context.iteration, Path(output_dir))
    except ContextError as error:
        return _make_unwritten_result(ErrorCode.CONTEXT_INVALID, str(error))
    except OutputPathError as error:
        return _make_unwritten_result(ErrorCode.OUTPUT_PATH_INVALID, str(error))
    except OSError as error:
        return _make_unwritten_result(ErrorCode.OUTPUT_WRITE_FAILED, f"출력 폴더를 만들지 못함: {type(error).__name__}")
    if (artifact_dir / VERIFICATION_RESULTS_FILE_NAME).exists():
        return _make_unwritten_result(ErrorCode.ARTIFACT_EXISTS, f"이미 공개된 파일이 있음: {VERIFICATION_RESULTS_FILE_NAME}")
    if operation != VERIFY_OPERATION:
        error = make_error_item(ErrorCode.OPERATION_UNSUPPORTED, f"지원 operation은 {VERIFY_OPERATION}")
        return _publish(run_context, artifact_dir, WorkResult(Status.FAILED, [error], None, _elapsed_ms(started)), [])
    work, input_refs = _verify_work(run_context, input_paths, session_executor, started)
    return _publish(run_context, artifact_dir, work, input_refs)


def parse_context(context: Mapping[str, Any]) -> RunContext:
    unknown_keys = set(context) - REQUIRED_CONTEXT_KEYS
    missing_keys = REQUIRED_CONTEXT_KEYS - set(context)
    if unknown_keys or missing_keys:
        raise ContextError(f"context 키 오류 (빠짐: {sorted(missing_keys)}, 정의 안 됨: {sorted(unknown_keys)})")
    run_id = context["run_id"]
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
        raise ContextError("run_id는 영문·숫자로 시작하고 영문·숫자·._-만 쓸 수 있음")
    iteration = context["iteration"]
    if isinstance(iteration, bool) or not isinstance(iteration, int) or iteration < 0:
        raise ContextError("iteration은 0 이상의 정수여야 함")
    if context["mode"] not in set(Mode):
        raise ContextError(f"mode는 {', '.join(Mode)} 중 하나여야 함")
    run_root = Path(context["run_root"])
    if run_root.name != run_id:
        raise ContextError("run_root 폴더 이름이 run_id와 같아야 함 (runs/<run_id>)")
    revision = context["source_graph_revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ContextError("source_graph_revision은 0 이상의 정수여야 함")
    return RunContext(run_id, iteration, Mode(context["mode"]), run_root, revision)


def make_run_id(now: datetime) -> str:
    return f"{now.strftime(RUN_ID_TIME_FORMAT)}-{secrets.token_hex(RUN_ID_RANDOM_BYTES)}"


def _verify_work(
    run_context: RunContext,
    input_paths: Sequence[str | Path],
    session_executor: SessionExecutor | None,
    started: float,
) -> tuple[WorkResult, list[dict[str, Any]]]:
    """입력 3종을 분류·검증하고 게이트를 돌린다. 입력 구성이 틀리면 failed."""
    try:
        loaded = _load_inputs(run_context, input_paths)
    except InputSetError as error:
        return WorkResult(Status.FAILED, [make_error_item(error.code, str(error))], None, _elapsed_ms(started)), []
    input_refs = [ref for _, _, ref in loaded.values()]
    scenarios_path, scenarios_doc, _ = loaded["test_scenarios"]
    _, decisions_doc, _ = loaded["safety_decisions"]
    _, crawl_doc, _ = loaded["crawl_result"]
    scenarios_file_sha256 = hashlib.sha256(scenarios_path.read_bytes()).hexdigest()
    execution_context = ExecutionContext(
        executor=session_executor,
        writer=EvidenceWriter(run_context.run_root, run_context.iteration),
        clock=time.monotonic,
        replay=load_replay_config(),
    )
    outcome = service.build_verification_results(
        scenarios_doc, decisions_doc, crawl_doc, scenarios_file_sha256,
        run_context.source_graph_revision, execution_context,
    )
    return WorkResult(outcome.status, outcome.errors, outcome.data, _elapsed_ms(started)), input_refs


def _load_inputs(
    run_context: RunContext, input_paths: Sequence[str | Path]
) -> dict[str, tuple[Path, dict[str, Any], dict[str, Any]]]:
    """정확히 test_scenarios·safety_decisions·crawl_result 하나씩. 각각 검증·run_id 대응 확인 후 (path, doc, ref)."""
    if len(input_paths) < len(INPUT_VALIDATORS):
        raise InputSetError(ErrorCode.INPUT_MISSING, f"verify는 입력 {len(INPUT_VALIDATORS)}종(test_scenarios·safety_decisions·crawl_result)을 받는다")
    if len(input_paths) > len(INPUT_VALIDATORS):
        raise InputSetError(ErrorCode.INPUT_UNEXPECTED, "입력이 필요한 수보다 많음")
    loaded: dict[str, tuple[Path, dict[str, Any], dict[str, Any]]] = {}
    for raw_path in input_paths:
        path = _resolve_input_path(run_context.run_root, raw_path)
        raw = path.read_bytes()
        try:
            document = json.loads(raw)
        except json.JSONDecodeError as error:
            raise InputSetError(ErrorCode.INPUT_CONTRACT_INVALID, f"입력 JSON 형식 오류: {error.msg}") from None
        artifact_type = document.get("artifact_type") if isinstance(document, dict) else None
        if artifact_type not in INPUT_VALIDATORS:
            raise InputSetError(ErrorCode.INPUT_CONTRACT_INVALID, "입력 artifact_type이 verify 입력 3종이 아님")
        if artifact_type in loaded:
            raise InputSetError(ErrorCode.INPUT_UNEXPECTED, f"같은 입력 종류가 두 번 옴: {artifact_type}")
        issues = INPUT_VALIDATORS[artifact_type](path)
        if issues:
            raise InputSetError(ErrorCode.INPUT_CONTRACT_INVALID, f"{artifact_type} 계약 위반 {len(issues)}건")
        if document["run_id"] != run_context.run_id:
            raise InputSetError(ErrorCode.INPUT_CONTRACT_INVALID, f"{artifact_type} run_id가 실행 run_id와 다름")
        if document["data"] is None:
            raise InputSetError(ErrorCode.INPUT_CONTRACT_INVALID, f"{artifact_type}가 failed(data=null)라 검증할 수 없음")
        loaded[artifact_type] = (path, document, _build_input_ref(run_context.run_root, path, raw, document))
    return loaded


def _resolve_input_path(run_root: Path, raw_path: str | Path) -> Path:
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = run_root / candidate
    resolved = candidate.resolve()
    if not resolved.is_relative_to(run_root.resolve()):
        raise InputSetError(ErrorCode.INPUT_PATH_INVALID, "입력 경로가 run_root 밖을 가리킴")
    if not resolved.is_file():
        raise InputSetError(ErrorCode.INPUT_PATH_INVALID, "입력 파일이 없음")
    return resolved


def _build_input_ref(run_root: Path, path: Path, raw: bytes, document: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "artifact_id": document["artifact_id"],
        "artifact_type": document["artifact_type"],
        "iteration": document["iteration"],
        "path": path.resolve().relative_to(run_root.resolve()).as_posix(),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _publish(
    run_context: RunContext, artifact_dir: Path, work: WorkResult, input_refs: list[dict[str, Any]]
) -> dict[str, Any]:
    document = build_envelope(run_context, work, input_refs=input_refs)
    raw = serialize_json(document)
    issues = validate_verification_results_bytes(raw)
    if issues:
        logger.error("자기 출력 검증 실패 %d건: 데이터 없이 failed로 공개", len(issues))
        failed_work = WorkResult(Status.FAILED, _describe_issues(issues), None, work.duration_ms)
        document = build_envelope(run_context, failed_work, input_refs=input_refs)
        raw = serialize_json(document)
        if validate_verification_results_bytes(raw):
            return _make_unwritten_result(ErrorCode.OUTPUT_CONTRACT_INVALID, "failed 결과도 계약 검증을 통과하지 못함")
    try:
        path = publish_file(artifact_dir, VERIFICATION_RESULTS_FILE_NAME, raw)
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


def _describe_issues(issues: Sequence[ValidationIssue]) -> list[dict[str, Any]]:
    described = [
        make_error_item(ErrorCode.OUTPUT_CONTRACT_INVALID, f"{issue.code} {issue.location}: {issue.message}")
        for issue in issues[:MAX_CONTRACT_ISSUES]
    ]
    if len(issues) > MAX_CONTRACT_ISSUES:
        described.append(make_error_item(ErrorCode.OUTPUT_CONTRACT_INVALID, f"그 밖의 문제 {len(issues) - MAX_CONTRACT_ISSUES}건 생략"))
    return described


def _make_unwritten_result(code: ErrorCode, message: str) -> dict[str, Any]:
    logger.error("결과 파일을 쓰지 않음: %s %s", code, message)
    return {"status": Status.FAILED.value, "artifact_path": None, "artifact_id": None, "sha256": None, "errors": [make_error_item(code, message)]}


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * MILLISECONDS_PER_SECOND)


def _log_summary(document: Mapping[str, Any], path: Path) -> None:
    data = document["data"] or {}
    logger.info("%s %s: 결과 %d·errors %d", document["status"], path, len(data.get("results", [])), len(document["errors"]))


def _exit_code(result: Mapping[str, Any]) -> int:
    if result["artifact_path"] is None:
        return EXIT_NO_FILE
    return {
        Status.COMPLETED.value: EXIT_COMPLETED,
        Status.PARTIAL.value: EXIT_PARTIAL,
        Status.FAILED.value: EXIT_FAILED_WITH_FILE,
    }[result["status"]]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m modules.verifier.entrypoint", description="허용된 재현 계획을 검증해 verification_results.json을 공개"
    )
    parser.add_argument("operation", help=f"공개 operation ({VERIFY_OPERATION})")
    parser.add_argument("--mode", required=True, choices=[mode.value for mode in Mode])
    parser.add_argument("--source-graph-revision", type=int, required=True, help="기준 KG revision")
    parser.add_argument("--input-path", action="append", default=[], help="입력 JSON(test_scenarios·safety_decisions·crawl_result). 3번 준다")
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
        "source_graph_revision": args.source_graph_revision,
    }
    result = run(args.operation, args.input_path, output_dir, context)
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    return _exit_code(result)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.exit(main())
