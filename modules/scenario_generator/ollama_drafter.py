"""Ollama 로컬 모델 drafter. ScenarioDrafter 프로토콜을 만족한다(replay의 실모델 버전).

의존성 0: 패키지 대신 stdlib urllib로 Ollama HTTP API(/api/generate)를 부른다(폐쇄망 친화).
입력은 build_draft_request가 만든 dict(헤더·쿠키·본문·근거경로·민감값 이미 제거)만 쓴다.
초안은 데이터다 — 반환한 {steps}는 service가 조립한 뒤(기준 단계·재현 조건은 프로그램이 추가) scenario_validator가 전부 재검증한다.
호출/파싱 실패는 DrafterError로 던진다(service가 후보별 DRAFTER_FAILED로 기록, 가짜 정상 금지).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from modules.scenario_generator.scenario_drafter import Draft, DrafterError, build_draft_json_schema

PROMPT_VERSION = "ollama-scenario-v8"
_GENERATE_PATH = "/api/generate"

# 식별자(account_id·role_id·session_ref)·method·url_template·body_ref·state_change와 preconditions·assertions·기준 단계는
# 프로그램이 만든다. 초안은 "무엇을 어느 계정으로 실행할지"만 고른다. 작은 모델이 식별자를 지어내거나 판정 기대값을
# 정상 동작(403)으로 적는 일을 막으려고 범위를 좁혔다(v7: 판정 조건도 프로그램 몫, v8: state_change도 프로그램 몫).
_SYSTEM = (
    "You draft the request steps of ONE reproduction plan for an access-control/business-logic finding, as STRICT JSON.\n"
    "Output ONLY a JSON object with EXACTLY one top-level key: steps. "
    "Do NOT output assertions, preconditions, scenario_id, candidate_id, expected_basis, resource_ids, or any other key. "
    "No prose, no markdown.\n"
    "The PROGRAM fills identifiers, the URL and the state-change estimate, adds the owner's baseline request, "
    "and builds the success conditions; you only SELECT steps. "
    "Do NOT output account_id, role_id, session_ref, method, url_template, body_ref, or state_change.\n"
    "Each STEP object has EXACTLY: step_id(string, unique), order(integer, contiguous from 0), "
    "source_request_id(one of source_requests[].request_id), "
    "account_role(\"actor\" or \"reference\"), request(object), "
    "bindings(array, usually []; may only reference earlier steps).\n"
    "account_role=\"actor\" is the account whose authorization is under test; it EXECUTES the resource request. "
    "For IDOR you replay the owner's observed resource request AS the actor — never as the request's original owner. "
    "Use \"reference\" ONLY for a setup step that must run as the owner, and ONLY if reference_account is not null.\n"
    "request has EXACTLY one key: parameters (array). The PROGRAM sends the chosen source_request's url as-is.\n"
    "Each PARAMETER has EXACTLY: name, location(path|query|body), value(literal JSON or null), "
    "binding_ref(string or null; null when value is literal). NEVER include is_sensitive or any other key. "
    "parameters SHOULD be [] (the chosen source_request already identifies the resource); "
    "add one only to deliberately change a value. "
    'source_requests[].parameters are shown in this same shape, e.g. {"name":"page","location":"query","value":"2","binding_ref":null}.\n'
    "To put a value from an EARLIER step's response into the URL path, declare a binding in THIS step's bindings and add "
    '{"name":<a path parameter name of THIS step\'s source_request>,"location":"path","value":null,"binding_ref":<that binding_id>}. '
    "A path parameter MUST use binding_ref, never a literal value. The first step has no earlier step, so it has no path parameter.\n"
    "CRITICAL: bindings MUST be [] unless a value truly must be extracted from an earlier step's response; never invent one.\n"
    "Required SHAPE (the leakable string values below are placeholders that WILL be rejected if copied — "
    "replace source_request_id with a real request_id):\n"
    '{"steps":[{"step_id":"s1","order":0,"source_request_id":"the_chosen_request_id",'
    '"account_role":"actor",'
    '"request":{"parameters":[]},'
    '"bindings":[]}]}'
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
            # "json"은 JSON인지만 강제한다. 초안 Schema를 넘겨 키·중첩·허용값을 디코딩 단계에서 막는다
            # (6차 입력 qwen2.5:1.5b가 bindings·state_change를 request 안에 넣었다).
            "format": build_draft_json_schema(),
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
