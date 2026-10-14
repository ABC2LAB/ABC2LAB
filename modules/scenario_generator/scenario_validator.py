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
# 단계의 응답을 검사하는 조건. subject_ref가 steps의 step_id여야 평가할 수 있다.
STEP_RESPONSE_CHECK_KINDS = frozenset(
    {"response_status", "response_json", "resource_state", "resource_owner", "baseline_match"}
)
# 같은 시나리오의 기준 단계 응답과 비교하는 조건. expected가 다른 계정이 실행한 기준 단계 step_id다(m5 Check 규약).
BASELINE_CHECK_KINDS = frozenset({"baseline_match", "resource_owner"})
# 재현 단계에 늘 있어야 하는 판정 조건. 상태 코드만으로는 위반을 확정할 수 없어(명세 m7) 기준 응답과도 비교한다.
REPRODUCTION_ASSERTION_KINDS = frozenset({"response_status", "baseline_match"})
# 응답이 JSON인지 정하는 content_type 조각. collector 근거 파일·세션 창구와 같은 기준이다.
JSON_CONTENT_TYPE_MARKER = "json"


@dataclass(frozen=True)
class ValidationContext:
    matched: MatchedCandidate
    index: CrawlIndex
    target_url: str


@functools.cache
def _get_scenario_validator() -> Draft202012Validator:
    schema = json.loads(OUTPUT_SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(
        {"$schema": schema["$schema"], "$ref": "#/$defs/scenario", "$defs": schema["$defs"]}
    )


@functools.cache
def _get_steps_validator() -> Draft202012Validator:
    schema = json.loads(OUTPUT_SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(
        {"$schema": schema["$schema"], "type": "array", "items": {"$ref": "#/$defs/scenarioStep"}, "$defs": schema["$defs"]}
    )


def find_steps_schema_problem(steps: list[Any]) -> str | None:
    """조립한 steps가 출력 ScenarioStep 모양인지. 기준 단계·판정 조건을 만들기 전에 타입을 믿을 수 있게 한다."""
    validator = _get_steps_validator()
    if validator.is_valid(steps):
        return None
    return f"Schema 위반(steps): {summarize_schema_errors(validator, steps)}"


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
    if scenario["resource_ids"] != candidate["resource_ids"]:
        problems.append("resource_ids가 원본 후보와 다르다")
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
    # 바인딩 치환이 없는 url_template은 선택한 원본 요청 URL과 정확히 같아야 한다.
    # (고정 예시 URL을 복사한 초안이 host/port만 맞아 통과하던 틈을 막는다. 동적 경로는 {바인딩}으로 열어 둔다.)
    url_template = plan["url_template"]
    if "{" not in url_template and url_template != source["url"]:
        problems.append(f"단계 {step_id}의 url_template이 원본 요청 {source['request_id']}의 URL과 다르다")
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
    problems: list[str] = []
    for step in scenario["steps"]:
        problems += _check_step_account(step, allowed_accounts)
        problems += _check_step_request(step, context.index)
        for binding in step["bindings"]:
            problems += _check_binding(step, binding, order_by_step_id)
        # 소비자(safety_policy·verifier·reporter)는 그 단계의 bindings만 치환에 쓴다. 앞 단계에 정의한 바인딩은 보이지 않는다.
        step_binding_ids = {binding["binding_id"] for binding in step["bindings"]}
        problems += _check_url(step, context.target_url, step_binding_ids)
        problems += _check_parameters(step, step_binding_ids)
    return problems


def _check_conditions(scenario: dict[str, Any]) -> list[str]:
    problems = []
    step_ids = {step["step_id"] for step in scenario["steps"]}
    account_by_step_id = {step["step_id"]: step["account_id"] for step in scenario["steps"]}
    for check in scenario["preconditions"] + scenario["assertions"]:
        check_id = check["check_id"]
        selector = check["selector"]
        if check["kind"] == "response_json" and selector is not None and not JSON_POINTER_PATTERN.fullmatch(selector):
            problems.append(f"조건 {check_id}의 selector가 JSON Pointer가 아니다")
        if check["kind"] in STEP_RESPONSE_CHECK_KINDS and check["subject_ref"] not in step_ids:
            problems.append(f"조건 {check_id}는 응답 조건인데 subject_ref가 steps의 step_id가 아니다")
        if check["operator"] == "in" and not isinstance(check["expected"], list):
            problems.append(f"조건 {check_id}는 operator=in인데 expected가 배열이 아니다")
        if check["kind"] in BASELINE_CHECK_KINDS and not _is_other_account_step(check, account_by_step_id):
            problems.append(f"조건 {check_id}의 expected가 다른 계정이 실행한 기준 단계 step_id가 아니다")
    return problems


def _is_other_account_step(check: dict[str, Any], account_by_step_id: dict[str, str]) -> bool:
    baseline_step_id = check["expected"]
    if not isinstance(baseline_step_id, str) or baseline_step_id not in account_by_step_id:
        return False
    return account_by_step_id[baseline_step_id] != account_by_step_id.get(check["subject_ref"])


def _check_session_preconditions(scenario: dict[str, Any]) -> list[str]:
    """steps에 쓰인 계정마다 세션 유효성을 사전조건으로 확인한다. 세션이 죽은 실행을 위반 판정에 쓰지 않기 위해서다."""
    step_account_ids = {step["account_id"] for step in scenario["steps"]}
    session_check_subjects = {
        check["subject_ref"] for check in scenario["preconditions"] if check["kind"] == "session_valid"
    }
    problems = [
        f"계정 {account_id}의 session_valid 사전조건이 없다"
        for account_id in sorted(step_account_ids - session_check_subjects)
    ]
    for check in scenario["preconditions"] + scenario["assertions"]:
        if check["kind"] != "session_valid":
            continue
        if check["subject_ref"] not in step_account_ids:
            problems.append(f"조건 {check['check_id']}는 session_valid인데 subject_ref가 steps에 쓰인 계정이 아니다")
        # exists는 값이 있기만 하면 참이라 세션이 죽어도(false) 통과한다. 유효성은 eq true로만 확인한다.
        if check["operator"] != "eq" or check["expected"] is not True:
            problems.append(f"조건 {check['check_id']}는 session_valid인데 operator=eq, expected=true가 아니다")
    return problems


def find_reproduction_step(steps: list[dict[str, Any]], matched: MatchedCandidate) -> dict[str, Any] | None:
    """재현 단계: 실행 계정이 후보 근거 요청(source_request_ids)을 보내는 단계 중 마지막. 없으면 None."""
    actor_account_id = matched.actor_account["account_id"]
    source_request_ids = set(matched.candidate["source_request_ids"])
    reproduction_steps = [
        step for step in steps
        if isinstance(step, dict) and step.get("account_id") == actor_account_id
        and step.get("source_request_id") in source_request_ids
    ]
    return reproduction_steps[-1] if reproduction_steps else None


def _check_reproduction_assertions(scenario: dict[str, Any], context: ValidationContext) -> list[str]:
    """재현 단계에 판정 조건이 있어야 한다: response_status·baseline_match, 근거 응답이 JSON이면 resource_owner도.

    baseline_match의 기준 단계는 기준 계정이 같은 근거 요청을 보낸 단계여야 한다(소유자의 정상 응답과 비교).
    """
    step = find_reproduction_step(scenario["steps"], context.matched)
    if step is None:
        return ["실행 계정이 후보 근거 요청을 재현하는 단계가 없다"]
    checks_by_kind: dict[str, list[dict[str, Any]]] = {}
    for check in scenario["assertions"]:
        if check["subject_ref"] == step["step_id"]:
            checks_by_kind.setdefault(check["kind"], []).append(check)
    required = set(REPRODUCTION_ASSERTION_KINDS)
    source = context.index.requests_by_id.get(step["source_request_id"])
    if source is not None and is_json_response(source):
        required.add("resource_owner")
    problems = [f"재현 단계 {step['step_id']}에 {kind} 판정 조건이 없다" for kind in sorted(required - set(checks_by_kind))]
    reference = context.matched.reference_account
    steps_by_id = {other["step_id"]: other for other in scenario["steps"]}
    for check in checks_by_kind.get("baseline_match", []):
        baseline = steps_by_id.get(check["expected"]) if isinstance(check["expected"], str) else None
        is_owner_replay = (
            baseline is not None and reference is not None
            and baseline["account_id"] == reference["account_id"]
            and baseline["source_request_id"] == step["source_request_id"]
        )
        if not is_owner_replay:
            problems.append(f"조건 {check['check_id']}의 기준 단계는 기준 계정이 같은 근거 요청을 보낸 단계여야 한다")
    return problems


def is_json_response(request: dict[str, Any]) -> bool:
    content_type = request["response"].get("content_type") or ""
    return JSON_CONTENT_TYPE_MARKER in content_type.lower()


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
        + _check_session_preconditions(scenario)
        + _check_reproduction_assertions(scenario, context)
    )
