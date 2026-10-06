"""공통 envelope·ArtifactRef 조립 (02-common-contract.md). 모듈 자체 구현."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

SCHEMA_VERSION = "0.1.0"
ARTIFACT_TYPE = "semantic_analysis"
PRODUCER = "semantic_analyzer"

STATUS_COMPLETED = "completed"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"


@dataclass(frozen=True)
class ArtifactRef:
    artifact_id: str
    artifact_type: str
    iteration: int
    path: str
    sha256: str

    def to_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "iteration": self.iteration,
            "path": self.path,
            "sha256": self.sha256,
        }


def utc_now_rfc3339() -> str:
    """UTC RFC3339 (초 단위, Z 표기)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def null_runtime_metrics() -> dict:
    """측정값을 수집하지 않았을 때의 필수 필드(전부 null)."""
    return {
        "duration_ms": None,
        "llm_calls": None,
        "input_tokens": None,
        "output_tokens": None,
        "peak_memory_mb": None,
    }


def build_envelope(
    *,
    artifact_id: str,
    run_id: str,
    iteration: int,
    mode: str,
    status: str,
    created_at: str,
    input_refs: list[dict],
    errors: list[dict],
    runtime_metrics: dict | None,
    data: dict | None,
) -> dict:
    """status 규칙(02-common-contract)을 지켜 공통 envelope dict를 만든다.

    completed면 errors=[]·data=객체, failed면 data=null, partial이면 errors≥1·data 유효.
    (여기서는 구조만 조립하고 규칙 위반은 출력 Schema 검증에서 다시 걸린다.)
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "artifact_id": artifact_id,
        "run_id": run_id,
        "iteration": iteration,
        "producer": PRODUCER,
        "mode": mode,
        "created_at": created_at,
        "status": status,
        "input_refs": input_refs,
        "errors": errors,
        "runtime_metrics": runtime_metrics,
        "data": data,
    }
