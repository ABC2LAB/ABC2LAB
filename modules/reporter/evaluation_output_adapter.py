"""Evaluation artifact construction, validation, and publication."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from modules.reporter.evaluation_service import build_evaluation_results
from modules.reporter.exceptions import ContractValidationError
from modules.reporter.models import (
    ErrorItem,
    EvaluationBuildResult,
    EvaluationControlResponse,
    EvaluationInputs,
)
from modules.reporter.utils.atomic_writer import write_json_atomically
from modules.reporter.utils.hashing import calculate_sha256
from modules.reporter.utils.validation import validate_schema

EVALUATION_RESULTS_SCHEMA = (
    Path(__file__).parent / "schemas/output/evaluation_results.schema.json"
)


def build_evaluation_artifact(
    inputs: EvaluationInputs,
    started_at: float,
    timestamp_factory: Callable[[], str] | None = None,
) -> dict[str, Any]:
    _require_evaluation_request(inputs)
    create_timestamp = timestamp_factory or _create_timestamp
    result = build_evaluation_results(inputs)
    return _artifact(inputs, result, create_timestamp(), started_at)


def publish_evaluation_artifact(
    inputs: EvaluationInputs,
    artifact: dict[str, Any],
) -> EvaluationControlResponse:
    _require_evaluation_request(inputs)
    _validate_source_integrity(inputs)
    validate_schema(artifact, EVALUATION_RESULTS_SCHEMA)
    validate_evaluation_artifact_against_inputs(artifact, inputs)
    write_json_atomically(inputs.request.output_path, artifact)
    return EvaluationControlResponse(
        status=artifact["status"],
        artifact_id=artifact["artifact_id"],
        output_path=inputs.request.output_relative_path,
        sha256=calculate_sha256(inputs.request.output_path),
        errors=tuple(
            ErrorItem.from_mapping(item) for item in artifact["errors"]
        ),
    )


def validate_evaluation_artifact_against_inputs(
    artifact: Mapping[str, Any],
    inputs: EvaluationInputs,
) -> None:
    request = inputs.request
    expected_context = {
        "artifact_id": _artifact_id(request.run_id, request.iteration),
        "run_id": request.run_id,
        "iteration": request.iteration,
        "mode": "development",
    }
    for field, expected_value in expected_context.items():
        if artifact[field] != expected_value:
            raise ContractValidationError(
                f"evaluation_results {field}가 실행 입력과 다름"
            )
    if artifact["input_refs"] != _input_references(inputs):
        raise ContractValidationError(
            "evaluation_results input_refs가 실제 입력과 다름"
        )

    expected = build_evaluation_results(inputs)
    expected_errors = [item.to_mapping() for item in expected.errors]
    expected_data = expected.data.to_mapping() if expected.data is not None else None
    if artifact["status"] != expected.status:
        raise ContractValidationError(
            "evaluation_results status가 입력 상태와 다름"
        )
    if artifact["errors"] != expected_errors:
        raise ContractValidationError(
            "evaluation_results errors가 입력 오류와 다름"
        )
    if artifact["data"] != expected_data:
        raise ContractValidationError(
            "evaluation_results 지표가 입력과 다름"
        )


def _require_evaluation_request(inputs: EvaluationInputs) -> None:
    request = inputs.request
    if request.operation != "evaluate" or request.mode != "development":
        raise ContractValidationError(
            "evaluation_results는 development evaluate 요청에서만 처리 가능"
        )


def _validate_source_integrity(inputs: EvaluationInputs) -> None:
    for source in inputs.artifacts:
        current_sha256 = calculate_sha256(
            inputs.request.run_root / source.source.relative_path
        )
        if current_sha256 != source.source.sha256:
            raise ContractValidationError(
                f"평가 중 {source.source.artifact_type} 파일이 변경됨"
            )
    project_root = inputs.request.project_root
    if project_root is None:
        raise ContractValidationError("평가 project_root가 없음")
    ground_truth_sha256 = calculate_sha256(
        project_root / inputs.ground_truth.relative_path
    )
    if ground_truth_sha256 != inputs.ground_truth.sha256:
        raise ContractValidationError("평가 중 ground_truth 파일이 변경됨")


def _artifact(
    inputs: EvaluationInputs,
    result: EvaluationBuildResult,
    created_at: str,
    started_at: float,
) -> dict[str, Any]:
    request = inputs.request
    data = result.data.to_mapping() if result.data is not None else None
    return {
        "schema_version": "0.1.0",
        "artifact_type": "evaluation_results",
        "artifact_id": _artifact_id(request.run_id, request.iteration),
        "run_id": request.run_id,
        "iteration": request.iteration,
        "producer": "reporter",
        "mode": "development",
        "created_at": created_at,
        "status": result.status,
        "input_refs": _input_references(inputs),
        "errors": [item.to_mapping() for item in result.errors],
        "runtime_metrics": {
            "duration_ms": max(0, int((perf_counter() - started_at) * 1000)),
            "llm_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "peak_memory_mb": None,
        },
        "data": data,
    }


def _input_references(inputs: EvaluationInputs) -> list[dict[str, Any]]:
    return [
        {
            "artifact_id": artifact.source.artifact_id,
            "artifact_type": artifact.source.artifact_type,
            "iteration": artifact.source.iteration,
            "path": artifact.source.relative_path,
            "sha256": artifact.source.sha256,
        }
        for artifact in inputs.artifacts
    ]


def _artifact_id(run_id: str, iteration: int) -> str:
    return f"evaluation_results_{run_id}_{iteration:03d}"


def _create_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
