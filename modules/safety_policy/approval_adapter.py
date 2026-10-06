"""Approval record loading and boundary validation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from modules.safety_policy.exceptions import (
    ApprovalRecordError,
    ApprovalRecordHashMismatchError,
    ContractValidationError,
)
from modules.safety_policy.models import (
    ApprovalRecord,
    EvaluationInput,
    EvaluationRequest,
    PolicyConfiguration,
)
from modules.safety_policy.utils.hashing import calculate_sha256
from modules.safety_policy.utils.validation import load_json, validate_schema

APPROVAL_RECORD_SCHEMA = (
    Path(__file__).parent / "schemas" / "input" / "approval_record.schema.json"
)


def load_approval_record(
    request: EvaluationRequest,
    evaluation_input: EvaluationInput,
    configuration: PolicyConfiguration,
    now_factory: Callable[[], datetime] | None = None,
) -> ApprovalRecord | None:
    if request.approval_record_path is None:
        return None
    expected_sha256 = request.approval_record_expected_sha256
    if expected_sha256 is None:
        raise ApprovalRecordError("승인 기록 SHA-256 전달값이 없음")
    actual_sha256 = calculate_sha256(request.approval_record_path)
    if actual_sha256 != expected_sha256:
        raise ApprovalRecordHashMismatchError(
            "승인 기록 SHA-256이 실제 파일과 다름"
        )
    try:
        value = load_json(request.approval_record_path)
        validate_schema(value, APPROVAL_RECORD_SCHEMA)
        record = _to_approval_record(value)
        _validate_record_binding(
            record,
            request,
            evaluation_input,
            configuration,
            (now_factory or _utc_now)(),
        )
    except ContractValidationError as error:
        raise ApprovalRecordError("승인 기록 계약이 올바르지 않음") from error
    return record


def _to_approval_record(value: Mapping[str, Any]) -> ApprovalRecord:
    try:
        approved_at = _parse_timestamp(value["approved_at"])
        expires_at = _parse_timestamp(value["expires_at"])
    except (TypeError, ValueError) as error:
        raise ContractValidationError("승인 기록 시각 형식이 올바르지 않음") from error
    return ApprovalRecord(
        approval_id=value["approval_id"],
        run_id=value["run_id"],
        iteration=value["iteration"],
        scenarios_sha256=value["scenarios_sha256"],
        policy_id=value["policy_id"],
        policy_version=value["policy_version"],
        approved_scenario_ids=tuple(value["approved_scenario_ids"]),
        approved_by=value["approved_by"],
        approved_at=approved_at,
        expires_at=expires_at,
    )


def _validate_record_binding(
    record: ApprovalRecord,
    request: EvaluationRequest,
    evaluation_input: EvaluationInput,
    configuration: PolicyConfiguration,
    now: datetime,
) -> None:
    if request.approval_record_path is None:
        raise ApprovalRecordError("승인 기록 경로가 없음")
    if request.approval_record_path.stem != record.approval_id:
        raise ApprovalRecordError("approval_id와 승인 기록 파일명이 다름")
    if record.run_id != request.run_id or record.iteration != request.iteration:
        raise ApprovalRecordError("승인 기록의 실행 범위가 현재 실행과 다름")
    if record.scenarios_sha256 != evaluation_input.source.sha256:
        raise ApprovalRecordError("승인 기록의 계획 해시가 현재 계획과 다름")
    if (
        record.policy_id != configuration.policy_id
        or record.policy_version != configuration.policy_version
    ):
        raise ApprovalRecordError("승인 기록의 Policy가 현재 Policy와 다름")
    scenario_ids = {
        scenario.scenario_id
        for scenario in evaluation_input.scenarios.scenarios
    }
    if not set(record.approved_scenario_ids).issubset(scenario_ids):
        raise ApprovalRecordError("승인 기록이 존재하지 않는 시나리오를 참조함")
    if record.approved_at >= record.expires_at:
        raise ApprovalRecordError("승인 만료 시각이 승인 시각보다 늦지 않음")
    normalized_now = _require_aware_timestamp(now)
    if normalized_now < record.approved_at:
        raise ApprovalRecordError("아직 유효하지 않은 승인 기록임")
    if normalized_now >= record.expires_at:
        raise ApprovalRecordError("만료된 승인 기록임")


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _require_aware_timestamp(parsed)


def _require_aware_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ApprovalRecordError("승인 기록 시각에는 timezone이 필요함")
    return value.astimezone(timezone.utc)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
