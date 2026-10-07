from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable

import pytest

from modules.safety_policy.evaluate_adapter import parse_evaluate_request
from modules.safety_policy.exceptions import (
    ApprovalRecordError,
    PolicyConfigurationError,
)
from modules.safety_policy.models import (
    AllowedTarget,
    ApprovalRecord,
    EvaluationInput,
    PolicyConfiguration,
    RequestPolicyRule,
    Scenario,
    ScenarioData,
    ScenarioStep,
    TestAccountPolicy as AccountPolicy,
)
from modules.safety_policy.policy import evaluate_policy
from modules.safety_policy.service import prepare_evaluation


@pytest.fixture
def evaluation_input(
    evaluate_arguments: tuple[dict[str, object], str, dict[str, object]],
) -> EvaluationInput:
    return prepare_evaluation(parse_evaluate_request(*evaluate_arguments))


@pytest.fixture
def policy_configuration() -> PolicyConfiguration:
    origin = "http://127.0.0.1:8001"
    return PolicyConfiguration(
        policy_id="abc2lab-test",
        policy_version="0.1.0",
        allowed_targets=(AllowedTarget(origin, ("/api/orders",)),),
        test_accounts=(
            AccountPolicy("account_user_001", ("role_user",), True),
            AccountPolicy("account_user_002", ("role_user",), True),
        ),
        max_requests=4,
        max_duration_ms=10_000,
        request_rules=(
            RequestPolicyRule(
                "orders.read",
                origin,
                "/api/orders",
                ("GET",),
                "none",
                "read_only",
                "low",
            ),
            RequestPolicyRule(
                "orders.update",
                origin,
                "/api/orders/2001/status",
                ("POST",),
                "require_approval",
                "require_approval",
                "low",
            ),
        ),
    )


def test_evaluate_policy_returns_one_decision_per_scenario(
    evaluation_input: EvaluationInput,
    policy_configuration: PolicyConfiguration,
) -> None:
    result = evaluate_policy(evaluation_input, policy_configuration)
    decision_by_scenario = {
        decision.scenario_id: decision for decision in result.decisions
    }

    assert result.policy_id == "abc2lab-test"
    assert result.scenarios_sha256 == evaluation_input.source.sha256
    assert set(decision_by_scenario) == {
        scenario.scenario_id for scenario in evaluation_input.scenarios.scenarios
    }
    read_decision = decision_by_scenario["scenario_read_order_001"]
    assert read_decision.decision == "allow"
    assert read_decision.reason_codes == ("ALL_ASSESSMENTS_PASSED",)
    assert read_decision.effective_origins == ("http://127.0.0.1:8001",)
    assert read_decision.effective_account_ids == ("account_user_001",)
    assert read_decision.limits.max_requests == 4
    assert read_decision.limits.allow_state_change is False

    update_decision = decision_by_scenario["scenario_update_order_001"]
    assert update_decision.decision == "require_approval"
    assert update_decision.effective_origins == ()
    assert update_decision.effective_account_ids == ()
    assert update_decision.limits.max_requests == 0


def test_verified_approval_allows_only_selected_scenario(
    evaluation_input: EvaluationInput,
    policy_configuration: PolicyConfiguration,
) -> None:
    approval = _approval_record(
        evaluation_input,
        policy_configuration,
        "scenario_update_order_001",
    )

    result = evaluate_policy(
        evaluation_input,
        policy_configuration,
        approval,
    )

    decisions = {item.scenario_id: item for item in result.decisions}
    read_decision = decisions["scenario_read_order_001"]
    update_decision = decisions["scenario_update_order_001"]
    assert read_decision.decision == "allow"
    assert read_decision.approval_ref is None
    assert update_decision.decision == "allow"
    assert update_decision.approval_ref == "approval_test_001"
    assert update_decision.assessment.statuses() == ("pass",) * 6
    assert update_decision.limits.allow_state_change is True


def test_approval_cannot_override_unknown_impact(
    evaluation_input: EvaluationInput,
    policy_configuration: PolicyConfiguration,
) -> None:
    read_input = _select_scenario(evaluation_input, "scenario_read_order_001")
    policy = replace(policy_configuration, request_rules=())
    approval = _approval_record(
        read_input,
        policy,
        "scenario_read_order_001",
    )

    with pytest.raises(ApprovalRecordError, match="불명확"):
        evaluate_policy(read_input, policy, approval)


def test_approval_cannot_override_blocked_scenario(
    evaluation_input: EvaluationInput,
    policy_configuration: PolicyConfiguration,
) -> None:
    update_input = _select_scenario(
        evaluation_input,
        "scenario_update_order_001",
    )
    blocked_rule = replace(
        policy_configuration.request_rules[1],
        service_impact="block",
    )
    policy = replace(policy_configuration, request_rules=(blocked_rule,))
    approval = _approval_record(
        update_input,
        policy,
        "scenario_update_order_001",
    )

    with pytest.raises(ApprovalRecordError, match="차단"):
        evaluate_policy(update_input, policy, approval)


def test_approval_cannot_be_attached_to_already_allowed_scenario(
    evaluation_input: EvaluationInput,
    policy_configuration: PolicyConfiguration,
) -> None:
    read_input = _select_scenario(evaluation_input, "scenario_read_order_001")
    approval = _approval_record(
        read_input,
        policy_configuration,
        "scenario_read_order_001",
    )

    with pytest.raises(ApprovalRecordError, match="필요하지 않은"):
        evaluate_policy(read_input, policy_configuration, approval)


def test_get_without_explicit_request_rule_is_not_allowed(
    evaluation_input: EvaluationInput,
    policy_configuration: PolicyConfiguration,
) -> None:
    read_input = _select_scenario(evaluation_input, "scenario_read_order_001")
    policy = replace(policy_configuration, request_rules=())

    decision = evaluate_policy(read_input, policy).decisions[0]

    assert decision.decision == "require_approval"
    assert decision.assessment.state_change.status == "unknown"
    assert decision.assessment.data_impact.status == "unknown"
    assert decision.assessment.service_impact.status == "unknown"


@pytest.mark.parametrize(
    "url_template",
    [
        "http://outside.example/api/orders",
        "http://127.0.0.1:8001/admin/orders",
        "http://127.0.0.1:8001/api/orders#result",
        "http://{dynamic_host}:8001/api/orders",
        "http://exa mple.test/api/orders",
        "http://127.0.0.1:8001/api/order list",
        "http://[fe80::1%25eth0]/api/orders",
    ],
)
def test_target_outside_configured_scope_is_blocked(
    evaluation_input: EvaluationInput,
    policy_configuration: PolicyConfiguration,
    url_template: str,
) -> None:
    changed = _change_step(
        evaluation_input,
        "scenario_read_order_001",
        "step_list_orders_001",
        lambda step: replace(
            step,
            request=replace(step.request, url_template=url_template),
        ),
    )

    decision = evaluate_policy(changed, policy_configuration).decisions[0]

    assert decision.decision == "block"
    assert decision.assessment.target_scope.status == "block"


@pytest.mark.parametrize(
    ("account_id", "role_id", "session_ref", "rule_id"),
    [
        ("account_unknown", "role_user", "session_user_001", "account.not_allowed"),
        (
            "account_user_001",
            "role_admin",
            "session_user_001",
            "account.role.not_allowed",
        ),
        ("account_user_001", "role_user", None, "account.session.required"),
    ],
)
def test_untrusted_account_context_is_blocked(
    evaluation_input: EvaluationInput,
    policy_configuration: PolicyConfiguration,
    account_id: str,
    role_id: str,
    session_ref: str | None,
    rule_id: str,
) -> None:
    changed = _change_step(
        evaluation_input,
        "scenario_read_order_001",
        "step_list_orders_001",
        lambda step: replace(
            step,
            account_id=account_id,
            role_id=role_id,
            session_ref=session_ref,
        ),
    )

    decision = evaluate_policy(changed, policy_configuration).decisions[0]

    assert decision.decision == "block"
    assert decision.assessment.test_accounts.rule_id == rule_id


def test_request_count_over_policy_limit_is_blocked(
    evaluation_input: EvaluationInput,
    policy_configuration: PolicyConfiguration,
) -> None:
    read_input = _select_scenario(evaluation_input, "scenario_read_order_001")
    policy = replace(policy_configuration, max_requests=1)

    decision = evaluate_policy(read_input, policy).decisions[0]

    assert decision.decision == "block"
    assert decision.assessment.request_budget.rule_id == "budget.request.exceeded"


@pytest.mark.parametrize(
    ("declared_state_change", "expected_status"),
    [
        ("possible", "require_approval"),
        ("expected", "require_approval"),
        ("unknown", "unknown"),
    ],
)
def test_plan_state_change_does_not_inherit_read_only_permission(
    evaluation_input: EvaluationInput,
    policy_configuration: PolicyConfiguration,
    declared_state_change: str,
    expected_status: str,
) -> None:
    changed = _change_step(
        evaluation_input,
        "scenario_read_order_001",
        "step_list_orders_001",
        lambda step: replace(step, state_change=declared_state_change),
    )

    decision = evaluate_policy(changed, policy_configuration).decisions[0]

    assert decision.decision == "require_approval"
    assert decision.assessment.state_change.status == expected_status


def test_explicit_policy_can_allow_state_change_with_enforced_limit(
    evaluation_input: EvaluationInput,
    policy_configuration: PolicyConfiguration,
) -> None:
    update_input = _select_scenario(evaluation_input, "scenario_update_order_001")
    allowed_rule = replace(
        policy_configuration.request_rules[1],
        state_change="allow",
        data_impact="allow",
    )
    policy = replace(policy_configuration, request_rules=(allowed_rule,))

    decision = evaluate_policy(update_input, policy).decisions[0]

    assert decision.decision == "allow"
    assert decision.assessment.statuses() == ("pass",) * 6
    assert decision.limits.allow_state_change is True


def test_block_has_priority_over_approval(
    evaluation_input: EvaluationInput,
    policy_configuration: PolicyConfiguration,
) -> None:
    update_input = _select_scenario(evaluation_input, "scenario_update_order_001")
    blocked_rule = replace(
        policy_configuration.request_rules[1],
        service_impact="block",
    )
    policy = replace(policy_configuration, request_rules=(blocked_rule,))

    decision = evaluate_policy(update_input, policy).decisions[0]

    assert decision.assessment.state_change.status == "require_approval"
    assert decision.assessment.service_impact.status == "block"
    assert decision.decision == "block"


def test_more_specific_request_rule_takes_precedence(
    evaluation_input: EvaluationInput,
    policy_configuration: PolicyConfiguration,
) -> None:
    update_input = _select_scenario(evaluation_input, "scenario_update_order_001")
    generic_rule = RequestPolicyRule(
        "orders.post.block",
        "http://127.0.0.1:8001",
        "/api/orders",
        ("POST",),
        "block",
        "block",
        "block",
    )
    allowed_rule = replace(
        policy_configuration.request_rules[1],
        state_change="allow",
        data_impact="allow",
    )
    policy = replace(
        policy_configuration,
        request_rules=(generic_rule, allowed_rule),
    )

    decision = evaluate_policy(update_input, policy).decisions[0]

    assert decision.decision == "allow"


def test_empty_scenario_collection_produces_empty_decisions(
    evaluation_input: EvaluationInput,
    policy_configuration: PolicyConfiguration,
) -> None:
    empty_input = replace(
        evaluation_input,
        scenarios=ScenarioData((), evaluation_input.scenarios.model_info),
    )

    result = evaluate_policy(empty_input, policy_configuration)

    assert result.decisions == ()


def test_default_ports_are_normalized_in_effective_origin(
    evaluation_input: EvaluationInput,
    policy_configuration: PolicyConfiguration,
) -> None:
    read_input = _select_scenario(evaluation_input, "scenario_read_order_001")
    scenario = _find_scenario(read_input, "scenario_read_order_001")
    changed_steps = tuple(
        replace(
            step,
            request=replace(
                step.request,
                url_template=step.request.url_template.replace(
                    "http://127.0.0.1:8001",
                    "http://example.test:80",
                ),
            ),
        )
        for step in scenario.steps
    )
    changed = replace(
        read_input,
        scenarios=ScenarioData(
            (replace(scenario, steps=changed_steps),),
            read_input.scenarios.model_info,
        ),
    )
    target = AllowedTarget("http://example.test", ("/api/orders",))
    rule = replace(
        policy_configuration.request_rules[0],
        origin="http://example.test:80",
    )
    policy = replace(
        policy_configuration,
        allowed_targets=(target,),
        request_rules=(rule,),
    )

    decision = evaluate_policy(changed, policy).decisions[0]

    assert decision.decision == "allow"
    assert decision.effective_origins == ("http://example.test",)


@pytest.mark.parametrize(
    "change_policy",
    [
        lambda policy: replace(policy, max_requests=0),
        lambda policy: replace(policy, max_requests=True),
        lambda policy: replace(
            policy,
            allowed_targets=(
                *policy.allowed_targets,
                AllowedTarget("http://127.0.0.1:8001", ("/other",)),
            ),
        ),
        lambda policy: replace(
            policy,
            request_rules=(
                *policy.request_rules,
                replace(policy.request_rules[0], rule_id="duplicate.read"),
            ),
        ),
        lambda policy: replace(
            policy,
            request_rules=(
                replace(policy.request_rules[0], path_prefix="/outside"),
            ),
        ),
        lambda policy: replace(
            policy,
            request_rules=(
                replace(policy.request_rules[0], methods=("get",)),
            ),
        ),
    ],
)
def test_invalid_or_ambiguous_policy_configuration_is_rejected(
    evaluation_input: EvaluationInput,
    policy_configuration: PolicyConfiguration,
    change_policy: Callable[[PolicyConfiguration], PolicyConfiguration],
) -> None:
    with pytest.raises(PolicyConfigurationError):
        evaluate_policy(evaluation_input, change_policy(policy_configuration))


def _select_scenario(
    evaluation_input: EvaluationInput,
    scenario_id: str,
) -> EvaluationInput:
    scenario = _find_scenario(evaluation_input, scenario_id)
    return replace(
        evaluation_input,
        scenarios=ScenarioData((scenario,), evaluation_input.scenarios.model_info),
    )


def _change_step(
    evaluation_input: EvaluationInput,
    scenario_id: str,
    step_id: str,
    change: Callable[[ScenarioStep], ScenarioStep],
) -> EvaluationInput:
    selected = _find_scenario(evaluation_input, scenario_id)
    changed_steps = tuple(
        change(step) if step.step_id == step_id else step for step in selected.steps
    )
    changed_scenario = replace(selected, steps=changed_steps)
    return replace(
        evaluation_input,
        scenarios=ScenarioData(
            (changed_scenario,),
            evaluation_input.scenarios.model_info,
        ),
    )


def _find_scenario(
    evaluation_input: EvaluationInput,
    scenario_id: str,
) -> Scenario:
    return next(
        scenario
        for scenario in evaluation_input.scenarios.scenarios
        if scenario.scenario_id == scenario_id
    )


def _approval_record(
    evaluation_input: EvaluationInput,
    policy: PolicyConfiguration,
    scenario_id: str,
) -> ApprovalRecord:
    return ApprovalRecord(
        approval_id="approval_test_001",
        run_id=evaluation_input.request.run_id,
        iteration=evaluation_input.request.iteration,
        scenarios_sha256=evaluation_input.source.sha256,
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        approved_scenario_ids=(scenario_id,),
        approved_by="operator_test_001",
        approved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )
