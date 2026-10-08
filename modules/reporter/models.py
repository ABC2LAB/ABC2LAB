"""Immutable internal models at the reporter contract boundary."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, TypeAlias

JsonValue: TypeAlias = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)


@dataclass(frozen=True)
class ErrorItem:
    code: str
    message: str
    item_ref: str | None
    retryable: bool

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ErrorItem:
        return cls(
            code=value["code"],
            message=value["message"],
            item_ref=value["item_ref"],
            retryable=value["retryable"],
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "item_ref": self.item_ref,
            "retryable": self.retryable,
        }


@dataclass(frozen=True)
class EvidenceReference:
    evidence_id: str
    kind: str
    path: str
    sha256: str
    redacted: bool

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> EvidenceReference:
        return cls(
            evidence_id=value["evidence_id"],
            kind=value["kind"],
            path=value["path"],
            sha256=value["sha256"],
            redacted=value["redacted"],
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "kind": self.kind,
            "path": self.path,
            "sha256": self.sha256,
            "redacted": self.redacted,
        }


@dataclass(frozen=True)
class ResolvedInput:
    name: str
    path: Path
    relative_path: str
    expected_sha256: str


@dataclass(frozen=True)
class ReporterRequest:
    operation: str
    inputs: tuple[ResolvedInput, ...]
    output_path: Path
    output_relative_path: str
    run_root: Path
    project_root: Path | None
    run_id: str
    iteration: int
    mode: str
    target_url: str | None
    matching_profile: str | None

    def input_by_name(self, name: str) -> ResolvedInput:
        for item in self.inputs:
            if item.name == name:
                return item
        raise KeyError(name)


@dataclass(frozen=True)
class ArtifactSource:
    artifact_id: str
    artifact_type: str
    producer: str
    relative_path: str
    sha256: str
    iteration: int
    status: str


@dataclass(frozen=True)
class InputArtifact:
    source: ArtifactSource
    value: Mapping[str, Any]
    errors: tuple[ErrorItem, ...]

    @property
    def data(self) -> Mapping[str, Any] | None:
        value = self.value["data"]
        return value if isinstance(value, Mapping) else None


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    category: str
    vulnerability_type: str
    actor_account_id: str
    actor_role_id: str
    reference_account_id: str | None
    resource_ids: tuple[str, ...]
    source_request_ids: tuple[str, ...]
    workflow_id: str | None
    hypothesis: str
    expected_behavior: str
    expected_basis: str
    evidence_refs: tuple[EvidenceReference, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> Candidate:
        return cls(
            candidate_id=value["candidate_id"],
            category=value["category"],
            vulnerability_type=value["vulnerability_type"],
            actor_account_id=value["actor_account_id"],
            actor_role_id=value["actor_role_id"],
            reference_account_id=value["reference_account_id"],
            resource_ids=tuple(value["resource_ids"]),
            source_request_ids=tuple(value["source_request_ids"]),
            workflow_id=value["workflow_id"],
            hypothesis=value["hypothesis"],
            expected_behavior=value["expected_behavior"],
            expected_basis=value["expected_basis"],
            evidence_refs=tuple(
                EvidenceReference.from_mapping(item)
                for item in value["evidence_refs"]
            ),
        )


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    candidate_id: str
    expected_basis: str
    step_ids: tuple[str, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> Scenario:
        return cls(
            scenario_id=value["scenario_id"],
            candidate_id=value["candidate_id"],
            expected_basis=value["expected_basis"],
            step_ids=tuple(step["step_id"] for step in value["steps"]),
        )


@dataclass(frozen=True)
class SafetyDecision:
    decision_id: str
    scenario_id: str
    decision: str
    reason_codes: tuple[str, ...]
    reason: str
    approval_ref: str | None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> SafetyDecision:
        return cls(
            decision_id=value["decision_id"],
            scenario_id=value["scenario_id"],
            decision=value["decision"],
            reason_codes=tuple(value["reason_codes"]),
            reason=value["reason"],
            approval_ref=value["approval_ref"],
        )


@dataclass(frozen=True)
class VerificationResult:
    verification_id: str
    candidate_id: str
    scenario_id: str
    decision_id: str
    policy_decision: str
    execution_status: str
    result: str
    reason: str
    evidence_refs: tuple[EvidenceReference, ...]
    step_evidence_refs: tuple[EvidenceReference, ...]
    errors: tuple[ErrorItem, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> VerificationResult:
        step_evidence_refs: list[EvidenceReference] = []
        for step in value["steps"]:
            step_evidence_refs.extend(
                EvidenceReference.from_mapping(item)
                for item in step["evidence_refs"]
            )
            for reference_name in ("request_ref", "response_ref"):
                reference = step[reference_name]
                if reference is not None:
                    step_evidence_refs.append(
                        EvidenceReference.from_mapping(reference)
                    )
        return cls(
            verification_id=value["verification_id"],
            candidate_id=value["candidate_id"],
            scenario_id=value["scenario_id"],
            decision_id=value["decision_id"],
            policy_decision=value["policy_decision"],
            execution_status=value["execution_status"],
            result=value["result"],
            reason=value["reason"],
            evidence_refs=tuple(
                EvidenceReference.from_mapping(item)
                for item in value["evidence_refs"]
            ),
            step_evidence_refs=tuple(step_evidence_refs),
            errors=tuple(
                ErrorItem.from_mapping(item) for item in value["errors"]
            ),
        )

    @property
    def all_evidence_refs(self) -> tuple[EvidenceReference, ...]:
        return (*self.evidence_refs, *self.step_evidence_refs)


@dataclass(frozen=True)
class Finding:
    finding_id: str
    candidate_id: str
    scenario_id: str | None
    verification_id: str | None
    category: str
    vulnerability_type: str
    status: str
    title: str
    description: str
    evidence_refs: tuple[EvidenceReference, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "candidate_id": self.candidate_id,
            "scenario_id": self.scenario_id,
            "verification_id": self.verification_id,
            "category": self.category,
            "vulnerability_type": self.vulnerability_type,
            "status": self.status,
            "title": self.title,
            "description": self.description,
            "evidence_refs": [item.to_mapping() for item in self.evidence_refs],
        }


@dataclass(frozen=True)
class ReportSummary:
    candidate_count: int
    confirmed_count: int
    not_confirmed_count: int
    suspected_count: int
    indeterminate_count: int
    policy_blocked_count: int
    approval_pending_count: int

    def to_mapping(self) -> dict[str, int]:
        return {
            "candidate_count": self.candidate_count,
            "confirmed_count": self.confirmed_count,
            "not_confirmed_count": self.not_confirmed_count,
            "suspected_count": self.suspected_count,
            "indeterminate_count": self.indeterminate_count,
            "policy_blocked_count": self.policy_blocked_count,
            "approval_pending_count": self.approval_pending_count,
        }


@dataclass(frozen=True)
class DiagnosisReportData:
    target_url: str
    summary: ReportSummary
    findings: tuple[Finding, ...]
    limitations: tuple[str, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "target_url": self.target_url,
            "summary": self.summary.to_mapping(),
            "findings": [item.to_mapping() for item in self.findings],
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True)
class DiagnosisBuildResult:
    status: str
    errors: tuple[ErrorItem, ...]
    data: DiagnosisReportData | None


@dataclass(frozen=True)
class ReportControlResponse:
    status: str
    artifact_id: str
    output_path: str
    sha256: str
    errors: tuple[ErrorItem, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "artifact_id": self.artifact_id,
            "output_path": self.output_path,
            "sha256": self.sha256,
            "errors": [item.to_mapping() for item in self.errors],
        }


@dataclass(frozen=True)
class Metric:
    metric_id: str
    value: float | None
    numerator: int
    denominator: int

    def to_mapping(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "value": self.value,
            "numerator": self.numerator,
            "denominator": self.denominator,
        }


@dataclass(frozen=True)
class CountTriple:
    tp: int
    fp: int
    fn: int

    def to_mapping(self) -> dict[str, int]:
        return {"tp": self.tp, "fp": self.fp, "fn": self.fn}


@dataclass(frozen=True)
class UnverifiedCounts:
    policy_blocked: int
    approval_pending: int
    indeterminate: int

    def to_mapping(self) -> dict[str, int]:
        return {
            "policy_blocked": self.policy_blocked,
            "approval_pending": self.approval_pending,
            "indeterminate": self.indeterminate,
        }


@dataclass(frozen=True)
class EvaluationData:
    ground_truth: GroundTruth
    source_graph_revision: int
    matching_profile: str
    metrics: tuple[Metric, ...]
    candidate_counts: CountTriple
    confirmed_counts: CountTriple
    unverified_counts: UnverifiedCounts
    models: Mapping[str, Mapping[str, JsonValue]]
    run_metrics: Mapping[str, JsonValue]
    notes: tuple[str, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "ground_truth_ref": {
                "dataset_id": self.ground_truth.dataset_id,
                "dataset_version": self.ground_truth.dataset_version,
                "path": self.ground_truth.relative_path,
                "sha256": self.ground_truth.sha256,
            },
            "source_graph_revision": self.source_graph_revision,
            "matching_profile": self.matching_profile,
            "metrics": [item.to_mapping() for item in self.metrics],
            "candidate_counts": self.candidate_counts.to_mapping(),
            "confirmed_counts": self.confirmed_counts.to_mapping(),
            "unverified_counts": self.unverified_counts.to_mapping(),
            "models": {
                name: dict(model) for name, model in self.models.items()
            },
            "run_metrics": dict(self.run_metrics),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class EvaluationBuildResult:
    status: str
    errors: tuple[ErrorItem, ...]
    data: EvaluationData | None


@dataclass(frozen=True)
class EvaluationControlResponse:
    status: str
    artifact_id: str
    output_path: str
    sha256: str
    errors: tuple[ErrorItem, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "artifact_id": self.artifact_id,
            "output_path": self.output_path,
            "sha256": self.sha256,
            "errors": [item.to_mapping() for item in self.errors],
        }


@dataclass(frozen=True)
class GraphReferenceIndex:
    node_type_by_id: Mapping[str, str]
    user_node_id_by_account_id: Mapping[str, str]
    role_node_id_by_role_id: Mapping[str, str]


@dataclass(frozen=True)
class GraphSnapshot:
    graph_id: str
    graph_revision: int
    nodes: tuple[Mapping[str, Any], ...]
    relationships: tuple[Mapping[str, Any], ...]
    workflows: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class GroundTruth:
    dataset_id: str
    dataset_version: str
    relative_path: str
    sha256: str
    entities: tuple[Mapping[str, Any], ...]
    relationships: tuple[Mapping[str, Any], ...]
    workflows: tuple[Mapping[str, Any], ...]
    cases: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class CrawlAccount:
    account_id: str
    role_id: str
    session_ref: str | None


@dataclass(frozen=True)
class CrawlRequest:
    request_id: str
    account_id: str
    role_id: str
    session_ref: str | None
    page_id: str | None
    action_id: str | None


@dataclass(frozen=True)
class CrawlIndex:
    role_ids: frozenset[str]
    accounts: tuple[CrawlAccount, ...]
    page_ids: frozenset[str]
    action_page_by_id: Mapping[str, str]
    requests: tuple[CrawlRequest, ...]

    def account_by_id(self, account_id: str) -> CrawlAccount | None:
        return next(
            (item for item in self.accounts if item.account_id == account_id),
            None,
        )

    def request_by_id(self, request_id: str) -> CrawlRequest | None:
        return next(
            (item for item in self.requests if item.request_id == request_id),
            None,
        )


@dataclass(frozen=True)
class ReportInputs:
    request: ReporterRequest
    artifacts: tuple[InputArtifact, ...]
    candidates: tuple[Candidate, ...]
    scenarios: tuple[Scenario, ...]
    decisions: tuple[SafetyDecision, ...]
    verification_results: tuple[VerificationResult, ...]

    def artifact_by_type(self, artifact_type: str) -> InputArtifact:
        for artifact in self.artifacts:
            if artifact.source.artifact_type == artifact_type:
                return artifact
        raise KeyError(artifact_type)


@dataclass(frozen=True)
class EvaluationInputs:
    request: ReporterRequest
    report_inputs: ReportInputs
    crawl_result: InputArtifact
    semantic_analysis: InputArtifact
    graph_query_result: InputArtifact
    graph_snapshot: GraphSnapshot | None
    ground_truth: GroundTruth

    @property
    def artifacts(self) -> tuple[InputArtifact, ...]:
        return (
            self.crawl_result,
            self.semantic_analysis,
            self.graph_query_result,
            *self.report_inputs.artifacts,
        )
