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


OUTPUT_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "output" / "test_scenarios.schema.json"


def _output_parameter_keys() -> tuple[set[str], set[str]]:
    """출력 Schema의 ParameterValue 키(전체, 필수). 테스트에 키를 적지 않고 Schema에서 읽는다."""
    definition = json.loads(OUTPUT_SCHEMA_PATH.read_text(encoding="utf-8"))["$defs"]["parameterValue"]
    return set(definition["properties"]), set(definition["required"])


def _viewed_parameters(matched_candidates: list[MatchedCandidate]) -> list[dict[str, Any]]:
    return [
        parameter
        for matched in matched_candidates
        for source in build_draft_request(matched, TARGET_URL)["source_requests"]
        for parameter in source["parameters"]
    ]


def test_viewed_parameter_has_output_schema_shape(matched_candidates: list[MatchedCandidate]) -> None:
    # 입력 뷰 모양이 출력과 다르면 LLM이 입력을 베끼다 필수 키를 빠뜨린다(10/11 실행: binding_ref 누락 DRAFT_INVALID).
    all_keys, required_keys = _output_parameter_keys()
    viewed = _viewed_parameters(matched_candidates)
    assert viewed, "fixture에 parameter가 있는 원본 요청이 있어야 이 테스트가 의미 있다"
    for parameter in viewed:
        assert set(parameter) == all_keys
        assert required_keys <= set(parameter)
        assert parameter["binding_ref"] is None  # 원본 값 그대로라 바인딩 없음


def test_viewed_parameter_hides_sensitivity_flag_but_keeps_value_null(matched_candidates: list[MatchedCandidate]) -> None:
    sensitive_names = {
        parameter["name"]
        for matched in matched_candidates
        for source in matched.source_requests
        for parameter in source["parameters"]
        if parameter["is_sensitive"]
    }
    assert sensitive_names, "fixture에 민감 parameter가 있어야 한다"
    viewed = _viewed_parameters(matched_candidates)
    assert all("is_sensitive" not in parameter for parameter in viewed)
    assert all(parameter["value"] is None for parameter in viewed if parameter["name"] in sensitive_names)


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
