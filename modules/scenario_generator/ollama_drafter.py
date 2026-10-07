"""Ollama 로컬 모델 drafter. ScenarioDrafter 프로토콜을 만족한다(replay의 실모델 버전).

의존성 0: 패키지 대신 stdlib urllib로 Ollama HTTP API(/api/generate)를 부른다(폐쇄망 친화).
입력은 build_draft_request가 만든 dict(헤더·쿠키·본문·근거경로·민감값 이미 제거)만 쓴다.
초안은 데이터다 — 반환한 {preconditions, steps, assertions}는 scenario_validator가 전부 재검증한다.
호출/파싱 실패는 DrafterError로 던진다(service가 후보별 DRAFTER_FAILED로 기록, 가짜 정상 금지).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from modules.scenario_generator.scenario_drafter import Draft, DrafterError

PROMPT_VERSION = "ollama-scenario-v1"
_GENERATE_PATH = "/api/generate"

_SYSTEM = (
    "You draft a reproduction plan for ONE access-control/business-logic finding. "
    "Use only the given candidate and source_requests. "
    "Return ONLY JSON with exactly these keys: "
    "{\"preconditions\": [Check], \"steps\": [ScenarioStep], \"assertions\": [Check]}. "
    "Do not include candidate_id, expected_basis, or scenario_id (the program fills them). "
    "steps.order is 0..N-1; bindings only reference earlier steps; "
    "url_template stays within the given target origin; no prose."
)

Transport = Callable[[str, bytes, float], bytes]


@dataclass(frozen=True)
class OllamaDrafterConfig:
    model_id: str
    base_url: str = "http://localhost:11434"
    temperature: float | None = 0.0
    seed: int | None = None
    timeout_seconds: float = 60.0
    model_version: str | None = None


def _default_transport(url: str, payload: bytes, timeout: float) -> bytes:
    request = urllib.request.Request(url, data=payload, method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (로컬 Ollama)
            return response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        # 메시지에 프롬프트·비밀값을 넣지 않는다. 전송 실패는 재시도 가능으로 표시.
        raise DrafterError(f"Ollama 호출 실패: {type(error).__name__}", is_retryable=True) from error


class OllamaScenarioDrafter:
    """provider=ollama일 때 쓰는 시나리오 초안 생성기."""

    def __init__(self, config: OllamaDrafterConfig, transport: Transport | None = None) -> None:
        if not config.model_id:
            raise DrafterError("ollama drafter에는 model_id가 필요하다")
        self._config = config
        self._transport = transport or _default_transport
        self.model_info: dict[str, Any] = {
            "model_id": config.model_id,
            "model_version": config.model_version or config.model_id,
            "prompt_version": PROMPT_VERSION,
            "temperature": config.temperature,
            "seed": config.seed,
        }

    def draft(self, request: dict[str, Any]) -> Draft:
        payload = self._build_payload(request)
        raw = self._transport(self._config.base_url.rstrip("/") + _GENERATE_PATH, payload,
                              self._config.timeout_seconds)
        scenario, input_tokens, output_tokens = _parse_draft(raw)
        return Draft(scenario=scenario, input_tokens=input_tokens, output_tokens=output_tokens)

    def _build_payload(self, request: dict[str, Any]) -> bytes:
        options: dict[str, Any] = {}
        if self._config.temperature is not None:
            options["temperature"] = self._config.temperature
        if self._config.seed is not None:
            options["seed"] = self._config.seed
        body = {
            "model": self._config.model_id,
            "system": _SYSTEM,
            "prompt": json.dumps(request, ensure_ascii=False),
            "stream": False,
            "format": "json",
            "options": options,
        }
        return json.dumps(body).encode("utf-8")


def _parse_draft(raw: bytes) -> tuple[dict[str, Any], int | None, int | None]:
    """응답에서 초안 dict와 토큰 수를 꺼낸다. dict가 아니면 DrafterError(모양·의미는 validator가 재검증)."""
    try:
        envelope = json.loads(raw.decode("utf-8"))
        generated = json.loads(envelope["response"])
    except (json.JSONDecodeError, KeyError, UnicodeDecodeError) as error:
        raise DrafterError(f"Ollama 응답 파싱 실패: {type(error).__name__}") from error
    if not isinstance(generated, dict):
        raise DrafterError("초안이 객체가 아니다")
    # 키 집합·내용 검증은 service._compose_scenario와 scenario_validator가 맡는다(여기선 전달만).
    input_tokens = envelope.get("prompt_eval_count")
    output_tokens = envelope.get("eval_count")
    return generated, _as_int_or_none(input_tokens), _as_int_or_none(output_tokens)


def _as_int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
