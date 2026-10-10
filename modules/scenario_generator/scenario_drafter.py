"""시나리오 초안 생성기(LLM) 규격. Protocol만 만족하면 구현체를 바꿔 끼울 수 있다.

초안은 데이터일 뿐이다. 타입·ID·참조 검증은 scenario_validator가 다시 한다 (CLAUDE.md 절대 규칙 4).
"""

from dataclasses import dataclass
from typing import Any, Protocol

from modules.scenario_generator.candidate_matcher import MatchedCandidate

# Scenario 중 이 세 키만 초안(LLM 몫)이다. 나머지는 프로그램이 원본 후보에서 채운다.
DRAFT_KEYS = frozenset({"preconditions", "steps", "assertions"})
# 인증 헤더·쿠키 변경은 ParameterValue 계약이 허용하지 않으므로 LLM에게 보여 줄 이유가 없다.
HIDDEN_PARAMETER_LOCATIONS = frozenset({"header", "cookie"})


class DrafterError(Exception):
    """초안을 만들지 못했다(LLM 호출 실패·시간 초과 등). 구현체는 자기 예외를 이것으로 감싸서 던진다.

    message에는 비밀값·프롬프트 원문을 넣지 않는다.
    """

    def __init__(self, message: str, is_retryable: bool = False) -> None:
        super().__init__(message)
        self.is_retryable = is_retryable


@dataclass(frozen=True)
class Draft:
    scenario: Any  # 신뢰하지 않는 값: {"preconditions", "steps", "assertions"} 모양이어야 한다
    input_tokens: int | None = None
    output_tokens: int | None = None


class ScenarioDrafter(Protocol):
    model_info: dict[str, Any] | None

    def draft(self, request: dict[str, Any]) -> Draft: ...


def _view_account(account: dict[str, Any]) -> dict[str, Any]:
    return {"account_id": account["account_id"], "role_id": account["role_id"], "session_ref": account["session_ref"]}


def _view_request(request: dict[str, Any]) -> dict[str, Any]:
    # 출력 ParameterValue와 같은 모양으로 보여 준다. 입력 모양({..., is_sensitive})을 베낀 초안이 필수 키
    # binding_ref를 빠뜨려 DRAFT_INVALID가 났다(10/11 실행). is_sensitive는 출력에 없는 키라 보여 주지 않는다.
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
        "account_id": request["account_id"],
        "role_id": request["role_id"],
        "session_ref": request["session_ref"],
        "method": request["method"],
        "url": request["url"],
        "parameters": parameters,
        "response": {
            "status_code": request["response"]["status_code"],
            "content_type": request["response"]["content_type"],
        },
    }


def build_draft_request(matched: MatchedCandidate, target_url: str) -> dict[str, Any]:
    """LLM에 보낼 입력. 헤더·쿠키·응답 본문·근거 파일 경로는 넣지 않는다."""
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
