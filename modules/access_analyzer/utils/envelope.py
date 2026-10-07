"""access_analyzer 산출물의 공통 envelope(명세 02 "공통 파일 형식")과 ErrorItem·RunContext.

prepare_queries는 graph_query, analyze는 vulnerability_candidates를 낸다. 둘 다 같은 envelope을 쓰고
artifact_type만 다르다. 다른 모듈 코드를 import하지 않고 자기 폴더 안에서 만든다(명세 03·m4).
"""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "0.1.0"
PRODUCER = "access_analyzer"
ARTIFACT_TYPE_GRAPH_QUERY = "graph_query"
ARTIFACT_TYPE_CANDIDATES = "vulnerability_candidates"
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
    INPUT_UNEXPECTED = "INPUT_UNEXPECTED"
    OUTPUT_CONTRACT_INVALID = "OUTPUT_CONTRACT_INVALID"
    # analyze 입력·판정 오류 (파일에 기록)
    INPUT_MISSING = "INPUT_MISSING"
    INPUT_CONTRACT_INVALID = "INPUT_CONTRACT_INVALID"
    INPUT_RESULT_FAILED = "INPUT_RESULT_FAILED"
    GRAPH_REVISION_STALE = "GRAPH_REVISION_STALE"
    GRAPH_ID_MISMATCH = "GRAPH_ID_MISMATCH"
    RUN_ID_MISMATCH = "RUN_ID_MISMATCH"
    QUERY_RESULT_PARTIAL = "QUERY_RESULT_PARTIAL"
    # 파일을 쓸 수 없어 반환값으로만 알리는 오류
    CONTEXT_INVALID = "CONTEXT_INVALID"
    OUTPUT_PATH_INVALID = "OUTPUT_PATH_INVALID"
    INPUT_PATH_INVALID = "INPUT_PATH_INVALID"
    ARTIFACT_EXISTS = "ARTIFACT_EXISTS"
    OUTPUT_WRITE_FAILED = "OUTPUT_WRITE_FAILED"


@dataclass(frozen=True)
class RunContext:
    """호출자가 준 실행 값. entrypoint가 검증해서 만든다.

    graph_id·expected_graph_revision은 KG 준비 상태(명세 03 63행 "필요한 graph 상태")로 받는다.
    expected_graph_revision이 null이면 현재 revision을 조회하고 결과에서 실제 값을 받는다.
    """

    run_id: str
    iteration: int
    mode: Mode
    run_root: Path
    graph_id: str
    expected_graph_revision: int | None


@dataclass(frozen=True)
class WorkResult:
    """operation 결과. envelope의 status·errors·data·runtime_metrics가 된다."""

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
    # prepare_queries는 LLM을 쓰지 않으므로 호출·토큰은 실제 0이다. 메모리는 재지 않아 null.
    return {"duration_ms": duration_ms, "llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "peak_memory_mb": None}


def make_artifact_id(artifact_type: str, run_context: RunContext) -> str:
    """재실행마다 새 ID가 되도록 난수를 붙인다."""
    suffix = secrets.token_hex(ARTIFACT_ID_RANDOM_BYTES)
    return f"{artifact_type}-{run_context.run_id}-{run_context.iteration:03d}-{suffix}"


def build_envelope(
    artifact_type: str,
    run_context: RunContext,
    result: WorkResult,
    input_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    # prepare_queries는 입력 산출물을 읽지 않아 []. analyze는 graph_query_result ArtifactRef를 넘긴다.
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": artifact_type,
        "artifact_id": make_artifact_id(artifact_type, run_context),
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
