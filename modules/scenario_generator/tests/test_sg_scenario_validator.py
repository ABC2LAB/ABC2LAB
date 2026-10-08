import copy
from collections.abc import Callable
from typing import Any

import pytest

from modules.scenario_generator.candidate_matcher import CrawlIndex, MatchedCandidate
from modules.scenario_generator.scenario_validator import (
    MAX_REPORTED_PROBLEMS,
    ValidationContext,
    find_scenario_problems,
    summarize_problems,
)

Mutation = Callable[[dict[str, Any], dict[str, Any]], None]


def compose_scenario(matched: MatchedCandidate, drafts: dict[str, Any]) -> dict[str, Any]:
    candidate = matched.candidate
    return {
        "scenario_id": f"scenario_{candidate['candidate_id']}",
        "candidate_id": candidate["candidate_id"],
        "expected_basis": candidate["expected_basis"],
        **copy.deepcopy(drafts[candidate["candidate_id"]]),
    }


@pytest.fixture
def chained_case(
    matched_candidates: list[MatchedCandidate], crawl_index: CrawlIndex, crawl_artifact: Any, drafts: dict[str, Any]
) -> tuple[ValidationContext, dict[str, Any], dict[str, Any]]:
    """바인딩으로 두 단계가 이어진 시나리오(대조 통과) + 변형에 필요한 값."""
    matched = next(
        item
        for item in matched_candidates
        if any(step["bindings"] for step in drafts[item.candidate["candidate_id"]]["steps"])
    )
    context = ValidationContext(matched, crawl_index, crawl_artifact.document["data"]["target_url"])
    allowed = {matched.actor_account["account_id"]}
    if matched.reference_account:
        allowed.add(matched.reference_account["account_id"])
    other_account = next(a for a in crawl_index.accounts_by_id.values() if a["account_id"] not in allowed)
    other_role_id = next(r for r in crawl_index.roles_by_id if r != matched.actor_account["role_id"])
    return context, compose_scenario(matched, drafts), {"other_account_id": other_account["account_id"], "other_role_id": other_role_id}


def get_consumer(scenario: dict[str, Any]) -> dict[str, Any]:
    return next(step for step in scenario["steps"] if step["bindings"])


def get_producer(scenario: dict[str, Any]) -> dict[str, Any]:
    source_step_id = get_consumer(scenario)["bindings"][0]["source_step_id"]
    return next(step for step in scenario["steps"] if step["step_id"] == source_step_id)


def test_valid_scenarios_have_no_problems(
    matched_candidates: list[MatchedCandidate], crawl_index: CrawlIndex, crawl_artifact: Any, drafts: dict[str, Any]
) -> None:
    target_url = crawl_artifact.document["data"]["target_url"]
    for matched in matched_candidates:
        scenario = compose_scenario(matched, drafts)
        assert find_scenario_problems(scenario, ValidationContext(matched, crawl_index, target_url)) == []


def mutate_orders_skip(s: dict[str, Any], env: dict[str, Any]) -> None:
    get_consumer(s)["order"] += 1


def mutate_orders_duplicate(s: dict[str, Any], env: dict[str, Any]) -> None:
    get_consumer(s)["order"] = get_producer(s)["order"]


def mutate_binding_to_same_step(s: dict[str, Any], env: dict[str, Any]) -> None:
    consumer = get_consumer(s)
    consumer["bindings"][0]["source_step_id"] = consumer["step_id"]


def mutate_binding_to_later_step(s: dict[str, Any], env: dict[str, Any]) -> None:
    get_producer(s)["bindings"] = [
        {"binding_id": "binding_late", "source_step_id": get_consumer(s)["step_id"], "source_part": "response_body", "selector": "/x"}
    ]


def mutate_binding_to_unknown_step(s: dict[str, Any], env: dict[str, Any]) -> None:
    get_consumer(s)["bindings"][0]["source_step_id"] = "no_such_step"


def mutate_parameter_unknown_binding(s: dict[str, Any], env: dict[str, Any]) -> None:
    get_consumer(s)["request"]["parameters"] = [{"name": "p", "location": "query", "value": None, "binding_ref": "no_such_binding"}]


def mutate_parameter_value_and_binding(s: dict[str, Any], env: dict[str, Any]) -> None:
    binding_id = get_consumer(s)["bindings"][0]["binding_id"]
    get_consumer(s)["request"]["parameters"] = [{"name": "p", "location": "query", "value": "x", "binding_ref": binding_id}]


def append_step_reusing_consumer_binding(s: dict[str, Any]) -> dict[str, Any]:
    """소비 단계 뒤에 bindings 없이 소비 단계의 바인딩을 다시 쓰는 단계를 붙인다."""
    consumer = get_consumer(s)
    later_step = copy.deepcopy(consumer)
    later_step["step_id"] = "step_reuses_binding"
    later_step["order"] = len(s["steps"])
    later_step["bindings"] = []
    later_step["request"]["url_template"] = consumer["request"]["url_template"].split("{")[0] + "{" + consumer["bindings"][0]["binding_id"] + "}"
    s["steps"].append(later_step)
    return later_step


def mutate_url_uses_earlier_step_binding(s: dict[str, Any], env: dict[str, Any]) -> None:
    append_step_reusing_consumer_binding(s)


def mutate_parameter_uses_earlier_step_binding(s: dict[str, Any], env: dict[str, Any]) -> None:
    binding_id = get_consumer(s)["bindings"][0]["binding_id"]
    later_step = append_step_reusing_consumer_binding(s)
    later_step["request"]["url_template"] = later_step["request"]["url_template"].split("{")[0].rstrip("/")
    later_step["request"]["parameters"] = [{"name": "p", "location": "query", "value": None, "binding_ref": binding_id}]


def mutate_url_unknown_placeholder(s: dict[str, Any], env: dict[str, Any]) -> None:
    get_consumer(s)["request"]["url_template"] += "/{no_such_binding}"


def mutate_url_stray_brace(s: dict[str, Any], env: dict[str, Any]) -> None:
    get_consumer(s)["request"]["url_template"] += "/{"


def mutate_url_other_host(s: dict[str, Any], env: dict[str, Any]) -> None:
    get_producer(s)["request"]["url_template"] = "http://other.example.test/items"


def mutate_url_placeholder_in_host(s: dict[str, Any], env: dict[str, Any]) -> None:
    binding_id = get_consumer(s)["bindings"][0]["binding_id"]
    get_consumer(s)["request"]["url_template"] = f"http://{{{binding_id}}}.example.test/x"


def mutate_url_userinfo(s: dict[str, Any], env: dict[str, Any]) -> None:
    request = get_producer(s)["request"]
    request["url_template"] = request["url_template"].replace("://", "://user:pw@", 1)


def mutate_url_without_scheme(s: dict[str, Any], env: dict[str, Any]) -> None:
    get_producer(s)["request"]["url_template"] = "//target.example.test/items"


def mutate_account_outside_candidate(s: dict[str, Any], env: dict[str, Any]) -> None:
    get_consumer(s)["account_id"] = env["other_account_id"]


def mutate_role_mismatch(s: dict[str, Any], env: dict[str, Any]) -> None:
    get_consumer(s)["role_id"] = env["other_role_id"]


def mutate_session_mismatch(s: dict[str, Any], env: dict[str, Any]) -> None:
    get_consumer(s)["session_ref"] = "session_ref_of_nobody"


def mutate_unknown_source_request(s: dict[str, Any], env: dict[str, Any]) -> None:
    get_consumer(s)["source_request_id"] = "no_such_request"


def mutate_method_differs(s: dict[str, Any], env: dict[str, Any]) -> None:
    get_consumer(s)["request"]["method"] = "DELETE"


def mutate_invented_body_ref(s: dict[str, Any], env: dict[str, Any]) -> None:
    get_consumer(s)["request"]["body_ref"] = {
        "evidence_id": "invented", "kind": "request", "path": "evidence/x.json", "sha256": "0" * 64, "redacted": True
    }


def mutate_pointer_without_slash(s: dict[str, Any], env: dict[str, Any]) -> None:
    get_consumer(s)["bindings"][0]["selector"] = "no-leading-slash"


def mutate_uppercase_header_selector(s: dict[str, Any], env: dict[str, Any]) -> None:
    binding = get_consumer(s)["bindings"][0]
    binding["source_part"] = "response_header"
    binding["selector"] = "X-Token"


def mutate_response_json_bad_selector(s: dict[str, Any], env: dict[str, Any]) -> None:
    s["assertions"].append(
        {"check_id": "extra_check", "kind": "response_json", "subject_ref": "x", "selector": "bad", "operator": "eq", "expected": 1}
    )


def mutate_operator_in_without_list(s: dict[str, Any], env: dict[str, Any]) -> None:
    s["assertions"][0]["operator"] = "in"
    s["assertions"][0]["expected"] = 200


def mutate_duplicate_check_id(s: dict[str, Any], env: dict[str, Any]) -> None:
    s["preconditions"].append(copy.deepcopy(s["assertions"][0]))


def remove_assertions_of_kind(kind: str) -> Mutation:
    def mutate(s: dict[str, Any], env: dict[str, Any]) -> None:
        s["assertions"] = [check for check in s["assertions"] if check["kind"] != kind]
    return mutate


def mutate_response_check_unknown_step(s: dict[str, Any], env: dict[str, Any]) -> None:
    content_check = next(check for check in s["assertions"] if check["kind"] == "response_json")
    content_check["subject_ref"] = get_consumer(s)["account_id"]


def mutate_actor_assertions_moved_to_reference_step(s: dict[str, Any], env: dict[str, Any]) -> None:
    for check in s["assertions"]:
        check["subject_ref"] = get_producer(s)["step_id"]


def mutate_missing_reference_session(s: dict[str, Any], env: dict[str, Any]) -> None:
    reference_account_id = get_producer(s)["account_id"]
    s["preconditions"] = [check for check in s["preconditions"] if check["subject_ref"] != reference_account_id]


def mutate_session_check_unused_account(s: dict[str, Any], env: dict[str, Any]) -> None:
    s["preconditions"][0]["subject_ref"] = env["other_account_id"]


def mutate_session_check_uses_exists(s: dict[str, Any], env: dict[str, Any]) -> None:
    s["preconditions"][0]["operator"] = "exists"


PROBLEM_CASES: list[tuple[str, Mutation, str]] = [
    ("order skips a number", mutate_orders_skip, "steps.order"),
    ("order duplicated", mutate_orders_duplicate, "steps.order"),
    ("steps empty", lambda s, e: s.update({"steps": []}), "steps가 비어"),
    ("assertions empty", lambda s, e: s.update({"assertions": []}), "assertions가 비어"),
    ("duplicate step_id", lambda s, e: get_consumer(s).update({"step_id": get_producer(s)["step_id"]}), "step_id가 중복"),
    ("duplicate binding_id", lambda s, e: get_producer(s).update({"bindings": copy.deepcopy(get_consumer(s)["bindings"])}), "binding_id가 중복"),
    ("duplicate check_id", mutate_duplicate_check_id, "check_id가 중복"),
    ("binding from the same step", mutate_binding_to_same_step, "앞선 단계"),
    ("binding from a later step", mutate_binding_to_later_step, "앞선 단계"),
    ("binding from an unknown step", mutate_binding_to_unknown_step, "source_step_id가 steps에 없다"),
    ("parameter uses unknown binding", mutate_parameter_unknown_binding, "정의되지 않은 바인딩"),
    ("parameter has value and binding_ref", mutate_parameter_value_and_binding, "함께 쓴다"),
    ("url uses a binding defined in an earlier step", mutate_url_uses_earlier_step_binding, "정의되지 않은 바인딩"),
    ("parameter uses a binding defined in an earlier step", mutate_parameter_uses_earlier_step_binding, "정의되지 않은 바인딩"),
    ("url uses unknown placeholder", mutate_url_unknown_placeholder, "정의되지 않은 바인딩"),
    ("url has stray brace", mutate_url_stray_brace, "중괄호"),
    ("url points to another host", mutate_url_other_host, "origin 밖"),
    ("url has placeholder in host", mutate_url_placeholder_in_host, "주소에 바인딩"),
    ("url has userinfo", mutate_url_userinfo, "사용자 정보"),
    ("url has no scheme", mutate_url_without_scheme, "origin 밖"),
    ("account is not actor or reference", mutate_account_outside_candidate, "실행·기준 계정이 아니다"),
    ("role does not match account", mutate_role_mismatch, "role_id가"),
    ("session_ref does not match account", mutate_session_mismatch, "session_ref가"),
    ("source request does not exist", mutate_unknown_source_request, "crawl_result에 없다"),
    ("method differs from source request", mutate_method_differs, "method가"),
    ("body_ref is not the source request's", mutate_invented_body_ref, "body_ref가"),
    ("binding pointer has no leading slash", mutate_pointer_without_slash, "JSON Pointer"),
    ("header selector is not lowercase", mutate_uppercase_header_selector, "소문자 헤더"),
    ("response_json check has bad pointer", mutate_response_json_bad_selector, "JSON Pointer"),
    ("operator in needs a list", mutate_operator_in_without_list, "operator=in"),
    ("response check subject is not a step", mutate_response_check_unknown_step, "subject_ref가 steps의 step_id가 아니다"),
    ("actor step has no response_json", remove_assertions_of_kind("response_json"), "실행 계정 단계에 response_status와 response_json"),
    ("actor step has no response_status", remove_assertions_of_kind("response_status"), "실행 계정 단계에 response_status와 response_json"),
    ("assertions only on reference step", mutate_actor_assertions_moved_to_reference_step, "실행 계정 단계에 response_status와 response_json"),
    ("account used in steps has no session check", mutate_missing_reference_session, "session_valid 사전조건이 없다"),
    ("session check on account not in steps", mutate_session_check_unused_account, "steps에 쓰인 계정이 아니다"),
    ("session check uses exists", mutate_session_check_uses_exists, "operator=eq, expected=true"),
    ("state_change outside enum", lambda s, e: get_consumer(s).update({"state_change": "maybe"}), "Schema 위반"),
    ("unknown key in step", lambda s, e: get_consumer(s).update({"extra": 1}), "Schema 위반"),
    ("candidate_id differs from candidate", lambda s, e: s.update({"candidate_id": "another"}), "candidate_id가"),
    ("expected_basis differs from candidate", lambda s, e: s.update({"expected_basis": "rule" if s["expected_basis"] != "rule" else "unknown"}), "expected_basis가"),
]


@pytest.mark.parametrize(("name", "mutate", "fragment"), PROBLEM_CASES, ids=[case[0] for case in PROBLEM_CASES])
def test_problem_is_detected(
    chained_case: tuple[ValidationContext, dict[str, Any], dict[str, Any]], name: str, mutate: Mutation, fragment: str
) -> None:
    context, scenario, env = chained_case
    mutate(scenario, env)
    problems = find_scenario_problems(scenario, context)
    assert any(fragment in problem for problem in problems), (name, problems)


def test_schema_violation_skips_semantic_checks(
    chained_case: tuple[ValidationContext, dict[str, Any], dict[str, Any]],
) -> None:
    context, scenario, _ = chained_case
    scenario["steps"] = "not a list"
    problems = find_scenario_problems(scenario, context)
    assert len(problems) == 1 and problems[0].startswith("Schema 위반")


def test_problem_messages_do_not_echo_input_values(
    chained_case: tuple[ValidationContext, dict[str, Any], dict[str, Any]],
) -> None:
    context, scenario, _ = chained_case
    get_consumer(scenario)["state_change"] = "super-secret-value-123"
    assert all("super-secret-value-123" not in problem for problem in find_scenario_problems(scenario, context))


def test_problem_summary_is_truncated() -> None:
    problems = [f"problem {n}" for n in range(MAX_REPORTED_PROBLEMS + 3)]
    summary = summarize_problems(problems)
    assert "problem 0" in summary and f"problem {MAX_REPORTED_PROBLEMS}" not in summary
    assert summary.endswith("외 3건")
