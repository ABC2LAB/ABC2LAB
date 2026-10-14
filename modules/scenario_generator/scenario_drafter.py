"""시나리오 초안 생성기(LLM) 규격. Protocol만 만족하면 구현체를 바꿔 끼울 수 있다.

초안은 데이터일 뿐이다. 타입·ID·참조 검증은 scenario_validator가 다시 한다 (CLAUDE.md 절대 규칙 4).
"""

import copy
import functools
import json
from dataclasses import dataclass
from typing import Any, Protocol

from modules.scenario_generator.candidate_matcher import MatchedCandidate
from modules.scenario_generator.scenario_validator import OUTPUT_SCHEMA_PATH

# 초안(LLM 몫)의 최상위 키. 초안은 steps만 고른다.
# preconditions(세션 사전조건)·assertions(재현 조건)·기준 단계는 프로그램이 만든다(reproduction_checks).
# 작은 모델이 판정 조건 기대값을 정상 동작(403)으로 적어 판정이 뒤집혔다(6차 입력 5건 중 4건).
# steps의 식별자(account_id·role_id·session_ref·method·body_ref)도 프로그램이 원본에서 채운다.
# 순서는 구조화 출력에서 모델이 키를 쓰는 순서가 된다.
DRAFT_FIELDS = ("steps",)
DRAFT_KEYS = frozenset(DRAFT_FIELDS)
# 초안 step이 가져야 하는 키(축소형). 식별자 대신 선택(account_role)만 담는다.
DRAFT_STEP_FIELDS = ("step_id", "order", "source_request_id", "account_role", "request", "bindings", "state_change")
DRAFT_STEP_KEYS = frozenset(DRAFT_STEP_FIELDS)
# 초안 step.request의 키. url_template·method·body_ref는 선택한 원본 요청에서 프로그램이 채운다.
# 원본 URL을 LLM에게 베끼게 하면 작은 모델이 path 파라미터 이름을 {…}로 넣은 템플릿을 지어냈다(10/12 6차 입력 실행).
DRAFT_REQUEST_FIELDS = ("parameters",)
DRAFT_REQUEST_KEYS = frozenset(DRAFT_REQUEST_FIELDS)
# account_role: 실행 단계가 어느 계정으로 도는지. 실제 account_id·role_id·session_ref는 프로그램이 채운다.
ACCOUNT_ROLE_ACTOR = "actor"
ACCOUNT_ROLE_REFERENCE = "reference"
ACCOUNT_ROLES = frozenset({ACCOUNT_ROLE_ACTOR, ACCOUNT_ROLE_REFERENCE})
# 인증 헤더·쿠키 변경은 ParameterValue 계약이 허용하지 않으므로 LLM에게 보여 줄 이유가 없다.
HIDDEN_PARAMETER_LOCATIONS = frozenset({"header", "cookie"})
# 구조화 출력 문법에 넣는 JSON 값(parameter value). 출력 jsonValue는 재귀라 문법이 커지므로
# 스칼라와 스칼라 배열까지만 허용한다. 출력 계약 검증은 scenario_validator가 출력 Schema로 따로 한다.
_SCALAR_TYPES = ["string", "number", "boolean", "null"]
_DRAFT_VALUE_SCHEMA = {"type": [*_SCALAR_TYPES, "array"], "items": {"type": _SCALAR_TYPES}}
_NON_EMPTY_STRING_SCHEMA = {"type": "string", "minLength": 1}
_NULLABLE_STRING_SCHEMA = {"type": ["string", "null"]}


class DrafterError(Exception):
    """초안을 만들지 못했다(LLM 호출 실패·시간 초과 등). 구현체는 자기 예외를 이것으로 감싸서 던진다.

    message에는 비밀값·프롬프트 원문을 넣지 않는다.
    """

    def __init__(self, message: str, is_retryable: bool = False) -> None:
        super().__init__(message)
        self.is_retryable = is_retryable


@dataclass(frozen=True)
class Draft:
    scenario: Any  # 신뢰하지 않는 값: {"steps"} 모양이어야 한다
    input_tokens: int | None = None
    output_tokens: int | None = None


class ScenarioDrafter(Protocol):
    model_info: dict[str, Any] | None

    def draft(self, request: dict[str, Any]) -> Draft: ...


def _view_account(account: dict[str, Any]) -> dict[str, Any]:
    # 역할만 보여 준다. account_id·session_ref는 프로그램이 채우므로 초안이 복사할 이유가 없다.
    return {"role_id": account["role_id"]}


def _view_request(request: dict[str, Any]) -> dict[str, Any]:
    # 출력 ParameterValue와 같은 모양으로 보여 준다. 입력 모양({..., is_sensitive})을 베낀 초안이 필수 키
    # binding_ref를 빠뜨려 DRAFT_INVALID가 났다(10/11 실행). is_sensitive는 출력에 없는 키라 보여 주지 않는다.
    # 원본 요청의 account_id·role_id·session_ref는 보여 주지 않는다: IDOR는 실행 계정(actor)으로 돌려야 하는데
    # 원본 요청 계정을 복사하면 안 되기 때문이다(실행 계정은 account_role로만 고르고 값은 프로그램이 채운다).
    parameters = [
        {
            "name": parameter["name"],
            "location": parameter["location"],
            # 스키마가 이미 보장하지만, 비밀값이 LLM으로 새는 일은 한 겹 더 막는다.
            "value": None if parameter["is_sensitive"] else parameter["value"],
            "binding_ref": None,
        }
        for parameter in request["parameters"]
        if parameter["location"] not in HIDDEN_PARAMETER_LOCATIONS
    ]
    return {
        "request_id": request["request_id"],
        "method": request["method"],
        "url": request["url"],
        "parameters": parameters,
        "response": {
            "status_code": request["response"]["status_code"],
            "content_type": request["response"]["content_type"],
        },
    }


def build_draft_request(matched: MatchedCandidate, target_url: str) -> dict[str, Any]:
    """LLM에 보낼 입력. 헤더·쿠키·응답 본문·근거 파일 경로·계정 식별자는 넣지 않는다.

    초안은 source_request와 account_role(actor|reference)만 고르고, 실제 식별자는 프로그램이 채운다.
    """
    candidate = matched.candidate
    return {
        "target_url": target_url,
        "candidate": {
            "candidate_id": candidate["candidate_id"],
            "category": candidate["category"],
            "vulnerability_type": candidate["vulnerability_type"],
            "hypothesis": candidate["hypothesis"],
            "expected_behavior": candidate["expected_behavior"],
            "expected_basis": candidate["expected_basis"],
            "resource_ids": candidate["resource_ids"],
            "workflow_id": candidate["workflow_id"],
        },
        "actor_account": _view_account(matched.actor_account),
        "reference_account": _view_account(matched.reference_account) if matched.reference_account else None,
        "source_requests": [_view_request(request) for request in matched.source_requests],
    }


def _object_schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


@functools.cache
def _load_output_defs() -> dict[str, Any]:
    return json.loads(OUTPUT_SCHEMA_PATH.read_text(encoding="utf-8"))["$defs"]


@functools.cache
def _build_draft_json_schema() -> dict[str, Any]:
    defs = _load_output_defs()
    parameter = _object_schema({
        "name": _NON_EMPTY_STRING_SCHEMA,
        "location": {"enum": defs["parameterValue"]["properties"]["location"]["enum"]},
        "value": _DRAFT_VALUE_SCHEMA,
        "binding_ref": _NULLABLE_STRING_SCHEMA,
    })
    binding = _object_schema({
        "binding_id": _NON_EMPTY_STRING_SCHEMA,
        "source_step_id": _NON_EMPTY_STRING_SCHEMA,
        "source_part": {"enum": defs["binding"]["properties"]["source_part"]["enum"]},
        "selector": _NON_EMPTY_STRING_SCHEMA,
    })
    request_properties = {"parameters": {"type": "array", "items": parameter}}
    step_properties = {
        "step_id": _NON_EMPTY_STRING_SCHEMA,
        "order": {"type": "integer", "minimum": 0},
        "source_request_id": _NON_EMPTY_STRING_SCHEMA,
        "account_role": {"enum": [ACCOUNT_ROLE_ACTOR, ACCOUNT_ROLE_REFERENCE]},
        "request": _object_schema({field: request_properties[field] for field in DRAFT_REQUEST_FIELDS}),
        "bindings": {"type": "array", "items": binding},
        "state_change": {"enum": defs["scenarioStep"]["properties"]["state_change"]["enum"]},
    }
    step = _object_schema({field: step_properties[field] for field in DRAFT_STEP_FIELDS})
    draft_properties = {"steps": {"type": "array", "minItems": 1, "items": step}}
    return _object_schema({field: draft_properties[field] for field in DRAFT_FIELDS})


def build_draft_json_schema() -> dict[str, Any]:
    """초안 JSON Schema(Ollama 구조화 출력 format용). 키·중첩·허용값만 디코딩 단계에서 강제한다.

    키는 DRAFT_* 순서, 허용값은 출력 Schema(test_scenarios)에서 가져와 계약과 어긋나지 않게 한다.
    ID 존재·순서·참조 같은 의미는 강제하지 못하므로 service 조립과 scenario_validator가 계속 검증한다.
    """
    return copy.deepcopy(_build_draft_json_schema())
