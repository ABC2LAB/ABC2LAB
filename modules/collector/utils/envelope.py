"""crawl_result.json의 공통 envelope(명세 02 "공통 파일 형식")과 ErrorItem을 만든다."""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "0.2.0"
ARTIFACT_TYPE = "crawl_result"
PRODUCER = "collector"
ARTIFACT_ID_RANDOM_BYTES = 4
UTC_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


class Mode(StrEnum):
    DIAGNOSIS = "diagnosis"
    DEVELOPMENT = "development"


class Status(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class ErrorCode(StrEnum):
    # 파일에 기록하는 작업 오류
    ACCOUNT_CRAWL_FAILED = "ACCOUNT_CRAWL_FAILED"
    CONFIG_INVALID = "CONFIG_INVALID"
    BROWSER_LAUNCH_FAILED = "BROWSER_LAUNCH_FAILED"
    SERVER_CLOCK_REGRESSION = "SERVER_CLOCK_REGRESSION"
    OPERATION_UNSUPPORTED = "OPERATION_UNSUPPORTED"
    INPUT_UNEXPECTED = "INPUT_UNEXPECTED"
    OUTPUT_CONTRACT_INVALID = "OUTPUT_CONTRACT_INVALID"
    EVIDENCE_WRITE_FAILED = "EVIDENCE_WRITE_FAILED"
    # 파일을 쓸 수 없어 반환값으로만 알리는 오류
    CONTEXT_INVALID = "CONTEXT_INVALID"
    OUTPUT_PATH_INVALID = "OUTPUT_PATH_INVALID"
    ARTIFACT_EXISTS = "ARTIFACT_EXISTS"
    OUTPUT_WRITE_FAILED = "OUTPUT_WRITE_FAILED"


@dataclass(frozen=True)
class RunContext:
    """호출자가 준 실행 값. entrypoint가 검증해서 만든다."""

    run_id: str
    iteration: int
    mode: Mode
    run_root: Path
    # collector 설정 TOML과 비밀값 .env. context 키가 아니라 CLI 옵션·환경변수·기본값 순으로 정한다.
    config_path: Path
    secrets_path: Path


@dataclass(frozen=True)
class WorkResult:
    """collect 작업 결과. envelope의 status·errors·data·runtime_metrics가 된다."""

    status: Status
    errors: list[dict[str, Any]]
    # status=failed면 None
    data: dict[str, Any] | None
    duration_ms: int | None


def make_error_item(
    code: ErrorCode, message: str, item_ref: str | None = None, is_retryable: bool = False
) -> dict[str, Any]:
    return {"code": code.value, "message": message, "item_ref": item_ref, "retryable": is_retryable}


def make_runtime_metrics(duration_ms: int | None) -> dict[str, Any]:
    # LLM을 쓰지 않으므로 호출·토큰은 실제 0이다. 메모리는 재지 않아 null.
    return {"duration_ms": duration_ms, "llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "peak_memory_mb": None}


def make_artifact_id(run_context: RunContext) -> str:
    """재실행마다 새 ID가 되도록 난수를 붙인다."""
    suffix = secrets.token_hex(ARTIFACT_ID_RANDOM_BYTES)
    return f"{ARTIFACT_TYPE}-{run_context.run_id}-{run_context.iteration:03d}-{suffix}"


def build_envelope(run_context: RunContext, result: WorkResult) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "artifact_id": make_artifact_id(run_context),
        "run_id": run_context.run_id,
        "iteration": run_context.iteration,
        "producer": PRODUCER,
        "mode": run_context.mode.value,
        "created_at": format_utc(datetime.now(UTC)),
        "status": result.status.value,
        # collector는 입력 산출물을 읽지 않는다.
        "input_refs": [],
        "errors": result.errors,
        "runtime_metrics": make_runtime_metrics(result.duration_ms),
        "data": result.data,
    }


def format_utc(moment: datetime) -> str:
    """UTC RFC3339(Z). 시간대 없는 값은 받지 않는다."""
    if moment.tzinfo is None:
        raise ValueError("시간대 없는 시각은 UTC로 바꿀 수 없음")
    return moment.astimezone(UTC).strftime(UTC_TIME_FORMAT)
