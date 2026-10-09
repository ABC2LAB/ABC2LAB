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


def _write_toml(path, provider: str, model_id: str = "m") -> None:
    path.write_text(f'[llm]\nprovider = "{provider}"\nmodel_id = "{model_id}"\n', encoding="utf-8")


def test_config_path_env_var_selects_config(tmp_path, monkeypatch):
    # SEMANTIC_ANALYZER_CONFIG_PATH로 가리킨 설정을 쓴다(커밋된 default.toml을 안 고쳐도 됨).
    cfg = tmp_path / "ollama.toml"
    _write_toml(cfg, "ollama")
    monkeypatch.setenv("SEMANTIC_ANALYZER_CONFIG_PATH", str(cfg))
    assert isinstance(build_llm_client(), OllamaClient)


def test_explicit_config_path_overrides_env(tmp_path, monkeypatch):
    env_cfg = tmp_path / "env.toml"
    _write_toml(env_cfg, "ollama")
    monkeypatch.setenv("SEMANTIC_ANALYZER_CONFIG_PATH", str(env_cfg))
    arg_cfg = tmp_path / "arg.toml"
    _write_toml(arg_cfg, "fake")
    assert isinstance(build_llm_client(config_path=str(arg_cfg)), FakeClient)


def test_missing_explicit_config_path_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("SEMANTIC_ANALYZER_CONFIG_PATH", raising=False)
    with pytest.raises(LlmError):
        build_llm_client(config_path=str(tmp_path / "nope.toml"))


def test_no_env_no_arg_uses_default_fake(monkeypatch):
    # 환경변수·인자 없으면 기본 default.toml(fake)로 동작한다.
    monkeypatch.delenv("SEMANTIC_ANALYZER_CONFIG_PATH", raising=False)
    assert isinstance(build_llm_client(), FakeClient)


def test_explicit_config_without_llm_table_raises(tmp_path, monkeypatch):
    # 명시 경로인데 [llm]이 없으면(예: [LLM] 대문자 오타) 조용히 fake로 떨어지지 않고 막는다.
    cfg = tmp_path / "typo_table.toml"
    cfg.write_text('[LLM]\nprovider = "ollama"\nmodel_id = "m"\n', encoding="utf-8")
    monkeypatch.setenv("SEMANTIC_ANALYZER_CONFIG_PATH", str(cfg))
    with pytest.raises(LlmError):
        build_llm_client()


def test_explicit_config_without_provider_key_raises(tmp_path, monkeypatch):
    # provider 키 오타(provder 등)도 조용히 fake가 되지 않게 막는다.
    cfg = tmp_path / "typo_key.toml"
    cfg.write_text('[llm]\nprovder = "ollama"\nmodel_id = "m"\n', encoding="utf-8")
    monkeypatch.setenv("SEMANTIC_ANALYZER_CONFIG_PATH", str(cfg))
    with pytest.raises(LlmError):
        build_llm_client()
