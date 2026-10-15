"""OpenRouter drafter. ScenarioDrafter 프로토콜을 만족한다(모델 비교용 선택지, 기본은 ollama).

의존성 0: stdlib urllib로 OpenAI 호환 Chat Completions API(/chat/completions)를 부른다.
프롬프트·초안 Schema는 Ollama와 같다(SYSTEM_PROMPT·PROMPT_VERSION 공유): 모델만 바꿔 비교하기 위해서다.
외부 서비스로 나가는 입력은 build_draft_request가 만든 dict(헤더·쿠키·본문·근거경로·민감값 이미 제거)뿐이다.
API key는 설정 파일이 아니라 환경변수(OPENROUTER_API_KEY)에서만 받고, 로그·오류 메시지·model_info에 넣지 않는다.
초안은 데이터다 — 반환한 {steps}는 service가 조립한 뒤 scenario_validator가 전부 재검증한다.
호출/파싱 실패는 DrafterError로 던진다(service가 후보별 DRAFTER_FAILED로 기록, 가짜 정상 금지).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from modules.scenario_generator.ollama_drafter import PROMPT_VERSION, SYSTEM_PROMPT
from modules.scenario_generator.scenario_drafter import Draft, DrafterError, build_draft_json_schema

API_KEY_ENV = "OPENROUTER_API_KEY"
_CHAT_COMPLETIONS_PATH = "/chat/completions"
_DRAFT_SCHEMA_NAME = "scenario_draft"
# 레이트 리밋·서버 오류만 다시 시도할 가치가 있다. 401·400 같은 설정 오류는 다시 보내도 같다.
_RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})

# (url, payload, headers, timeout) → 응답 본문. 테스트는 가짜 transport를 넣어 네트워크 없이 돈다.
Transport = Callable[[str, bytes, Mapping[str, str], float], bytes]


@dataclass(frozen=True)
class OpenRouterDrafterConfig:
    model_id: str
    base_url: str = "https://openrouter.ai/api/v1"
    temperature: float | None = 0.0
    seed: int | None = None
    timeout_seconds: float = 60.0
    model_version: str | None = None


def read_api_key() -> str:
    """환경변수의 API key. 없거나 비면 DrafterError(키 값은 메시지에 넣지 않는다)."""
    api_key = os.environ.get(API_KEY_ENV, "").strip()
    if not api_key:
        raise DrafterError(f"openrouter drafter에는 {API_KEY_ENV} 환경변수가 필요하다")
    return api_key


def _default_transport(url: str, payload: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
    request = urllib.request.Request(url, data=payload, method="POST", headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (설정한 OpenRouter 주소)
            return response.read()
    except urllib.error.HTTPError as error:
        # 응답 본문은 요청 일부를 되돌려 줄 수 있어 메시지에 넣지 않는다. 상태 코드만 남긴다.
        raise DrafterError(f"OpenRouter 호출 실패: HTTP {error.code}",
                           is_retryable=error.code in _RETRYABLE_STATUS_CODES) from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise DrafterError(f"OpenRouter 호출 실패: {type(error).__name__}", is_retryable=True) from error


class OpenRouterScenarioDrafter:
    """provider=openrouter일 때 쓰는 시나리오 초안 생성기."""

    def __init__(self, config: OpenRouterDrafterConfig, api_key: str, transport: Transport | None = None) -> None:
        if not config.model_id:
            raise DrafterError("openrouter drafter에는 model_id가 필요하다")
        if not api_key:
            raise DrafterError(f"openrouter drafter에는 {API_KEY_ENV} 환경변수가 필요하다")
        self._config = config
        # config(dataclass)에 두면 repr로 새어 나갈 수 있어 따로 둔다.
        self._api_key = api_key
        self._transport = transport or _default_transport
        self.model_info: dict[str, Any] = {
            "model_id": config.model_id,
            "model_version": config.model_version or config.model_id,
            "prompt_version": PROMPT_VERSION,
            "temperature": config.temperature,
            "seed": config.seed,
        }

    def draft(self, request: dict[str, Any]) -> Draft:
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self._api_key}"}
        raw = self._transport(self._config.base_url.rstrip("/") + _CHAT_COMPLETIONS_PATH,
                              self._build_payload(request), headers, self._config.timeout_seconds)
        scenario, input_tokens, output_tokens = _parse_draft(raw)
        return Draft(scenario=scenario, input_tokens=input_tokens, output_tokens=output_tokens)

    def _build_payload(self, request: dict[str, Any]) -> bytes:
        body: dict[str, Any] = {
            "model": self._config.model_id,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
            ],
            "stream": False,
            # Ollama format과 같은 초안 Schema로 구조화 출력을 요구한다.
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": _DRAFT_SCHEMA_NAME, "strict": True, "schema": build_draft_json_schema()},
            },
            # 구조화 출력을 지원하지 않는 공급자로 라우팅되면 Schema가 조용히 무시된다. 지원하는 곳으로만 보낸다.
            "provider": {"require_parameters": True},
        }
        if self._config.temperature is not None:
            body["temperature"] = self._config.temperature
        if self._config.seed is not None:
            body["seed"] = self._config.seed
        return json.dumps(body).encode("utf-8")


def _parse_draft(raw: bytes) -> tuple[dict[str, Any], int | None, int | None]:
    """응답에서 초안 dict와 토큰 수를 꺼낸다. dict가 아니면 DrafterError(모양·의미는 validator가 재검증)."""
    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise DrafterError(f"OpenRouter 응답 파싱 실패: {type(error).__name__}") from error
    if not isinstance(envelope, dict):
        raise DrafterError("OpenRouter 응답이 객체가 아니다")
    if "error" in envelope:
        # HTTP 200이어도 공급자 오류를 본문 error로 돌려줄 수 있다. 메시지 원문은 넣지 않는다.
        raise DrafterError("OpenRouter 응답에 error가 있다", is_retryable=True)
    try:
        content = envelope["choices"][0]["message"]["content"]
        generated = json.loads(content)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        raise DrafterError(f"OpenRouter 응답 파싱 실패: {type(error).__name__}") from error
    if not isinstance(generated, dict):
        raise DrafterError("초안이 객체가 아니다")
    usage = envelope.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    return generated, _as_int_or_none(usage.get("prompt_tokens")), _as_int_or_none(usage.get("completion_tokens"))


def _as_int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
