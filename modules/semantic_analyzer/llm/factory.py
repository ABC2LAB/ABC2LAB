"""설정(configs/default.toml)의 [llm]로 LLM 클라이언트를 고른다. 기본은 fake(네트워크 0).

모델만 바꿀 수 있게 provider 선택을 한 곳에 모은다. 알 수 없는 provider는 조용히 fake로 떨어지지 않고 거절한다.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from modules.semantic_analyzer.llm.adapter import FakeClient, LlmClient, LlmError
from modules.semantic_analyzer.llm.ollama_client import OllamaClient, OllamaConfig

PROVIDER_FAKE = "fake"
PROVIDER_OLLAMA = "ollama"
_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "default.toml"


def load_llm_config(config_path: Path | None = None) -> dict:
    """default.toml의 [llm] 테이블을 돌려준다. 없으면 provider=fake로 본다."""
    path = config_path or _DEFAULT_CONFIG_PATH
    if not path.exists():
        return {"provider": PROVIDER_FAKE}
    with open(path, "rb") as handle:
        data = tomllib.load(handle)
    return data.get("llm", {"provider": PROVIDER_FAKE})


def build_llm_client(config: dict | None = None) -> LlmClient:
    """[llm] 설정으로 클라이언트를 만든다. 기본 fake."""
    config = load_llm_config() if config is None else config
    provider = config.get("provider", PROVIDER_FAKE)
    if provider == PROVIDER_FAKE:
        return FakeClient()
    if provider == PROVIDER_OLLAMA:
        return OllamaClient(OllamaConfig(
            model_id=config.get("model_id", ""),
            base_url=config.get("base_url", "http://localhost:11434"),
            temperature=config.get("temperature", 0.0),
            seed=config.get("seed"),
            timeout_seconds=config.get("timeout_seconds", 30.0),
            model_version=config.get("model_version"),
        ))
    raise LlmError(f"알 수 없는 llm provider: {provider!r} (지원: {PROVIDER_FAKE}, {PROVIDER_OLLAMA})")
