import copy
import dataclasses
from collections.abc import Callable
from typing import Any

import pytest

from modules.scenario_generator.input_adapter import InputError, InputErrorCode, LoadedArtifact
from modules.scenario_generator.reproduction_checks import BASELINE_PREFIX
from modules.scenario_generator.scenario_drafter import Draft, DrafterError
from modules.scenario_generator.service import GenerationErrorCode, generate_scenarios

DraftBehavior = Callable[[dict[str, Any]], Draft]


class StubDrafter:
    def __init__(self, behavior: DraftBehavior) -> None:
        self._behavior = behavior
        self.model_info: dict[str, Any] | None = None
        self.requests: list[dict[str, Any]] = []

    def draft(self, request: dict[str, Any]) -> Draft:
        self.requests.append(request)
        return self._behavior(request)


def replaying(drafts: dict[str, Any], input_tokens: int | None = None, output_tokens: int | None = None) -> DraftBehavior:
    def behavior(request: dict[str, Any]) -> Draft:
        scenario = copy.deepcopy(drafts[request["candidate"]["candidate_id"]])
        return Draft(scenario, input_tokens, output_tokens)

    return behavior


def with_document(artifact: LoadedArtifact, mutate: Callable[[dict[str, Any]], None]) -> LoadedArtifact:
    document = copy.deepcopy(artifact.document)
    mutate(document)
    return dataclasses.replace(artifact, document=document)


def candidate_ids(artifact: LoadedArtifact) -> list[str]:
    return [c["candidate_id"] for c in artifact.document["data"]["candidates"]]


def test_all_candidates_become_scenarios(
    candidates_artifact: LoadedArtifact, crawl_artifact: LoadedArtifact, drafts: dict[str, Any]
) -> None:
    drafter = StubDrafter(replaying(drafts, input_tokens=10, output_tokens=5))
    outcome = generate_scenarios(candidates_artifact, crawl_artifact, drafter)

    ids = candidate_ids(candidates_artifact)
    assert outcome.errors == []
    assert [s["candidate_id"] for s in outcome.scenarios] == ids
    assert [s["scenario_id"] for s in outcome.scenarios] == [f"scenario_{i}" for i in ids]
    assert outcome.candidate_count == outcome.llm_calls == len(ids)
    assert outcome.input_tokens == 10 * len(ids) and outcome.output_tokens == 5 * len(ids)


def test_expected_basis_comes_from_the_candidate_not_the_model(
    candidates_artifact: LoadedArtifact, crawl_artifact: LoadedArtifact, drafts: dict[str, Any]
) -> None:
    outcome = generate_scenarios(candidates_artifact, crawl_artifact, StubDrafter(replaying(drafts)))
    expected = {c["candidate_id"]: c["expected_basis"] for c in candidates_artifact.document["data"]["candidates"]}
    assert {s["candidate_id"]: s["expected_basis"] for s in outcome.scenarios} == expected


def test_resource_ids_are_copied_from_the_candidate_in_order(
    candidates_artifact: LoadedArtifact, crawl_artifact: LoadedArtifact, drafts: dict[str, Any]
) -> None:
    def give_each_candidate_several_resources(document: dict[str, Any]) -> None:
        # 원래 순서를 뒤집은 여러 개로 만들어 앞쪽만·정렬해서·뒤집어서 옮기는 실수도 드러나게 한다.
        for candidate in document["data"]["candidates"]:
            first = candidate["resource_ids"][0]
            candidate["resource_ids"] = [f"{first}:second", first]

    candidates = with_document(candidates_artifact, give_each_candidate_several_resources)
    outcome = generate_scenarios(candidates, crawl_artifact, StubDrafter(replaying(drafts)))

    expected = {c["candidate_id"]: c["resource_ids"] for c in candidates.document["data"]["candidates"]}
    assert outcome.errors == []
    assert {s["candidate_id"]: s["resource_ids"] for s in outcome.scenarios} == expected


def test_draft_carrying_resource_ids_is_rejected_even_with_the_candidate_values(
    candidates_artifact: LoadedArtifact, crawl_artifact: LoadedArtifact, drafts: dict[str, Any]
) -> None:
    broken_id = candidate_ids(candidates_artifact)[0]
    base = replaying(drafts)

    def behavior(request: dict[str, Any]) -> Draft:
        draft = base(request)
        if request["candidate"]["candidate_id"] == broken_id:
            # 값이 원본과 같아도 받지 않는다. 이 키를 채우는 건 프로그램 몫이다.
            draft.scenario["resource_ids"] = list(request["candidate"]["resource_ids"])
        return draft

    outcome = generate_scenarios(candidates_artifact, crawl_artifact, StubDrafter(behavior))

    assert [(e["code"], e["item_ref"]) for e in outcome.errors] == [(GenerationErrorCode.DRAFT_INVALID, broken_id)]
    assert broken_id not in [s["candidate_id"] for s in outcome.scenarios]


def test_unmeasured_tokens_stay_null(
    candidates_artifact: LoadedArtifact, crawl_artifact: LoadedArtifact, drafts: dict[str, Any]
) -> None:
    outcome = generate_scenarios(candidates_artifact, crawl_artifact, StubDrafter(replaying(drafts)))
    assert outcome.input_tokens is None and outcome.output_tokens is None


@pytest.mark.parametrize("is_retryable", [True, False])
def test_drafter_failure_is_tracked_and_others_continue(
    candidates_artifact: LoadedArtifact, crawl_artifact: LoadedArtifact, drafts: dict[str, Any], is_retryable: bool
) -> None:
    failing_id = candidate_ids(candidates_artifact)[0]
    base = replaying(drafts)

    def behavior(request: dict[str, Any]) -> Draft:
        if request["candidate"]["candidate_id"] == failing_id:
            raise DrafterError("model unavailable", is_retryable=is_retryable)
        return base(request)

    outcome = generate_scenarios(candidates_artifact, crawl_artifact, StubDrafter(behavior))

    assert [e["item_ref"] for e in outcome.errors] == [failing_id]
    assert outcome.errors[0]["code"] == GenerationErrorCode.DRAFTER_FAILED
    assert outcome.errors[0]["retryable"] is is_retryable
    assert failing_id not in [s["candidate_id"] for s in outcome.scenarios]
    assert len(outcome.scenarios) == len(candidate_ids(candidates_artifact)) - 1


@pytest.mark.parametrize(
    "break_draft",
    [
        lambda draft: draft.update({"steps": []}),
        lambda draft: draft.update({"candidate_id": "smuggled"}),
        lambda draft: draft.update({"assertions": [
            {"check_id": "a1", "kind": "response_status", "subject_ref": "x", "selector": None, "operator": "eq", "expected": 403}
        ]}),
        lambda draft: draft.pop("steps"),
        lambda draft: draft["steps"][0].update({"account_id": "no_such_account"}),
        # 기준 단계는 재현 단계를 복사해 만든다. 모양이 틀린 단계로 만들다 터지지 않고 초안 오류로 남아야 한다.
        lambda draft: draft["steps"][-1].update({"bindings": "not a list"}),
    ],
    ids=["no steps", "extra key", "draft writes assertions", "missing key", "unknown account", "bindings not a list"],
)
def test_invalid_draft_is_rejected_not_hidden(
    candidates_artifact: LoadedArtifact,
    crawl_artifact: LoadedArtifact,
    drafts: dict[str, Any],
    break_draft: Callable[[dict[str, Any]], None],
) -> None:
    broken_id = candidate_ids(candidates_artifact)[0]
    base = replaying(drafts)

    def behavior(request: dict[str, Any]) -> Draft:
        draft = base(request)
        if request["candidate"]["candidate_id"] == broken_id:
            break_draft(draft.scenario)
        return draft

    outcome = generate_scenarios(candidates_artifact, crawl_artifact, StubDrafter(behavior))

    assert [(e["code"], e["item_ref"]) for e in outcome.errors] == [(GenerationErrorCode.DRAFT_INVALID, broken_id)]
    assert broken_id not in [s["candidate_id"] for s in outcome.scenarios]


def test_non_object_draft_is_rejected(candidates_artifact: LoadedArtifact, crawl_artifact: LoadedArtifact) -> None:
    outcome = generate_scenarios(
        candidates_artifact, crawl_artifact, StubDrafter(lambda request: Draft("just some text"))
    )
    assert outcome.scenarios == []
    assert {e["code"] for e in outcome.errors} == {GenerationErrorCode.DRAFT_INVALID}


def test_candidate_failing_the_match_never_reaches_the_drafter(
    candidates_artifact: LoadedArtifact, crawl_artifact: LoadedArtifact, drafts: dict[str, Any]
) -> None:
    broken = with_document(
        candidates_artifact, lambda doc: doc["data"]["candidates"][0].update({"actor_account_id": "no_such_account"})
    )
    drafter = StubDrafter(replaying(drafts))
    outcome = generate_scenarios(broken, crawl_artifact, drafter)

    skipped_id = candidate_ids(candidates_artifact)[0]
    assert skipped_id not in [r["candidate"]["candidate_id"] for r in drafter.requests]
    assert outcome.llm_calls == len(drafter.requests) == len(candidate_ids(candidates_artifact)) - 1
    assert [e["item_ref"] for e in outcome.errors] == [skipped_id]


def test_partial_upstream_is_reported_even_when_everything_else_succeeds(
    candidates_artifact: LoadedArtifact, crawl_artifact: LoadedArtifact, drafts: dict[str, Any]
) -> None:
    partial_crawl = dataclasses.replace(crawl_artifact, status="partial")
    outcome = generate_scenarios(candidates_artifact, partial_crawl, StubDrafter(replaying(drafts)))

    assert [e["code"] for e in outcome.errors] == [InputErrorCode.UPSTREAM_PARTIAL]
    assert len(outcome.scenarios) == len(candidate_ids(candidates_artifact))


def test_no_candidates_gives_empty_result_without_llm_calls(
    candidates_artifact: LoadedArtifact, crawl_artifact: LoadedArtifact, drafts: dict[str, Any]
) -> None:
    empty = with_document(candidates_artifact, lambda doc: doc["data"].update({"candidates": []}))
    drafter = StubDrafter(replaying(drafts))
    outcome = generate_scenarios(empty, crawl_artifact, drafter)

    assert outcome.scenarios == [] and outcome.errors == []
    assert outcome.candidate_count == outcome.llm_calls == 0 and drafter.requests == []


def test_duplicate_ids_in_crawl_result_raise_input_error(
    candidates_artifact: LoadedArtifact, crawl_artifact: LoadedArtifact, drafts: dict[str, Any]
) -> None:
    broken = with_document(
        crawl_artifact, lambda doc: doc["data"]["accounts"].append(copy.deepcopy(doc["data"]["accounts"][0]))
    )
    with pytest.raises(InputError) as caught:
        generate_scenarios(candidates_artifact, broken, StubDrafter(replaying(drafts)))
    assert caught.value.code is InputErrorCode.DUPLICATE_ID


def published_by_candidate(outcome: Any) -> dict[str, dict[str, Any]]:
    return {s["candidate_id"]: s for s in outcome.scenarios}


def reproduction_step(scenario: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    return [
        step for step in scenario["steps"]
        if step["account_id"] == candidate["actor_account_id"] and step["source_request_id"] in candidate["source_request_ids"]
    ][-1]


def test_assertions_are_built_by_the_program_from_the_candidate(
    candidates_artifact: LoadedArtifact, crawl_artifact: LoadedArtifact, drafts: dict[str, Any]
) -> None:
    # 기대 동작(기준 계정이 아니면 거부)의 부정이 재현 조건이다: 실행 계정이 기준 계정과 같은 응답을 받는다.
    requests = requests_by_id(crawl_artifact)
    outcome = generate_scenarios(candidates_artifact, crawl_artifact, StubDrafter(replaying(drafts)))
    assert outcome.errors == []
    for candidate in candidates_artifact.document["data"]["candidates"]:
        scenario = published_by_candidate(outcome)[candidate["candidate_id"]]
        step = reproduction_step(scenario, candidate)
        baseline_id = f"{BASELINE_PREFIX}{step['step_id']}"
        observed_status = requests[step["source_request_id"]]["response"]["status_code"]
        assert scenario["assertions"] == [
            {"check_id": "assertion_status", "kind": "response_status", "subject_ref": step["step_id"], "selector": None, "operator": "eq", "expected": observed_status},
            {"check_id": "assertion_baseline", "kind": "baseline_match", "subject_ref": step["step_id"], "selector": None, "operator": "eq", "expected": baseline_id},
            {"check_id": "assertion_owner", "kind": "resource_owner", "subject_ref": step["step_id"], "selector": None, "operator": "eq", "expected": baseline_id},
        ]


def test_baseline_step_replays_the_same_request_as_the_reference_account(
    candidates_artifact: LoadedArtifact, crawl_artifact: LoadedArtifact, drafts: dict[str, Any]
) -> None:
    outcome = generate_scenarios(candidates_artifact, crawl_artifact, StubDrafter(replaying(drafts)))
    accounts = {a["account_id"]: a for a in crawl_artifact.document["data"]["accounts"]}
    for candidate in candidates_artifact.document["data"]["candidates"]:
        scenario = published_by_candidate(outcome)[candidate["candidate_id"]]
        step = reproduction_step(scenario, candidate)
        position = scenario["steps"].index(step)
        baseline = scenario["steps"][position - 1]
        reference = accounts[candidate["reference_account_id"]]
        assert baseline["step_id"] == f"{BASELINE_PREFIX}{step['step_id']}"
        assert (baseline["account_id"], baseline["role_id"], baseline["session_ref"]) == (
            reference["account_id"], reference["role_id"], reference["session_ref"]
        )
        assert baseline["source_request_id"] == step["source_request_id"]
        assert baseline["request"]["method"] == step["request"]["method"]
        assert [s["order"] for s in scenario["steps"]] == list(range(len(scenario["steps"])))
        # 기준 계정 세션도 사전조건으로 확인한다(세션이 죽은 기준 응답과 비교하지 않게).
        assert reference["account_id"] in [c["subject_ref"] for c in scenario["preconditions"]]


def test_observed_status_is_the_expected_status_not_a_fixed_value(
    candidates_artifact: LoadedArtifact, crawl_artifact: LoadedArtifact, drafts: dict[str, Any]
) -> None:
    # 기대 상태 코드는 근거 요청에서 관찰한 값이다(200으로 고정하지 않는다).
    target_id = candidates_artifact.document["data"]["candidates"][0]["source_request_ids"][0]

    def created(document: dict[str, Any]) -> None:
        for request in document["data"]["requests"]:
            if request["request_id"] == target_id:
                request["response"]["status_code"] = 201

    outcome = generate_scenarios(candidates_artifact, with_document(crawl_artifact, created), StubDrafter(replaying(drafts)))
    scenario = published_by_candidate(outcome)[candidate_ids(candidates_artifact)[0]]
    status_check = next(check for check in scenario["assertions"] if check["kind"] == "response_status")
    assert status_check["expected"] == 201


def test_non_json_source_gets_no_resource_owner_check(
    candidates_artifact: LoadedArtifact, crawl_artifact: LoadedArtifact, drafts: dict[str, Any]
) -> None:
    # 식별자 비교는 JSON에만 적용된다. HTML 근거 응답이면 status·baseline_match만 만들고 오류 없이 발행한다.
    target_id = candidates_artifact.document["data"]["candidates"][0]["source_request_ids"][0]

    def to_html(document: dict[str, Any]) -> None:
        for request in document["data"]["requests"]:
            if request["request_id"] == target_id:
                request["response"]["content_type"] = "text/html; charset=utf-8"

    outcome = generate_scenarios(candidates_artifact, with_document(crawl_artifact, to_html), StubDrafter(replaying(drafts)))
    assert outcome.errors == []
    scenario = published_by_candidate(outcome)[candidate_ids(candidates_artifact)[0]]
    assert [check["kind"] for check in scenario["assertions"]] == ["response_status", "baseline_match"]


def test_candidate_without_reference_account_is_reported_without_calling_the_model(
    candidates_artifact: LoadedArtifact, crawl_artifact: LoadedArtifact, drafts: dict[str, Any]
) -> None:
    no_reference_id = candidate_ids(candidates_artifact)[-1]
    candidates = with_document(
        candidates_artifact, lambda doc: doc["data"]["candidates"][-1].update({"reference_account_id": None})
    )
    drafter = StubDrafter(replaying(drafts))
    outcome = generate_scenarios(candidates, crawl_artifact, drafter)

    assert no_reference_id not in [r["candidate"]["candidate_id"] for r in drafter.requests]
    assert outcome.llm_calls == len(candidate_ids(candidates_artifact)) - 1
    assert [(e["code"], e["item_ref"]) for e in outcome.errors] == [
        (GenerationErrorCode.ASSERTION_BASELINE_UNAVAILABLE, no_reference_id)
    ]
    assert no_reference_id not in published_by_candidate(outcome)
    assert outcome.diagnostics == []


@pytest.mark.parametrize("status", [302, 403])
def test_non_2xx_source_cannot_be_reproduced(
    candidates_artifact: LoadedArtifact, crawl_artifact: LoadedArtifact, drafts: dict[str, Any], status: int
) -> None:
    # 소유자도 정상(2xx)으로 받지 않은 요청은 "실행 계정도 받으면 재현"이라는 조건이 성립하지 않는다.
    broken_id = candidate_ids(candidates_artifact)[0]
    target_id = candidates_artifact.document["data"]["candidates"][0]["source_request_ids"][0]

    def set_status(document: dict[str, Any]) -> None:
        for request in document["data"]["requests"]:
            if request["request_id"] == target_id:
                request["response"]["status_code"] = status

    outcome = generate_scenarios(candidates_artifact, with_document(crawl_artifact, set_status), StubDrafter(replaying(drafts)))
    assert [(e["code"], e["item_ref"]) for e in outcome.errors] == [
        (GenerationErrorCode.ASSERTION_BASELINE_UNAVAILABLE, broken_id)
    ]
    assert broken_id not in published_by_candidate(outcome)
    assert outcome.diagnostics == []


def test_state_change_comes_from_the_source_request_method_not_the_draft(
    candidates_artifact: LoadedArtifact, crawl_artifact: LoadedArtifact, drafts: dict[str, Any]
) -> None:
    # 초안에는 state_change가 없다. GET 조회 근거면 모든 단계(기준 단계 포함)가 none으로 채워져 기준 응답을 만든다
    # (1.5b가 GET /api/users/{id}를 possible로 적어 시나리오가 안 나오던 문제).
    assert all("state_change" not in step for draft in drafts.values() for step in draft["steps"])
    sources = requests_by_id(crawl_artifact)

    outcome = generate_scenarios(candidates_artifact, crawl_artifact, StubDrafter(replaying(drafts)))

    assert outcome.errors == []
    steps = [step for scenario in outcome.scenarios for step in scenario["steps"]]
    assert steps and all(sources[step["source_request_id"]]["method"] == "GET" for step in steps)
    assert {step["state_change"] for step in steps} == {"none"}


@pytest.mark.parametrize("method", ["POST", "DELETE"])
def test_state_changing_source_request_cannot_get_a_baseline(
    candidates_artifact: LoadedArtifact, crawl_artifact: LoadedArtifact, drafts: dict[str, Any], method: str
) -> None:
    # 상태를 바꿀 수 있는 요청은 기준 계정이 먼저 보내면 상태가 달라져 같은 요청으로 기준 응답을 만들 수 없다.
    # 초안이 아니라 원본 요청 method가 근거라, 초안 탓(DRAFT_INVALID·진단 기록)이 아니다.
    broken_id = candidate_ids(candidates_artifact)[0]
    target_id = candidates_artifact.document["data"]["candidates"][0]["source_request_ids"][0]

    def set_method(document: dict[str, Any]) -> None:
        for request in document["data"]["requests"]:
            if request["request_id"] == target_id:
                request["method"] = method

    outcome = generate_scenarios(candidates_artifact, with_document(crawl_artifact, set_method), StubDrafter(replaying(drafts)))
    assert [(e["code"], e["item_ref"]) for e in outcome.errors] == [
        (GenerationErrorCode.ASSERTION_BASELINE_UNAVAILABLE, broken_id)
    ]
    assert broken_id not in published_by_candidate(outcome)
    assert outcome.diagnostics == []


def test_draft_carrying_state_change_is_rejected_even_with_the_estimated_value(
    candidates_artifact: LoadedArtifact, crawl_artifact: LoadedArtifact, drafts: dict[str, Any]
) -> None:
    broken_id = candidate_ids(candidates_artifact)[0]
    base = replaying(drafts)

    def behavior(request: dict[str, Any]) -> Draft:
        draft = base(request)
        if request["candidate"]["candidate_id"] == broken_id:
            # 프로그램이 채울 값과 같아도 받지 않는다. 초안이 이 키를 쓰면 모델 크기에 따라 판정이 갈린다.
            for step in draft.scenario["steps"]:
                step["state_change"] = "none"
        return draft

    outcome = generate_scenarios(candidates_artifact, crawl_artifact, StubDrafter(behavior))
    assert [(e["code"], e["item_ref"]) for e in outcome.errors] == [(GenerationErrorCode.DRAFT_INVALID, broken_id)]
    assert [record["candidate_id"] for record in outcome.diagnostics] == [broken_id]


def test_draft_without_actor_replay_is_invalid(
    candidates_artifact: LoadedArtifact, crawl_artifact: LoadedArtifact, drafts: dict[str, Any]
) -> None:
    # 실행 계정이 후보 근거 요청을 재현하지 않으면 재현 조건을 붙일 단계가 없다(초안 탓 → DRAFT_INVALID, 진단 기록).
    broken_id = candidate_ids(candidates_artifact)[0]
    base = replaying(drafts)

    def behavior(request: dict[str, Any]) -> Draft:
        draft = base(request)
        if request["candidate"]["candidate_id"] == broken_id:
            for step in draft.scenario["steps"]:
                step["account_role"] = "reference"
        return draft

    outcome = generate_scenarios(candidates_artifact, crawl_artifact, StubDrafter(behavior))
    assert [(e["code"], e["item_ref"]) for e in outcome.errors] == [(GenerationErrorCode.DRAFT_INVALID, broken_id)]
    assert [record["candidate_id"] for record in outcome.diagnostics] == [broken_id]


def requests_by_id(crawl_artifact: LoadedArtifact) -> dict[str, dict[str, Any]]:
    return {r["request_id"]: r for r in crawl_artifact.document["data"]["requests"]}


def observed_path_value(source: dict[str, Any], name: str) -> Any:
    return next(p["value"] for p in source["parameters"] if p["location"] == "path" and p["name"] == name)


def find_path_binding(drafts: dict[str, Any]) -> tuple[str, int, int]:
    """fixture에서 path 파라미터에 바인딩을 건 초안 위치: (후보 ID, step 위치, parameter 위치)."""
    for candidate_id, draft in drafts.items():
        for step_index, step in enumerate(draft["steps"]):
            for parameter_index, parameter in enumerate(step["request"]["parameters"]):
                if parameter["location"] == "path" and parameter["binding_ref"] is not None:
                    return candidate_id, step_index, parameter_index
    raise AssertionError("fixture에 path 바인딩 초안이 있어야 이 테스트가 의미 있다")


def test_url_template_is_built_by_the_program_from_the_source_request(
    candidates_artifact: LoadedArtifact, crawl_artifact: LoadedArtifact, drafts: dict[str, Any]
) -> None:
    # m5: url_template은 프로그램이 선택한 원본 요청 URL로 채운다. 초안이 path 파라미터에 바인딩을 걸면
    # 원본 URL에서 그 관찰값 조각 하나만 {binding_id}로 바뀌고, 그 파라미터는 URL로 흡수돼 출력에 남지 않는다.
    requests = requests_by_id(crawl_artifact)
    outcome = generate_scenarios(candidates_artifact, crawl_artifact, StubDrafter(replaying(drafts)))
    assert outcome.errors == []

    published = {s["candidate_id"]: s for s in outcome.scenarios}
    binding_count = 0
    for candidate_id, draft in drafts.items():
        drafted_steps = [step for step in published[candidate_id]["steps"] if not step["step_id"].startswith(BASELINE_PREFIX)]
        for draft_step, step in zip(draft["steps"], drafted_steps, strict=True):
            source = requests[step["source_request_id"]]
            restored = step["request"]["url_template"]
            for parameter in draft_step["request"]["parameters"]:
                if parameter["location"] != "path":
                    continue
                placeholder = "{" + parameter["binding_ref"] + "}"
                assert restored.count(placeholder) == 1
                restored = restored.replace(placeholder, str(observed_path_value(source, parameter["name"])))
                binding_count += 1
            assert restored == source["url"]
            assert all(p["location"] != "path" for p in step["request"]["parameters"])
    assert binding_count >= 1


def _with_url_written(step: dict[str, Any], source: dict[str, Any], parameter: dict[str, Any]) -> None:
    step["request"]["url_template"] = source["url"]  # 값이 원본과 같아도 URL은 프로그램 몫이다


def _with_literal_path_value(step: dict[str, Any], source: dict[str, Any], parameter: dict[str, Any]) -> None:
    parameter.update({"value": observed_path_value(source, parameter["name"]), "binding_ref": None})


def _with_value_and_binding(step: dict[str, Any], source: dict[str, Any], parameter: dict[str, Any]) -> None:
    parameter["value"] = observed_path_value(source, parameter["name"])


def _with_unknown_path_name(step: dict[str, Any], source: dict[str, Any], parameter: dict[str, Any]) -> None:
    parameter["name"] = parameter["name"] + "_not_in_source"


def _with_undeclared_binding(step: dict[str, Any], source: dict[str, Any], parameter: dict[str, Any]) -> None:
    step["bindings"] = []


@pytest.mark.parametrize(
    "break_step",
    [_with_url_written, _with_literal_path_value, _with_value_and_binding, _with_unknown_path_name, _with_undeclared_binding],
    ids=["draft writes url_template", "literal path value", "value with binding", "unknown path name", "undeclared binding"],
)
def test_path_change_outside_the_binding_rule_is_rejected(
    candidates_artifact: LoadedArtifact,
    crawl_artifact: LoadedArtifact,
    drafts: dict[str, Any],
    break_step: Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], None],
) -> None:
    # verifier는 URL에 {이름} 자리가 없는 path 파라미터를 조용히 무시한다. 효과 없는 변경을 "실행됨"으로 보이지 않게 거절한다.
    broken_id, step_index, parameter_index = find_path_binding(drafts)
    source = requests_by_id(crawl_artifact)[drafts[broken_id]["steps"][step_index]["source_request_id"]]
    base = replaying(drafts)

    def behavior(request: dict[str, Any]) -> Draft:
        draft = base(request)
        if request["candidate"]["candidate_id"] == broken_id:
            step = draft.scenario["steps"][step_index]
            break_step(step, source, step["request"]["parameters"][parameter_index])
        return draft

    outcome = generate_scenarios(candidates_artifact, crawl_artifact, StubDrafter(behavior))

    assert [(e["code"], e["item_ref"]) for e in outcome.errors] == [(GenerationErrorCode.DRAFT_INVALID, broken_id)]
    assert broken_id not in [s["candidate_id"] for s in outcome.scenarios]
    assert source["url"] not in outcome.errors[0]["message"]


@pytest.mark.parametrize("segment_count", [0, 2], ids=["value not in path", "value twice in path"])
def test_path_binding_needs_exactly_one_matching_segment(
    candidates_artifact: LoadedArtifact, crawl_artifact: LoadedArtifact, drafts: dict[str, Any], segment_count: int
) -> None:
    # 관찰값과 같은 경로 조각이 하나가 아니면 어느 조각을 바꿀지 모른다. 추측하지 않고 거절한다.
    broken_id, step_index, parameter_index = find_path_binding(drafts)
    draft_step = drafts[broken_id]["steps"][step_index]
    source_id = draft_step["source_request_id"]
    parameter_name = draft_step["request"]["parameters"][parameter_index]["name"]
    value = str(observed_path_value(requests_by_id(crawl_artifact)[source_id], parameter_name))

    def change_url(document: dict[str, Any]) -> None:
        for request in document["data"]["requests"]:
            if request["request_id"] == source_id:
                assert request["url"].count(f"/{value}") == 1
                replacement = f"/{value}/{value}" if segment_count == 2 else f"/{value}0"
                request["url"] = request["url"].replace(f"/{value}", replacement)

    outcome = generate_scenarios(candidates_artifact, with_document(crawl_artifact, change_url), StubDrafter(replaying(drafts)))

    assert [(e["code"], e["item_ref"]) for e in outcome.errors] == [(GenerationErrorCode.DRAFT_INVALID, broken_id)]
    assert broken_id not in [s["candidate_id"] for s in outcome.scenarios]
    assert value not in outcome.errors[0]["message"]
