"""verification_results.json의 공통 envelope(명세 02)과 ErrorItem·RunContext.

다른 모듈 코드를 import하지 않고 자기 폴더 안에서 만든다(명세 03·m7).
"""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "0.1.0"
PRODUCER = "verifier"
ARTIFACT_TYPE = "verification_results"
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
    OPERATION_UNSUPPORTED = "OPERATION_UNSUPPORTED"
    INPUT_MISSING = "INPUT_MISSING"
    INPUT_UNEXPECTED = "INPUT_UNEXPECTED"
    INPUT_CONTRACT_INVALID = "INPUT_CONTRACT_INVALID"
    SCENARIOS_HASH_MISMATCH = "SCENARIOS_HASH_MISMATCH"
    OUTPUT_CONTRACT_INVALID = "OUTPUT_CONTRACT_INVALID"
    # 시나리오별 판단불가 사유(VerificationItem.errors에도 쓰임)
    POLICY_DECISION_MISSING = "POLICY_DECISION_MISSING"
    ACCOUNT_SCOPE_MISMATCH = "ACCOUNT_SCOPE_MISMATCH"
    ROLE_MISMATCH = "ROLE_MISMATCH"
    SESSION_INVALID = "SESSION_INVALID"
    # 실행 중 미전송·중단 사유. 모두 result=indeterminate(미실행·판단불가를 '없음'으로 합치지 않는다).
    ORIGIN_OUT_OF_SCOPE = "ORIGIN_OUT_OF_SCOPE"
    STATE_CHANGE_NOT_ALLOWED = "STATE_CHANGE_NOT_ALLOWED"
    STATE_RESET_UNAVAILABLE = "STATE_RESET_UNAVAILABLE"
    BODY_REF_UNAVAILABLE = "BODY_REF_UNAVAILABLE"
    BINDING_UNRESOLVED = "BINDING_UNRESOLVED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    TRANSPORT_ERROR = "TRANSPORT_ERROR"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    SESSION_UNAVAILABLE = "SESSION_UNAVAILABLE"
    # 참인 assertion이 상태 코드·세션 유효성뿐이라 응답 내용 확인 없이 success로 올리지 않음(m7 51행).
    ASSERTION_STATUS_ONLY = "ASSERTION_STATUS_ONLY"
    # 파일을 쓸 수 없어 반환값으로만 알리는 오류
    CONTEXT_INVALID = "CONTEXT_INVALID"
    OUTPUT_PATH_INVALID = "OUTPUT_PATH_INVALID"
    INPUT_PATH_INVALID = "INPUT_PATH_INVALID"
    ARTIFACT_EXISTS = "ARTIFACT_EXISTS"
    OUTPUT_WRITE_FAILED = "OUTPUT_WRITE_FAILED"


@dataclass(frozen=True)
class RunContext:
    """호출자가 준 실행 값. entrypoint가 검증해서 만든다.

    source_graph_revision은 시나리오·후보의 기준 KG revision으로 실행 인자(명세 m7 "기준 graph_revision")에서 받는다.
    """

    run_id: str
    iteration: int
    mode: Mode
    run_root: Path
    source_graph_revision: int


@dataclass(frozen=True)
class WorkResult:
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
    # verifier는 LLM을 쓰지 않으므로 호출·토큰은 실제 0이다. 메모리는 재지 않아 null.
    return {"duration_ms": duration_ms, "llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "peak_memory_mb": None}


def make_artifact_id(run_context: RunContext) -> str:
    suffix = secrets.token_hex(ARTIFACT_ID_RANDOM_BYTES)
    return f"{ARTIFACT_TYPE}-{run_context.run_id}-{run_context.iteration:03d}-{suffix}"


def build_envelope(
    run_context: RunContext, result: WorkResult, input_refs: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
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
        "input_refs": input_refs if input_refs is not None else [],
        "errors": result.errors,
        "runtime_metrics": make_runtime_metrics(result.duration_ms),
        "data": result.data,
    }


def format_utc(moment: datetime) -> str:
    """UTC RFC3339(Z). 시간대 없는 값은 받지 않는다."""
    if moment.tzinfo is None:
        raise ValueError("시간대 없는 시각은 UTC로 바꿀 수 없음")
    return moment.astimezone(UTC).strftime(UTC_TIME_FORMAT)
