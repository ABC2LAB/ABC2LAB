"""Ollama 로컬 모델 provider. 같은 LlmClient 프로토콜을 만족한다.

의존성 0: 패키지 대신 stdlib urllib로 Ollama HTTP API(/api/generate)를 부른다(폐쇄망 친화).
입력 특징(method·path_template·parameter_names)만 보낸다 — 비밀값·본문은 애초에 features에 없다.
출력은 데이터다: 프로그램이 모양(action_meaning:str, resource_keys:list[str])을 다시 검증하고,
어긋나면 LlmError를 던진다(service가 '추론 실패'로 기록). 실행 여부/판정은 이 모듈이 정하지 않는다.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from modules.semantic_analyzer.llm.adapter import LlmError, RequestFeatures, RequestMeaning

PROMPT_VERSION = "ollama-meaning-v1"
_GENERATE_PATH = "/api/generate"

_SYSTEM = (
    "You label one observed HTTP request. "
    "Return ONLY JSON: {\"action_meaning\": string, \"resource_keys\": [string]}. "
    "action_meaning is a short verb_noun like read_order. "
    "resource_keys are singular resource nouns from the path (no ids, no prose)."
)

# 호출 주입점: 테스트는 네트워크 없이 가짜 transport를 넣는다.
Transport = Callable[[str, bytes, float], bytes]


@dataclass(frozen=True)
class OllamaConfig:
    model_id: str
    base_url: str = "http://localhost:11434"
    temperature: float | None = 0.0
    seed: int | None = None
    timeout_seconds: float = 30.0
    model_version: str | None = None


def _default_transport(url: str, payload: bytes, timeout: float) -> bytes:
    request = urllib.request.Request(url, data=payload, method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (로컬 Ollama)
            return response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        # 메시지에 프롬프트·비밀값을 넣지 않는다.
        raise LlmError(f"Ollama 호출 실패: {type(error).__name__}") from error


class OllamaClient:
    """provider=ollama일 때 쓰는 의미 추론 클라이언트."""

    def __init__(self, config: OllamaConfig, transport: Transport | None = None) -> None:
        if not config.model_id:
            raise LlmError("ollama provider에는 model_id가 필요하다")
        self._config = config
        self._transport = transport or _default_transport

    def infer_request_meaning(self, features: RequestFeatures) -> RequestMeaning:
        payload = self._build_payload(features)
        raw = self._transport(self._config.base_url.rstrip("/") + _GENERATE_PATH, payload,
                              self._config.timeout_seconds)
        return _parse_meaning(raw)

    def model_info(self) -> dict | None:
        return {
            "model_id": self._config.model_id,
            "model_version": self._config.model_version or self._config.model_id,
            "prompt_version": PROMPT_VERSION,
            "temperature": self._config.temperature,
            "seed": self._config.seed,
        }

    def _build_payload(self, features: RequestFeatures) -> bytes:
        prompt = (
            f"method: {features.method}\n"
            f"path_template: {features.path_template}\n"
            f"parameter_names: {list(features.parameter_names)}"
        )
        options: dict[str, object] = {}
        if self._config.temperature is not None:
            options["temperature"] = self._config.temperature
        if self._config.seed is not None:
            options["seed"] = self._config.seed
        body = {
            "model": self._config.model_id,
            "system": _SYSTEM,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": options,
        }
        return json.dumps(body).encode("utf-8")


def _parse_meaning(raw: bytes) -> RequestMeaning:
    """Ollama 응답(JSON)에서 의미를 꺼내 모양을 검증한다. 어긋나면 LlmError."""
    try:
        envelope = json.loads(raw.decode("utf-8"))
        generated = json.loads(envelope["response"])
    except (json.JSONDecodeError, KeyError, UnicodeDecodeError) as error:
        raise LlmError(f"Ollama 응답 파싱 실패: {type(error).__name__}") from error

    action_meaning = generated.get("action_meaning")
    resource_keys = generated.get("resource_keys", [])
    if not isinstance(action_meaning, str) or not action_meaning:
        raise LlmError("action_meaning이 비어있거나 문자열이 아니다")
    if not isinstance(resource_keys, list) or not all(isinstance(key, str) for key in resource_keys):
        raise LlmError("resource_keys가 문자열 배열이 아니다")
    return RequestMeaning(action_meaning=action_meaning, resource_keys=tuple(resource_keys))
