"""실패한 LLM 초안의 개발용 진단 레코드. 비밀값은 빼고 구조·바인딩만 남긴다.

공개 `test_scenarios.json`에는 초안을 넣지 않는다(실패는 status=failed·data=null 유지). 이 레코드는
scenario_generator private 영역에만 저장해 "실제로 어느 필드·바인딩이 틀렸는지"를 사람이 보게 한다.
값은 진단 대상이 아니라 구조·바인딩이 대상이므로, parameter의 literal value는 전부 마스킹한다.
"""

import copy
import re
from typing import Any

from modules.scenario_generator.scenario_drafter import (
    ACCOUNT_ROLES,
    DRAFT_KEYS,
    DRAFT_REQUEST_KEYS,
    DRAFT_STEP_KEYS,
)

REDACTED = "[REDACTED]"
FAILURE_DRAFT_INVALID = "DRAFT_INVALID"
FAILURE_DRAFTER_FAILED = "DRAFTER_FAILED"
# url_template이 참조하는 {binding_id}. scenario_validator와 같은 표기 규칙이다.
_PLACEHOLDER_PATTERN = re.compile(r"\{([^{}]*)\}")


def _key_diff(value: Any, expected_keys: frozenset[str]) -> dict[str, list[str]]:
    keys = set(value) if isinstance(value, dict) else set()
    return {"missing": sorted(expected_keys - keys), "extra": sorted(keys - expected_keys)}


def _step_type_problems(step: Any) -> list[str]:
    """값이 아니라 기본 타입·허용값만 본다. 정밀 타입 검사는 초안 Schema(후속 단계)가 맡는다."""
    if not isinstance(step, dict):
        return ["step이 객체가 아니다"]
    problems: list[str] = []
    if "order" in step and (isinstance(step["order"], bool) or not isinstance(step["order"], int)):
        problems.append("order가 정수가 아니다")
    if "account_role" in step and step["account_role"] not in ACCOUNT_ROLES:
        problems.append("account_role가 actor/reference가 아니다")
    if "bindings" in step and not isinstance(step["bindings"], list):
        problems.append("bindings가 배열이 아니다")
    request = step.get("request")
    if isinstance(request, dict) and "parameters" in request and not isinstance(request["parameters"], list):
        problems.append("request.parameters가 배열이 아니다")
    return problems


def _diagnose_fields(draft: Any) -> dict[str, Any]:
    """기대 초안 모양에서 벗어난 곳(누락/추가 필드·기본 타입)을 짚는다."""
    diagnosis: dict[str, Any] = {"top_level": _key_diff(draft, DRAFT_KEYS)}
    steps = draft.get("steps") if isinstance(draft, dict) else None
    if not isinstance(steps, list):
        diagnosis["steps"] = "steps가 배열이 아니다"
        return diagnosis
    step_diagnoses = []
    for index, step in enumerate(steps):
        step_diagnosis: dict[str, Any] = {
            "index": index,
            "step_id": step.get("step_id") if isinstance(step, dict) else None,
            **_key_diff(step, DRAFT_STEP_KEYS),
            "request": _key_diff(step.get("request") if isinstance(step, dict) else None, DRAFT_REQUEST_KEYS),
            "type_problems": _step_type_problems(step),
        }
        step_diagnoses.append(step_diagnosis)
    diagnosis["steps"] = step_diagnoses
    return diagnosis


def _binding_analysis(draft: Any) -> list[dict[str, Any]]:
    """step마다 url_template이 참조한 바인딩 vs 그 step에 선언된 바인딩 vs 미정의(참조−선언)."""
    steps = draft.get("steps") if isinstance(draft, dict) else None
    if not isinstance(steps, list):
        return []
    analysis = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        request = step.get("request")
        url_template = request.get("url_template") if isinstance(request, dict) else None
        referenced = _PLACEHOLDER_PATTERN.findall(url_template) if isinstance(url_template, str) else []
        raw_bindings = step.get("bindings")
        declared = (
            [binding.get("binding_id") for binding in raw_bindings if isinstance(binding, dict)]
            if isinstance(raw_bindings, list)
            else []
        )
        analysis.append({
            "step_id": step.get("step_id"),
            "url_referenced": referenced,
            "declared": declared,
            "undefined": sorted(set(referenced) - set(declared)),
        })
    return analysis


def _redact_draft(draft: Any) -> Any:
    """비밀값 제거 사본: parameter의 literal value만 마스킹한다(구조·url_template·binding은 보존)."""
    clone = copy.deepcopy(draft)
    steps = clone.get("steps") if isinstance(clone, dict) else None
    if not isinstance(steps, list):
        return clone
    for step in steps:
        request = step.get("request") if isinstance(step, dict) else None
        parameters = request.get("parameters") if isinstance(request, dict) else None
        if not isinstance(parameters, list):
            continue
        for parameter in parameters:
            if isinstance(parameter, dict) and parameter.get("value") is not None:
                parameter["value"] = REDACTED
    return clone


def build_draft_invalid_record(
    candidate_id: str, draft: Any, problems: list[str], model_info: dict[str, Any] | None
) -> dict[str, Any]:
    """검증을 통과하지 못한 초안의 진단 레코드. 초안은 비밀값을 지우고 남긴다."""
    return {
        "candidate_id": candidate_id,
        "failure": FAILURE_DRAFT_INVALID,
        "model_info": model_info,
        "problems": list(problems),
        "field_diagnosis": _diagnose_fields(draft),
        "binding_analysis": _binding_analysis(draft),
        "draft": _redact_draft(draft),
    }


def build_drafter_failed_record(
    candidate_id: str, message: str, model_info: dict[str, Any] | None
) -> dict[str, Any]:
    """초안을 받지 못한 경우(LLM 호출·파싱 실패). 초안이 없으므로 모델 정보와 사유만 남긴다."""
    return {
        "candidate_id": candidate_id,
        "failure": FAILURE_DRAFTER_FAILED,
        "model_info": model_info,
        "problems": [message],
        "field_diagnosis": None,
        "binding_analysis": None,
        "draft": None,
    }
