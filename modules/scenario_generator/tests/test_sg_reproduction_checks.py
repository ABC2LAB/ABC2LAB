"""재현 조건·기준 단계 생성(reproduction_checks) 단위 테스트. 리터럴 입력으로 규칙 하나씩 본다.

후보 기대 동작(기준 계정이 아니면 거부)의 부정이 재현 조건이다: 실행 계정이 기준 계정과 같은 응답을 받는다.
"""

import copy
from typing import Any

from modules.scenario_generator.candidate_matcher import CrawlIndex, MatchedCandidate
from modules.scenario_generator.reproduction_checks import (
    Reproduction,
    ReproductionProblem,
    build_reproduction,
    estimate_state_change,
    find_baseline_blocker,
)

ORIGIN = "http://target.example.test"
ACTOR = {"account_id": "acc_actor", "role_id": "role_user", "alias": "actor", "session_ref": "sess_actor"}
OWNER = {"account_id": "acc_owner", "role_id": "role_user", "alias": "owner", "session_ref": "sess_owner"}


def _request(request_id: str, path: str, status: int = 200, content_type: str | None = "application/json") -> dict[str, Any]:
    return {
        "request_id": request_id, "account_id": OWNER["account_id"], "role_id": OWNER["role_id"],
        "session_ref": OWNER["session_ref"], "method": "GET", "url": f"{ORIGIN}{path}", "parameters": [],
        "body_ref": None, "response": {"status_code": status, "content_type": content_type, "headers": [], "body_ref": None},
    }


def _index(*requests: dict[str, Any]) -> CrawlIndex:
    return CrawlIndex(roles_by_id={}, accounts_by_id={}, requests_by_id={r["request_id"]: r for r in requests})


def _matched(source_request_ids: tuple[str, ...] = ("req_detail",), reference: dict[str, Any] | None = OWNER) -> MatchedCandidate:
    candidate = {
        "candidate_id": "cand_1", "actor_account_id": ACTOR["account_id"],
        "reference_account_id": reference["account_id"] if reference else None,
        "source_request_ids": list(source_request_ids),
    }
    return MatchedCandidate(candidate=candidate, actor_account=ACTOR, reference_account=reference, source_requests=[])


def _step(
    step_id: str, order: int, account: dict[str, Any], source_request_id: str, url: str,
    bindings: list[dict[str, Any]] | None = None, state_change: str = "none",
) -> dict[str, Any]:
    return {
        "step_id": step_id, "order": order, "source_request_id": source_request_id,
        "account_id": account["account_id"], "role_id": account["role_id"], "session_ref": account["session_ref"],
        "request": {"method": "GET", "url_template": url, "parameters": [], "body_ref": None},
        "bindings": bindings or [], "state_change": state_change,
    }


def _build(steps: list[dict[str, Any]], index: CrawlIndex, matched: MatchedCandidate | None = None) -> Reproduction:
    result = build_reproduction(matched or _matched(), steps, index)
    assert isinstance(result, Reproduction), result
    return result


def _problem(steps: list[dict[str, Any]], index: CrawlIndex, matched: MatchedCandidate | None = None) -> ReproductionProblem:
    result = build_reproduction(matched or _matched(), steps, index)
    assert isinstance(result, ReproductionProblem), result
    return result


def test_json_target_gets_baseline_step_and_three_assertions() -> None:
    index = _index(_request("req_detail", "/items/7", status=201))
    result = _build([_step("s1", 0, ACTOR, "req_detail", f"{ORIGIN}/items/7")], index)

    baseline, target = result.steps
    assert (baseline["step_id"], baseline["order"], target["step_id"], target["order"]) == ("baseline_s1", 0, "s1", 1)
    assert (baseline["account_id"], baseline["role_id"], baseline["session_ref"]) == ("acc_owner", "role_user", "sess_owner")
    assert baseline["request"] == target["request"] and baseline["source_request_id"] == "req_detail"
    assert result.assertions == [
        # 기대 상태 코드는 관찰값(201)이다. 200으로 고정하지 않는다.
        {"check_id": "assertion_status", "kind": "response_status", "subject_ref": "s1", "selector": None, "operator": "eq", "expected": 201},
        {"check_id": "assertion_baseline", "kind": "baseline_match", "subject_ref": "s1", "selector": None, "operator": "eq", "expected": "baseline_s1"},
        {"check_id": "assertion_owner", "kind": "resource_owner", "subject_ref": "s1", "selector": None, "operator": "eq", "expected": "baseline_s1"},
    ]


def test_non_json_target_gets_no_resource_owner() -> None:
    for content_type in ("text/html; charset=utf-8", None):
        index = _index(_request("req_detail", "/items/7", content_type=content_type))
        result = _build([_step("s1", 0, ACTOR, "req_detail", f"{ORIGIN}/items/7")], index)
        assert [check["kind"] for check in result.assertions] == ["response_status", "baseline_match"]


def test_baseline_copies_bindings_with_renamed_ids() -> None:
    # 목록(기준 계정 setup) → 상세(실행 계정, 목록 응답에서 id 바인딩). 기준 단계는 상세 바로 앞에 같은 바인딩으로 들어간다.
    index = _index(_request("req_list", "/items"), _request("req_detail", "/items/7"))
    binding = {"binding_id": "b_item", "source_step_id": "s_list", "source_part": "response_body", "selector": "/0/id"}
    steps = [
        _step("s_list", 0, OWNER, "req_list", f"{ORIGIN}/items"),
        _step("s_detail", 1, ACTOR, "req_detail", f"{ORIGIN}/items/{{b_item}}", bindings=[binding]),
    ]
    steps[1]["request"]["parameters"] = [{"name": "page", "location": "query", "value": None, "binding_ref": "b_item"}]
    result = _build(steps, index)

    assert [(s["step_id"], s["order"]) for s in result.steps] == [("s_list", 0), ("baseline_s_detail", 1), ("s_detail", 2)]
    baseline = result.steps[1]
    assert baseline["bindings"] == [{**binding, "binding_id": "baseline_b_item"}]  # source_step_id는 그대로
    assert baseline["request"]["url_template"] == f"{ORIGIN}/items/{{baseline_b_item}}"
    assert baseline["request"]["parameters"][0]["binding_ref"] == "baseline_b_item"
    # 재현 단계는 원래 바인딩 그대로다.
    assert result.steps[2]["request"]["url_template"] == f"{ORIGIN}/items/{{b_item}}"


def test_last_actor_replay_is_the_reproduction_step() -> None:
    index = _index(_request("req_detail", "/items/7"))
    steps = [
        _step("s_first", 0, ACTOR, "req_detail", f"{ORIGIN}/items/7"),
        _step("s_last", 1, ACTOR, "req_detail", f"{ORIGIN}/items/7"),
    ]
    result = _build(steps, index)
    assert [s["step_id"] for s in result.steps] == ["s_first", "baseline_s_last", "s_last"]
    assert {check["subject_ref"] for check in result.assertions} == {"s_last"}


def test_input_steps_are_not_modified() -> None:
    index = _index(_request("req_detail", "/items/7"))
    steps = [_step("s1", 0, ACTOR, "req_detail", f"{ORIGIN}/items/7")]
    original = copy.deepcopy(steps)
    _build(steps, index)
    assert steps == original


def test_invalid_draft_orders_are_shifted_not_renumbered() -> None:
    # 초안 order가 틀렸으면(0, 5) 틀린 채로 남겨 validator가 잡게 한다. 0..N-1로 다시 매기면 초안 오류가 숨는다.
    index = _index(_request("req_list", "/items"), _request("req_detail", "/items/7"))
    steps = [_step("s_list", 0, OWNER, "req_list", f"{ORIGIN}/items"), _step("s_detail", 5, ACTOR, "req_detail", f"{ORIGIN}/items/7")]
    result = _build(steps, index)
    assert [s["order"] for s in result.steps] == [0, 5, 6]


def test_missing_reference_account_is_unavailable_before_drafting() -> None:
    matched = _matched(reference=None)
    assert find_baseline_blocker(matched) is not None
    problem = _problem([_step("s1", 0, ACTOR, "req_detail", f"{ORIGIN}/items/7")], _index(_request("req_detail", "/items/7")), matched)
    assert problem.is_baseline_unavailable
    assert find_baseline_blocker(_matched()) is None


def test_state_change_is_estimated_from_the_method_alone() -> None:
    # 안전 method(RFC 9110)만 none. 그 밖은 바꿀 수 있다고 보고 possible로 통일한다(expected·unknown은 쓰지 않는다).
    for method in ("GET", "HEAD", "OPTIONS"):
        assert estimate_state_change(method) == "none"
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert estimate_state_change(method) == "possible"


def test_state_changing_target_is_unavailable() -> None:
    # 가드는 조립된 단계 값을 본다. 지금은 method 추정값이지만, 나중에 collector가 요청별 상태 변경 정보를 주면 그 값이 올 자리다.
    index = _index(_request("req_detail", "/items/7"))
    for state_change in ("possible", "expected", "unknown"):
        problem = _problem([_step("s1", 0, ACTOR, "req_detail", f"{ORIGIN}/items/7", state_change=state_change)], index)
        assert problem.is_baseline_unavailable


def test_non_2xx_source_is_unavailable() -> None:
    for status in (199, 302, 403, 500):
        index = _index(_request("req_detail", "/items/7", status=status))
        problem = _problem([_step("s1", 0, ACTOR, "req_detail", f"{ORIGIN}/items/7")], index)
        assert problem.is_baseline_unavailable
    for status in (200, 299):
        _build([_step("s1", 0, ACTOR, "req_detail", f"{ORIGIN}/items/7")], _index(_request("req_detail", "/items/7", status=status)))


def test_no_actor_replay_of_candidate_request_is_a_draft_problem() -> None:
    index = _index(_request("req_detail", "/items/7"), _request("req_other", "/other"))
    # 실행 계정이 후보 근거 요청이 아닌 요청만 보내거나, 근거 요청을 기준 계정만 보내면 재현 단계가 없다.
    for steps in (
        [_step("s1", 0, ACTOR, "req_other", f"{ORIGIN}/other")],
        [_step("s1", 0, OWNER, "req_detail", f"{ORIGIN}/items/7")],
    ):
        assert not _problem(steps, index).is_baseline_unavailable


def test_baseline_id_collision_is_a_draft_problem() -> None:
    index = _index(_request("req_detail", "/items/7"), _request("req_list", "/items"))
    binding = {"binding_id": "baseline_b", "source_step_id": "s_list", "source_part": "response_body", "selector": "/0/id"}
    taken_step_id = [_step("baseline_s1", 0, OWNER, "req_list", f"{ORIGIN}/items"), _step("s1", 1, ACTOR, "req_detail", f"{ORIGIN}/items/7")]
    taken_binding_id = [
        _step("s_list", 0, OWNER, "req_list", f"{ORIGIN}/items", bindings=[dict(binding, source_step_id="s_list")]),
        _step("s1", 1, ACTOR, "req_detail", f"{ORIGIN}/items/{{b}}", bindings=[dict(binding, binding_id="b")]),
    ]
    for steps in (taken_step_id, taken_binding_id):
        assert not _problem(steps, index).is_baseline_unavailable
