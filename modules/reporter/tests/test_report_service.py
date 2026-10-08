from dataclasses import replace
from time import perf_counter
from typing import Any

import pytest

from modules.reporter.contracts import prepare_report_inputs
from modules.reporter.exceptions import (
    ContractValidationError,
    OutputArtifactExistsError,
)
from modules.reporter.input_adapter import parse_report_request
from modules.reporter.models import ErrorItem, InputArtifact, ReportInputs
from modules.reporter.output_adapter import (
    DIAGNOSIS_REPORT_SCHEMA,
    build_diagnosis_artifact,
    publish_diagnosis_artifact,
    validate_diagnosis_artifact_against_inputs,
)
from modules.reporter.service import build_diagnosis_report
from modules.reporter.utils.hashing import calculate_sha256
from modules.reporter.utils.validation import load_json, validate_schema

Arguments = tuple[dict[str, Any], str, dict[str, Any]]
FIXED_TIMESTAMP = "2026-10-07T03:00:00Z"


@pytest.fixture
def prepared_report(report_arguments: Arguments) -> ReportInputs:
    input_paths, output_dir, context = report_arguments
    request = parse_report_request(input_paths, output_dir, context)
    return prepare_report_inputs(request)


def test_build_diagnosis_report_classifies_and_preserves_candidates(
    prepared_report: ReportInputs,
) -> None:
    result = build_diagnosis_report(prepared_report)

    assert result.status == "completed"
    assert result.errors == ()
    assert result.data is not None
    assert result.data.summary.to_mapping() == {
        "candidate_count": 3,
        "confirmed_count": 1,
        "not_confirmed_count": 0,
        "suspected_count": 0,
        "indeterminate_count": 1,
        "policy_blocked_count": 0,
        "approval_pending_count": 1,
    }
    finding_by_candidate = {
        item.candidate_id: item for item in result.data.findings
    }
    assert finding_by_candidate["candidate_idor_001"].status == "confirmed"
    assert (
        finding_by_candidate["candidate_workflow_001"].status
        == "approval_pending"
    )
    unplanned = finding_by_candidate["candidate_unplanned_001"]
    assert unplanned.status == "indeterminate"
    assert unplanned.scenario_id is None
    assert unplanned.verification_id is None
    assert any("candidate_unplanned_001" in item for item in result.data.limitations)


@pytest.mark.parametrize(
    ("expected_basis", "result_name", "has_evidence", "has_errors", "status"),
    [
        ("rule", "success", True, False, "confirmed"),
        ("inferred", "success", True, False, "suspected"),
        ("unknown", "success", True, False, "indeterminate"),
        ("rule", "success", False, False, "suspected"),
        ("rule", "failure", True, False, "not_confirmed"),
        ("rule", "indeterminate", True, False, "indeterminate"),
        ("rule", "success", True, True, "indeterminate"),
    ],
)
def test_build_diagnosis_report_uses_conservative_status_rules(
    prepared_report: ReportInputs,
    expected_basis: str,
    result_name: str,
    has_evidence: bool,
    has_errors: bool,
    status: str,
) -> None:
    candidate = replace(
        prepared_report.candidates[0],
        expected_basis=expected_basis,
    )
    scenario = replace(
        prepared_report.scenarios[0],
        expected_basis=expected_basis,
    )
    verification = replace(
        prepared_report.verification_results[0],
        result=result_name,
        evidence_refs=(
            prepared_report.verification_results[0].evidence_refs
            if has_evidence
            else ()
        ),
        step_evidence_refs=(),
        errors=(
            (
                ErrorItem(
                    code="SESSION_WARNING",
                    message="실행 중 오류가 기록됨",
                    item_ref="scenario_idor_001",
                    retryable=True,
                ),
            )
            if has_errors
            else ()
        ),
    )
    changed = replace(
        prepared_report,
        candidates=(candidate, *prepared_report.candidates[1:]),
        scenarios=(scenario, *prepared_report.scenarios[1:]),
        verification_results=(
            verification,
            *prepared_report.verification_results[1:],
        ),
    )

    report = build_diagnosis_report(changed)

    assert report.data is not None
    finding = next(
        item
        for item in report.data.findings
        if item.candidate_id == candidate.candidate_id
    )
    assert finding.status == status


def test_build_diagnosis_report_keeps_allowed_missing_result_indeterminate(
    prepared_report: ReportInputs,
) -> None:
    changed = replace(
        prepared_report,
        verification_results=prepared_report.verification_results[1:],
    )

    result = build_diagnosis_report(changed)

    assert result.data is not None
    finding = next(
        item
        for item in result.data.findings
        if item.candidate_id == "candidate_idor_001"
    )
    assert finding.status == "indeterminate"
    assert finding.verification_id is None
    assert any("검증 결과 누락" in item for item in result.data.limitations)


def test_build_diagnosis_report_distinguishes_policy_block(
    prepared_report: ReportInputs,
) -> None:
    blocked_decision = replace(
        prepared_report.decisions[1],
        decision="block",
        reason="상태 변경 실행을 차단함",
    )
    blocked_result = replace(
        prepared_report.verification_results[1],
        policy_decision="block",
    )
    changed = replace(
        prepared_report,
        decisions=(prepared_report.decisions[0], blocked_decision),
        verification_results=(
            prepared_report.verification_results[0],
            blocked_result,
        ),
    )

    result = build_diagnosis_report(changed)

    assert result.data is not None
    finding = next(
        item
        for item in result.data.findings
        if item.candidate_id == "candidate_workflow_001"
    )
    assert finding.status == "policy_blocked"
    assert result.data.summary.policy_blocked_count == 1
    assert result.data.summary.approval_pending_count == 0


def test_build_diagnosis_report_propagates_partial_upstream_error(
    prepared_report: ReportInputs,
) -> None:
    source = prepared_report.artifacts[0]
    partial_source = replace(
        source,
        source=replace(source.source, status="partial"),
        errors=(
            ErrorItem(
                code="CANDIDATE_SKIPPED",
                message="일부 후보 생성 실패",
                item_ref="request_missing",
                retryable=False,
            ),
        ),
    )
    changed = replace(
        prepared_report,
        artifacts=(partial_source, *prepared_report.artifacts[1:]),
    )

    result = build_diagnosis_report(changed)

    assert result.status == "partial"
    assert result.data is not None
    assert result.errors[0].code.endswith("CANDIDATE_SKIPPED")
    assert any("일부 후보 생성 실패" in item for item in result.data.limitations)
    artifact = build_diagnosis_artifact(changed, perf_counter())
    assert artifact["schema_version"] == "0.2.0"
    assert artifact["status"] == "partial"
    validate_schema(artifact, DIAGNOSIS_REPORT_SCHEMA)


def test_build_diagnosis_report_fails_without_candidate_data(
    prepared_report: ReportInputs,
) -> None:
    source = prepared_report.artifacts[0]
    unavailable_source = InputArtifact(
        source=replace(source.source, status="failed"),
        value={**source.value, "status": "failed", "data": None},
        errors=(
            ErrorItem(
                code="ANALYSIS_FAILED",
                message="후보 분석 실패",
                item_ref=None,
                retryable=False,
            ),
        ),
    )
    changed = replace(
        prepared_report,
        artifacts=(unavailable_source, *prepared_report.artifacts[1:]),
    )

    result = build_diagnosis_report(changed)

    assert result.status == "failed"
    assert result.data is None
    assert result.errors

    artifact = build_diagnosis_artifact(changed, perf_counter())
    assert artifact["schema_version"] == "0.2.0"
    assert artifact["status"] == "failed"
    assert artifact["data"] is None
    validate_schema(artifact, DIAGNOSIS_REPORT_SCHEMA)


def test_build_diagnosis_artifact_uses_actual_input_references(
    prepared_report: ReportInputs,
) -> None:
    artifact = build_diagnosis_artifact(
        prepared_report,
        perf_counter(),
        timestamp_factory=lambda: FIXED_TIMESTAMP,
    )

    assert artifact["schema_version"] == "0.2.0"
    assert artifact["created_at"] == FIXED_TIMESTAMP
    assert artifact["artifact_id"] == "diagnosis_report_run_demo_001_000"
    assert artifact["data"]["summary"]["candidate_count"] == 3
    assert [item["artifact_type"] for item in artifact["input_refs"]] == [
        "vulnerability_candidates",
        "test_scenarios",
        "safety_decisions",
        "verification_results",
    ]
    assert [item["sha256"] for item in artifact["input_refs"]] == [
        item.source.sha256 for item in prepared_report.artifacts
    ]


def test_publish_diagnosis_artifact_validates_and_writes_atomically(
    prepared_report: ReportInputs,
) -> None:
    prepared_report.request.output_path.unlink()
    artifact = build_diagnosis_artifact(
        prepared_report,
        perf_counter(),
        timestamp_factory=lambda: FIXED_TIMESTAMP,
    )

    response = publish_diagnosis_artifact(prepared_report, artifact)

    assert response.status == "completed"
    assert response.output_path.endswith("/reporter/diagnosis_report.json")
    assert response.sha256 == calculate_sha256(
        prepared_report.request.output_path
    )
    assert load_json(prepared_report.request.output_path) == artifact
    with pytest.raises(OutputArtifactExistsError):
        publish_diagnosis_artifact(prepared_report, artifact)


@pytest.mark.parametrize("schema_version", ["0.1.0", "0.3.0"])
def test_publish_diagnosis_rejects_unsupported_output_version(
    prepared_report: ReportInputs,
    schema_version: str,
) -> None:
    prepared_report.request.output_path.unlink()
    artifact = build_diagnosis_artifact(prepared_report, perf_counter())
    artifact["schema_version"] = schema_version

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        publish_diagnosis_artifact(prepared_report, artifact)

    assert not prepared_report.request.output_path.exists()


def test_output_validation_rejects_tampered_summary(
    prepared_report: ReportInputs,
) -> None:
    artifact = build_diagnosis_artifact(
        prepared_report,
        perf_counter(),
        timestamp_factory=lambda: FIXED_TIMESTAMP,
    )
    artifact["data"]["summary"]["confirmed_count"] = 0

    with pytest.raises(ContractValidationError, match="data 집계"):
        validate_diagnosis_artifact_against_inputs(artifact, prepared_report)


def test_publish_rejects_input_changed_after_preparation(
    prepared_report: ReportInputs,
) -> None:
    prepared_report.request.output_path.unlink()
    artifact = build_diagnosis_artifact(
        prepared_report,
        perf_counter(),
        timestamp_factory=lambda: FIXED_TIMESTAMP,
    )
    source_path = prepared_report.request.input_by_name(
        "test_scenarios"
    ).path
    source_path.write_text(
        source_path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ContractValidationError, match="파일이 변경됨"):
        publish_diagnosis_artifact(prepared_report, artifact)
