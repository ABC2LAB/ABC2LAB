"""OpenRouterClient·factory 테스트. 네트워크 0 — 가짜 transport를 주입한다.

API key는 테스트용 표식 문자열만 쓰고, 그 값이 요청 헤더 밖(오류 메시지·model_info)으로 새지 않는지 본다.
"""
import json

import pytest

from modules.semantic_analyzer.llm import openrouter_client
from modules.semantic_analyzer.llm.adapter import LlmError, RequestFeatures
from modules.semantic_analyzer.llm.factory import build_llm_client
from modules.semantic_analyzer.llm.ollama_client import PROMPT_VERSION, SYSTEM_PROMPT, build_prompt
from modules.semantic_analyzer.llm.openrouter_client import (
    API_KEY_ENV,
    OpenRouterClient,
    OpenRouterConfig,
    _default_transport,
)

FAKE_API_KEY = "test-key-marker"
FEATURES = RequestFeatures("GET", "/orders/{id}", ("id",))


def _response_bytes(generated) -> bytes:
    return json.dumps({"choices": [{"message": {"role": "assistant", "content": json.dumps(generated)}}]}).encode()


def _transport_returning(generated):
    def transport(url, payload, headers, timeout):
        return _response_bytes(generated)
    return transport


def _client(transport) -> OpenRouterClient:
    return OpenRouterClient(OpenRouterConfig(model_id="vendor/test-model", seed=7), FAKE_API_KEY, transport=transport)


def test_parses_well_formed_meaning():
    meaning = _client(_transport_returning({"action_meaning": "read_order", "resource_keys": ["order"]})) \
        .infer_request_meaning(FEATURES)
    assert meaning.action_meaning == "read_order"
    assert meaning.resource_keys == ("order",)


def test_request_uses_chat_completions_with_shared_prompt():
    captured = {}

    def transport(url, payload, headers, timeout):
        captured.update(url=url, body=json.loads(payload), headers=headers, timeout=timeout)
        return _response_bytes({"action_meaning": "read_order", "resource_keys": []})

    _client(transport).infer_request_meaning(FEATURES)

    body = captured["body"]
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["timeout"] == 30.0
    assert captured["headers"]["Authorization"] == f"Bearer {FAKE_API_KEY}"
    assert body["model"] == "vendor/test-model" and body["stream"] is False
    # Ollama와 같은 프롬프트여야 모델만 바꿔 비교할 수 있다.
    assert body["messages"] == [{"role": "system", "content": SYSTEM_PROMPT},
                                {"role": "user", "content": build_prompt(FEATURES)}]
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["provider"] == {"require_parameters": True}
    assert body["temperature"] == 0.0 and body["seed"] == 7


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b"not json", id="not_json"),
        pytest.param(b"[]", id="not_object"),
        pytest.param(b'{"choices": []}', id="no_choices"),
        pytest.param(b'{"choices": [{"message": {"content": null}}]}', id="null_content"),
        pytest.param(_response_bytes(["read_order"]), id="meaning_not_object"),
        pytest.param(_response_bytes({"action_meaning": 123, "resource_keys": []}), id="action_not_string"),
        pytest.param(_response_bytes({"action_meaning": "", "resource_keys": []}), id="action_empty"),
        pytest.param(_response_bytes({"action_meaning": "read_x", "resource_keys": [1]}), id="keys_not_strings"),
    ],
)
def test_rejects_malformed_response(raw):
    with pytest.raises(LlmError):
        _client(lambda u, p, h, t: raw).infer_request_meaning(FEATURES)


def test_error_body_with_200_hides_message():
    raw = json.dumps({"error": {"code": 502, "message": "upstream said something"}}).encode()
    with pytest.raises(LlmError) as caught:
        _client(lambda u, p, h, t: raw).infer_request_meaning(FEATURES)
    assert "upstream" not in str(caught.value)


def test_default_transport_wraps_network_error_without_key():
    with pytest.raises(LlmError) as caught:
        _default_transport("http://127.0.0.1:1/chat/completions", b"{}", {"Authorization": f"Bearer {FAKE_API_KEY}"}, 0.5)
    assert FAKE_API_KEY not in str(caught.value)


def test_model_info_has_required_fields_and_no_key():
    info = _client(_transport_returning({})).model_info()
    assert set(info) == {"model_id", "model_version", "prompt_version", "temperature", "seed"}
    assert info["model_version"] == "vendor/test-model" and info["prompt_version"] == PROMPT_VERSION
    assert FAKE_API_KEY not in json.dumps(info)


def test_empty_model_id_rejected():
    with pytest.raises(LlmError):
        OpenRouterClient(OpenRouterConfig(model_id=""), FAKE_API_KEY)


def test_empty_api_key_rejected():
    with pytest.raises(LlmError, match=API_KEY_ENV):
        OpenRouterClient(OpenRouterConfig(model_id="m"), "")


def test_factory_builds_openrouter_with_env_key(monkeypatch):
    calls = []

    def recorder(url, payload, headers, timeout):
        calls.append((url, json.loads(payload), dict(headers), timeout))
        return _response_bytes({"action_meaning": "read_order", "resource_keys": []})

    monkeypatch.setattr(openrouter_client, "_default_transport", recorder)
    monkeypatch.setenv(API_KEY_ENV, FAKE_API_KEY)
    client = build_llm_client({"provider": "openrouter", "model_id": "vendor/probe",
                               "base_url": "http://127.0.0.1:9/api/v1/", "timeout_seconds": 300.0})
    assert isinstance(client, OpenRouterClient)

    client.infer_request_meaning(FEATURES)

    [(url, body, headers, timeout)] = calls
    assert url == "http://127.0.0.1:9/api/v1/chat/completions"
    assert timeout == 300.0 and body["model"] == "vendor/probe"
    assert headers["Authorization"] == f"Bearer {FAKE_API_KEY}"


def test_factory_openrouter_without_env_key_raises(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    with pytest.raises(LlmError, match=API_KEY_ENV):
        build_llm_client({"provider": "openrouter", "model_id": "m"})


def test_local_web_openrouter_template_is_accepted(monkeypatch, tmp_path):
    # local_web의 OpenRouter 설정 초안 키(provider·model_id·base_url·temperature·timeout_seconds)를 그대로 받는다.
    monkeypatch.setenv(API_KEY_ENV, FAKE_API_KEY)
    config_path = tmp_path / "openrouter.toml"
    config_path.write_text('[llm]\nprovider = "openrouter"\nmodel_id = "vendor/x"\n'
                           'base_url = "https://openrouter.ai/api/v1"\ntemperature = 0.0\ntimeout_seconds = 300.0\n',
                           encoding="utf-8")
    assert isinstance(build_llm_client(config_path=config_path), OpenRouterClient)
