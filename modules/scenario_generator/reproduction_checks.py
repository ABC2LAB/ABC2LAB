"""재현 조건(assertions)과 기준 단계를 프로그램이 만든다. LLM 초안은 steps만 고른다.

후보의 기대 동작은 "기준 계정(소유자·상위 역할)이 아닌 실행 계정은 거부돼야 한다"이고, 그 부정이 재현 조건이다:
실행 계정이 기준 계정과 같은 응답을 받는다. 그래서 실행 계정이 근거 요청을 보내는 재현 단계 바로 앞에
기준 계정이 같은 요청을 보내는 기준 단계를 넣고, 재현 단계 응답을 그 기준 응답과 비교하는 조건을 만든다.
- response_status eq 근거 요청에서 관찰한 상태 코드(2xx)
- baseline_match eq 기준 단계
- 근거 응답이 JSON이면 resource_owner eq 기준 단계(같은 식별자 = 같은 자원)
판정 규약은 verifier README "Check 평가와 result 분류"(m5·m7 "Check 규약")를 따른다. 텍스트(HTML) 응답 비교는
control 단계(실행 계정 자기 자원)가 들어오기 전까지 verifier가 기록만 한다.
"""

import copy
from dataclasses import dataclass
from typing import Any

from modules.scenario_generator.candidate_matcher import CrawlIndex, MatchedCandidate
from modules.scenario_generator.scenario_validator import PLACEHOLDER_PATTERN, find_reproduction_step, is_json_response

# 기준 단계 step_id·binding_id 접두어. 프로그램이 재현 단계 것을 복사해 만든다(LLM이 만들지 않는다).
BASELINE_PREFIX = "baseline_"
# 프로그램이 만드는 판정 조건 check_id. 세션 사전조건(precondition_session_*)과 겹치지 않는다.
STATUS_CHECK_ID = "assertion_status"
BASELINE_CHECK_ID = "assertion_baseline"
OWNER_CHECK_ID = "assertion_owner"
# 기준 단계로 다시 보내도 상태가 바뀌지 않는 단계만 기준 응답을 만든다.
STATE_CHANGE_NONE = "none"
# 정상 접근으로 관찰한 응답 상태 범위. 그 밖이면 "실행 계정도 이 응답을 받으면 재현"이라는 조건이 성립하지 않는다.
SUCCESS_STATUS_MIN = 200
SUCCESS_STATUS_MAX = 299


@dataclass(frozen=True)
class Reproduction:
    steps: list[dict[str, Any]]
    assertions: list[dict[str, Any]]


@dataclass(frozen=True)
class ReproductionProblem:
    message: str
    # True면 초안 탓이 아니라 이 후보로는 기준 응답을 만들 수 없다(ASSERTION_BASELINE_UNAVAILABLE).
    # False면 초안이 재현 단계를 고르지 않았거나 ID가 겹친다(DRAFT_INVALID).
    is_baseline_unavailable: bool


def find_baseline_blocker(matched: MatchedCandidate) -> str | None:
    """초안 없이도 알 수 있는, 기준 응답을 만들 수 없는 사유. LLM을 부르기 전에 본다."""
    if matched.reference_account is None:
        return "후보에 기준 계정이 없어 기준 응답을 만들 수 없다"
    return None


def build_reproduction(
    matched: MatchedCandidate, composed_steps: list[dict[str, Any]], index: CrawlIndex
) -> Reproduction | ReproductionProblem:
    """조립한 초안 steps에 기준 단계를 넣고 판정 조건을 만든다. composed_steps는 바꾸지 않는다."""
    blocker = find_baseline_blocker(matched)
    if blocker is not None:
        return ReproductionProblem(blocker, is_baseline_unavailable=True)
    target = find_reproduction_step(composed_steps, matched)
    if target is None:
        return ReproductionProblem("실행 계정이 후보 근거 요청을 재현하는 단계가 없다", is_baseline_unavailable=False)
    if target["state_change"] != STATE_CHANGE_NONE:
        return ReproductionProblem(
            f"재현 단계 {target['step_id']}가 상태 변경 가능(state_change≠none)으로 추정돼 같은 요청으로 기준 응답을 만들 수 없다",
            is_baseline_unavailable=True,
        )
    source = index.requests_by_id[target["source_request_id"]]
    observed_status = source["response"]["status_code"]
    if not SUCCESS_STATUS_MIN <= observed_status <= SUCCESS_STATUS_MAX:
        return ReproductionProblem(
            f"재현 단계 {target['step_id']}의 근거 요청 응답이 2xx가 아니라 재현 조건을 만들 수 없다",
            is_baseline_unavailable=True,
        )
    baseline = _build_baseline_step(target, matched.reference_account)
    collision = _find_id_collision(composed_steps, baseline)
    if collision is not None:
        return ReproductionProblem(collision, is_baseline_unavailable=False)
    steps = _insert_before(composed_steps, target, baseline)
    assertions = _build_assertions(target["step_id"], baseline["step_id"], observed_status, is_json_response(source))
    return Reproduction(steps, assertions)


def _build_baseline_step(target: dict[str, Any], reference_account: dict[str, Any]) -> dict[str, Any]:
    """재현 단계를 기준 계정으로 복사한다. 바인딩 ID는 접두어로 바꿔 시나리오 안에서 유일하게 둔다."""
    baseline = copy.deepcopy(target)
    baseline["step_id"] = f"{BASELINE_PREFIX}{target['step_id']}"
    baseline["account_id"] = reference_account["account_id"]
    baseline["role_id"] = reference_account["role_id"]
    baseline["session_ref"] = reference_account["session_ref"]
    renamed = {binding["binding_id"]: f"{BASELINE_PREFIX}{binding['binding_id']}" for binding in baseline["bindings"]}
    for binding in baseline["bindings"]:
        binding["binding_id"] = renamed[binding["binding_id"]]
    request = baseline["request"]
    request["url_template"] = PLACEHOLDER_PATTERN.sub(
        lambda match: "{" + renamed.get(match.group(1), match.group(1)) + "}", request["url_template"]
    )
    for parameter in request["parameters"]:
        if parameter["binding_ref"] in renamed:
            parameter["binding_ref"] = renamed[parameter["binding_ref"]]
    return baseline


def _find_id_collision(steps: list[dict[str, Any]], baseline: dict[str, Any]) -> str | None:
    step_ids = {step["step_id"] for step in steps}
    binding_ids = {binding["binding_id"] for step in steps for binding in step["bindings"]}
    if baseline["step_id"] in step_ids:
        return f"기준 단계 ID {baseline['step_id']}가 초안 step_id와 겹친다"
    if any(binding["binding_id"] in binding_ids for binding in baseline["bindings"]):
        return "기준 단계 바인딩 ID가 초안 binding_id와 겹친다"
    return None


def _insert_before(steps: list[dict[str, Any]], target: dict[str, Any], baseline: dict[str, Any]) -> list[dict[str, Any]]:
    """기준 단계를 재현 단계 자리에 넣고 그 뒤 단계의 order를 하나씩 민다.

    order를 0부터 다시 매기지 않는다. 초안 order가 틀렸으면 틀린 채로 남아 validator가 잡게 한다.
    """
    position = next(position for position, step in enumerate(steps) if step is target)
    shifted = [copy.deepcopy(step) for step in steps]
    for step in shifted[position:]:
        if isinstance(step["order"], int) and not isinstance(step["order"], bool):
            step["order"] += 1
    baseline["order"] = target["order"]
    return shifted[:position] + [baseline] + shifted[position:]


def _build_assertions(target_step_id: str, baseline_step_id: str, observed_status: int, is_json: bool) -> list[dict[str, Any]]:
    assertions = [
        _check(STATUS_CHECK_ID, "response_status", target_step_id, observed_status),
        _check(BASELINE_CHECK_ID, "baseline_match", target_step_id, baseline_step_id),
    ]
    if is_json:
        assertions.append(_check(OWNER_CHECK_ID, "resource_owner", target_step_id, baseline_step_id))
    return assertions


def _check(check_id: str, kind: str, subject_ref: str, expected: Any) -> dict[str, Any]:
    return {"check_id": check_id, "kind": kind, "subject_ref": subject_ref, "selector": None, "operator": "eq", "expected": expected}
