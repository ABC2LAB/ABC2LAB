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

PROMPT_VERSION = "ollama-scenario-v3"
_GENERATE_PATH = "/api/generate"

# 상위 3키뿐 아니라 중첩 레코드(ScenarioStep·RequestPlan·ParameterValue·Binding·Check)의
# 정확한 필드까지 알려줘야 작은 모델이 유효 초안을 낸다. 예시는 모양만 보이고 값은 입력에서 채우게 한다.
_SYSTEM = (
    "You draft ONE reproduction plan for an access-control/business-logic finding, as STRICT JSON.\n"
    "Use ONLY the given candidate, actor_account, and source_requests. "
    "Output ONLY a JSON object with EXACTLY these top-level keys: preconditions, steps, assertions. "
    "Do NOT output candidate_id, expected_basis, scenario_id, or any other top-level key. No prose, no markdown.\n"
    "Each STEP object has EXACTLY: step_id(string, unique), order(integer, contiguous from 0), "
    "source_request_id(one of source_requests[].request_id), account_id(=actor_account.account_id), "
    "role_id(=actor_account.role_id), session_ref(=actor_account.session_ref), "
    "request(object), bindings(array, usually []; may only reference earlier steps).\n"
    "request has EXACTLY: method(=that source request's method), url_template(=that source request's url, "
    "same scheme/host/port, no credentials), parameters(array of {name, location(path|query|body), value, binding_ref}), "
    "body_ref(null unless that source request had one).\n"
    "Each CHECK (in preconditions and assertions) has EXACTLY: check_id(string), "
    "kind(session_valid|response_status|response_json|resource_state|resource_owner|baseline_match), "
    "subject_ref(string, e.g. an account_id or step_id), selector(JSON Pointer string or null), "
    "operator(exists|eq|ne|in|contains), expected(any JSON).\n"
    "preconditions: >=1 (e.g. session_valid for the actor). assertions: >=1; ALL assertions true = violation "
    "reproduced. An HTTP status alone NEVER proves a violation: assertions MUST include at least one check that "
    "looks at response content or resource state (response_json, resource_state, resource_owner, baseline_match), "
    "usually response_json on the actor's step proving the response actually carries the target resource. "
    "response_status/response_json subject_ref MUST be a step_id from steps; session_valid subject_ref is an account_id.\n"
    "Each PARAMETER in request.parameters has EXACTLY: name(string), location(path|query|body), "
    "value(literal JSON or null), binding_ref(string or null; null when value is literal). "
    "NEVER include is_sensitive or any other key in a parameter.\n"
    "CRITICAL: bindings MUST be [] unless a value truly must be extracted from an earlier step's response; "
    "never invent a binding. parameters SHOULD be [] (the url_template already identifies the resource); "
    "only add a parameter when you deliberately change one value. "
    "url_template MUST be one source request's url copied verbatim (full http://host:port/path); "
    "do NOT insert any {placeholder} into it. "
    "Never output angle brackets, ellipses, or the word 'placeholder' — copy the REAL values from the input.\n"
    "Below is the required SHAPE. The literal values shown are only illustrative; replace every value with the "
    "matching real value from actor_account and source_requests:\n"
    '{"preconditions":[{"check_id":"pc1","kind":"session_valid","subject_ref":"account_user_a",'
    '"selector":null,"operator":"exists","expected":true}],'
    '"steps":[{"step_id":"s1","order":0,"source_request_id":"request_1",'
    '"account_id":"account_user_a","role_id":"role_user","session_ref":"session_user_a",'
    '"request":{"method":"GET","url_template":"http://localhost:8001/products/1","parameters":[],"body_ref":null},'
    '"bindings":[]}],'
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
