"""Deterministic safety policy evaluation rules."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from ipaddress import IPv6Address
from urllib.parse import SplitResult, unquote_to_bytes, urlsplit

from modules.safety_policy.exceptions import (
    ApprovalRecordError,
    PolicyConfigurationError,
)
from modules.safety_policy.models import (
    AllowedTarget,
    ApprovalRecord,
    EvaluationInput,
    PolicyAssessmentItem,
    PolicyConfiguration,
    PolicyLimits,
    RequestPolicyRule,
    SafetyAssessment,
    SafetyDecision,
    SafetyDecisionsData,
    Scenario,
    ScenarioStep,
    TestAccountPolicy,
)

_STATE_CHANGE_VALUES = frozenset(
    {"none", "allow", "require_approval", "block"}
)
_DATA_IMPACT_VALUES = frozenset(
    {"read_only", "allow", "require_approval", "block"}
)
_SERVICE_IMPACT_VALUES = frozenset(
    {"low", "allow", "require_approval", "block"}
)
_PASS_VALUES = frozenset({"none", "read_only", "low", "allow"})
_STATUS_PRIORITY = {"pass": 0, "unknown": 1, "require_approval": 2, "block": 3}
_PLACEHOLDER_PATTERN = re.compile(r"\{[^{}]+\}")
_INVALID_PERCENT_PATTERN = re.compile(r"%(?![0-9A-Fa-f]{2})")
_METHOD_PATTERN = re.compile(r"^[A-Z]+$")
_REG_NAME_PATTERN = re.compile(r"^[a-z0-9._-]+$")


@dataclass(frozen=True)
class _RequestTarget:
    origin: str
    path: str


@dataclass(frozen=True)
class _RequestEvaluation:
    step: ScenarioStep
    target: _RequestTarget | None
    target_error: str | None
    rule: RequestPolicyRule | None


def evaluate_policy(
    evaluation_input: EvaluationInput,
    configuration: PolicyConfiguration,
    approval_record: ApprovalRecord | None = None,
) -> SafetyDecisionsData:
    policy = _validate_policy_configuration(configuration)
    approval_id_by_scenario = (
        {
            scenario_id: approval_record.approval_id
            for scenario_id in approval_record.approved_scenario_ids
        }
        if approval_record is not None
        else {}
    )
    decisions = tuple(
        _evaluate_scenario(
            scenario,
            policy,
            approval_id_by_scenario.get(scenario.scenario_id),
        )
        for scenario in evaluation_input.scenarios.scenarios
    )
    return SafetyDecisionsData(
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        scenarios_sha256=evaluation_input.source.sha256,
        decisions=decisions,
    )


def _evaluate_scenario(
    scenario: Scenario,
    policy: PolicyConfiguration,
    approval_id: str | None,
) -> SafetyDecision:
    requests = tuple(_evaluate_request(step, policy) for step in scenario.steps)
    assessment = SafetyAssessment(
        target_scope=_assess_target_scope(requests, policy),
        test_accounts=_assess_test_accounts(scenario, policy),
        request_budget=_assess_request_budget(scenario, policy),
        state_change=_assess_impact(requests, "state_change"),
        data_impact=_assess_impact(requests, "data_impact"),
        service_impact=_assess_impact(requests, "service_impact"),
    )
    approved_state_change = False
    if approval_id is not None:
        approved_state_change = assessment.state_change.status == "require_approval"
        assessment = _apply_approval(assessment, approval_id)
    decision = _choose_decision(assessment)
    is_allowed = decision == "allow"
    return SafetyDecision(
        decision_id=f"decision_{scenario.scenario_id}",
        scenario_id=scenario.scenario_id,
        decision=decision,
        reason_codes=_reason_codes(assessment),
        reason=_decision_reason(decision, approval_id if is_allowed else None),
        assessment=assessment,
        effective_origins=(
            _unique_origins(requests) if is_allowed else ()
        ),
        effective_account_ids=(
            _unique_account_ids(scenario) if is_allowed else ()
        ),
        limits=_effective_limits(
            requests,
            policy,
            is_allowed,
            approved_state_change,
        ),
        approval_ref=approval_id if is_allowed else None,
    )


def _apply_approval(
    assessment: SafetyAssessment,
    approval_id: str,
) -> SafetyAssessment:
    statuses = assessment.statuses()
    if "block" in statuses:
        raise ApprovalRecordError("차단된 시나리오는 승인으로 허용할 수 없음")
    if "unknown" in statuses:
        raise ApprovalRecordError("영향이 불명확한 시나리오는 승인으로 허용할 수 없음")
    if "require_approval" not in statuses:
        raise ApprovalRecordError("승인이 필요하지 않은 시나리오가 승인 기록에 포함됨")
    return SafetyAssessment(
        target_scope=_approve_item(assessment.target_scope, approval_id),
        test_accounts=_approve_item(assessment.test_accounts, approval_id),
        request_budget=_approve_item(assessment.request_budget, approval_id),
        state_change=_approve_item(assessment.state_change, approval_id),
        data_impact=_approve_item(assessment.data_impact, approval_id),
        service_impact=_approve_item(assessment.service_impact, approval_id),
    )


def _approve_item(
    item: PolicyAssessmentItem,
    approval_id: str,
) -> PolicyAssessmentItem:
    if item.status != "require_approval":
        return item
    return PolicyAssessmentItem(
        status="pass",
        rule_id=item.rule_id,
        reason=f"검증된 사용자 승인 기록({approval_id})으로 허용됨",
    )


def _evaluate_request(
    step: ScenarioStep,
    policy: PolicyConfiguration,
) -> _RequestEvaluation:
    try:
        target = _parse_request_target(step.request.url_template)
    except ValueError as error:
        return _RequestEvaluation(step, None, str(error), None)
    rule = _select_request_rule(step, target, policy.request_rules)
    return _RequestEvaluation(step, target, None, rule)


def _assess_target_scope(
    requests: tuple[_RequestEvaluation, ...],
    policy: PolicyConfiguration,
) -> PolicyAssessmentItem:
    for request in requests:
        if request.target is None:
            return PolicyAssessmentItem(
                "block", "scope.url.invalid", request.target_error or "URL이 올바르지 않음"
            )
        if not _is_target_allowed(request.target, policy.allowed_targets):
            return PolicyAssessmentItem(
                "block",
                "scope.target.not_allowed",
                "요청 대상이 허용 origin 또는 경로 범위를 벗어남",
            )
    return PolicyAssessmentItem(
        "pass", "scope.targets.allowed", "모든 요청이 허용 대상 범위에 포함됨"
    )


def _assess_test_accounts(
    scenario: Scenario,
    policy: PolicyConfiguration,
) -> PolicyAssessmentItem:
    account_by_id = {account.account_id: account for account in policy.test_accounts}
    for step in scenario.steps:
        account = account_by_id.get(step.account_id)
        if account is None:
            return PolicyAssessmentItem(
                "block", "account.not_allowed", "허용되지 않은 테스트 계정이 포함됨"
            )
        if step.role_id not in account.role_ids:
            return PolicyAssessmentItem(
                "block", "account.role.not_allowed", "계정에 허용되지 않은 역할이 지정됨"
            )
        if account.requires_session and step.session_ref is None:
            return PolicyAssessmentItem(
                "block", "account.session.required", "필수 테스트 세션 참조가 없음"
            )
    return PolicyAssessmentItem(
        "pass", "account.tests.allowed", "모든 단계가 허용 테스트 계정을 사용함"
    )


def _assess_request_budget(
    scenario: Scenario,
    policy: PolicyConfiguration,
) -> PolicyAssessmentItem:
    if len(scenario.steps) > policy.max_requests:
        return PolicyAssessmentItem(
            "block",
            "budget.request.exceeded",
            "계획 요청 수가 Policy 최대 요청 수를 초과함",
        )
    return PolicyAssessmentItem(
        "pass", "budget.request.within_limit", "계획 요청 수가 제한 이내임"
    )


def _assess_impact(
    requests: tuple[_RequestEvaluation, ...],
    dimension: str,
) -> PolicyAssessmentItem:
    items = tuple(_assess_request_impact(request, dimension) for request in requests)
    if not items:
        return PolicyAssessmentItem(
            "pass", f"{dimension}.no_requests", "평가할 요청이 없음"
        )
    worst = max(items, key=lambda item: _STATUS_PRIORITY[item.status])
    if worst.status != "pass":
        return worst
    return PolicyAssessmentItem(
        "pass", f"{dimension}.all_requests_allowed", _pass_reason(dimension)
    )


def _assess_request_impact(
    request: _RequestEvaluation,
    dimension: str,
) -> PolicyAssessmentItem:
    if request.rule is None:
        return PolicyAssessmentItem(
            "unknown",
            f"{dimension}.rule_missing",
            "요청에 대응하는 Policy 규칙이 없어 영향을 판단할 수 없음",
        )
    configured_value = getattr(request.rule, dimension)
    if dimension == "state_change":
        conflict = _state_change_conflict(request.step, configured_value)
        if conflict is not None:
            return conflict
    status = "pass" if configured_value in _PASS_VALUES else configured_value
    return PolicyAssessmentItem(
        status,
        request.rule.rule_id,
        _impact_reason(dimension, status),
    )


def _state_change_conflict(
    step: ScenarioStep,
    configured_value: str,
) -> PolicyAssessmentItem | None:
    if configured_value != "none" or step.state_change == "none":
        return None
    if step.state_change == "unknown":
        return PolicyAssessmentItem(
            "unknown",
            "state_change.plan_unknown",
            "계획의 상태 변경 가능성이 불명확함",
        )
    return PolicyAssessmentItem(
        "require_approval",
        "state_change.plan_conflict",
        "계획의 상태 변경 추정이 읽기 전용 Policy 규칙과 충돌함",
    )


def _choose_decision(assessment: SafetyAssessment) -> str:
    statuses = assessment.statuses()
    if "block" in statuses:
        return "block"
    if "require_approval" in statuses or "unknown" in statuses:
        return "require_approval"
    return "allow"


def _reason_codes(assessment: SafetyAssessment) -> tuple[str, ...]:
    names = (
        "target_scope",
        "test_accounts",
        "request_budget",
        "state_change",
        "data_impact",
        "service_impact",
    )
    codes = tuple(
        f"{name.upper()}_{status.upper()}"
        for name, status in zip(names, assessment.statuses(), strict=True)
        if status != "pass"
    )
    return codes or ("ALL_ASSESSMENTS_PASSED",)


def _decision_reason(decision: str, approval_id: str | None = None) -> str:
    if decision == "allow":
        if approval_id is not None:
            return "검증된 사용자 승인과 실행 제한 안에서 검증을 수행할 수 있음"
        return "정의된 실행 범위와 제한 안에서 검증을 수행할 수 있음"
    if decision == "block":
        return "Policy 안전 평가에서 차단 항목이 확인됨"
    return "사용자 승인 또는 추가 Policy 설정이 필요한 항목이 있음"


def _effective_limits(
    requests: tuple[_RequestEvaluation, ...],
    policy: PolicyConfiguration,
    is_allowed: bool,
    approved_state_change: bool = False,
) -> PolicyLimits:
    if not is_allowed:
        return PolicyLimits(0, 0, False)
    allows_state_change = approved_state_change or any(
        request.rule is not None and request.rule.state_change == "allow"
        for request in requests
    )
    return PolicyLimits(
        max_requests=policy.max_requests,
        max_duration_ms=policy.max_duration_ms,
        allow_state_change=allows_state_change,
    )


def _unique_origins(
    requests: tuple[_RequestEvaluation, ...],
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            request.target.origin
            for request in requests
            if request.target is not None
        )
    )


def _unique_account_ids(scenario: Scenario) -> tuple[str, ...]:
    return tuple(dict.fromkeys(step.account_id for step in scenario.steps))


def _is_target_allowed(
    target: _RequestTarget,
    allowed_targets: tuple[AllowedTarget, ...],
) -> bool:
    return any(
        target.origin == allowed.origin
        and any(_path_matches(target.path, prefix) for prefix in allowed.path_prefixes)
        for allowed in allowed_targets
    )


def _select_request_rule(
    step: ScenarioStep,
    target: _RequestTarget,
    rules: tuple[RequestPolicyRule, ...],
) -> RequestPolicyRule | None:
    matching = [
        rule
        for rule in rules
        if rule.origin == target.origin
        and step.request.method in rule.methods
        and _path_matches(target.path, rule.path_prefix)
    ]
    if not matching:
        return None
    return max(matching, key=lambda rule: len(rule.path_prefix))


def _path_matches(path: str, prefix: str) -> bool:
    if prefix == "/":
        return True
    return path == prefix or path.startswith(f"{prefix}/")


def _validate_policy_configuration(
    configuration: PolicyConfiguration,
) -> PolicyConfiguration:
    _require_non_empty(configuration.policy_id, "policy_id")
    _require_non_empty(configuration.policy_version, "policy_version")
    _require_positive_integer(configuration.max_requests, "max_requests")
    _require_positive_integer(configuration.max_duration_ms, "max_duration_ms")
    targets = tuple(
        _normalize_allowed_target(item) for item in configuration.allowed_targets
    )
    accounts = tuple(
        _validate_test_account(item) for item in configuration.test_accounts
    )
    rules = tuple(
        _normalize_request_rule(item, targets)
        for item in configuration.request_rules
    )
    _require_unique((item.origin for item in targets), "allowed target origin")
    _require_unique((item.account_id for item in accounts), "test account_id")
    _require_unique((item.rule_id for item in rules), "request rule_id")
    _reject_ambiguous_rules(rules)
    return PolicyConfiguration(
        policy_id=configuration.policy_id,
        policy_version=configuration.policy_version,
        allowed_targets=targets,
        test_accounts=accounts,
        max_requests=configuration.max_requests,
        max_duration_ms=configuration.max_duration_ms,
        request_rules=rules,
    )


def _normalize_allowed_target(target: AllowedTarget) -> AllowedTarget:
    origin = _normalize_origin(target.origin)
    if not target.path_prefixes:
        raise PolicyConfigurationError("allowed target에는 path_prefix가 필요함")
    paths = tuple(_normalize_path_prefix(path) for path in target.path_prefixes)
    _require_unique(paths, f"path_prefix ({origin})")
    return AllowedTarget(origin=origin, path_prefixes=paths)


def _validate_test_account(account: TestAccountPolicy) -> TestAccountPolicy:
    _require_non_empty(account.account_id, "account_id")
    if not account.role_ids:
        raise PolicyConfigurationError("test account에는 role_id가 필요함")
    for role_id in account.role_ids:
        _require_non_empty(role_id, "role_id")
    _require_unique(account.role_ids, f"role_id ({account.account_id})")
    if not isinstance(account.requires_session, bool):
        raise PolicyConfigurationError("requires_session은 boolean이어야 함")
    return TestAccountPolicy(
        account_id=account.account_id,
        role_ids=tuple(account.role_ids),
        requires_session=account.requires_session,
    )


def _normalize_request_rule(
    rule: RequestPolicyRule,
    targets: tuple[AllowedTarget, ...],
) -> RequestPolicyRule:
    _require_non_empty(rule.rule_id, "rule_id")
    origin = _normalize_origin(rule.origin)
    path_prefix = _normalize_path_prefix(rule.path_prefix)
    methods = tuple(_normalize_method(method) for method in rule.methods)
    if not methods:
        raise PolicyConfigurationError("request rule에는 method가 필요함")
    _require_unique(methods, f"method ({rule.rule_id})")
    _require_choice(rule.state_change, _STATE_CHANGE_VALUES, "state_change")
    _require_choice(rule.data_impact, _DATA_IMPACT_VALUES, "data_impact")
    _require_choice(rule.service_impact, _SERVICE_IMPACT_VALUES, "service_impact")
    if not _is_rule_in_allowed_targets(origin, path_prefix, targets):
        raise PolicyConfigurationError("request rule이 허용 대상 범위를 벗어남")
    return RequestPolicyRule(
        rule_id=rule.rule_id,
        origin=origin,
        path_prefix=path_prefix,
        methods=methods,
        state_change=rule.state_change,
        data_impact=rule.data_impact,
        service_impact=rule.service_impact,
    )


def _is_rule_in_allowed_targets(
    origin: str,
    path_prefix: str,
    targets: tuple[AllowedTarget, ...],
) -> bool:
    return any(
        origin == target.origin
        and any(_path_matches(path_prefix, prefix) for prefix in target.path_prefixes)
        for target in targets
    )


def _reject_ambiguous_rules(rules: tuple[RequestPolicyRule, ...]) -> None:
    keys: set[tuple[str, str, str]] = set()
    for rule in rules:
        for method in rule.methods:
            key = (rule.origin, rule.path_prefix, method)
            if key in keys:
                raise PolicyConfigurationError("같은 요청에 중복 Policy 규칙이 있음")
            keys.add(key)


def _parse_request_target(url_template: str) -> _RequestTarget:
    if _has_whitespace_or_control(url_template):
        raise ValueError("요청 URL에 공백 또는 제어 문자가 포함됨")
    try:
        parsed = urlsplit(url_template)
        origin = _origin_from_split(parsed)
    except (UnicodeError, ValueError) as error:
        raise ValueError("요청 URL origin 형식이 올바르지 않음") from error
    if _PLACEHOLDER_PATTERN.search(parsed.scheme) or _PLACEHOLDER_PATTERN.search(
        parsed.netloc
    ):
        raise ValueError("요청 URL origin에는 binding을 사용할 수 없음")
    if parsed.fragment:
        raise ValueError("요청 URL에 fragment를 사용할 수 없음")
    return _RequestTarget(origin, _normalize_request_path(parsed.path))


def _normalize_origin(origin: str) -> str:
    _require_non_empty(origin, "origin")
    if _has_whitespace_or_control(origin):
        raise PolicyConfigurationError("origin에 공백 또는 제어 문자를 사용할 수 없음")
    try:
        parsed = urlsplit(origin)
        normalized = _origin_from_split(parsed)
    except (UnicodeError, ValueError) as error:
        raise PolicyConfigurationError("origin 형식이 올바르지 않음") from error
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise PolicyConfigurationError("origin에는 경로·query·fragment를 넣을 수 없음")
    if _PLACEHOLDER_PATTERN.search(origin):
        raise PolicyConfigurationError("origin에는 binding을 사용할 수 없음")
    return normalized


def _origin_from_split(parsed: SplitResult) -> str:
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("HTTP(S) 절대 URL이 아님")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL 사용자 정보는 허용되지 않음")
    hostname = parsed.hostname
    if hostname is None:
        raise ValueError("URL hostname이 없음")
    canonical_host = hostname.encode("idna").decode("ascii").lower().rstrip(".")
    if not canonical_host:
        raise ValueError("URL hostname이 없음")
    if ":" in canonical_host:
        try:
            canonical_host = IPv6Address(canonical_host).compressed
        except ValueError as error:
            raise ValueError("IPv6 hostname 형식이 올바르지 않음") from error
        host = f"[{canonical_host}]"
    else:
        if (
            _REG_NAME_PATTERN.fullmatch(canonical_host) is None
            or canonical_host.startswith(".")
            or ".." in canonical_host
        ):
            raise ValueError("URL hostname 형식이 올바르지 않음")
        host = canonical_host
    port = parsed.port
    if port == 0:
        raise ValueError("URL port는 1 이상이어야 함")
    default_port = 80 if scheme == "http" else 443
    port_suffix = f":{port}" if port is not None and port != default_port else ""
    return f"{scheme}://{host}{port_suffix}"


def _normalize_path_prefix(path: str) -> str:
    _require_non_empty(path, "path_prefix")
    if "?" in path or "#" in path:
        raise PolicyConfigurationError("path_prefix에 query·fragment를 넣을 수 없음")
    if _PLACEHOLDER_PATTERN.search(path):
        raise PolicyConfigurationError("path_prefix에는 binding을 사용할 수 없음")
    try:
        return _normalize_request_path(path)
    except ValueError as error:
        raise PolicyConfigurationError("path_prefix 형식이 올바르지 않음") from error


def _normalize_request_path(path: str) -> str:
    path = path or "/"
    if (
        not path.startswith("/")
        or "\\" in path
        or _INVALID_PERCENT_PATTERN.search(path)
    ):
        raise ValueError("URL path 형식이 올바르지 않음")
    try:
        decoded = unquote_to_bytes(path).decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("URL path가 UTF-8이 아님") from error
    if "\\" in decoded or _has_whitespace_or_control(decoded) or "//" in decoded:
        raise ValueError("URL path에 허용되지 않은 문자가 포함됨")
    if any(segment in {".", ".."} for segment in decoded.split("/")):
        raise ValueError("URL path에 dot segment를 사용할 수 없음")
    return decoded.rstrip("/") or "/"


def _normalize_method(method: str) -> str:
    _require_non_empty(method, "method")
    if _METHOD_PATTERN.fullmatch(method) is None:
        raise PolicyConfigurationError("method는 대문자 영문이어야 함")
    return method


def _require_non_empty(value: str, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise PolicyConfigurationError(f"{label}는 비어 있지 않은 문자열이어야 함")


def _require_positive_integer(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PolicyConfigurationError(f"{label}는 1 이상의 정수여야 함")


def _require_choice(value: str, choices: frozenset[str], label: str) -> None:
    if value not in choices:
        raise PolicyConfigurationError(f"{label} 값이 허용 범위를 벗어남")


def _require_unique(values: Iterable[str], label: str) -> None:
    values_list = list(values)
    if len(values_list) != len(set(values_list)):
        raise PolicyConfigurationError(f"중복 {label}")


def _has_whitespace_or_control(value: str) -> bool:
    return any(character.isspace() or ord(character) == 127 for character in value)


def _pass_reason(dimension: str) -> str:
    reasons = {
        "state_change": "모든 요청의 상태 변경 영향이 Policy에서 허용됨",
        "data_impact": "모든 요청의 데이터 영향이 Policy에서 허용됨",
        "service_impact": "모든 요청의 서비스 영향이 Policy에서 허용됨",
    }
    return reasons[dimension]


def _impact_reason(dimension: str, status: str) -> str:
    labels = {
        "state_change": "상태 변경",
        "data_impact": "데이터 영향",
        "service_impact": "서비스 영향",
    }
    outcomes = {
        "pass": "Policy 규칙에서 허용됨",
        "require_approval": "사용자 승인이 필요함",
        "block": "Policy 규칙에서 차단됨",
    }
    return f"요청의 {labels[dimension]}이 {outcomes[status]}"
