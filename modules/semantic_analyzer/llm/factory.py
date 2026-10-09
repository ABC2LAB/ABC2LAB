"""설정(configs/default.toml)의 [llm]로 LLM 클라이언트를 고른다. 기본은 fake(네트워크 0).

모델만 바꿀 수 있게 provider 선택을 한 곳에 모은다. 알 수 없는 provider는 조용히 fake로 떨어지지 않고 거절한다.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

from modules.semantic_analyzer.llm.adapter import FakeClient, LlmClient, LlmError
from modules.semantic_analyzer.llm.ollama_client import OllamaClient, OllamaConfig

PROVIDER_FAKE = "fake"
PROVIDER_OLLAMA = "ollama"
# 설정 파일 경로 우선순위: 명시 인자(CLI --config) > 환경변수 > 모듈 기본. collector의 COLLECTOR_CONFIG_PATH와 같은 방식.
ENV_CONFIG_PATH = "SEMANTIC_ANALYZER_CONFIG_PATH"
_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "default.toml"


def _resolve_config_path(config_path: str | Path | None) -> tuple[Path, bool]:
    """(해석된 경로, 명시 지정 여부). 명시=인자 또는 환경변수로 준 경우."""
    if config_path:
        return Path(config_path), True
    env_value = os.environ.get(ENV_CONFIG_PATH, "").strip()
    if env_value:
        return Path(env_value), True
    return _DEFAULT_CONFIG_PATH, False


def load_llm_config(config_path: str | Path | None = None) -> dict:
    """설정의 [llm] 테이블을 돌려준다. 기본 경로가 없으면 provider=fake, 명시 경로가 없으면 오류."""
    path, explicit = _resolve_config_path(config_path)
    if not path.exists():
        if explicit:
            raise LlmError(f"설정 파일을 찾을 수 없음: {path} ({ENV_CONFIG_PATH} 또는 --config 확인)")
        return {"provider": PROVIDER_FAKE}
    with open(path, "rb") as handle:
        data = tomllib.load(handle)
    return data.get("llm", {"provider": PROVIDER_FAKE})


def build_llm_client(config: dict | None = None, config_path: str | Path | None = None) -> LlmClient:
    """[llm] 설정으로 클라이언트를 만든다. 기본 fake. config_path 미지정 시 환경변수/기본 경로를 쓴다."""
    config = load_llm_config(config_path) if config is None else config
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
