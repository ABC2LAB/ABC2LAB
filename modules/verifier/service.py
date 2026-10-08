"""verifier 처리 로직. 안전 게이트 + allow 재현 실행(PR2).

게이트: 계획 해시 == Policy.scenarios_sha256 확인(전역) → 시나리오별 Policy 판정 대응 → allow는 계정·역할·세션
대응 확인. block·require_approval은 요청 없이 blocked(steps=[]). 게이트를 통과한 allow는 세션 창구가 주입되면 실행하고
(execution.py), 주입되지 않았으면 indeterminate+SESSION_UNAVAILABLE로 둔다. 실행·Check 판정은 execution 모듈.
"""

from dataclasses import dataclass
from typing import Any

from modules.verifier import execution
from modules.verifier.execution import ExecutionContext
from modules.verifier.utils.envelope import ErrorCode, Status, make_error_item


@dataclass(frozen=True)
class VerifyOutcome:
    status: Status
    errors: list[dict[str, Any]]
    # status=failed면 None
    data: dict[str, Any] | None


def build_verification_results(
    scenarios_artifact: dict[str, Any],
    decisions_artifact: dict[str, Any],
    crawl_artifact: dict[str, Any],
    scenarios_file_sha256: str,
    source_graph_revision: int,
    execution_context: ExecutionContext | None = None,
) -> VerifyOutcome:
    """게이트를 돌리고, allow는 execution_context가 있으면 실행해 verification_results의 status·errors·data를 만든다."""
    decisions_data = decisions_artifact["data"]
    # 전역 해시 게이트: 실행 직전 계획의 정확한 해시가 Policy가 판정한 해시와 같아야 한다(명세 m7).
    if scenarios_file_sha256 != decisions_data["scenarios_sha256"]:
        error = make_error_item(
            ErrorCode.SCENARIOS_HASH_MISMATCH,
            "test_scenarios 파일 해시가 safety_decisions.scenarios_sha256과 다름 — 어떤 요청도 보내지 않음",
            is_retryable=False,
        )
        return VerifyOutcome(Status.FAILED, [error], None)

    accounts_by_id = {account["account_id"]: account for account in crawl_artifact["data"]["accounts"]}
    decision_by_scenario = {decision["scenario_id"]: decision for decision in decisions_data["decisions"]}

    results: list[dict[str, Any]] = []
    envelope_errors: list[dict[str, Any]] = []
    for scenario in scenarios_artifact["data"]["scenarios"]:
        decision = decision_by_scenario.get(scenario["scenario_id"])
        if decision is None:
            # 판정 없는 시나리오는 VerificationItem(decision_id 필수)을 만들 수 없다. 결과에서 빼고 envelope 오류로 추적.
            envelope_errors.append(
                make_error_item(
                    ErrorCode.POLICY_DECISION_MISSING,
                    "시나리오에 대응하는 Policy 판정이 없음",
                    item_ref=scenario["scenario_id"],
                    is_retryable=False,
                )
            )
            continue
        results.append(_verify_scenario(scenario, decision, accounts_by_id, execution_context))

    data = {
        "source_graph_revision": source_graph_revision,
        "scenarios_sha256": scenarios_file_sha256,
        "results": results,
        # graph_updates(검증된 success 근거)는 Check 평가가 들어오는 다음 단계부터 채운다. 지금은 비어 있다.
        "graph_updates": {"source_verification_ids": [], "nodes": [], "relationships": []},
    }
    status = Status.PARTIAL if envelope_errors else Status.COMPLETED
    return VerifyOutcome(status, envelope_errors, data)


def _verify_scenario(
    scenario: dict[str, Any],
    decision: dict[str, Any],
    accounts_by_id: dict[str, dict[str, Any]],
    execution_context: ExecutionContext | None,
) -> dict[str, Any]:
    policy_decision = decision["decision"]
    if policy_decision in {"block", "require_approval"}:
        reason = "Policy가 차단함" if policy_decision == "block" else "사용자 승인 대기 — 실행하지 않음"
        return _item(scenario, decision, result="blocked", execution_status="not_executed", reason=reason)

    # allow: 계정·역할·세션 대응을 먼저 확인한다(미실행). 어긋나면 그 시나리오는 실행하지 않는다.
    gate_error = _account_gate(scenario, decision, accounts_by_id)
    if gate_error is not None:
        return _item(
            scenario, decision, result="indeterminate", execution_status="not_executed",
            reason="allow이지만 계정·역할·세션 대응이 맞지 않아 실행하지 않음", errors=[gate_error],
        )
    # 세션 공개 창구가 주입되지 않으면(CLI 등) 실행할 수 없다.
    if execution_context is None or execution_context.executor is None:
        unavailable = make_error_item(
            ErrorCode.SESSION_UNAVAILABLE, "세션 공개 창구가 주입되지 않아 실행할 수 없음",
            item_ref=scenario["scenario_id"], is_retryable=True,
        )
        return _item(
            scenario, decision, result="indeterminate", execution_status="not_executed",
            reason="allow 판정 통과 — 세션 창구 미연결", errors=[unavailable],
        )
    # 게이트 통과 allow를 실행하고 결과를 분류한다.
    outcome = execution.execute_scenario(scenario, decision, execution_context)
    return execution.classify_scenario(scenario, decision, outcome)


def _account_gate(
    scenario: dict[str, Any], decision: dict[str, Any], accounts_by_id: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    """각 step의 계정이 허용 계정·crawl 계정·역할·세션과 맞는지. 어긋나면 첫 오류를 돌려준다."""
    effective = set(decision["effective_account_ids"])
    for step in scenario["steps"]:
        account_id = step["account_id"]
        if account_id not in effective:
            return make_error_item(
                ErrorCode.ACCOUNT_SCOPE_MISMATCH, "step 계정이 effective_account_ids 밖임",
                item_ref=step["step_id"], is_retryable=False,
            )
        account = accounts_by_id.get(account_id)
        if account is None:
            return make_error_item(
                ErrorCode.ACCOUNT_SCOPE_MISMATCH, "step 계정이 crawl_result에 없음",
                item_ref=step["step_id"], is_retryable=False,
            )
        if account["role_id"] != step["role_id"]:
            return make_error_item(
                ErrorCode.ROLE_MISMATCH, "step 역할이 crawl_result 계정 역할과 다름",
                item_ref=step["step_id"], is_retryable=False,
            )
        if step["session_ref"] != account["session_ref"]:
            return make_error_item(
                ErrorCode.SESSION_INVALID, "step session_ref가 crawl_result 계정 세션과 다름",
                item_ref=step["step_id"], is_retryable=True,
            )
    return None


def _item(
    scenario: dict[str, Any],
    decision: dict[str, Any],
    result: str,
    execution_status: str,
    reason: str,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "verification_id": f"verification_{scenario['scenario_id']}",
        "candidate_id": scenario["candidate_id"],
        "scenario_id": scenario["scenario_id"],
        "decision_id": decision["decision_id"],
        "policy_decision": decision["decision"],
        "execution_status": execution_status,
        "result": result,
        "reason": reason,
        # 이 PR은 요청을 보내지 않으므로 항상 빈 steps.
        "steps": [],
        "evidence_refs": [],
        "errors": errors if errors is not None else [],
    }
