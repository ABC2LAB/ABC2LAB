"""Internal immutable models for safety policy contracts."""

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
class EvaluationRequest:
    input_path: Path
    input_relative_path: str
    expected_sha256: str
    output_path: Path
    output_relative_path: str
    run_id: str
    iteration: int
    mode: str


@dataclass(frozen=True)
class SourceArtifact:
    artifact_id: str
    artifact_type: str
    relative_path: str
    sha256: str
    iteration: int
    status: str


@dataclass(frozen=True)
class EvaluationInput:
    request: EvaluationRequest
    source: SourceArtifact
    scenarios: ScenarioData
    source_errors: tuple[ErrorItem, ...]


@dataclass(frozen=True)
class AllowedTarget:
    origin: str
    path_prefixes: tuple[str, ...]


@dataclass(frozen=True)
class TestAccountPolicy:
    account_id: str
    role_ids: tuple[str, ...]
    requires_session: bool


@dataclass(frozen=True)
class RequestPolicyRule:
    rule_id: str
    origin: str
    path_prefix: str
    methods: tuple[str, ...]
    state_change: str
    data_impact: str
    service_impact: str


@dataclass(frozen=True)
class PolicyConfiguration:
    policy_id: str
    policy_version: str
    allowed_targets: tuple[AllowedTarget, ...]
    test_accounts: tuple[TestAccountPolicy, ...]
    max_requests: int
    max_duration_ms: int
    request_rules: tuple[RequestPolicyRule, ...]


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
class ModelInformation:
    model_id: str
    model_version: str
    prompt_version: str
    temperature: float | None
    seed: int | None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ModelInformation:
        return cls(
            model_id=value["model_id"],
            model_version=value["model_version"],
            prompt_version=value["prompt_version"],
            temperature=value["temperature"],
            seed=value["seed"],
        )


@dataclass(frozen=True)
class Check:
    check_id: str
    kind: str
    subject_ref: str
    selector: str | None
    operator: str
    expected: JsonValue

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> Check:
        return cls(
            check_id=value["check_id"],
            kind=value["kind"],
            subject_ref=value["subject_ref"],
            selector=value["selector"],
            operator=value["operator"],
            expected=value["expected"],
        )


@dataclass(frozen=True)
class ParameterValue:
    name: str
    location: str
    value: JsonValue
    binding_ref: str | None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ParameterValue:
        return cls(
            name=value["name"],
            location=value["location"],
            value=value["value"],
            binding_ref=value["binding_ref"],
        )


@dataclass(frozen=True)
class Binding:
    binding_id: str
    source_step_id: str
    source_part: str
    selector: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> Binding:
        return cls(
            binding_id=value["binding_id"],
            source_step_id=value["source_step_id"],
            source_part=value["source_part"],
            selector=value["selector"],
        )


@dataclass(frozen=True)
class RequestPlan:
    method: str
    url_template: str
    parameters: tuple[ParameterValue, ...]
    body_ref: EvidenceReference | None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> RequestPlan:
        body_ref = value["body_ref"]
        return cls(
            method=value["method"],
            url_template=value["url_template"],
            parameters=tuple(
                ParameterValue.from_mapping(item) for item in value["parameters"]
            ),
            body_ref=(
                EvidenceReference.from_mapping(body_ref)
                if body_ref is not None
                else None
            ),
        )


@dataclass(frozen=True)
class ScenarioStep:
    step_id: str
    order: int
    source_request_id: str
    account_id: str
    role_id: str
    session_ref: str | None
    request: RequestPlan
    bindings: tuple[Binding, ...]
    state_change: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ScenarioStep:
        return cls(
            step_id=value["step_id"],
            order=value["order"],
            source_request_id=value["source_request_id"],
            account_id=value["account_id"],
            role_id=value["role_id"],
            session_ref=value["session_ref"],
            request=RequestPlan.from_mapping(value["request"]),
            bindings=tuple(
                Binding.from_mapping(item) for item in value["bindings"]
            ),
            state_change=value["state_change"],
        )


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    candidate_id: str
    expected_basis: str
    preconditions: tuple[Check, ...]
    steps: tuple[ScenarioStep, ...]
    assertions: tuple[Check, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> Scenario:
        return cls(
            scenario_id=value["scenario_id"],
            candidate_id=value["candidate_id"],
            expected_basis=value["expected_basis"],
            preconditions=tuple(
                Check.from_mapping(item) for item in value["preconditions"]
            ),
            steps=tuple(ScenarioStep.from_mapping(item) for item in value["steps"]),
            assertions=tuple(
                Check.from_mapping(item) for item in value["assertions"]
            ),
        )


@dataclass(frozen=True)
class ScenarioData:
    scenarios: tuple[Scenario, ...]
    model_info: ModelInformation | None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ScenarioData:
        model_info = value["model_info"]
        return cls(
            scenarios=tuple(
                Scenario.from_mapping(item) for item in value["scenarios"]
            ),
            model_info=(
                ModelInformation.from_mapping(model_info)
                if model_info is not None
                else None
            ),
        )


@dataclass(frozen=True)
class PolicyAssessmentItem:
    status: str
    rule_id: str
    reason: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> PolicyAssessmentItem:
        return cls(
            status=value["status"],
            rule_id=value["rule_id"],
            reason=value["reason"],
        )


@dataclass(frozen=True)
class SafetyAssessment:
    target_scope: PolicyAssessmentItem
    test_accounts: PolicyAssessmentItem
    request_budget: PolicyAssessmentItem
    state_change: PolicyAssessmentItem
    data_impact: PolicyAssessmentItem
    service_impact: PolicyAssessmentItem

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> SafetyAssessment:
        return cls(
            target_scope=PolicyAssessmentItem.from_mapping(value["target_scope"]),
            test_accounts=PolicyAssessmentItem.from_mapping(value["test_accounts"]),
            request_budget=PolicyAssessmentItem.from_mapping(
                value["request_budget"]
            ),
            state_change=PolicyAssessmentItem.from_mapping(value["state_change"]),
            data_impact=PolicyAssessmentItem.from_mapping(value["data_impact"]),
            service_impact=PolicyAssessmentItem.from_mapping(
                value["service_impact"]
            ),
        )

    def statuses(self) -> tuple[str, ...]:
        return (
            self.target_scope.status,
            self.test_accounts.status,
            self.request_budget.status,
            self.state_change.status,
            self.data_impact.status,
            self.service_impact.status,
        )


@dataclass(frozen=True)
class PolicyLimits:
    max_requests: int
    max_duration_ms: int
    allow_state_change: bool

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> PolicyLimits:
        return cls(
            max_requests=value["max_requests"],
            max_duration_ms=value["max_duration_ms"],
            allow_state_change=value["allow_state_change"],
        )


@dataclass(frozen=True)
class SafetyDecision:
    decision_id: str
    scenario_id: str
    decision: str
    reason_codes: tuple[str, ...]
    reason: str
    assessment: SafetyAssessment
    effective_origins: tuple[str, ...]
    effective_account_ids: tuple[str, ...]
    limits: PolicyLimits
    approval_ref: str | None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> SafetyDecision:
        return cls(
            decision_id=value["decision_id"],
            scenario_id=value["scenario_id"],
            decision=value["decision"],
            reason_codes=tuple(value["reason_codes"]),
            reason=value["reason"],
            assessment=SafetyAssessment.from_mapping(value["assessment"]),
            effective_origins=tuple(value["effective_origins"]),
            effective_account_ids=tuple(value["effective_account_ids"]),
            limits=PolicyLimits.from_mapping(value["limits"]),
            approval_ref=value["approval_ref"],
        )


@dataclass(frozen=True)
class SafetyDecisionsData:
    policy_id: str
    policy_version: str
    scenarios_sha256: str
    decisions: tuple[SafetyDecision, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> SafetyDecisionsData:
        return cls(
            policy_id=value["policy_id"],
            policy_version=value["policy_version"],
            scenarios_sha256=value["scenarios_sha256"],
            decisions=tuple(
                SafetyDecision.from_mapping(item) for item in value["decisions"]
            ),
        )
