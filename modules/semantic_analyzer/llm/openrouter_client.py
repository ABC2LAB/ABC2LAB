"""OpenRouter provider. 같은 LlmClient 프로토콜을 만족한다(모델 비교용 선택지, 폐쇄망 운영은 ollama).

의존성 0: stdlib urllib로 OpenAI 호환 Chat Completions API(/chat/completions)를 부른다.
프롬프트(SYSTEM_PROMPT·build_prompt)·PROMPT_VERSION은 Ollama와 같다: 모델만 바꿔 비교하기 위해서다.
외부 서비스로 나가는 것은 요청 특징(method·path_template·parameter_names)뿐이다 — 비밀값·본문은 features에 없다.
API key는 설정 파일이 아니라 환경변수(OPENROUTER_API_KEY)에서만 받고, 오류 메시지·model_info에 넣지 않는다.
출력은 데이터다: 프로그램이 모양을 다시 검증하고 어긋나면 LlmError(service가 '추론 실패'로 기록).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from modules.semantic_analyzer.llm.adapter import LlmError, RequestFeatures, RequestMeaning
from modules.semantic_analyzer.llm.ollama_client import PROMPT_VERSION, SYSTEM_PROMPT, build_prompt

API_KEY_ENV = "OPENROUTER_API_KEY"
_CHAT_COMPLETIONS_PATH = "/chat/completions"
_MEANING_SCHEMA_NAME = "request_meaning"
# SYSTEM_PROMPT가 요구하는 모양. 구조화 출력으로 디코딩 단계에서 강제하고, 파싱 후에도 다시 검증한다.
_MEANING_SCHEMA = {
    "type": "object",
    "properties": {
        "action_meaning": {"type": "string"},
        "resource_keys": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["action_meaning", "resource_keys"],
    "additionalProperties": False,
}

# 호출 주입점: (url, payload, headers, timeout) → 응답 본문. 테스트는 네트워크 없이 가짜 transport를 넣는다.
Transport = Callable[[str, bytes, Mapping[str, str], float], bytes]


@dataclass(frozen=True)
class OpenRouterConfig:
    model_id: str
    base_url: str = "https://openrouter.ai/api/v1"
    temperature: float | None = 0.0
    seed: int | None = None
    timeout_seconds: float = 30.0
    model_version: str | None = None


def read_api_key() -> str:
    """환경변수의 API key. 없거나 비면 LlmError(키 값은 메시지에 넣지 않는다)."""
    api_key = os.environ.get(API_KEY_ENV, "").strip()
    if not api_key:
        raise LlmError(f"openrouter provider에는 {API_KEY_ENV} 환경변수가 필요하다")
    return api_key


def _default_transport(url: str, payload: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
    request = urllib.request.Request(url, data=payload, method="POST", headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (설정한 OpenRouter 주소)
            return response.read()
    except urllib.error.HTTPError as error:
        # 응답 본문은 요청 일부를 되돌려 줄 수 있어 메시지에 넣지 않는다. 상태 코드만 남긴다.
        raise LlmError(f"OpenRouter 호출 실패: HTTP {error.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise LlmError(f"OpenRouter 호출 실패: {type(error).__name__}") from error


class OpenRouterClient:
    """provider=openrouter일 때 쓰는 의미 추론 클라이언트."""

    def __init__(self, config: OpenRouterConfig, api_key: str, transport: Transport | None = None) -> None:
        if not config.model_id:
            raise LlmError("openrouter provider에는 model_id가 필요하다")
        if not api_key:
            raise LlmError(f"openrouter provider에는 {API_KEY_ENV} 환경변수가 필요하다")
        self._config = config
        # config(dataclass)에 두면 repr로 새어 나갈 수 있어 따로 둔다.
        self._api_key = api_key
        self._transport = transport or _default_transport

    def infer_request_meaning(self, features: RequestFeatures) -> RequestMeaning:
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self._api_key}"}
        raw = self._transport(self._config.base_url.rstrip("/") + _CHAT_COMPLETIONS_PATH,
                              self._build_payload(features), headers, self._config.timeout_seconds)
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
        body: dict[str, object] = {
            "model": self._config.model_id,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_prompt(features)},
            ],
            "stream": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": _MEANING_SCHEMA_NAME, "strict": True, "schema": _MEANING_SCHEMA},
            },
            # 구조화 출력을 지원하지 않는 공급자로 라우팅되면 Schema가 조용히 무시된다. 지원하는 곳으로만 보낸다.
            "provider": {"require_parameters": True},
        }
        if self._config.temperature is not None:
            body["temperature"] = self._config.temperature
        if self._config.seed is not None:
            body["seed"] = self._config.seed
        return json.dumps(body).encode("utf-8")


def _parse_meaning(raw: bytes) -> RequestMeaning:
    """Chat Completions 응답에서 의미를 꺼내 모양을 검증한다. 어긋나면 LlmError."""
    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise LlmError(f"OpenRouter 응답 파싱 실패: {type(error).__name__}") from error
    if not isinstance(envelope, dict):
        raise LlmError("OpenRouter 응답이 객체가 아니다")
    if "error" in envelope:
        # HTTP 200이어도 공급자 오류를 본문 error로 돌려줄 수 있다. 메시지 원문은 넣지 않는다.
        raise LlmError("OpenRouter 응답에 error가 있다")
    try:
        generated = json.loads(envelope["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        raise LlmError(f"OpenRouter 응답 파싱 실패: {type(error).__name__}") from error
    if not isinstance(generated, dict):
        raise LlmError("의미 응답이 객체가 아니다")

    action_meaning = generated.get("action_meaning")
    resource_keys = generated.get("resource_keys", [])
    if not isinstance(action_meaning, str) or not action_meaning:
        raise LlmError("action_meaning이 비어있거나 문자열이 아니다")
    if not isinstance(resource_keys, list) or not all(isinstance(key, str) for key in resource_keys):
        raise LlmError("resource_keys가 문자열 배열이 아니다")
    return RequestMeaning(action_meaning=action_meaning, resource_keys=tuple(resource_keys))
