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

PROMPT_VERSION = "ollama-scenario-v5"
_GENERATE_PATH = "/api/generate"

# 식별자(account_id·role_id·session_ref·method·body_ref)와 preconditions는 프로그램이 원본에서 채운다.
# 초안은 "무엇을 어느 계정으로 실행할지"만 고른다. 작은 모델이 식별자를 지어내거나 예시를 복사하지 못하게 범위를 좁혔다.
_SYSTEM = (
    "You draft ONE reproduction plan for an access-control/business-logic finding, as STRICT JSON.\n"
    "Output ONLY a JSON object with EXACTLY these top-level keys: steps, assertions. "
    "Do NOT output preconditions, scenario_id, candidate_id, expected_basis, resource_ids, or any other key. "
    "No prose, no markdown.\n"
    "The PROGRAM fills identifiers; you only SELECT. Do NOT output account_id, role_id, session_ref, method, or body_ref.\n"
    "Each STEP object has EXACTLY: step_id(string, unique), order(integer, contiguous from 0), "
    "source_request_id(one of source_requests[].request_id), "
    "account_role(\"actor\" or \"reference\"), request(object), "
    "bindings(array, usually []; may only reference earlier steps), "
    "state_change(none|possible|expected|unknown; your estimate of whether this request changes server state).\n"
    "account_role=\"actor\" is the account whose authorization is under test; it EXECUTES the resource request. "
    "For IDOR you replay the owner's observed resource request AS the actor — never as the request's original owner. "
    "Use \"reference\" ONLY for a setup step that must run as the owner, and ONLY if reference_account is not null.\n"
    "request has EXACTLY: url_template, parameters(array of {name, location(path|query|body), value, binding_ref}). "
    "url_template MUST equal the chosen source_request's url, copied EXACTLY (full http://host:port/path). "
    "Insert a {binding_id} into it ONLY where a value must come from an earlier step's response. "
    "Never invent a URL, never output angle brackets, ellipses, or the word 'placeholder'.\n"
    "Each PARAMETER has EXACTLY: name, location(path|query|body), value(literal JSON or null), "
    "binding_ref(string or null; null when value is literal). NEVER include is_sensitive or any other key. "
    "parameters SHOULD be [] (url_template already identifies the resource); add one only to deliberately change a value. "
    'source_requests[].parameters are shown in this same shape, e.g. {"name":"page","location":"query","value":"2","binding_ref":null}.\n'
    "Each CHECK in assertions has EXACTLY: check_id(string; do NOT start it with 'precondition_'), "
    "kind(session_valid|response_status|response_json|resource_state|resource_owner|baseline_match), "
    "subject_ref(string), selector(JSON Pointer string or null), operator(exists|eq|ne|in|contains), expected(any JSON).\n"
    "ALL assertions true = violation reproduced; a valid format is NOT a confirmed vulnerability. "
    "An HTTP status alone NEVER proves a violation. On the ACTOR step (subject_ref = that step's step_id): "
    "include a response_status check reflecting the candidate's expected_behavior (not a status picked to pass); "
    "AND, only when that source_request's response.content_type is JSON, also a response_json check whose selector "
    "is a JSON Pointer to the field that proves the TARGET resource is actually returned (pick the real field; do not default to /id). "
    "If the response is NOT JSON, use response_status ONLY — do NOT add response_json.\n"
    "CRITICAL: bindings MUST be [] unless a value truly must be extracted from an earlier step's response; never invent one.\n"
    "Required SHAPE (the leakable string values below are placeholders that WILL be rejected if copied — "
    "replace source_request_id with a real request_id and url_template with that request's real url):\n"
    '{"steps":[{"step_id":"s1","order":0,"source_request_id":"the_chosen_request_id",'
    '"account_role":"actor",'
    '"request":{"url_template":"the_chosen_source_request_url","parameters":[]},'
    '"bindings":[],"state_change":"none"}],'
    '"assertions":[{"check_id":"a1","kind":"response_status","subject_ref":"s1","selector":null,'
    '"operator":"eq","expected":200},'
    '{"check_id":"a2","kind":"response_json","subject_ref":"s1","selector":"/id",'
    '"operator":"exists","expected":true}]}'
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
