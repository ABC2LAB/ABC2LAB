"""Check 평가와 result 분류(step4). 명세 m7: HTTP 상태만으로 success를 확정하지 않는다.

각 테스트는 '이 규칙을 빼면 통과해 버리는' 버그 하나를 겨눈다. 미평가 kind·판단불가는 failure가 아니라 indeterminate여야 한다.
"""

from pathlib import Path

from modules.verifier.execution import ExecutionContext, classify_scenario, execute_scenario
from modules.verifier.executor import ReplayResponse
from modules.verifier.tests.helpers import ScriptedExecutor, check, decision, scenario, step
from modules.verifier.utils.config import ReplayConfig
from modules.verifier.utils.evidence import EvidenceWriter

ORIGIN = "http://127.0.0.1:8001"


def _decision() -> dict:
    verdict = decision("d1", "s1", "allow", ["acc_user"])
    verdict["effective_origins"] = [ORIGIN]
    verdict["limits"] = {"max_requests": 8, "max_duration_ms": 10000, "allow_state_change": False}
    return verdict


def _run(scn, responses, run_root, invalid_accounts=()):
    executor = ScriptedExecutor(responses, invalid_accounts=invalid_accounts)
    ctx = ExecutionContext(executor, EvidenceWriter(run_root, 0), lambda: 0.0, ReplayConfig(0))
    outcome = execute_scenario(scn, _decision(), ctx)
    return classify_scenario(scn, _decision(), outcome), outcome


def _one(assertions=None, preconditions=None, url=f"{ORIGIN}/orders/7"):
    s = step("st1", "acc_user", "role_user", "sess_user", url_template=url)
    return scenario("s1", "c1", [s], preconditions=preconditions, assertions=assertions)


RESP = {("GET", f"{ORIGIN}/orders/7"): ReplayResponse(200, body={"owner_id": 3, "name": "a"})}


def test_all_assertions_true_is_success(tmp_path: Path) -> None:
    scn = _one(assertions=[check("a1", "response_status", "st1", "eq", 200), check("a2", "response_json", "st1", "eq", 3, selector="/owner_id")])
    item, _ = _run(scn, RESP, tmp_path / "run_demo_001")
    assert item["result"] == "success"


def test_status_matches_but_other_assertion_false_is_not_success(tmp_path: Path) -> None:
    # 상태 코드만 맞고 다른 조건(owner_id)이 거짓 → success가 아니라 failure(명세 m7: 200만으로 확정 금지).
    scn = _one(assertions=[check("a1", "response_status", "st1", "eq", 200), check("a2", "response_json", "st1", "eq", 999, selector="/owner_id")])
    item, _ = _run(scn, RESP, tmp_path / "run_demo_001")
    assert item["result"] == "failure"


def test_unevaluated_kind_is_indeterminate_not_failure(tmp_path: Path) -> None:
    # resource_owner는 PR2 미평가(passed=null) → 상태가 맞아도 success/failure가 아니라 indeterminate.
    scn = _one(assertions=[check("a1", "response_status", "st1", "eq", 200), check("a2", "resource_owner", "st1", "eq", "acc_user")])
    item, _ = _run(scn, RESP, tmp_path / "run_demo_001")
    assert item["result"] == "indeterminate"


def test_response_json_missing_path_is_indeterminate(tmp_path: Path) -> None:
    # 없는 경로 eq 비교 → 판단불가(null) → indeterminate(불일치 failure로 단정하지 않음).
    scn = _one(assertions=[check("a1", "response_json", "st1", "eq", 1, selector="/missing")])
    item, _ = _run(scn, RESP, tmp_path / "run_demo_001")
    assert item["result"] == "indeterminate"


def test_exists_operator_on_missing_is_false_then_failure(tmp_path: Path) -> None:
    scn = _one(assertions=[check("a1", "response_json", "st1", "exists", True, selector="/missing")])
    item, _ = _run(scn, RESP, tmp_path / "run_demo_001")
    assert item["result"] == "failure"  # 경로 없음 → exists False → 미재현


def test_precondition_false_is_indeterminate_not_failure(tmp_path: Path) -> None:
    # precondition이 거짓이면 전제가 성립 안 함 → indeterminate(failure 아님).
    scn = _one(
        preconditions=[check("p1", "response_status", "st1", "eq", 404)],
        assertions=[check("a1", "response_status", "st1", "eq", 200)],
    )
    item, _ = _run(scn, RESP, tmp_path / "run_demo_001")
    assert item["result"] == "indeterminate"


def test_precondition_unevaluable_is_indeterminate(tmp_path: Path) -> None:
    scn = _one(
        preconditions=[check("p1", "baseline_match", "st1", "eq", "x")],
        assertions=[check("a1", "response_status", "st1", "eq", 200)],
    )
    item, _ = _run(scn, RESP, tmp_path / "run_demo_001")
    assert item["result"] == "indeterminate"


def test_session_valid_on_unused_account_is_indeterminate(tmp_path: Path) -> None:
    # 어느 단계에도 안 쓰인 계정의 session_valid precondition → passed=null → 시나리오 indeterminate.
    scn = _one(
        preconditions=[check("p1", "session_valid", "acc_never_used", "eq", True)],
        assertions=[check("a1", "response_status", "st1", "eq", 200)],
    )
    item, _ = _run(scn, RESP, tmp_path / "run_demo_001")
    assert item["result"] == "indeterminate"


def test_status_only_assertions_is_indeterminate_not_success(tmp_path: Path) -> None:
    # 참인 assertion이 response_status·session_valid뿐 → 응답 내용 미확인 → success 아니라 indeterminate(m7 51행).
    scn = _one(assertions=[check("a1", "response_status", "st1", "eq", 200), check("a2", "session_valid", "acc_user", "eq", True)])
    item, outcome = _run(scn, RESP, tmp_path / "run_demo_001")
    assert item["result"] == "indeterminate"
    assert item["errors"][0]["code"] == "ASSERTION_STATUS_ONLY"
    # 두 Check 모두 실제로는 참이었다(평가 자체는 됨).
    passed = [c["passed"] for s in outcome.executed_steps for c in s["check_results"]]
    assert passed == [True, True]


def test_content_check_makes_success(tmp_path: Path) -> None:
    # 상태만으로는 indeterminate지만 response_json(내용)이 하나라도 참이면 success.
    scn = _one(assertions=[check("a1", "response_status", "st1", "eq", 200), check("a2", "response_json", "st1", "eq", 3, selector="/owner_id")])
    item, _ = _run(scn, RESP, tmp_path / "run_demo_001")
    assert item["result"] == "success"


def test_check_result_attached_to_subject_step(tmp_path: Path) -> None:
    scn = _one(assertions=[check("a1", "response_status", "st1", "eq", 200)])
    _, outcome = _run(scn, RESP, tmp_path / "run_demo_001")
    step_record = outcome.executed_steps[0]
    assert [c["check_id"] for c in step_record["check_results"]] == ["a1"]
    assert step_record["check_results"][0]["passed"] is True


def test_check_observed_is_redacted(tmp_path: Path) -> None:
    resp = {("GET", f"{ORIGIN}/orders/7"): ReplayResponse(200, body={"token": "secret-xyz"})}
    scn = _one(assertions=[check("a1", "response_json", "st1", "exists", True, selector="/token")])
    _, outcome = _run(scn, resp, tmp_path / "run_demo_001")
    # observed에 비밀 키 값이 그대로 남지 않는다(collector와 같은 기준으로 가림).
    observed = outcome.executed_steps[0]["check_results"][0]["observed"]
    assert observed == "***"
