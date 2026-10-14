"""기준 비교 Check(baseline_match·resource_owner)와 resource_state 평가. 리터럴 응답으로 규칙 하나씩 본다.

시나리오 모양은 SG가 만드는 것과 같다: 기준 계정(소유자)이 먼저 같은 요청을 보내고(base), 실행 계정이 다시 보낸다(st1).
JSON은 판정하고, 텍스트(HTML)는 control 응답이 생기기 전까지 coverage·포함 여부만 기록한다(판정 없음, ASSERTION_TEXT_ONLY).
"""

from pathlib import Path

import pytest

from modules.verifier.execution import ExecutionContext, classify_scenario, execute_scenario
from modules.verifier.executor import ReplayResponse
from modules.verifier.tests.helpers import ScriptedExecutor, check, decision, scenario, step
from modules.verifier.utils.config import ReplayConfig
from modules.verifier.utils.evidence import EvidenceWriter

ORIGIN = "http://127.0.0.1:8001"
URL = f"{ORIGIN}/orders/7"
OWNER = "acc_owner"
ACTOR = "acc_user"

LAYOUT_HEAD = "<html><head><title>Shop</title></head><body><nav><a href='/'>Home</a><a href='/orders'>Orders</a>"
LAYOUT_TAIL = "</main></body></html>"
ORDER_ROWS = "<h2>Order #7</h2><p>Ship to: 1 Main St</p><table><tr><td>Pen</td><td>2</td></tr></table><p>Total: 3,000</p>"
# 기준(소유자) 화면: 보이는 줄 10개(Shop·Home·Orders·Bob(user)·Order #7·Ship to·Pen·2·Total·Back)
OWNER_PAGE = f"{LAYOUT_HEAD}<span>Bob(user)</span></nav><main>{ORDER_ROWS}<a href='/orders'>Back</a>{LAYOUT_TAIL}"
# 실행 계정이 소유자 주문을 본 화면: 사용자 이름 한 줄만 다르다 → 기준 10줄 중 9줄
ACTOR_IDOR_PAGE = f"{LAYOUT_HEAD}<span>Alice(user)</span></nav><main>{ORDER_ROWS}<a href='/orders'>Back</a>{LAYOUT_TAIL}"
# 같은 틀의 200 오류 화면: 틀(Shop·Home·Orders)만 겹친다 → 기준 10줄 중 3줄
ACTOR_ERROR_PAGE = f"{LAYOUT_HEAD}<span>Alice(user)</span></nav><main><p>Not allowed</p>{LAYOUT_TAIL}"


def _decision() -> dict:
    verdict = decision("d1", "s1", "allow", [OWNER, ACTOR])
    verdict["effective_origins"] = [ORIGIN]
    verdict["limits"] = {"max_requests": 8, "max_duration_ms": 10000, "allow_state_change": False}
    return verdict


def _pair(assertions: list[dict], baseline_account: str = OWNER) -> dict:
    base = step("base", baseline_account, "role_user", f"sess_{baseline_account}", url_template=URL, order=0)
    actor = step("st1", ACTOR, "role_user", f"sess_{ACTOR}", url_template=URL, order=1)
    return scenario("s1", "c1", [base, actor], assertions=assertions)


def _responses(owner_response: ReplayResponse, actor_response: ReplayResponse) -> dict:
    return {(OWNER, "GET", URL): owner_response, (ACTOR, "GET", URL): actor_response}


def _run(scn: dict, responses: dict, run_root: Path):
    executor = ScriptedExecutor(responses)
    ctx = ExecutionContext(executor, EvidenceWriter(run_root, 0), lambda: 0.0, ReplayConfig(0))
    outcome = execute_scenario(scn, _decision(), ctx)
    return classify_scenario(scn, _decision(), outcome), outcome


def _error_refs(item: dict) -> list[tuple[str, str | None]]:
    return [(error["code"], error["item_ref"]) for error in item["errors"]]


def _results(outcome) -> dict[str, dict]:
    return {c["check_id"]: c for s in outcome.executed_steps for c in s["check_results"]}


STATUS = check("a_status", "response_status", "st1", "eq", 200)


def _baseline_match(selector: str | None = None, operator: str = "eq", expected: object = "base") -> dict:
    return check("a_base", "baseline_match", "st1", operator, expected, selector=selector)


def _resource_owner(selector: str | None = None, operator: str = "eq") -> dict:
    return check("a_owner", "resource_owner", "st1", operator, "base", selector=selector)


# --- 텍스트(HTML): 기록만 한다 ---


@pytest.mark.parametrize(
    "actor_page, expected_coverage",
    [(ACTOR_IDOR_PAGE, 0.9), (ACTOR_ERROR_PAGE, 0.3)],
    ids=["idor_shape", "error_page_200"],
)
def test_text_baseline_match_records_coverage_without_verdict(tmp_path: Path, actor_page: str, expected_coverage: float) -> None:
    # control 응답 없이 텍스트로는 위반(높음)·거부(낮음) 어느 쪽도 확정하지 않는다 → 둘 다 indeterminate.
    responses = _responses(ReplayResponse(200, body=OWNER_PAGE), ReplayResponse(200, body=actor_page))
    item, outcome = _run(_pair([STATUS, _baseline_match()]), responses, tmp_path / "run_demo_001")
    assert item["result"] == "indeterminate"
    assert _error_refs(item) == [("ASSERTION_TEXT_ONLY", "a_base")]
    result = _results(outcome)["a_base"]
    assert result["passed"] is None
    # 본문 텍스트는 남기지 않고 기준 단계와 coverage만 남긴다.
    assert result["observed"] == {"baseline_step_id": "base", "coverage": expected_coverage}


def test_text_denial_status_is_failure(tmp_path: Path) -> None:
    # 확정 음성은 상태 코드에서 나온다: 거부 코드면 텍스트 기록과 상관없이 failure.
    responses = _responses(ReplayResponse(200, body=OWNER_PAGE), ReplayResponse(403, body="<h1>403 Forbidden</h1>"))
    item, _ = _run(_pair([STATUS, _baseline_match()]), responses, tmp_path / "run_demo_001")
    assert item["result"] == "failure"


def test_hidden_text_difference_does_not_change_coverage(tmp_path: Path) -> None:
    # 스크립트 상수(토큰 등)는 요청마다 다를 수 있다. 보이는 줄만 비교한다.
    owner = "<body><script>var t = 'tok-a';</script><p>Order #7</p></body>"
    actor = "<body><script>var t = 'tok-b';</script><p>Order #7</p></body>"
    responses = _responses(ReplayResponse(200, body=owner), ReplayResponse(200, body=actor))
    _, outcome = _run(_pair([STATUS, _baseline_match()]), responses, tmp_path / "run_demo_001")
    assert _results(outcome)["a_base"]["observed"]["coverage"] == 1.0


def test_text_baseline_match_with_selector_is_not_applicable(tmp_path: Path) -> None:
    responses = _responses(ReplayResponse(200, body=OWNER_PAGE), ReplayResponse(200, body=ACTOR_IDOR_PAGE))
    item, _ = _run(_pair([STATUS, _baseline_match(selector="/order")]), responses, tmp_path / "run_demo_001")
    assert _error_refs(item) == [("CHECK_NOT_APPLICABLE", "a_base")]


# --- JSON baseline_match: 판정한다 ---


def test_json_baseline_match_equal_body_is_success(tmp_path: Path) -> None:
    body = {"id": 7, "owner_id": 2, "address": "1 Main St"}
    responses = _responses(ReplayResponse(200, body=body), ReplayResponse(200, body=dict(body)))
    item, outcome = _run(_pair([STATUS, _baseline_match()]), responses, tmp_path / "run_demo_001")
    assert item["result"] == "success"
    assert _results(outcome)["a_base"]["observed"] == {"baseline_step_id": "base", "matched": True}


def test_json_baseline_match_different_body_is_failure(tmp_path: Path) -> None:
    responses = _responses(ReplayResponse(200, body={"id": 7, "owner_id": 2}), ReplayResponse(200, body={"id": 9, "owner_id": 3}))
    item, _ = _run(_pair([STATUS, _baseline_match()]), responses, tmp_path / "run_demo_001")
    assert item["result"] == "failure"


def test_json_baseline_match_selector_compares_only_that_value(tmp_path: Path) -> None:
    # 보는 사람마다 다른 필드(viewer)는 selector 밖이면 비교하지 않는다.
    owner = {"order": {"id": 7, "total": 3000}, "viewer": "bob"}
    actor = {"order": {"id": 7, "total": 3000}, "viewer": "alice"}
    responses = _responses(ReplayResponse(200, body=owner), ReplayResponse(200, body=actor))
    item, _ = _run(_pair([STATUS, _baseline_match(selector="/order")]), responses, tmp_path / "run_demo_001")
    assert item["result"] == "success"


def test_json_baseline_match_selector_missing_in_baseline_is_not_applicable(tmp_path: Path) -> None:
    responses = _responses(ReplayResponse(200, body={"id": 7}), ReplayResponse(200, body={"id": 7}))
    item, _ = _run(_pair([STATUS, _baseline_match(selector="/order")]), responses, tmp_path / "run_demo_001")
    assert _error_refs(item) == [("SELECTOR_ROOT_MISSING", "a_base")]


def test_ne_operator_inverts_the_match(tmp_path: Path) -> None:
    responses = _responses(ReplayResponse(200, body={"id": 7}), ReplayResponse(200, body={"id": 9}))
    item, _ = _run(_pair([STATUS, _baseline_match(operator="ne")]), responses, tmp_path / "run_demo_001")
    assert item["result"] == "success"


def test_operator_other_than_eq_ne_is_not_applicable(tmp_path: Path) -> None:
    responses = _responses(ReplayResponse(200, body={"id": 7}), ReplayResponse(200, body={"id": 7}))
    item, _ = _run(_pair([STATUS, _baseline_match(operator="exists")]), responses, tmp_path / "run_demo_001")
    assert _error_refs(item) == [("CHECK_NOT_APPLICABLE", "a_base")]


def test_json_against_text_is_not_applicable(tmp_path: Path) -> None:
    # 기준은 JSON인데 실행 응답이 HTML(로그인 화면 등)이면 비교 규칙이 없다 → 불일치로 단정하지 않는다.
    responses = _responses(ReplayResponse(200, body={"id": 7}), ReplayResponse(200, body="<p>login</p>"))
    item, _ = _run(_pair([STATUS, _baseline_match(), _resource_owner()]), responses, tmp_path / "run_demo_001")
    assert item["result"] == "indeterminate"
    assert _error_refs(item) == [("CHECK_NOT_APPLICABLE", "a_base"), ("CHECK_NOT_APPLICABLE", "a_owner")]


# --- resource_owner: 식별자로 같은 자원인지 본다 ---


def test_resource_owner_same_identifiers_is_success(tmp_path: Path) -> None:
    owner = {"id": 7, "owner_id": 2, "items": [{"product_id": 5}], "note": "x"}
    actor = {"id": 7, "owner_id": 2, "items": [{"product_id": 5}], "note": "different text"}
    responses = _responses(ReplayResponse(200, body=owner), ReplayResponse(200, body=actor))
    item, outcome = _run(_pair([STATUS, _resource_owner()]), responses, tmp_path / "run_demo_001")
    assert item["result"] == "success"
    assert _results(outcome)["a_owner"]["observed"] == {"baseline_step_id": "base", "matched_count": 3, "baseline_count": 3}


def test_resource_owner_actor_own_resource_is_failure(tmp_path: Path) -> None:
    # 앱이 요청 id를 무시하고 실행 계정 자기 자원을 돌려주면 키는 같고 값이 다르다 → 다른 자원이다.
    responses = _responses(ReplayResponse(200, body={"id": 7, "owner_id": 2}), ReplayResponse(200, body={"id": 9, "owner_id": 3}))
    item, outcome = _run(_pair([STATUS, _resource_owner()]), responses, tmp_path / "run_demo_001")
    assert item["result"] == "failure"
    assert _results(outcome)["a_owner"]["observed"]["matched_count"] == 0


def test_resource_owner_ignores_sensitive_identifier_keys(tmp_path: Path) -> None:
    # session_id는 식별자가 아니다(collector와 같은 기준). 계정마다 달라도 같은 자원 판정을 흔들지 않는다.
    owner = {"id": 7, "session_id": 1}
    actor = {"id": 7, "session_id": 2}
    responses = _responses(ReplayResponse(200, body=owner), ReplayResponse(200, body=actor))
    item, _ = _run(_pair([STATUS, _resource_owner()]), responses, tmp_path / "run_demo_001")
    assert item["result"] == "success"


def test_resource_owner_without_baseline_identifiers_is_not_applicable(tmp_path: Path) -> None:
    responses = _responses(ReplayResponse(200, body={"name": "x"}), ReplayResponse(200, body={"name": "x"}))
    item, _ = _run(_pair([STATUS, _resource_owner()]), responses, tmp_path / "run_demo_001")
    assert item["result"] == "indeterminate"
    assert _error_refs(item) == [("CHECK_NOT_APPLICABLE", "a_owner")]


# --- SG가 내는 조합: response_status + baseline_match + resource_owner (JSON) ---


def test_sg_shaped_assertions_all_true_is_success(tmp_path: Path) -> None:
    # 소유자(bob→bob 주문)와 실행 계정(alice→bob 주문)이 같은 자원을 받음 → 셋 다 참 → 시나리오 판정 success(위반 재현).
    body = {"id": 7, "owner_id": 2, "items": [{"product_id": 5}], "address": "1 Main St"}
    responses = _responses(ReplayResponse(200, body=body), ReplayResponse(200, body=dict(body)))
    item, outcome = _run(_pair([STATUS, _baseline_match(), _resource_owner()]), responses, tmp_path / "run_demo_001")
    assert [result["passed"] for result in _results(outcome).values()] == [True, True, True]
    assert item["result"] == "success"
    assert item["errors"] == []


def test_sg_shaped_assertions_actor_own_resource_is_failure_by_owner_check(tmp_path: Path) -> None:
    # status는 2xx, baseline_match도 참(같은 위치 값)으로 두고 resource_owner만 거짓 → failure의 원인이 자원 동일성뿐이다.
    owner = {"order": {"id": 7, "owner_id": 2}, "shop": "Test Shop"}
    actor = {"order": {"id": 9, "owner_id": 3}, "shop": "Test Shop"}
    responses = _responses(ReplayResponse(200, body=owner), ReplayResponse(200, body=actor))
    assertions = [STATUS, _baseline_match(selector="/shop"), _resource_owner()]
    item, outcome = _run(_pair(assertions), responses, tmp_path / "run_demo_001")
    assert [result["passed"] for result in _results(outcome).values()] == [True, True, False]
    assert item["result"] == "failure"
    assert item["errors"] == []


# --- 기준 단계를 쓸 수 없으면 판단불가 ---


def test_baseline_step_not_executed_is_unavailable(tmp_path: Path) -> None:
    responses = _responses(ReplayResponse(200, body={"id": 7}), ReplayResponse(200, body={"id": 7}))
    item, _ = _run(_pair([STATUS, _baseline_match(expected="no_such_step")]), responses, tmp_path / "run_demo_001")
    assert item["result"] == "indeterminate"
    assert _error_refs(item) == [("BASELINE_UNAVAILABLE", "a_base")]


def test_baseline_step_by_same_account_is_unavailable(tmp_path: Path) -> None:
    # 같은 계정의 두 응답은 권한 차이를 비교한 게 아니다.
    responses = {(ACTOR, "GET", URL): ReplayResponse(200, body={"id": 7})}
    item, _ = _run(_pair([STATUS, _baseline_match()], baseline_account=ACTOR), responses, tmp_path / "run_demo_001")
    assert item["result"] == "indeterminate"
    assert _error_refs(item) == [("BASELINE_UNAVAILABLE", "a_base")]


@pytest.mark.parametrize("baseline_status", [302, 403, 404])
def test_non_2xx_baseline_is_unavailable(tmp_path: Path, baseline_status: int) -> None:
    # 소유자도 정상으로 받지 못한 응답은 정상 기준이 아니다.
    responses = _responses(ReplayResponse(baseline_status, body={"id": 7}), ReplayResponse(200, body={"id": 7}))
    item, _ = _run(_pair([STATUS, _baseline_match()]), responses, tmp_path / "run_demo_001")
    assert item["result"] == "indeterminate"
    assert _error_refs(item) == [("BASELINE_UNAVAILABLE", "a_base")]


def test_server_error_subject_is_not_compared(tmp_path: Path) -> None:
    responses = _responses(ReplayResponse(200, body={"id": 7}), ReplayResponse(500, body={"id": 7}))
    item, _ = _run(_pair([_baseline_match()]), responses, tmp_path / "run_demo_001")
    assert item["result"] == "indeterminate"
    assert _error_refs(item) == [("SERVER_ERROR_RESPONSE", "a_base")]


# --- resource_state ---


def test_json_resource_state_is_judged(tmp_path: Path) -> None:
    responses = _responses(ReplayResponse(200, body={"id": 7}), ReplayResponse(200, body={"status": "cancelled"}))
    state = check("a_state", "resource_state", "st1", "eq", "cancelled", selector="/status")
    item, _ = _run(_pair([STATUS, state]), responses, tmp_path / "run_demo_001")
    assert item["result"] == "success"


def test_json_resource_state_observed_is_redacted(tmp_path: Path) -> None:
    responses = _responses(ReplayResponse(200, body={"id": 7}), ReplayResponse(200, body={"token": "secret-xyz"}))
    state = check("a_state", "resource_state", "st1", "exists", True, selector="/token")
    _, outcome = _run(_pair([state]), responses, tmp_path / "run_demo_001")
    assert _results(outcome)["a_state"]["observed"] == "***"


def test_text_resource_state_contains_is_recorded_only(tmp_path: Path) -> None:
    responses = _responses(ReplayResponse(200, body=OWNER_PAGE), ReplayResponse(200, body=ACTOR_IDOR_PAGE))
    state = check("a_state", "resource_state", "st1", "contains", "Ship   to: 1 Main St")
    item, outcome = _run(_pair([STATUS, state]), responses, tmp_path / "run_demo_001")
    assert item["result"] == "indeterminate"
    assert _error_refs(item) == [("ASSERTION_TEXT_ONLY", "a_state")]
    result = _results(outcome)["a_state"]
    assert result["passed"] is None
    assert result["observed"] == {"contains": True}


@pytest.mark.parametrize(
    "operator, expected, selector",
    [("eq", "Order #7", None), ("contains", "", None), ("contains", 7, None), ("contains", "Order", "/x")],
)
def test_text_resource_state_other_forms_are_not_applicable(
    tmp_path: Path, operator: str, expected: object, selector: str | None
) -> None:
    responses = _responses(ReplayResponse(200, body=OWNER_PAGE), ReplayResponse(200, body=ACTOR_IDOR_PAGE))
    state = check("a_state", "resource_state", "st1", operator, expected, selector=selector)
    item, _ = _run(_pair([STATUS, state]), responses, tmp_path / "run_demo_001")
    assert _error_refs(item) == [("CHECK_NOT_APPLICABLE", "a_state")]
