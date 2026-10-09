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
