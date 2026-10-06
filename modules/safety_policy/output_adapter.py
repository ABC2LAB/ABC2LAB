"""Safety decision artifact construction, validation, and publication."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from modules.safety_policy.contracts import (
    SAFETY_DECISIONS_SCHEMA,
    load_test_scenarios,
)
from modules.safety_policy.exceptions import ContractValidationError
from modules.safety_policy.models import (
    ErrorItem,
    EvaluationControlResponse,
    EvaluationInput,
    SafetyDecisionsData,
)
from modules.safety_policy.utils.atomic_writer import write_json_atomically
from modules.safety_policy.utils.hashing import calculate_sha256
from modules.safety_policy.utils.validation import (
    validate_safety_decisions_against_scenarios,
    validate_safety_decisions_semantics,
    validate_schema,
)


@dataclass(frozen=True)
class _ArtifactValues:
    created_at: str
    status: str
    errors: tuple[ErrorItem, ...]
    data: dict[str, Any] | None
    started_at: float


def build_evaluation_artifact(
    prepared: EvaluationInput,
    data: SafetyDecisionsData,
    started_at: float,
    timestamp_factory: Callable[[], str] | None = None,
) -> dict[str, Any]:
    create_timestamp = timestamp_factory or _create_timestamp
    errors = prepared.source_errors
    return _artifact(
        prepared,
        _ArtifactValues(
            created_at=create_timestamp(),
            status="partial" if errors else "completed",
            errors=errors,
            data=data.to_mapping(),
            started_at=started_at,
        ),
    )


def build_failed_evaluation_artifact(
    prepared: EvaluationInput,
    error: ErrorItem,
    started_at: float,
    timestamp_factory: Callable[[], str] | None = None,
) -> dict[str, Any]:
    create_timestamp = timestamp_factory or _create_timestamp
    return _artifact(
        prepared,
        _ArtifactValues(
            created_at=create_timestamp(),
            status="failed",
            errors=(*prepared.source_errors, error),
            data=None,
            started_at=started_at,
        ),
    )


def publish_evaluation_artifact(
    prepared: EvaluationInput,
    artifact: dict[str, Any],
) -> EvaluationControlResponse:
    _validate_source_integrity(prepared, artifact)
    validate_schema(artifact, SAFETY_DECISIONS_SCHEMA)
    validate_safety_decisions_semantics(artifact)
    scenarios_artifact, _ = load_test_scenarios(prepared.request.input_path)
    validate_safety_decisions_against_scenarios(
        artifact,
        scenarios_artifact,
        prepared.request.input_path,
    )
    write_json_atomically(prepared.request.output_path, artifact)
    output_sha256 = calculate_sha256(prepared.request.output_path)
    return EvaluationControlResponse(
        status=artifact["status"],
        artifact_id=artifact["artifact_id"],
        output_path=prepared.request.output_relative_path,
        sha256=output_sha256,
        errors=tuple(ErrorItem.from_mapping(item) for item in artifact["errors"]),
    )


def _validate_source_integrity(
    prepared: EvaluationInput,
    artifact: dict[str, Any],
) -> None:
    request = prepared.request
    scenario_sha256 = calculate_sha256(request.input_path)
    if scenario_sha256 != prepared.source.sha256:
        raise ContractValidationError("평가 중 test_scenarios 파일이 변경됨")
    error_codes = {error["code"] for error in artifact["errors"]}
    if "CONFIG_HASH_MISMATCH" not in error_codes:
        policy_sha256 = calculate_sha256(request.policy_config_path)
        if policy_sha256 != request.policy_config_expected_sha256:
            raise ContractValidationError("평가 중 Policy 설정 파일이 변경됨")
    if (
        request.approval_record_path is not None
        and "APPROVAL_HASH_MISMATCH" not in error_codes
    ):
        approval_sha256 = calculate_sha256(request.approval_record_path)
        if approval_sha256 != request.approval_record_expected_sha256:
            raise ContractValidationError("평가 중 승인 기록 파일이 변경됨")


def _artifact(
    prepared: EvaluationInput,
    values: _ArtifactValues,
) -> dict[str, Any]:
    source = prepared.source
    request = prepared.request
    return {
        "schema_version": "0.1.0",
        "artifact_type": "safety_decisions",
        "artifact_id": f"safety_decisions_{request.run_id}_{request.iteration:03d}",
        "run_id": request.run_id,
        "iteration": request.iteration,
        "producer": "safety_policy",
        "mode": request.mode,
        "created_at": values.created_at,
        "status": values.status,
        "input_refs": [
            {
                "artifact_id": source.artifact_id,
                "artifact_type": source.artifact_type,
                "iteration": source.iteration,
                "path": source.relative_path,
                "sha256": source.sha256,
            }
        ],
        "errors": [error.to_mapping() for error in values.errors],
        "runtime_metrics": {
            "duration_ms": max(
                0,
                int((perf_counter() - values.started_at) * 1000),
            ),
            "llm_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "peak_memory_mb": None,
        },
        "data": values.data,
    }


def _create_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
