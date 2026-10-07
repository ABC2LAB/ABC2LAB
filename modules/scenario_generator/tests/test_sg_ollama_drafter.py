"""OllamaScenarioDrafter 테스트. 네트워크 0 — 가짜 transport를 주입한다.

service 레벨 테스트는 후보별로 '유효 초안(drafts fixture)'을 돌려주는 transport로,
Ollama 경로 → scenario_validator 재검증 → scenarios 생성까지 흐르는지 본다.
"""
import json
from typing import Any

import pytest

from modules.scenario_generator.ollama_drafter import (
    OllamaDrafterConfig,
    OllamaScenarioDrafter,
    _default_transport,
)
from modules.scenario_generator.scenario_drafter import DrafterError
from modules.scenario_generator.service import generate_scenarios


def _config() -> OllamaDrafterConfig:
    return OllamaDrafterConfig(model_id="test-model", temperature=0.0, seed=3)


def _response_bytes(generated: Any, *, in_tokens: int | None = None, out_tokens: int | None = None) -> bytes:
    envelope: dict[str, Any] = {"response": json.dumps(generated)}
    if in_tokens is not None:
        envelope["prompt_eval_count"] = in_tokens
    if out_tokens is not None:
        envelope["eval_count"] = out_tokens
    return json.dumps(envelope).encode("utf-8")


def _request() -> dict[str, Any]:
    return {"target_url": "http://localhost", "candidate": {"candidate_id": "c1"},
            "actor_account": {}, "reference_account": None, "source_requests": []}


def test_parses_three_key_draft_and_tokens():
    draft_obj = {"preconditions": [], "steps": [], "assertions": []}
    client = OllamaScenarioDrafter(_config(),
                                   transport=lambda u, p, t: _response_bytes(draft_obj, in_tokens=11, out_tokens=22))
    result = client.draft(_request())
    assert result.scenario == draft_obj
    assert result.input_tokens == 11 and result.output_tokens == 22


def test_prompt_carries_request_as_json_only():
    captured: dict[str, Any] = {}

    def transport(url: str, payload: bytes, timeout: float) -> bytes:
        captured["body"] = json.loads(payload)
        return _response_bytes({"preconditions": [], "steps": [], "assertions": []})

    OllamaScenarioDrafter(_config(), transport=transport).draft(_request())
    body = captured["body"]
    assert body["model"] == "test-model" and body["format"] == "json" and body["stream"] is False
    assert json.loads(body["prompt"])["candidate"]["candidate_id"] == "c1"
    assert body["options"]["seed"] == 3


def test_non_dict_draft_raises():
    client = OllamaScenarioDrafter(_config(), transport=lambda u, p, t: _response_bytes([1, 2, 3]))
    with pytest.raises(DrafterError):
        client.draft(_request())


def test_unparseable_response_raises():
    client = OllamaScenarioDrafter(_config(), transport=lambda u, p, t: b"not json")
    with pytest.raises(DrafterError):
        client.draft(_request())


def test_default_transport_wraps_network_error_as_retryable():
    with pytest.raises(DrafterError) as caught:
        _default_transport("http://127.0.0.1:1/api/generate", b"{}", 0.5)
    assert caught.value.is_retryable is True


def test_model_info_has_required_fields():
    info = OllamaScenarioDrafter(_config()).model_info
    assert set(info) == {"model_id", "model_version", "prompt_version", "temperature", "seed"}
    assert info["model_id"] == "test-model"


def test_empty_model_id_rejected():
    with pytest.raises(DrafterError):
        OllamaScenarioDrafter(OllamaDrafterConfig(model_id=""))


def test_service_flow_with_ollama_valid_drafts(candidates_artifact, crawl_artifact, drafts):
    # 후보 candidate_id에 맞는 유효 초안(drafts fixture)을 돌려주는 transport.
    def transport(url: str, payload: bytes, timeout: float) -> bytes:
        candidate_id = json.loads(json.loads(payload)["prompt"])["candidate"]["candidate_id"]
        return _response_bytes(drafts[candidate_id], in_tokens=5, out_tokens=9)

    drafter = OllamaScenarioDrafter(_config(), transport=transport)
    outcome = generate_scenarios(candidates_artifact, crawl_artifact, drafter)
    assert outcome.scenarios, "유효 초안인데 시나리오가 하나도 안 나옴"
    assert not [e for e in outcome.errors if e["code"] in ("DRAFTER_FAILED", "DRAFT_INVALID")]
    assert outcome.input_tokens is not None and outcome.output_tokens is not None
