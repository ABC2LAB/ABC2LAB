"""collector 공개 실행 창구: run(operation, input_paths, output_dir, context)와 CLI.

    .venv/bin/python -m modules.collector.entrypoint collect --mode development [--run-id ID] [--iteration 0]
        [--runs-dir runs] [--config 설정.toml] [--secrets .env]

처리 순서: 실행 값 검증 → 수집(service) → 공개 형식 변환(export·envelope) → 자기 출력 검증(validation)
→ 원자적 공개(storage). 반환값과 CLI stdout 한 줄은 {status, artifact_path, artifact_id, sha256, errors}다.
파일을 쓰지 못했으면 artifact_path·artifact_id·sha256이 null이고 이유는 errors에 있다.

context 키는 명세 03 실행 값만 받는다: run_id·iteration·mode·run_root(신뢰된 루트, runs/<run_id>).
대상·역할·계정·실행 제한은 collector 설정 TOML에서, 계정 로그인 ID·비밀번호는 비밀값 .env에서 읽는다.
위치는 각각 CLI 옵션 > 환경변수 > 기본값 순: --config > COLLECTOR_CONFIG_PATH > modules/collector/configs/collector.toml,
--secrets > COLLECTOR_SECRETS_PATH > .env.

세션 공개 창구: open_session_executor()가 같은 설정 위치(환경변수 > 기본값)로 창구를 열 컨텍스트 매니저를 돌려준다.
런너가 이걸로 verifier에 창구를 주입한다(명세 03). 런너는 collector 설정 타입을 몰라도 된다.
"""

import argparse
import hashlib
import json
import logging
import os
import re
import secrets
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from modules.collector import service, session_gateway
from modules.collector.core.config import ConfigError, load_config_files
from modules.collector.utils import export
from modules.collector.utils.envelope import (
    ErrorCode,
    Mode,
    RunContext,
    Status,
    WorkResult,
    build_envelope,
    make_error_item,
)
from modules.collector.utils.evidence import EvidenceWriter
from modules.collector.utils.storage import (
    ArtifactExistsError,
    OutputPathError,
    StorageError,
    prepare_output_dir,
    publish_file,
    serialize_json,
)
from modules.collector.utils.validation import (
    ARTIFACT_FILE_NAME,
    ARTIFACTS_DIR_NAME,
    ITERATION_DIR_FORMAT,
    PRODUCER,
    validate_crawl_result_bytes,
)

logger = logging.getLogger(__name__)

COLLECT_OPERATION = "collect"
REQUIRED_CONTEXT_KEYS = frozenset({"run_id", "iteration", "mode", "run_root"})
# 설정 파일 위치는 실행 값이 아니라 collector 자기 설정이라 context가 아니라 환경변수로 받는다.
CONFIG_PATH_ENV = "COLLECTOR_CONFIG_PATH"
SECRETS_PATH_ENV = "COLLECTOR_SECRETS_PATH"
DEFAULT_CONFIG_PATH = Path("modules") / "collector" / "configs" / "collector.toml"
DEFAULT_SECRETS_PATH = Path(".env")
DEFAULT_RUNS_DIR = Path("runs")
# 폴더 이름으로 쓰이므로 경로 구분자·..가 들어갈 수 없는 모양만 받는다.
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
RUN_ID_TIME_FORMAT = "%Y%m%d-%H%M%S"
RUN_ID_RANDOM_BYTES = 2
MILLISECONDS_PER_SECOND = 1000
# 자기 출력 검증에 실패했을 때 errors로 옮길 문제 수 상한. 나머지는 개수만 알린다.
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
    """collect를 실행하고 crawl_result.json을 output_dir에 공개한다. 예외 대신 결과 객체로 알린다."""
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
    if (artifact_dir / ARTIFACT_FILE_NAME).exists():
        return _make_unwritten_result(ErrorCode.ARTIFACT_EXISTS, f"이미 공개된 파일이 있음: {ARTIFACT_FILE_NAME}")
    call_errors = _check_call(operation, input_paths)
    if call_errors:
        work = WorkResult(Status.FAILED, call_errors, None, _elapsed_ms(started))
        return _publish(run_context, artifact_dir, work, ())
    work, known_secrets = _collect(run_context, started)
    return _publish(run_context, artifact_dir, work, known_secrets)


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
    config_path, secrets_path = _resolve_config_paths()
    return RunContext(run_id, iteration, Mode(context["mode"]), run_root, config_path, secrets_path)


def resolve_path(env_key: str, default: Path) -> Path:
    """환경변수가 있으면 그 경로, 없거나 비었으면 기본값(현재 폴더 기준)."""
    configured = os.environ.get(env_key, "").strip()
    return Path(configured) if configured else default


def open_session_executor() -> AbstractContextManager[session_gateway.BrowserSessionExecutor]:
    """런너가 verifier에 주입할 세션 공개 창구. 런너는 collector 설정 타입을 몰라도 된다.

    설정 위치는 collect와 같다(COLLECTOR_CONFIG_PATH > 기본값, 계정 값은 COLLECTOR_SECRETS_PATH > .env).
    설정이 없거나 틀리면 호출 즉시 ValueError(메시지는 위치·키 이름만). 브라우저를 못 띄우면 with 진입 때 RuntimeError.
    """
    config_path, secrets_path = _resolve_config_paths()
    # 설정을 먼저 읽어 브라우저를 띄우기 전에 실패한다. 반환은 session_gateway의 컨텍스트 매니저 그대로.
    return session_gateway.open_session_executor(load_config_files(config_path, secrets_path))


def _resolve_config_paths() -> tuple[Path, Path]:
    """collect와 세션 창구가 같은 규칙으로 설정·비밀값 파일 위치를 정한다."""
    return resolve_path(CONFIG_PATH_ENV, DEFAULT_CONFIG_PATH), resolve_path(SECRETS_PATH_ENV, DEFAULT_SECRETS_PATH)


def make_run_id(now: datetime) -> str:
    """시각 + 짧은 난수. 같은 초에 두 번 돌려도 실행이 갈린다."""
    return f"{now.strftime(RUN_ID_TIME_FORMAT)}-{secrets.token_hex(RUN_ID_RANDOM_BYTES)}"


def _check_call(operation: str, input_paths: Sequence[str | Path]) -> list[dict[str, Any]]:
    errors = []
    if operation != COLLECT_OPERATION:
        errors.append(make_error_item(ErrorCode.OPERATION_UNSUPPORTED, f"collector operation은 {COLLECT_OPERATION}뿐"))
    if input_paths:
        errors.append(make_error_item(ErrorCode.INPUT_UNEXPECTED, "collector는 입력 JSON을 받지 않음"))
    return errors


def _collect(run_context: RunContext, started: float) -> tuple[WorkResult, tuple[str, ...]]:
    """설정 읽기 → 탐색 → 변환. 돌려주는 비밀값은 공개 전 노출 검사에 쓴다."""
    try:
        config = load_config_files(run_context.config_path, run_context.secrets_path)
    except ConfigError as error:
        return _make_failed(started, ErrorCode.CONFIG_INVALID, str(error), is_retryable=False), ()
    known_secrets = config.known_passwords
    try:
        outcome = service.collect(config)
    except service.BrowserLaunchError as error:
        return _make_failed(started, ErrorCode.BROWSER_LAUNCH_FAILED, str(error), is_retryable=True), known_secrets
    account_errors = export.list_account_errors(outcome)
    if outcome.clock_regressions:
        # 절대 규칙 7: 시계 역행이 섞인 결과는 쓰지 않고 다시 돌린다. 데이터는 공개하지 않는다.
        message = f"대상 서버 시계 역행 {outcome.clock_regressions}회 감지: 결과를 쓰지 말고 다시 돌려야 함"
        clock_error = make_error_item(ErrorCode.SERVER_CLOCK_REGRESSION, message, is_retryable=True)
        return WorkResult(Status.FAILED, [clock_error, *account_errors], None, _elapsed_ms(started)), known_secrets
    try:
        data = export.build_data(config, outcome, EvidenceWriter(run_context.run_root, known_secrets))
    except (StorageError, OSError) as error:
        # 같은 run에 근거 파일이 이미 있거나(재실행·다른 회차) 쓸 수 없다. 새 run_id로 다시 돌려야 한다.
        message = f"근거 파일을 쓰지 못함: {type(error).__name__} {error}"
        return _make_failed(started, ErrorCode.EVIDENCE_WRITE_FAILED, message, is_retryable=False), known_secrets
    if not account_errors:
        return WorkResult(Status.COMPLETED, [], data, _elapsed_ms(started)), known_secrets
    if export.has_observations(data):
        return WorkResult(Status.PARTIAL, account_errors, data, _elapsed_ms(started)), known_secrets
    return WorkResult(Status.FAILED, account_errors, None, _elapsed_ms(started)), known_secrets


def _publish(
    run_context: RunContext, artifact_dir: Path, work: WorkResult, known_secrets: Iterable[str]
) -> dict[str, Any]:
    """자기 출력 검증을 통과한 바이트만 공개한다. 통과 못 하면 데이터 없이 failed로 공개한다."""
    known_secrets = tuple(known_secrets)
    document = build_envelope(run_context, work)
    raw = serialize_json(document)
    issues = validate_crawl_result_bytes(raw, run_context.run_root, known_secrets)
    if issues:
        logger.error("자기 출력 검증 실패 %d건: 데이터 없이 failed로 공개", len(issues))
        failed_work = WorkResult(Status.FAILED, _describe_issues(issues), None, work.duration_ms)
        document = build_envelope(run_context, failed_work)
        raw = serialize_json(document)
        if validate_crawl_result_bytes(raw, run_context.run_root, known_secrets):
            return _make_unwritten_result(ErrorCode.OUTPUT_CONTRACT_INVALID, "failed 결과도 계약 검증을 통과하지 못함")
    try:
        path = publish_file(artifact_dir, ARTIFACT_FILE_NAME, raw)
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


def _make_failed(started: float, code: ErrorCode, message: str, is_retryable: bool) -> WorkResult:
    error = make_error_item(code, message, is_retryable=is_retryable)
    return WorkResult(Status.FAILED, [error], None, _elapsed_ms(started))


def _make_unwritten_result(code: ErrorCode, message: str) -> dict[str, Any]:
    logger.error("결과 파일을 쓰지 않음: %s %s", code, message)
    error = make_error_item(code, message)
    return {
        "status": Status.FAILED.value,
        "artifact_path": None,
        "artifact_id": None,
        "sha256": None,
        "errors": [error],
    }


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * MILLISECONDS_PER_SECOND)


def _log_summary(document: Mapping[str, Any], path: Path) -> None:
    """runs/는 직접 열어보지 않으므로 건수로 결과를 확인한다. 값은 남기지 않는다."""
    data = document["data"] or {}
    logger.info(
        "%s %s: 계정 %d·페이지 %d·행동 %d·요청 %d·errors %d",
        document["status"],
        path,
        len(data.get("accounts", [])),
        len(data.get("pages", [])),
        len(data.get("actions", [])),
        len(data.get("requests", [])),
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
        prog="python -m modules.collector.entrypoint", description="역할별로 대상 앱을 탐색해 crawl_result.json을 공개"
    )
    parser.add_argument("operation", help=f"공개 operation ({COLLECT_OPERATION})")
    parser.add_argument("--mode", required=True, choices=[mode.value for mode in Mode])
    parser.add_argument("--run-id", help="없으면 시각+난수로 만든다")
    parser.add_argument("--iteration", type=int, default=0)
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR, help=f"기본 {DEFAULT_RUNS_DIR}")
    parser.add_argument(
        "--config",
        type=Path,
        help=f"collector 설정 TOML (없으면 {CONFIG_PATH_ENV}, 그것도 없으면 {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--secrets",
        type=Path,
        help=f"계정 로그인 ID·비밀번호 .env (없으면 {SECRETS_PATH_ENV}, 그것도 없으면 {DEFAULT_SECRETS_PATH})",
    )
    args = parser.parse_args(argv)

    run_id = args.run_id or make_run_id(datetime.now(UTC))
    run_root = args.runs_dir / run_id
    output_dir = run_root / ARTIFACTS_DIR_NAME / ITERATION_DIR_FORMAT.format(args.iteration) / PRODUCER
    context = {
        "run_id": run_id,
        "iteration": args.iteration,
        "mode": args.mode,
        "run_root": str(run_root),
    }
    # run()은 context에 명세 실행 값만 받으므로 설정 파일 위치는 이 프로세스의 환경변수로 넘긴다.
    if args.config is not None:
        os.environ[CONFIG_PATH_ENV] = str(args.config)
    if args.secrets is not None:
        os.environ[SECRETS_PATH_ENV] = str(args.secrets)
    result = run(args.operation, [], output_dir, context)
    # stdout은 호출자가 읽는 결과 한 줄이다. 로그는 stderr로 간다.
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    return _exit_code(result)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.exit(main())
