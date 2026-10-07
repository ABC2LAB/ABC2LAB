"""OllamaClient·factory 테스트. 네트워크 0 — 가짜 transport를 주입한다."""
import json

import pytest

from modules.semantic_analyzer.llm.adapter import FakeClient, LlmError, RequestFeatures
from modules.semantic_analyzer.llm.factory import build_llm_client
from modules.semantic_analyzer.llm.ollama_client import OllamaClient, OllamaConfig


def _transport_returning(generated: dict):
    """Ollama /api/generate 응답 모양: {"response": "<모델이 낸 JSON 문자열>"}."""
    def transport(url: str, payload: bytes, timeout: float) -> bytes:
        return json.dumps({"response": json.dumps(generated)}).encode("utf-8")
    return transport


def _config() -> OllamaConfig:
    return OllamaConfig(model_id="test-model", temperature=0.0, seed=7)


def test_parses_well_formed_meaning():
    client = OllamaClient(_config(), transport=_transport_returning(
        {"action_meaning": "read_order", "resource_keys": ["order"]}))
    meaning = client.infer_request_meaning(RequestFeatures("GET", "/orders/{id}", ("id",)))
    assert meaning.action_meaning == "read_order"
    assert meaning.resource_keys == ("order",)


def test_payload_carries_no_values_only_features():
    captured = {}

    def transport(url: str, payload: bytes, timeout: float) -> bytes:
        captured["body"] = json.loads(payload)
        return json.dumps({"response": json.dumps(
            {"action_meaning": "read_order", "resource_keys": []})}).encode("utf-8")

    client = OllamaClient(_config(), transport=transport)
    client.infer_request_meaning(RequestFeatures("GET", "/orders/{id}", ("id",)))
    body = captured["body"]
    assert body["model"] == "test-model"
    assert body["stream"] is False and body["format"] == "json"
    assert body["options"]["seed"] == 7


def test_rejects_non_string_action_meaning():
    client = OllamaClient(_config(), transport=_transport_returning(
        {"action_meaning": 123, "resource_keys": []}))
    with pytest.raises(LlmError):
        client.infer_request_meaning(RequestFeatures("GET", "/x", ()))


def test_rejects_non_string_resource_keys():
    client = OllamaClient(_config(), transport=_transport_returning(
        {"action_meaning": "read_x", "resource_keys": [1, 2]}))
    with pytest.raises(LlmError):
        client.infer_request_meaning(RequestFeatures("GET", "/x", ()))


def test_rejects_unparseable_response():
    def transport(url: str, payload: bytes, timeout: float) -> bytes:
        return b"not json"

    client = OllamaClient(_config(), transport=transport)
    with pytest.raises(LlmError):
        client.infer_request_meaning(RequestFeatures("GET", "/x", ()))


def test_default_transport_wraps_network_error_as_llm_error():
    # 기본 transport는 네트워크 오류를 LlmError로 감싼다(닫힌 포트로 즉시 실패).
    from modules.semantic_analyzer.llm.ollama_client import _default_transport
    with pytest.raises(LlmError):
        _default_transport("http://127.0.0.1:1/api/generate", b"{}", 0.5)


def test_model_info_has_required_fields():
    info = OllamaClient(_config()).model_info()
    assert set(info) == {"model_id", "model_version", "prompt_version", "temperature", "seed"}
    assert info["model_id"] == "test-model"
    assert info["model_version"] == "test-model"  # model_version 미지정 → model_id 사용


def test_empty_model_id_rejected():
    with pytest.raises(LlmError):
        OllamaClient(OllamaConfig(model_id=""))


def test_factory_defaults_to_fake():
    assert isinstance(build_llm_client({"provider": "fake"}), FakeClient)
    assert isinstance(build_llm_client({}), FakeClient)


def test_factory_builds_ollama():
    client = build_llm_client({"provider": "ollama", "model_id": "m"})
    assert isinstance(client, OllamaClient)


def test_factory_rejects_unknown_provider():
    with pytest.raises(LlmError):
        build_llm_client({"provider": "openai"})
