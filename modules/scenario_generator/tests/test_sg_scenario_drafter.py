import copy
import json
from pathlib import Path
from typing import Any

import pytest

from modules.scenario_generator.candidate_matcher import MatchedCandidate
from modules.scenario_generator.replay_drafter import ReplayScenarioDrafter
from modules.scenario_generator.scenario_drafter import DrafterError, build_draft_request

TARGET_URL = "http://target.example.test"


def test_draft_request_carries_candidate_accounts_and_requests(matched_candidates: list[MatchedCandidate]) -> None:
    for matched in matched_candidates:
        request = build_draft_request(matched, TARGET_URL)
        assert request["target_url"] == TARGET_URL
        assert request["candidate"]["candidate_id"] == matched.candidate["candidate_id"]
        assert request["actor_account"]["account_id"] == matched.actor_account["account_id"]
        assert [r["request_id"] for r in request["source_requests"]] == matched.candidate["source_request_ids"]
        expected_reference = matched.reference_account["account_id"] if matched.reference_account else None
        actual_reference = request["reference_account"]["account_id"] if request["reference_account"] else None
        assert actual_reference == expected_reference


def test_draft_request_never_contains_secrets_or_evidence(matched_candidates: list[MatchedCandidate]) -> None:
    matched = copy.deepcopy(matched_candidates[0])
    source = matched.source_requests[0]
    # 스키마가 막아 주는 값까지 일부러 넣어서, 이 함수가 한 겹 더 막는지 본다.
    source["parameters"] = [
        {"name": "leaky_token", "location": "body", "value": "sensitive-value-xyz", "is_sensitive": True},
        {"name": "session_cookie", "location": "cookie", "value": "cookie-value-xyz", "is_sensitive": False},
        {"name": "auth_header", "location": "header", "value": "header-value-xyz", "is_sensitive": False},
        {"name": "plain_query", "location": "query", "value": "visible-value", "is_sensitive": False},
    ]
    source["headers"] = [{"name": "cookie", "value": "header-cookie-xyz", "redacted": False}]

    request = build_draft_request(matched, TARGET_URL)
    dumped = json.dumps(request)

    for secret in ("sensitive-value-xyz", "cookie-value-xyz", "header-value-xyz", "header-cookie-xyz"):
        assert secret not in dumped
    assert "evidence_refs" not in dumped and "headers" not in dumped and "body_ref" not in dumped
    names = [p["name"] for p in request["source_requests"][0]["parameters"]]
    assert names == ["leaky_token", "plain_query"]
    assert request["source_requests"][0]["parameters"][0]["value"] is None
    assert request["source_requests"][0]["parameters"][1]["value"] == "visible-value"


def test_replay_drafter_returns_the_prepared_draft(
    matched_candidates: list[MatchedCandidate], drafts: dict[str, Any]
) -> None:
    drafter = ReplayScenarioDrafter(drafts)
    for matched in matched_candidates:
        candidate_id = matched.candidate["candidate_id"]
        draft = drafter.draft(build_draft_request(matched, TARGET_URL))
        assert draft.scenario == drafts[candidate_id]


def test_replay_drafter_returns_copies(matched_candidates: list[MatchedCandidate], drafts: dict[str, Any]) -> None:
    drafter = ReplayScenarioDrafter(drafts)
    request = build_draft_request(matched_candidates[0], TARGET_URL)
    first = drafter.draft(request).scenario
    first["steps"].clear()
    assert drafter.draft(request).scenario["steps"] != []


def test_replay_drafter_fails_for_unknown_candidate(matched_candidates: list[MatchedCandidate]) -> None:
    drafter = ReplayScenarioDrafter({})
    with pytest.raises(DrafterError):
        drafter.draft(build_draft_request(matched_candidates[0], TARGET_URL))


def test_replay_drafter_is_honest_about_not_being_a_model() -> None:
    assert ReplayScenarioDrafter({}).model_info["model_id"] == "replay_file"


def test_replay_drafter_loads_from_file(
    drafts_path: Path, matched_candidates: list[MatchedCandidate], drafts: dict[str, Any]
) -> None:
    drafter = ReplayScenarioDrafter.from_file(drafts_path)
    request = build_draft_request(matched_candidates[0], TARGET_URL)
    assert drafter.draft(request).scenario == drafts[matched_candidates[0].candidate["candidate_id"]]


def test_replay_drafter_rejects_non_object_file(tmp_path: Path) -> None:
    path = tmp_path / "drafts.json"
    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError):
        ReplayScenarioDrafter.from_file(path)
