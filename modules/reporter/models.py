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
    errors: tuple[ErrorItem, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> VerificationResult:
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
            errors=tuple(
                ErrorItem.from_mapping(item) for item in value["errors"]
            ),
        )


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
