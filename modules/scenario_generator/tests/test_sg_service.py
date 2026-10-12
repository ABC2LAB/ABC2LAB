import copy
import dataclasses
from collections.abc import Callable
from typing import Any

import pytest

from modules.scenario_generator.input_adapter import InputError, InputErrorCode, LoadedArtifact
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
        lambda draft: draft.pop("assertions"),
        lambda draft: draft["steps"][0].update({"account_id": "no_such_account"}),
    ],
    ids=["no steps", "extra key", "missing key", "unknown account"],
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


def test_response_json_on_non_json_step_is_dropped_and_reported(
    candidates_artifact: LoadedArtifact, crawl_artifact: LoadedArtifact, drafts: dict[str, Any]
) -> None:
    # 원본 요청 하나의 응답을 HTML로 바꾸면, 그 요청을 쓰는 단계의 response_json만 버리고 시나리오는 발행한다.
    def content_steps(draft: dict[str, Any]) -> list[tuple[str, str]]:
        source_by_step = {step["step_id"]: step["source_request_id"] for step in draft["steps"]}
        return [
            (check["check_id"], source_by_step[check["subject_ref"]])
            for check in draft["assertions"]
            if check["kind"] == "response_json" and check["subject_ref"] in source_by_step
        ]

    html_request_id = next(source for draft in drafts.values() for _, source in content_steps(draft))
    requests = {r["request_id"]: r for r in crawl_artifact.document["data"]["requests"]}

    def to_html(document: dict[str, Any]) -> None:
        for request in document["data"]["requests"]:
            if request["request_id"] == html_request_id:
                request["response"]["content_type"] = "text/html; charset=utf-8"

    outcome = generate_scenarios(candidates_artifact, with_document(crawl_artifact, to_html), StubDrafter(replaying(drafts)))

    expected = [
        (candidate_id, check_id)
        for candidate_id in candidate_ids(candidates_artifact)
        for check_id, source in content_steps(drafts[candidate_id])
        if source == html_request_id
    ]
    assert expected
    assert [(e["code"], e["item_ref"]) for e in outcome.errors] == [
        (GenerationErrorCode.ASSERTION_NON_JSON_RESPONSE, candidate_id) for candidate_id, _ in expected
    ]
    published = {s["candidate_id"]: s for s in outcome.scenarios}
    assert [s["candidate_id"] for s in outcome.scenarios] == candidate_ids(candidates_artifact)
    for (candidate_id, check_id), error in zip(expected, outcome.errors):
        remaining = published[candidate_id]["preconditions"] + published[candidate_id]["assertions"]
        assert check_id not in [c["check_id"] for c in remaining]
        assert check_id in error["message"]
        # 메시지에는 check_id만: 원본 URL·content_type·parameter 값은 넣지 않는다.
        source = requests[html_request_id]
        assert source["url"] not in error["message"] and "text/html" not in error["message"]
        assert all(str(p["value"]) not in error["message"] for p in source["parameters"] if p["value"])


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
        for draft_step, step in zip(draft["steps"], published[candidate_id]["steps"], strict=True):
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
