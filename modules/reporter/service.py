"""Diagnosis report classification and aggregation."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256

from modules.reporter.models import (
    Candidate,
    DiagnosisBuildResult,
    DiagnosisReportData,
    ErrorItem,
    EvidenceReference,
    Finding,
    ReportInputs,
    ReportSummary,
    SafetyDecision,
    Scenario,
    VerificationResult,
)

CONFIRMING_EVIDENCE_KINDS = frozenset(
    {"response", "dom", "screenshot", "state", "execution_log"}
)


@dataclass(frozen=True)
class _Assessment:
    status: str
    description: str
    limitation: str | None


def build_diagnosis_report(inputs: ReportInputs) -> DiagnosisBuildResult:
    """Build a report result while preserving upstream partial failures."""
    upstream_errors = _collect_upstream_errors(inputs)
    candidate_artifact = inputs.artifact_by_type("vulnerability_candidates")
    if candidate_artifact.data is None:
        errors = upstream_errors or (
            ErrorItem(
                code="UPSTREAM_CANDIDATES_UNAVAILABLE",
                message="vulnerability_candidates 입력 data를 사용할 수 없음",
                item_ref=None,
                retryable=False,
            ),
        )
        return DiagnosisBuildResult(status="failed", errors=errors, data=None)

    target_url = inputs.request.target_url
    if target_url is None:
        errors = (
            *upstream_errors,
            ErrorItem(
                code="TARGET_URL_UNAVAILABLE",
                message="진단 대상 URL을 확인할 수 없음",
                item_ref=None,
                retryable=False,
            ),
        )
        return DiagnosisBuildResult(status="failed", errors=errors, data=None)

    limitations = _upstream_limitations(inputs)
    findings, finding_limitations = _build_findings(inputs)
    limitations.extend(finding_limitations)
    data = DiagnosisReportData(
        target_url=target_url,
        summary=_summarize(inputs.candidates, findings),
        findings=findings,
        limitations=tuple(dict.fromkeys(limitations)),
    )
    return DiagnosisBuildResult(
        status="partial" if upstream_errors else "completed",
        errors=upstream_errors,
        data=data,
    )


def _build_findings(
    inputs: ReportInputs,
) -> tuple[tuple[Finding, ...], list[str]]:
    scenarios_by_candidate: dict[str, list[Scenario]] = {}
    for scenario in inputs.scenarios:
        scenarios_by_candidate.setdefault(scenario.candidate_id, []).append(
            scenario
        )
    decision_by_scenario = {
        item.scenario_id: item for item in inputs.decisions
    }
    result_by_scenario = {
        item.scenario_id: item for item in inputs.verification_results
    }

    findings: list[Finding] = []
    limitations: list[str] = []
    for candidate in inputs.candidates:
        scenarios = scenarios_by_candidate.get(candidate.candidate_id, [])
        if not scenarios:
            finding, limitation = _create_finding(
                candidate,
                scenario=None,
                decision=None,
                result=None,
            )
            findings.append(finding)
            if limitation is not None:
                limitations.append(limitation)
            continue
        for scenario in scenarios:
            finding, limitation = _create_finding(
                candidate,
                scenario,
                decision_by_scenario.get(scenario.scenario_id),
                result_by_scenario.get(scenario.scenario_id),
            )
            findings.append(finding)
            if limitation is not None:
                limitations.append(limitation)
    return tuple(findings), limitations


def _create_finding(
    candidate: Candidate,
    scenario: Scenario | None,
    decision: SafetyDecision | None,
    result: VerificationResult | None,
) -> tuple[Finding, str | None]:
    assessment = _assess_finding(candidate, scenario, decision, result)
    scenario_id = scenario.scenario_id if scenario is not None else None
    verification_id = result.verification_id if result is not None else None
    evidence_refs = _collect_finding_evidence(candidate, result)
    finding = Finding(
        finding_id=_create_finding_id(candidate.candidate_id, scenario_id),
        candidate_id=candidate.candidate_id,
        scenario_id=scenario_id,
        verification_id=verification_id,
        category=candidate.category,
        vulnerability_type=candidate.vulnerability_type,
        status=assessment.status,
        title=(
            f"{candidate.vulnerability_type} 후보 "
            f"({candidate.candidate_id})"
        ),
        description=assessment.description,
        evidence_refs=evidence_refs,
    )
    return finding, assessment.limitation


def _assess_finding(
    candidate: Candidate,
    scenario: Scenario | None,
    decision: SafetyDecision | None,
    result: VerificationResult | None,
) -> _Assessment:
    if scenario is None:
        message = "시나리오가 생성되지 않아 후보를 검증하지 못함"
        return _Assessment(
            status="indeterminate",
            description=message,
            limitation=f"{candidate.candidate_id}: {message}",
        )
    if decision is None:
        message = "Safety Policy 판정이 없어 시나리오를 실행할 수 없음"
        return _Assessment(
            status="indeterminate",
            description=message,
            limitation=f"{scenario.scenario_id}: {message}",
        )
    if decision.decision == "block":
        message = (
            f"Safety Policy가 실행을 차단함: {decision.reason}. "
            "미실행 결과를 취약점 부재로 판단하지 않음"
        )
        return _Assessment(
            status="policy_blocked",
            description=message,
            limitation=f"{scenario.scenario_id}: Policy 차단으로 미검증",
        )
    if decision.decision == "require_approval":
        message = (
            f"사용자 승인이 필요해 실행하지 않음: {decision.reason}. "
            "미실행 결과를 취약점 부재로 판단하지 않음"
        )
        return _Assessment(
            status="approval_pending",
            description=message,
            limitation=f"{scenario.scenario_id}: 사용자 승인 대기로 미검증",
        )
    if result is None:
        message = "허용된 시나리오의 검증 결과가 없어 판단할 수 없음"
        return _Assessment(
            status="indeterminate",
            description=message,
            limitation=f"{scenario.scenario_id}: 검증 결과 누락",
        )
    if result.result == "indeterminate" or result.execution_status != "completed":
        message = f"검증 결과를 판단할 수 없음: {result.reason}"
        return _Assessment(
            status="indeterminate",
            description=message,
            limitation=f"{result.verification_id}: {message}",
        )
    if result.errors:
        message = f"검증 오류가 있어 결과를 확정할 수 없음: {result.reason}"
        return _Assessment(
            status="indeterminate",
            description=message,
            limitation=f"{result.verification_id}: 검증 오류 포함",
        )
    if result.result == "failure":
        return _Assessment(
            status="not_confirmed",
            description=(
                f"유효한 실행에서 위반이 재현되지 않음: {result.reason}. "
                "이 결과는 대상 전체의 취약점 부재를 의미하지 않음"
            ),
            limitation=None,
        )
    if result.result != "success":
        message = f"지원하지 않는 검증 결과로 판단할 수 없음: {result.result}"
        return _Assessment(
            status="indeterminate",
            description=message,
            limitation=f"{result.verification_id}: {message}",
        )

    has_evidence = any(
        item.kind in CONFIRMING_EVIDENCE_KINDS
        for item in result.all_evidence_refs
    )
    if candidate.expected_basis == "rule" and has_evidence:
        return _Assessment(
            status="confirmed",
            description=(
                f"규칙 기반 기대조건에 대한 위반이 실제 실행에서 재현됨: "
                f"{result.reason}"
            ),
            limitation=None,
        )
    if candidate.expected_basis == "unknown":
        message = "기대 동작의 근거가 불명확해 재현 결과를 확정할 수 없음"
        return _Assessment(
            status="indeterminate",
            description=f"{message}: {result.reason}",
            limitation=f"{result.verification_id}: 기대 동작 근거 불명확",
        )
    reason = (
        "기대 동작이 추론에 기반함"
        if candidate.expected_basis == "inferred"
        else "확정에 필요한 실행 근거가 부족함"
    )
    return _Assessment(
        status="suspected",
        description=f"위반 정황은 재현됐으나 {reason}: {result.reason}",
        limitation=f"{result.verification_id}: {reason}",
    )


def _collect_finding_evidence(
    candidate: Candidate,
    result: VerificationResult | None,
) -> tuple[EvidenceReference, ...]:
    values = [
        *(result.all_evidence_refs if result is not None else ()),
        *candidate.evidence_refs,
    ]
    unique_values: list[EvidenceReference] = []
    seen: set[EvidenceReference] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            unique_values.append(value)
    return tuple(unique_values)


def _summarize(
    candidates: tuple[Candidate, ...],
    findings: tuple[Finding, ...],
) -> ReportSummary:
    counts = Counter(item.status for item in findings)
    return ReportSummary(
        candidate_count=len({item.candidate_id for item in candidates}),
        confirmed_count=counts["confirmed"],
        not_confirmed_count=counts["not_confirmed"],
        suspected_count=counts["suspected"],
        indeterminate_count=counts["indeterminate"],
        policy_blocked_count=counts["policy_blocked"],
        approval_pending_count=counts["approval_pending"],
    )


def _collect_upstream_errors(inputs: ReportInputs) -> tuple[ErrorItem, ...]:
    errors: list[ErrorItem] = []
    for artifact in inputs.artifacts:
        for error in artifact.errors:
            errors.append(
                ErrorItem(
                    code=(
                        f"UPSTREAM_{artifact.source.artifact_type.upper()}_"
                        f"{error.code}"
                    ),
                    message=(
                        f"{artifact.source.artifact_type} 입력 오류: "
                        f"{error.message}"
                    ),
                    item_ref=error.item_ref,
                    retryable=error.retryable,
                )
            )
    return tuple(errors)


def _upstream_limitations(inputs: ReportInputs) -> list[str]:
    return [
        f"{artifact.source.artifact_type} 입력 오류({error.code}): {error.message}"
        for artifact in inputs.artifacts
        for error in artifact.errors
    ]


def _create_finding_id(candidate_id: str, scenario_id: str | None) -> str:
    identity = f"{candidate_id}\0{scenario_id or ''}".encode("utf-8")
    return f"finding_{sha256(identity).hexdigest()[:24]}"
