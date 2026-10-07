import copy
import json
from pathlib import Path
from typing import Any

import pytest

from modules.scenario_generator.candidate_matcher import (
    MatchErrorCode,
    build_crawl_index,
    match_candidates,
)
from modules.scenario_generator.input_adapter import InputError, InputErrorCode

FIXTURE_ARTIFACTS = Path(__file__).parent / "fixtures" / "runs" / "run_demo_001" / "artifacts" / "iteration-000"


def load_data(relative_path: str) -> dict[str, Any]:
    return json.loads((FIXTURE_ARTIFACTS / relative_path).read_text(encoding="utf-8"))["data"]


@pytest.fixture
def crawl_data() -> dict[str, Any]:
    return load_data("collector/crawl_result.json")


@pytest.fixture
def candidates() -> list[dict[str, Any]]:
    return load_data("access_analyzer/vulnerability_candidates.json")["candidates"]


def other_role_id(crawl_data: dict[str, Any], role_id: str) -> str:
    return next(role["role_id"] for role in crawl_data["roles"] if role["role_id"] != role_id)


def test_fixture_candidates_all_match_in_order(crawl_data: dict[str, Any], candidates: list[dict[str, Any]]) -> None:
    result = match_candidates(candidates, build_crawl_index(crawl_data))

    assert result.rejected == []
    assert [item.candidate["candidate_id"] for item in result.matched] == [c["candidate_id"] for c in candidates]
    for item in result.matched:
        assert item.actor_account["account_id"] == item.candidate["actor_account_id"]
        assert [r["request_id"] for r in item.source_requests] == item.candidate["source_request_ids"]
        expected_reference = item.candidate["reference_account_id"]
        actual_reference = item.reference_account["account_id"] if item.reference_account else None
        assert actual_reference == expected_reference


def test_unknown_actor_account_is_rejected(crawl_data: dict[str, Any], candidates: list[dict[str, Any]]) -> None:
    broken = copy.deepcopy(candidates[0])
    broken["actor_account_id"] = "no_such_account"
    result = match_candidates([broken], build_crawl_index(crawl_data))

    assert result.matched == []
    assert result.rejected[0].code is MatchErrorCode.CANDIDATE_ACCOUNT_INVALID


def test_existing_ids_with_wrong_role_relation_are_rejected(
    crawl_data: dict[str, Any], candidates: list[dict[str, Any]]
) -> None:
    # 계정 ID도 역할 ID도 실제로 있지만, 그 계정의 역할이 아니다.
    broken = copy.deepcopy(candidates[0])
    broken["actor_role_id"] = other_role_id(crawl_data, broken["actor_role_id"])
    result = match_candidates([broken], build_crawl_index(crawl_data))

    assert result.matched == []
    assert result.rejected[0].code is MatchErrorCode.CANDIDATE_ACCOUNT_INVALID


def test_unknown_reference_account_is_rejected(crawl_data: dict[str, Any], candidates: list[dict[str, Any]]) -> None:
    broken = next(copy.deepcopy(c) for c in candidates if c["reference_account_id"] is not None)
    broken["reference_account_id"] = "no_such_account"
    result = match_candidates([broken], build_crawl_index(crawl_data))

    assert result.rejected[0].code is MatchErrorCode.CANDIDATE_ACCOUNT_INVALID


def test_null_reference_account_is_allowed(crawl_data: dict[str, Any], candidates: list[dict[str, Any]]) -> None:
    candidate = next(copy.deepcopy(c) for c in candidates if c["reference_account_id"] is not None)
    candidate["reference_account_id"] = None
    result = match_candidates([candidate], build_crawl_index(crawl_data))

    assert result.rejected == []
    assert result.matched[0].reference_account is None


@pytest.mark.parametrize("source_request_ids", [[], ["no_such_request"]])
def test_missing_or_empty_source_requests_are_rejected(
    crawl_data: dict[str, Any], candidates: list[dict[str, Any]], source_request_ids: list[str]
) -> None:
    broken = copy.deepcopy(candidates[0])
    broken["source_request_ids"] = source_request_ids
    result = match_candidates([broken], build_crawl_index(crawl_data))

    assert result.matched == []
    assert result.rejected[0].code is MatchErrorCode.CANDIDATE_REQUEST_INVALID


def test_request_with_role_not_matching_its_account_is_rejected(
    crawl_data: dict[str, Any], candidates: list[dict[str, Any]]
) -> None:
    data = copy.deepcopy(crawl_data)
    request_id = candidates[0]["source_request_ids"][0]
    request = next(r for r in data["requests"] if r["request_id"] == request_id)
    request["role_id"] = other_role_id(data, request["role_id"])
    result = match_candidates(candidates[:1], build_crawl_index(data))

    assert result.rejected[0].code is MatchErrorCode.CANDIDATE_REQUEST_INVALID


def test_request_with_session_ref_not_matching_its_account_is_rejected(
    crawl_data: dict[str, Any], candidates: list[dict[str, Any]]
) -> None:
    data = copy.deepcopy(crawl_data)
    request_id = candidates[0]["source_request_ids"][0]
    request = next(r for r in data["requests"] if r["request_id"] == request_id)
    request["session_ref"] = f"{request['session_ref']}_other"
    result = match_candidates(candidates[:1], build_crawl_index(data))

    assert result.rejected[0].code is MatchErrorCode.CANDIDATE_REQUEST_INVALID


def test_null_session_refs_on_both_sides_match(crawl_data: dict[str, Any], candidates: list[dict[str, Any]]) -> None:
    data = copy.deepcopy(crawl_data)
    candidate = candidates[0]
    request = next(r for r in data["requests"] if r["request_id"] == candidate["source_request_ids"][0])
    account = next(a for a in data["accounts"] if a["account_id"] == request["account_id"])
    request["session_ref"] = None
    account["session_ref"] = None
    result = match_candidates([candidate], build_crawl_index(data))

    assert result.rejected == []


def test_one_bad_candidate_does_not_stop_the_others(
    crawl_data: dict[str, Any], candidates: list[dict[str, Any]]
) -> None:
    broken = copy.deepcopy(candidates[0])
    broken["candidate_id"] = "broken_candidate"
    broken["actor_account_id"] = "no_such_account"
    result = match_candidates([broken, *candidates], build_crawl_index(crawl_data))

    assert [item.candidate["candidate_id"] for item in result.matched] == [c["candidate_id"] for c in candidates]
    assert [item.candidate_id for item in result.rejected] == ["broken_candidate"]


def test_duplicate_candidate_ids_are_all_excluded_with_one_error(
    crawl_data: dict[str, Any], candidates: list[dict[str, Any]]
) -> None:
    twin = copy.deepcopy(candidates[0])
    result = match_candidates([candidates[0], twin, *candidates[1:]], build_crawl_index(crawl_data))

    assert candidates[0]["candidate_id"] not in [m.candidate["candidate_id"] for m in result.matched]
    assert [(r.candidate_id, r.code) for r in result.rejected] == [
        (candidates[0]["candidate_id"], MatchErrorCode.CANDIDATE_ID_DUPLICATE)
    ]
    assert len(result.matched) == len(candidates) - 1


def test_no_candidates_gives_empty_result(crawl_data: dict[str, Any]) -> None:
    result = match_candidates([], build_crawl_index(crawl_data))
    assert result.matched == [] and result.rejected == []


@pytest.mark.parametrize("collection", ["roles", "accounts", "requests"])
def test_duplicate_ids_in_crawl_result_are_an_input_error(crawl_data: dict[str, Any], collection: str) -> None:
    data = copy.deepcopy(crawl_data)
    data[collection].append(copy.deepcopy(data[collection][0]))
    with pytest.raises(InputError) as caught:
        build_crawl_index(data)
    assert caught.value.code is InputErrorCode.DUPLICATE_ID


def test_rejected_candidate_error_item_points_to_the_candidate(
    crawl_data: dict[str, Any], candidates: list[dict[str, Any]]
) -> None:
    broken = copy.deepcopy(candidates[0])
    broken["source_request_ids"] = []
    item = match_candidates([broken], build_crawl_index(crawl_data)).rejected[0].to_error_item()

    assert set(item) == {"code", "message", "item_ref", "retryable"}
    assert item["item_ref"] == broken["candidate_id"]
