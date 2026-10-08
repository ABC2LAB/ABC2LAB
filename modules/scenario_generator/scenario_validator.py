"""LLM 초안을 코드로 다시 검증한다. Schema(모양)와 의미(순서·참조·범위·안전)를 모두 본다.

문제 목록의 문구에는 ID와 위치만 넣고 입력 값은 넣지 않는다.
모듈 규칙(명세가 직접 적지 않은 해석)은 README의 "명세 해석" 절에 따로 적어 둔다.
"""

import functools
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator

from modules.scenario_generator.candidate_matcher import CrawlIndex, MatchedCandidate
from modules.scenario_generator.utils.schema_errors import summarize_schema_errors

OUTPUT_SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "output" / "test_scenarios.schema.json"
# RFC 6901 JSON Pointer 문법. 실행 코드가 아니라 위치 표기라는 점만 확인한다.
JSON_POINTER_PATTERN = re.compile(r"^(/([^/~]|~[01])*)*$")
# 헤더 이름은 소문자로 기록한다(명세 02). 공백이 있으면 이름이 아니다.
HEADER_NAME_PATTERN = re.compile(r"^[^A-Z\s]+$")
PLACEHOLDER_PATTERN = re.compile(r"\{([^{}]*)\}")
MAX_REPORTED_PROBLEMS = 5
# 응답 내용을 보지 않는 조건. 이것만으로는 위반 재현을 판정할 수 없다(명세 m7: HTTP 200만으로 확정 금지).
STATUS_ONLY_CHECK_KINDS = frozenset({"response_status", "session_valid"})
# 단계의 응답을 검사하는 조건. subject_ref가 steps의 step_id여야 평가할 수 있다.
STEP_RESPONSE_CHECK_KINDS = frozenset({"response_status", "response_json"})


@dataclass(frozen=True)
class ValidationContext:
    matched: MatchedCandidate
    index: CrawlIndex
    target_url: str


@functools.cache
def _get_scenario_validator() -> Draft202012Validator:
    schema = json.loads(OUTPUT_SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(
        {"$schema": schema["$schema"], "$ref": "#/$defs/Scenario", "$defs": schema["$defs"]}
    )


def summarize_problems(problems: list[str]) -> str:
    shown = "; ".join(problems[:MAX_REPORTED_PROBLEMS])
    remaining = len(problems) - MAX_REPORTED_PROBLEMS
    return f"{shown} 외 {remaining}건" if remaining > 0 else shown


def _find_duplicates(values: list[str]) -> list[str]:
    return [value for value, count in Counter(values).items() if count > 1]


def _check_candidate_link(scenario: dict[str, Any], candidate: dict[str, Any]) -> list[str]:
    problems = []
    if scenario["candidate_id"] != candidate["candidate_id"]:
        problems.append("candidate_id가 원본 후보와 다르다")
    if scenario["expected_basis"] != candidate["expected_basis"]:
        problems.append("expected_basis가 원본 후보와 다르다")
    return problems


def _check_identifiers(scenario: dict[str, Any]) -> list[str]:
    problems = []
    steps = scenario["steps"]
    if not steps:
        problems.append("steps가 비어 있다")
    if not scenario["assertions"]:
        problems.append("assertions가 비어 있다(위반 재현 여부를 판단할 조건이 없다)")
    if [step["order"] for step in steps] != list(range(len(steps))):
        problems.append("steps.order가 0..N-1을 순서대로 유일하게 채우지 않는다")
    for step_id in _find_duplicates([step["step_id"] for step in steps]):
        problems.append(f"step_id가 중복이다: {step_id}")
    binding_ids = [binding["binding_id"] for step in steps for binding in step["bindings"]]
    for binding_id in _find_duplicates(binding_ids):
        problems.append(f"binding_id가 중복이다: {binding_id}")
    check_ids = [check["check_id"] for check in scenario["preconditions"] + scenario["assertions"]]
    for check_id in _find_duplicates(check_ids):
        problems.append(f"check_id가 중복이다: {check_id}")
    return problems


def _check_step_account(step: dict[str, Any], allowed_accounts: dict[str, dict[str, Any]]) -> list[str]:
    step_id = step["step_id"]
    account = allowed_accounts.get(step["account_id"])
    if account is None:
        # 후보가 지목하지 않은 계정(예: 관리자)으로 요청을 보내면 진단 범위를 넘는다.
        return [f"단계 {step_id}의 계정 {step['account_id']}은(는) 후보의 실행·기준 계정이 아니다"]
    problems = []
    if account["role_id"] != step["role_id"]:
        problems.append(f"단계 {step_id}의 role_id가 계정 {account['account_id']}의 역할과 다르다")
    if account["session_ref"] != step["session_ref"]:
        problems.append(f"단계 {step_id}의 session_ref가 계정 {account['account_id']}의 것과 다르다")
    return problems


def _check_step_request(step: dict[str, Any], index: CrawlIndex) -> list[str]:
    step_id = step["step_id"]
    source = index.requests_by_id.get(step["source_request_id"])
    if source is None:
        return [f"단계 {step_id}의 source_request_id {step['source_request_id']}이(가) crawl_result에 없다"]
    problems = []
    plan = step["request"]
    if plan["method"] != source["method"]:
        problems.append(f"단계 {step_id}의 method가 원본 요청 {source['request_id']}과 다르다")
    if plan["body_ref"] is not None and plan["body_ref"] != source["body_ref"]:
        problems.append(f"단계 {step_id}의 body_ref가 원본 요청 {source['request_id']}의 것이 아니다")
    return problems


def _check_binding(step: dict[str, Any], binding: dict[str, Any], order_by_step_id: dict[str, int]) -> list[str]:
    binding_id = binding["binding_id"]
    problems = []
    source_order = order_by_step_id.get(binding["source_step_id"])
    if source_order is None:
        problems.append(f"바인딩 {binding_id}의 source_step_id가 steps에 없다")
    elif source_order >= step["order"]:
        problems.append(f"바인딩 {binding_id}는 현재 단계보다 앞선 단계에서만 가져올 수 있다")
    selector = binding["selector"]
    if binding["source_part"] == "response_body" and not JSON_POINTER_PATTERN.fullmatch(selector):
        problems.append(f"바인딩 {binding_id}의 selector가 JSON Pointer가 아니다")
    if binding["source_part"] == "response_header" and not HEADER_NAME_PATTERN.fullmatch(selector):
        problems.append(f"바인딩 {binding_id}의 selector가 소문자 헤더 이름이 아니다")
    return problems


def _check_url(step: dict[str, Any], target_url: str, visible_binding_ids: set[str]) -> list[str]:
    step_id = step["step_id"]
    url_template = step["request"]["url_template"]
    problems = []
    for name in PLACEHOLDER_PATTERN.findall(url_template):
        if name not in visible_binding_ids:
            problems.append(f"단계 {step_id}의 url_template이 정의되지 않은 바인딩을 쓴다: {name}")
    if re.search(r"[{}]", PLACEHOLDER_PATTERN.sub("", url_template)):
        problems.append(f"단계 {step_id}의 url_template에 짝이 맞지 않는 중괄호가 있다")
    try:
        raw = urlsplit(url_template)
        masked = urlsplit(PLACEHOLDER_PATTERN.sub("x", url_template))
        target = urlsplit(target_url)
        # 주소(scheme://host:port)는 바인딩으로 바꿀 수 없다. 바인딩이 대상 밖으로 요청을 보내는 길이 되면 안 된다.
        if "{" in raw.netloc:
            problems.append(f"단계 {step_id}의 url_template 주소에 바인딩을 쓸 수 없다")
        elif (masked.scheme, masked.hostname, masked.port) != (target.scheme, target.hostname, target.port):
            problems.append(f"단계 {step_id}의 url_template이 대상 origin 밖을 가리킨다")
        if masked.username is not None or masked.password is not None:
            problems.append(f"단계 {step_id}의 url_template에 사용자 정보가 있다")
    except ValueError:
        problems.append(f"단계 {step_id}의 url_template을 URL로 해석할 수 없다")
    return problems


def _check_parameters(step: dict[str, Any], visible_binding_ids: set[str]) -> list[str]:
    problems = []
    for parameter in step["request"]["parameters"]:
        reference = parameter["binding_ref"]
        if reference is None:
            continue
        if parameter["value"] is not None:
            problems.append(f"단계 {step['step_id']}의 파라미터 {parameter['name']}이(가) value와 binding_ref를 함께 쓴다")
        if reference not in visible_binding_ids:
            problems.append(f"단계 {step['step_id']}의 파라미터 {parameter['name']}이(가) 정의되지 않은 바인딩을 쓴다")
    return problems


def _check_steps(scenario: dict[str, Any], context: ValidationContext) -> list[str]:
    matched = context.matched
    allowed_accounts = {matched.actor_account["account_id"]: matched.actor_account}
    if matched.reference_account is not None:
        allowed_accounts[matched.reference_account["account_id"]] = matched.reference_account

    order_by_step_id = {step["step_id"]: step["order"] for step in scenario["steps"]}
    visible_binding_ids: set[str] = set()
    problems: list[str] = []
    for step in scenario["steps"]:
        problems += _check_step_account(step, allowed_accounts)
        problems += _check_step_request(step, context.index)
        for binding in step["bindings"]:
            problems += _check_binding(step, binding, order_by_step_id)
            visible_binding_ids.add(binding["binding_id"])
        problems += _check_url(step, context.target_url, visible_binding_ids)
        problems += _check_parameters(step, visible_binding_ids)
    return problems


def _check_conditions(scenario: dict[str, Any]) -> list[str]:
    problems = []
    step_ids = {step["step_id"] for step in scenario["steps"]}
    for check in scenario["preconditions"] + scenario["assertions"]:
        check_id = check["check_id"]
        selector = check["selector"]
        if check["kind"] == "response_json" and selector is not None and not JSON_POINTER_PATTERN.fullmatch(selector):
            problems.append(f"조건 {check_id}의 selector가 JSON Pointer가 아니다")
        if check["kind"] in STEP_RESPONSE_CHECK_KINDS and check["subject_ref"] not in step_ids:
            problems.append(f"조건 {check_id}는 응답 조건인데 subject_ref가 steps의 step_id가 아니다")
        if check["operator"] == "in" and not isinstance(check["expected"], list):
            problems.append(f"조건 {check_id}는 operator=in인데 expected가 배열이 아니다")
    assertion_kinds = {check["kind"] for check in scenario["assertions"]}
    if assertion_kinds and assertion_kinds <= STATUS_ONLY_CHECK_KINDS:
        problems.append("assertions가 상태 코드·세션 조건뿐이다(응답 내용·자원 상태를 보는 조건이 최소 1개 필요)")
    return problems


def find_scenario_problems(scenario: Any, context: ValidationContext) -> list[str]:
    """문제가 없으면 빈 목록. Schema 위반이면 의미 검사는 건너뛴다(타입을 믿을 수 없어서)."""
    validator = _get_scenario_validator()
    if not validator.is_valid(scenario):
        return [f"Schema 위반: {summarize_schema_errors(validator, scenario)}"]
    return (
        _check_candidate_link(scenario, context.matched.candidate)
        + _check_identifiers(scenario)
        + _check_steps(scenario, context)
        + _check_conditions(scenario)
    )
