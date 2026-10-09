import json
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from modules.safety_policy.approval_adapter import load_approval_record
from modules.safety_policy.config_adapter import load_policy_configuration
from modules.safety_policy.evaluate_adapter import prepare_evaluate_request
from modules.safety_policy.exceptions import (
    ApprovalRecordError,
    ApprovalRecordHashMismatchError,
)
from modules.safety_policy.models import (
    EvaluationInput,
    EvaluationRequest,
    PolicyConfiguration,
)
from modules.safety_policy.service import prepare_evaluation
from modules.safety_policy.utils.hashing import calculate_sha256
from modules.safety_policy.utils.validation import load_json

APPROVAL_RELATIVE_PATH = (
    "private/safety_policy/approvals/approval_demo_001.json"
)
FIXED_NOW = datetime(2026, 10, 6, tzinfo=timezone.utc)


def test_load_approval_record_returns_bound_internal_model(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    evaluate_run_root: Path,
) -> None:
    request, prepared, configuration = _prepare_with_approval(
        evaluate_arguments,
        evaluate_run_root,
    )

    record = load_approval_record(
        request,
        prepared,
        configuration,
        now_factory=lambda: FIXED_NOW,
    )

    assert record is not None
    assert record.approval_id == "approval_demo_001"
    assert record.approved_scenario_ids == ("scenario_update_order_001",)
    assert record.scenarios_sha256 == prepared.source.sha256


def test_load_approval_record_rejects_legacy_scenario_bytes(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    evaluate_run_root: Path,
) -> None:
    input_path = evaluate_run_root / (
        "artifacts/iteration-000/scenario_generator/test_scenarios.json"
    )
    source_bytes = input_path.read_bytes()
    legacy_bytes = source_bytes.replace(
        b'"schema_version": "0.2.0"',
        b'"schema_version": "0.1.0"',
        1,
    )
    assert legacy_bytes != source_bytes
    approval_path = evaluate_run_root / APPROVAL_RELATIVE_PATH
    approval = load_json(approval_path)
    assert approval["schema_version"] == "0.1.0"
    approval["scenarios_sha256"] = sha256(legacy_bytes).hexdigest()
    approval_path.write_text(json.dumps(approval), encoding="utf-8")
    request, prepared, configuration = _prepare_with_approval(
        evaluate_arguments,
        evaluate_run_root,
    )

    with pytest.raises(ApprovalRecordError, match="계획 해시"):
        load_approval_record(
            request,
            prepared,
            configuration,
            now_factory=lambda: FIXED_NOW,
        )


def test_load_approval_record_rejects_hash_mismatch(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    evaluate_run_root: Path,
) -> None:
    request, prepared, configuration = _prepare_with_approval(
        evaluate_arguments,
        evaluate_run_root,
        expected_sha256="0" * 64,
    )

    with pytest.raises(ApprovalRecordHashMismatchError, match="SHA-256"):
        load_approval_record(request, prepared, configuration)


@pytest.mark.parametrize(
    "resource_ids",
    [
        ["opaque-replacement"],
        ["opaque-a", "opaque-z"],
        ["opaque-z", "opaque-a", "opaque-extra"],
    ],
    ids=["replace", "reorder", "append"],
)
def test_load_approval_record_rejects_changed_resource_ids(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    evaluate_run_root: Path,
    resource_ids: list[str],
) -> None:
    input_paths, _, _ = evaluate_arguments
    input_path = evaluate_run_root / input_paths["test_scenarios"]["path"]
    source = load_json(input_path)
    for scenario in source["data"]["scenarios"]:
        scenario["resource_ids"] = ["opaque-z", "opaque-a"]
    input_path.write_text(json.dumps(source), encoding="utf-8")
    approved_hash = calculate_sha256(input_path)
    input_paths["test_scenarios"]["sha256"] = approved_hash
    approval_path = evaluate_run_root / APPROVAL_RELATIVE_PATH
    approval = load_json(approval_path)
    approval["scenarios_sha256"] = approved_hash
    approval_path.write_text(json.dumps(approval), encoding="utf-8")
    request, prepared, configuration = _prepare_with_approval(
        evaluate_arguments,
        evaluate_run_root,
    )
    assert load_approval_record(
        request,
        prepared,
        configuration,
        now_factory=lambda: FIXED_NOW,
    ) is not None

    for scenario in source["data"]["scenarios"]:
        scenario["resource_ids"] = resource_ids.copy()
    input_path.write_text(json.dumps(source), encoding="utf-8")
    input_paths["test_scenarios"]["sha256"] = calculate_sha256(input_path)
    assert input_paths["test_scenarios"]["sha256"] != approved_hash
    request = replace(
        request,
        expected_sha256=input_paths["test_scenarios"]["sha256"],
    )
    prepared = prepare_evaluation(request)

    with pytest.raises(ApprovalRecordError, match="계획 해시"):
        load_approval_record(
            request,
            prepared,
            configuration,
            now_factory=lambda: FIXED_NOW,
        )
    assert load_json(approval_path) == approval


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("run_id", "run_other", "실행 범위"),
        ("iteration", 1, "실행 범위"),
        ("scenarios_sha256", "0" * 64, "계획 해시"),
        ("policy_version", "9.9.9", "Policy"),
        ("approved_scenario_ids", ["scenario_unknown"], "시나리오"),
        ("approval_id", "approval_other", "파일명"),
        ("approved_at", "2100-01-01T00:00:00Z", "늦지 않음"),
        ("expires_at", "2026-01-01T00:00:00Z", "만료"),
    ],
)
def test_load_approval_record_rejects_unbound_or_expired_record(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    evaluate_run_root: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    approval_path = evaluate_run_root / APPROVAL_RELATIVE_PATH
    approval = load_json(approval_path)
    approval[field] = value
    approval_path.write_text(json.dumps(approval), encoding="utf-8")
    request, prepared, configuration = _prepare_with_approval(
        evaluate_arguments,
        evaluate_run_root,
    )

    with pytest.raises(ApprovalRecordError, match=message):
        load_approval_record(
            request,
            prepared,
            configuration,
            now_factory=lambda: FIXED_NOW,
        )


def test_load_approval_record_rejects_schema_violation(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    evaluate_run_root: Path,
) -> None:
    approval_path = evaluate_run_root / APPROVAL_RELATIVE_PATH
    approval = load_json(approval_path)
    approval["unexpected"] = True
    approval_path.write_text(json.dumps(approval), encoding="utf-8")
    request, prepared, configuration = _prepare_with_approval(
        evaluate_arguments,
        evaluate_run_root,
    )

    with pytest.raises(ApprovalRecordError, match="계약"):
        load_approval_record(request, prepared, configuration)


def _prepare_with_approval(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    run_root: Path,
    expected_sha256: str | None = None,
) -> tuple[EvaluationRequest, EvaluationInput, PolicyConfiguration]:
    input_paths, output_dir, context = evaluate_arguments
    approval_path = run_root / APPROVAL_RELATIVE_PATH
    context["approval_record"] = {
        "path": APPROVAL_RELATIVE_PATH,
        "sha256": expected_sha256 or calculate_sha256(approval_path),
    }
    request = prepare_evaluate_request(input_paths, output_dir, context)
    return (
        request,
        prepare_evaluation(request),
        load_policy_configuration(request),
    )
