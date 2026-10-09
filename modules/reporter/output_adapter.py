"""Diagnosis report artifact construction, validation, and publication."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from modules.reporter.exceptions import ContractValidationError
from modules.reporter.models import (
    DiagnosisBuildResult,
    ErrorItem,
    ReportControlResponse,
    ReportInputs,
)
from modules.reporter.service import build_diagnosis_report
from modules.reporter.utils.atomic_writer import write_json_atomically
from modules.reporter.utils.hashing import calculate_sha256
from modules.reporter.utils.validation import validate_schema

DIAGNOSIS_REPORT_SCHEMA = (
    Path(__file__).parent / "schemas/output/diagnosis_report.schema.json"
)


def build_diagnosis_artifact(
    inputs: ReportInputs,
    started_at: float,
    timestamp_factory: Callable[[], str] | None = None,
) -> dict[str, Any]:
    if inputs.request.operation != "report":
        raise ContractValidationError("diagnosis_report는 report 요청에서만 생성 가능")
    create_timestamp = timestamp_factory or _create_timestamp
    result = build_diagnosis_report(inputs)
    return _artifact(inputs, result, create_timestamp(), started_at)


def publish_diagnosis_artifact(
    inputs: ReportInputs,
    artifact: dict[str, Any],
) -> ReportControlResponse:
    if inputs.request.operation != "report":
        raise ContractValidationError("diagnosis_report는 report 요청에서만 저장 가능")
    _validate_source_integrity(inputs)
    validate_schema(artifact, DIAGNOSIS_REPORT_SCHEMA)
    validate_diagnosis_artifact_against_inputs(artifact, inputs)
    write_json_atomically(inputs.request.output_path, artifact)
    return ReportControlResponse(
        status=artifact["status"],
        artifact_id=artifact["artifact_id"],
        output_path=inputs.request.output_relative_path,
        sha256=calculate_sha256(inputs.request.output_path),
        errors=tuple(
            ErrorItem.from_mapping(item) for item in artifact["errors"]
        ),
    )


def validate_diagnosis_artifact_against_inputs(
    artifact: Mapping[str, Any],
    inputs: ReportInputs,
) -> None:
    request = inputs.request
    expected_context = {
        "artifact_id": _artifact_id(request.run_id, request.iteration),
        "run_id": request.run_id,
        "iteration": request.iteration,
        "mode": request.mode,
    }
    for field, expected_value in expected_context.items():
        if artifact[field] != expected_value:
            raise ContractValidationError(
                f"diagnosis_report {field}가 실행 입력과 다름"
            )
    if artifact["input_refs"] != _input_references(inputs):
        raise ContractValidationError(
            "diagnosis_report input_refs가 실제 입력과 다름"
        )

    expected = build_diagnosis_report(inputs)
    expected_errors = [item.to_mapping() for item in expected.errors]
    expected_data = expected.data.to_mapping() if expected.data is not None else None
    if artifact["status"] != expected.status:
        raise ContractValidationError("diagnosis_report status 분류가 입력과 다름")
    if artifact["errors"] != expected_errors:
        raise ContractValidationError("diagnosis_report errors가 입력 오류와 다름")
    if artifact["data"] != expected_data:
        raise ContractValidationError("diagnosis_report data 집계가 입력과 다름")


def _validate_source_integrity(inputs: ReportInputs) -> None:
    for source in inputs.artifacts:
        current_sha256 = calculate_sha256(
            inputs.request.run_root / source.source.relative_path
        )
        if current_sha256 != source.source.sha256:
            raise ContractValidationError(
                f"리포트 생성 중 {source.source.artifact_type} 파일이 변경됨"
            )


def _artifact(
    inputs: ReportInputs,
    result: DiagnosisBuildResult,
    created_at: str,
    started_at: float,
) -> dict[str, Any]:
    request = inputs.request
    data = result.data.to_mapping() if result.data is not None else None
    return {
        "schema_version": "0.2.0",
        "artifact_type": "diagnosis_report",
        "artifact_id": _artifact_id(request.run_id, request.iteration),
        "run_id": request.run_id,
        "iteration": request.iteration,
        "producer": "reporter",
        "mode": request.mode,
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


def _input_references(inputs: ReportInputs) -> list[dict[str, Any]]:
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
    return f"diagnosis_report_{run_id}_{iteration:03d}"


def _create_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
