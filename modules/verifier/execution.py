"""allow 시나리오 재현 실행 엔진(PR2). 게이트를 통과한 allow만 여기로 온다.

전송 직전 최종 URL의 origin을 effective_origins로 검사하고, 리다이렉트는 설정(max_redirects, 기본 0)만큼만 hop마다 다시
검사하며 따라가고 max_requests에 센다. limits·state_change·body_ref·세션/통신 오류는 요청을 보내지 않거나 중단하고
indeterminate로 남긴다. 미실행·판단불가를 '취약점 없음'으로 합치지 않는다(명세 m7).

Check 평가(success/failure 판정)는 step4다. 이 모듈은 실행·근거까지 하고, classify_scenario가 결과를 정한다.
"""

from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import quote, urljoin, urlsplit

from modules.verifier.executor import (
    ReplayRequest,
    ReplayResponse,
    SessionExecutor,
    SessionExpiredError,
    SessionLease,
    SessionTransportError,
)
from modules.verifier.utils.config import ReplayConfig
from modules.verifier.utils.envelope import ErrorCode, make_error_item
from modules.verifier.utils.evidence import SECRET_MASK, EvidenceWriter, is_sensitive_key, redact_value

LOCATION_HEADER = "location"
PLACEHOLDER_PREFIX = "{"
PLACEHOLDER_SUFFIX = "}"
# PR2에서 완전 평가하는 Check 종류. 나머지는 observed=null → passed=null(판단불가).
EVALUATED_CHECK_KINDS = frozenset({"session_valid", "response_status", "response_json"})
# 응답 내용을 보지 않는 Check 종류. 이것만으로는 위반을 확정하지 않는다(명세 m7 51행: HTTP 200만으로 확정 금지).
STATUS_ONLY_CHECK_KINDS = frozenset({"response_status", "session_valid"})
# 접근 거부로 읽는 상태 코드. 상태 Check가 이 값으로 거짓이면 적용 불가 Check보다 앞서 failure(거부 근거)다.
# 400·422(잘못 만든 요청일 수 있음)·3xx(리다이렉트)는 거부 근거로 보지 않는다.
DENIAL_STATUS_CODES = frozenset({401, 403, 404})
# 이 값 이상은 서버 오류. 거부도 허용도 관찰하지 못한 것이라 상태 Check를 판단불가로 둔다.
SERVER_ERROR_STATUS_MIN = 500


class _Missing:
    """관찰 경로·값이 없음(JSON의 null과 구분). exists는 False, 다른 operator는 판단불가(None)."""


MISSING = _Missing()


@dataclass(frozen=True)
class ExecutionContext:
    """실행에 필요한 외부 연결. entrypoint가 만든다. executor가 None이면 allow를 실행할 수 없다."""

    executor: SessionExecutor | None
    writer: EvidenceWriter
    clock: Callable[[], float]
    replay: ReplayConfig


@dataclass
class _Budget:
    """실제 전송 수(리다이렉트 포함)와 경과 시간으로 한도를 강제한다."""

    max_requests: int
    max_duration_ms: int
    clock: Callable[[], float]
    start: float
    sends: int = 0

    def exhausted(self) -> bool:
        if self.sends >= self.max_requests:
            return True
        elapsed_ms = (self.clock() - self.start) * 1000
        return elapsed_ms >= self.max_duration_ms

    def spend(self) -> None:
        self.sends += 1


@dataclass
class ExecutionOutcome:
    executed_steps: list[dict[str, Any]]
    responses_by_step: dict[str, dict[str, Any]]
    send_count: int
    # 중단 사유(ErrorItem). 없으면 모든 단계가 정상 전송됨.
    terminal_error: dict[str, Any] | None = None
    # 계정별 lease.is_valid() 결과. 안 쓰인 계정은 없음(session_valid Check는 그 경우 null).
    session_valid_by_account: dict[str, bool] = field(default_factory=dict)
    # 계정이 처음 쓰인(lease한) 단계 step_id. precondition CheckResult 붙일 단계 결정에 쓴다.
    account_first_step: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class _CheckOutcome:
    """Check 하나의 평가 결과. error는 그 Check를 응답에 적용할 수 없었던 사유(ErrorItem, 적용했으면 None)."""

    passed: bool | None
    observed: Any
    error: dict[str, Any] | None = None


def execution_status_for(outcome: ExecutionOutcome) -> str:
    """실행 상태는 전송 수로 정한다(result 분류와 독립). 중단 없이 전부 전송되면 completed,
    중단됐으면 전송 0건은 not_executed, 1건 이상은 error. result 분류(step4)가 바뀌어도 이 규칙은 그대로다."""
    if outcome.terminal_error is None:
        return "completed"
    return "error" if outcome.send_count >= 1 else "not_executed"


def classify_scenario(scenario: dict[str, Any], decision: dict[str, Any], outcome: ExecutionOutcome) -> dict[str, Any]:
    """실행 결과를 VerificationItem으로 만든다.

    중단 사유가 있으면 그 사유로 indeterminate. 전부 전송됐으면 preconditions·assertions(Check)를 평가해
    success/failure/indeterminate로 분류한다(_classify_by_checks). execution_status는 execution_status_for로 따로 정한다.
    """
    execution_status = execution_status_for(outcome)
    if outcome.terminal_error is not None:
        reason = "allow이지만 실행을 끝내지 못해 판단불가"
        return _verification_item(scenario, decision, "indeterminate", execution_status, reason,
                                  outcome.executed_steps, [outcome.terminal_error])
    # 전부 전송됨. 사전조건·판정조건(Check)을 평가해 result를 정한다(명세 m7: 상태 코드만으로 확정하지 않는다).
    result, reason, errors = _classify_by_checks(scenario, outcome)
    return _verification_item(scenario, decision, result, execution_status, reason, outcome.executed_steps, errors)


def _classify_by_checks(scenario: dict[str, Any], outcome: ExecutionOutcome) -> tuple[str, str, list[dict[str, Any]]]:
    """사전조건 → 판정조건 순으로 Check를 평가하고 CheckResult를 단계에 붙인 뒤 result를 정한다.

    precondition이 하나라도 거짓/판단불가 → indeterminate. 모두 참이면 assertions가 하나라도 판단불가 → indeterminate,
    하나 이상 거짓 → failure, 모두 참이면 → success. 단, 참인 assertion이 상태 코드·세션 유효성뿐이면(응답 내용 미확인)
    success 대신 indeterminate로 둔다(m7 51행: HTTP 200만으로 확정 금지).
    응답에 적용할 수 없었던 Check(CHECK_NOT_APPLICABLE·SELECTOR_ROOT_MISSING·SERVER_ERROR_RESPONSE)는 판단불가이고,
    그 사유를 Check마다 errors에 남긴다. 단 상태 Check가 거부 코드(DENIAL_STATUS_CODES)로 거짓이면 거부 근거라서
    판단불가 Check가 있어도 failure다(거부 응답엔 내용 Check를 적용할 값이 없는 게 보통이다).
    """
    preconditions = [_run_check(check, outcome) for check in scenario["preconditions"]]
    errors = _not_applicable_errors(preconditions)
    if any(evaluation.passed is None for evaluation in preconditions):
        return "indeterminate", "사전조건을 판단할 수 없어 재현 여부를 판정하지 않음", errors
    if any(evaluation.passed is False for evaluation in preconditions):
        return "indeterminate", "사전조건이 성립하지 않아 재현 여부를 판정하지 않음", errors
    assertions = [(check, _run_check(check, outcome)) for check in scenario["assertions"]]
    errors += _not_applicable_errors([evaluation for _, evaluation in assertions])
    if not assertions:
        return "indeterminate", "판정 조건이 없어 재현 여부를 판정할 수 없음", errors
    if any(_is_denied(check, evaluation) for check, evaluation in assertions):
        return "failure", "거부 상태 코드를 받아 유효 실행에서 위반이 재현되지 않음", errors
    if any(evaluation.passed is None for _, evaluation in assertions):
        return "indeterminate", "일부 판정 조건을 평가할 수 없어 판단불가(미평가·적용 불가 Check 등)", errors
    if any(evaluation.passed is False for _, evaluation in assertions):
        return "failure", "유효 실행에서 위반이 재현되지 않음", errors
    # 모두 참. 응답 내용을 확인한 참 assertion이 하나도 없으면(상태·세션뿐) 확정하지 않는다.
    if not any(check["kind"] not in STATUS_ONLY_CHECK_KINDS for check, _ in assertions):
        error = make_error_item(
            ErrorCode.ASSERTION_STATUS_ONLY,
            "참인 판정 조건이 상태 코드·세션 유효성뿐이라 응답 내용 확인 없이 재현으로 확정하지 않음",
            item_ref=scenario["scenario_id"], is_retryable=False,
        )
        return "indeterminate", "상태 코드만 맞고 응답 내용을 확인한 조건이 없어 판단불가", errors + [error]
    return "success", "응답 내용을 포함한 모든 판정 조건이 충족되어 위반이 재현됨", errors


def _not_applicable_errors(evaluations: list[_CheckOutcome]) -> list[dict[str, Any]]:
    return [evaluation.error for evaluation in evaluations if evaluation.error is not None]


def _is_denied(check: dict[str, Any], evaluation: _CheckOutcome) -> bool:
    return (
        check["kind"] == "response_status"
        and evaluation.passed is False
        and evaluation.observed in DENIAL_STATUS_CODES
    )


def _run_check(check: dict[str, Any], outcome: ExecutionOutcome) -> _CheckOutcome:
    """Check 하나를 평가하고 CheckResult를 대상 단계에 붙인다. 평가 결과(passed·적용 불가 사유)를 돌려준다."""
    evaluation = _evaluate_check(check, outcome)
    check_result = {
        "check_id": check["check_id"],
        "passed": evaluation.passed,
        "observed": _redact_observed(check, evaluation.observed),
        "description": _check_description(check, evaluation.passed),
    }
    _attach_check_result(check, outcome, check_result)
    return evaluation


def _evaluate_check(check: dict[str, Any], outcome: ExecutionOutcome) -> _CheckOutcome:
    kind = check["kind"]
    if kind not in EVALUATED_CHECK_KINDS:
        # resource_state·resource_owner·baseline_match는 PR2에서 평가하지 않는다(판단불가).
        return _CheckOutcome(None, MISSING)
    if kind == "session_valid":
        valid = outcome.session_valid_by_account.get(check["subject_ref"], MISSING)
        if valid is MISSING:
            return _CheckOutcome(None, MISSING)  # 어느 단계에도 안 쓰인 계정 → 유효로 기록하지 않는다
        return _CheckOutcome(_apply_operator(check["operator"], valid, check["expected"]), valid)
    response = outcome.responses_by_step.get(check["subject_ref"])
    if response is None:
        return _CheckOutcome(None, MISSING)  # subject_ref가 전송된 단계가 아님
    if kind == "response_status":
        observed = response["status_code"]
        if observed >= SERVER_ERROR_STATUS_MIN:
            return _not_applicable(
                check, ErrorCode.SERVER_ERROR_RESPONSE, "서버 오류 응답이라 거부·허용을 관찰하지 못함", observed=observed
            )
        return _CheckOutcome(_apply_operator(check["operator"], observed, check["expected"]), observed)
    return _evaluate_response_json(check, response["body"])


def _evaluate_response_json(check: dict[str, Any], body: Any) -> _CheckOutcome:
    """응답에 적용할 수 없으면 판단불가와 그 사유를 돌려준다. 적용 못 한 Check를 미재현으로 쓰면 놓친 위반이 '없음'이 된다.

    selector 없음(null·"")은 응답 전체를 가리켜 내용 확인 없는 참이 되므로 적용하지 않는다. 첫 키가 있으면 operator대로
    평가한다: 더 깊은 경로가 없을 때 exists는 '없음'을 관찰한 것(False), 값 비교는 판단불가(_apply_operator).
    """
    selector = check["selector"]
    if not selector:
        return _not_applicable(check, ErrorCode.CHECK_NOT_APPLICABLE, "selector가 없어(null·빈 문자열) 응답 내용을 확인할 수 없음")
    if not isinstance(body, (dict, list)):
        return _not_applicable(check, ErrorCode.CHECK_NOT_APPLICABLE, "응답이 JSON 객체·배열이 아니라 response_json을 적용할 수 없음")
    if _json_pointer(body, _root_pointer(selector)) is MISSING:
        return _not_applicable(
            check, ErrorCode.SELECTOR_ROOT_MISSING, "selector 첫 키가 응답에 없어 selector 오류인지 자원 부재인지 구분할 수 없음"
        )
    observed = _json_pointer(body, selector)
    return _CheckOutcome(_apply_operator(check["operator"], observed, check["expected"]), observed)


def _root_pointer(selector: str) -> str:
    """JSON Pointer의 첫 토큰만 남긴다("/a/b" → "/a")."""
    return "/" + selector.lstrip("/").split("/", 1)[0]


def _not_applicable(check: dict[str, Any], code: ErrorCode, message: str, observed: Any = MISSING) -> _CheckOutcome:
    return _CheckOutcome(None, observed, make_error_item(code, message, item_ref=check["check_id"], is_retryable=False))


def _apply_operator(operator: str, observed: Any, expected: Any) -> bool | None:
    if operator == "exists":
        return observed is not MISSING
    if observed is MISSING:
        return None  # 관찰값이 없으면 비교 불가(판단불가). 불일치(False)로 단정하지 않는다
    if operator == "eq":
        return observed == expected
    if operator == "ne":
        return observed != expected
    if operator == "in":
        return _contains(expected, observed)
    if operator == "contains":
        return _contains(observed, expected)
    return None


def _contains(container: Any, member: Any) -> bool | None:
    if isinstance(container, (list, str)):
        return member in container
    return None


def _attach_check_result(check: dict[str, Any], outcome: ExecutionOutcome, check_result: dict[str, Any]) -> None:
    """CheckResult를 대상 단계의 check_results에 붙인다. 규칙은 README '사전조건·판정 Check 부착'."""
    target_step_id = _check_step_id(check, outcome)
    for executed_step in outcome.executed_steps:
        if executed_step["step_id"] == target_step_id:
            executed_step["check_results"].append(check_result)
            return
    # 대상 단계를 못 찾으면(계정이 안 쓰임 등) 첫 단계에 붙인다.
    if outcome.executed_steps:
        outcome.executed_steps[0]["check_results"].append(check_result)


def _check_step_id(check: dict[str, Any], outcome: ExecutionOutcome) -> str | None:
    subject = check["subject_ref"]
    if subject in outcome.responses_by_step:
        return subject  # subject가 단계면 그 단계
    if subject in outcome.account_first_step:
        return outcome.account_first_step[subject]  # 계정이면 처음 쓰인 단계
    return None


def _redact_observed(check: dict[str, Any], observed: Any) -> Any:
    """CheckResult.observed는 비밀을 뺀 값이다(명세 m7). 민감 키 selector에서 뽑은 스칼라도 가린다."""
    if observed is MISSING:
        return None
    selector = check.get("selector")
    if check["kind"] == "response_json" and selector:
        last_token = selector.rstrip("/").rsplit("/", 1)[-1].replace("~1", "/").replace("~0", "~")
        if last_token and is_sensitive_key(last_token) and not isinstance(observed, (dict, list)):
            return SECRET_MASK
    return redact_value(observed)


def _check_description(check: dict[str, Any], passed: bool | None) -> str:
    verdict = "판단불가" if passed is None else ("충족" if passed else "불충족")
    return f"{check['kind']} {check['operator']} → {verdict}"


def _verification_item(
    scenario: dict[str, Any], decision: dict[str, Any], result: str, execution_status: str,
    reason: str, steps: list[dict[str, Any]], errors: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "verification_id": f"verification_{scenario['scenario_id']}",
        "candidate_id": scenario["candidate_id"],
        "scenario_id": scenario["scenario_id"],
        "decision_id": decision["decision_id"],
        "policy_decision": decision["decision"],
        "execution_status": execution_status,
        "result": result,
        "reason": reason,
        "steps": steps,
        "evidence_refs": [],
        "errors": errors,
    }


class _TerminalError(Exception):
    """이 단계에서 시나리오 실행을 멈춘다. 현재 단계는 error, 남은 단계는 skipped."""

    def __init__(self, error_item: dict[str, Any], sent: bool, request_url: str | None, request_ref: dict | None) -> None:
        self.error_item = error_item
        self.sent = sent
        self.request_url = request_url
        self.request_ref = request_ref


def execute_scenario(scenario: dict[str, Any], decision: dict[str, Any], context: ExecutionContext) -> ExecutionOutcome:
    """게이트를 통과한 allow 시나리오를 실행한다. lease는 계정별로 캐시하되 단계마다 is_valid를 다시 확인한다."""
    effective_origins = set(decision["effective_origins"])
    limits = decision["limits"]
    allow_state_change = limits["allow_state_change"]
    budget = _Budget(limits["max_requests"], limits["max_duration_ms"], context.clock, context.clock())
    verification_id = f"verification_{scenario['scenario_id']}"
    steps = sorted(scenario["steps"], key=lambda step: step["order"])

    executed: list[dict[str, Any]] = []
    responses: dict[str, dict[str, Any]] = {}
    leases: dict[str, SessionLease] = {}
    session_valid: dict[str, bool] = {}
    first_step: dict[str, str] = {}
    terminal: dict[str, Any] | None = None
    try:
        stop = False
        for step in steps:
            if stop:
                executed.append(_skipped_step(step["step_id"]))
                continue
            # 한도(요청 수·시간) 초과: 이 단계부터 보내지 않고 skipped로 둔다(하드 에러가 아니라 안전 상한).
            if budget.exhausted():
                executed.append(_skipped_step(step["step_id"]))
                if terminal is None:
                    terminal = make_error_item(
                        ErrorCode.LIMIT_EXCEEDED, "요청 수·시간 한도 초과로 남은 단계를 보내지 않음",
                        item_ref=step["step_id"], is_retryable=False,
                    )
                continue
            try:
                executed_step, response = _run_step(
                    step, verification_id, context, budget, responses, leases,
                    session_valid, first_step, effective_origins, allow_state_change,
                )
            except _TerminalError as error:
                executed.append(_errored_step(step["step_id"], error))
                terminal = error.error_item
                stop = True
                continue
            executed.append(executed_step)
            if response is not None:
                responses[step["step_id"]] = response
    finally:
        for lease in leases.values():
            lease.release()
    return ExecutionOutcome(executed, responses, budget.sends, terminal, session_valid, first_step)


def _run_step(
    step: dict[str, Any],
    verification_id: str,
    context: ExecutionContext,
    budget: _Budget,
    responses: dict[str, dict[str, Any]],
    leases: dict[str, SessionLease],
    session_valid: dict[str, bool],
    first_step: dict[str, str],
    effective_origins: set[str],
    allow_state_change: bool,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    plan = step["request"]
    # 1) body_ref는 PR2에서 역참조하지 않는다. 본문 뺀 요청으로 판정하지 않는다.
    if plan["body_ref"] is not None:
        raise _not_sent(ErrorCode.BODY_REF_UNAVAILABLE, "body_ref 원문을 역참조하지 않아 보내지 않음", step)
    # 2) 상태 변경 단계는 PR2에서 보내지 않는다(초기화 훅은 PR3).
    if step["state_change"] != "none":
        code = ErrorCode.STATE_CHANGE_NOT_ALLOWED if not allow_state_change else ErrorCode.STATE_RESET_UNAVAILABLE
        raise _not_sent(code, "상태 변경 단계를 보내지 않음", step)
    # 3) 한도 초과는 호출부(execute_scenario)가 단계 전에 skipped로 처리한다(여기 오면 한도 안).
    # 4) 세션: 캐시해도 단계마다 유효성 재확인.
    account_id = step["account_id"]
    lease = _lease_for(account_id, context, leases)
    if lease is None:
        raise _not_sent(ErrorCode.SESSION_UNAVAILABLE, "세션 공개 창구가 주입되지 않음", step)
    first_step.setdefault(account_id, step["step_id"])
    valid = lease.is_valid()
    session_valid[account_id] = valid  # session_valid Check 판정에 쓴다(안 쓰인 계정은 기록 없음 → null)
    if not valid:
        raise _not_sent(ErrorCode.SESSION_INVALID, "세션이 유효하지 않음", step)
    # 5) 바인딩·파라미터로 최종 URL 생성.
    url = _resolve_url(step, responses)
    if url is None:
        raise _not_sent(ErrorCode.BINDING_UNRESOLVED, "바인딩 값을 추출하지 못함", step)
    # 6) 전송 직전 최종 URL scope 검사.
    if _origin(url) not in effective_origins:
        raise _not_sent(ErrorCode.ORIGIN_OUT_OF_SCOPE, "최종 URL origin이 effective_origins 밖", step)
    return _send_with_redirects(step, verification_id, plan, url, lease, context, budget, effective_origins)


def _send_with_redirects(
    step: dict[str, Any],
    verification_id: str,
    plan: dict[str, Any],
    url: str,
    lease: SessionLease,
    context: ExecutionContext,
    budget: _Budget,
    effective_origins: set[str],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    hop_evidence: list[dict[str, Any]] = []
    current_url = url
    hop = 0
    request_ref = response_ref = None
    response: ReplayResponse | None = None
    while True:
        request = ReplayRequest(method=plan["method"], url=current_url)
        evidence_id = f"{verification_id}-{step['step_id']}-hop{hop}"
        request_ref = context.writer.write_request(evidence_id, request)
        budget.spend()
        try:
            response = lease.send(request)
        except SessionExpiredError:
            raise _sent(ErrorCode.SESSION_EXPIRED, "실행 중 세션 만료", step, current_url, request_ref)
        except SessionTransportError:
            raise _sent(ErrorCode.TRANSPORT_ERROR, "통신 실패", step, current_url, request_ref)
        response_ref = context.writer.write_response(evidence_id, response)
        # 리다이렉트: 기본(max_redirects=0)은 따라가지 않고 3xx를 그 단계 응답으로 기록한다.
        location = _redirect_location(response)
        if location is None or hop >= context.replay.max_redirects or budget.exhausted():
            break
        hop_evidence.append(request_ref)
        hop_evidence.append(response_ref)
        next_url = urljoin(current_url, location)
        if _origin(next_url) not in effective_origins:
            # out-of-scope Location은 보내지 않는다. 지금까지의 응답(3xx)을 그 단계 결과로 둔다.
            break
        current_url = next_url
        hop += 1
    executed_step = {
        "step_id": step["step_id"], "status": "completed", "request_url": current_url,
        "response_status": response.status_code, "request_ref": request_ref, "response_ref": response_ref,
        "check_results": [], "evidence_refs": hop_evidence, "errors": [],
    }
    response_record = {"status_code": response.status_code, "headers": list(response.headers), "body": response.body}
    return executed_step, response_record


def _lease_for(account_id: str, context: ExecutionContext, leases: dict[str, SessionLease]) -> SessionLease | None:
    if context.executor is None:
        return None
    if account_id not in leases:
        leases[account_id] = context.executor.lease(account_id)
    return leases[account_id]


def _resolve_url(step: dict[str, Any], responses: dict[str, dict[str, Any]]) -> str | None:
    """url_template의 {binding_id}·{path param}을 치환하고 query 파라미터를 붙인다. 못 풀면 None."""
    substitutions: dict[str, str] = {}
    for binding in step["bindings"]:
        value = _extract_binding(binding, responses)
        if value is None:
            return None
        substitutions[binding["binding_id"]] = value
    query_pairs: list[tuple[str, str]] = []
    for parameter in step["request"]["parameters"]:
        value = _parameter_value(parameter, substitutions)
        if value is None:
            return None
        if parameter["location"] == "path":
            substitutions[parameter["name"]] = value
        elif parameter["location"] == "query":
            query_pairs.append((parameter["name"], value))
        # body 파라미터는 PR2 읽기 시나리오에선 URL에 영향 없음(필요 시 PR2 후속에서 바디 구성)
    url = _apply_placeholders(step["request"]["url_template"], substitutions)
    if url is None:
        return None
    if query_pairs:
        separator = "&" if "?" in url else "?"
        url = url + separator + "&".join(f"{quote(name, safe='')}={quote(value, safe='')}" for name, value in query_pairs)
    return url


def _apply_placeholders(template: str, substitutions: dict[str, str]) -> str | None:
    result = template
    for name, value in substitutions.items():
        placeholder = f"{PLACEHOLDER_PREFIX}{name}{PLACEHOLDER_SUFFIX}"
        if placeholder in result:
            # 치환값은 경로 한 조각으로만 들어간다('/' 포함 모두 인코딩).
            result = result.replace(placeholder, quote(value, safe=""))
    if PLACEHOLDER_PREFIX in result:
        # 남은 치환자가 있으면 바인딩/파라미터로 못 푼 것.
        return None
    return result


def _parameter_value(parameter: dict[str, Any], substitutions: dict[str, str]) -> str | None:
    if parameter["binding_ref"] is not None:
        resolved = substitutions.get(parameter["binding_ref"])
        return resolved
    value = parameter["value"]
    return value if isinstance(value, str) else ("" if value is None else str(value))


def _extract_binding(binding: dict[str, Any], responses: dict[str, dict[str, Any]]) -> str | None:
    source = responses.get(binding["source_step_id"])
    if source is None:
        return None
    if binding["source_part"] == "response_header":
        for name, value in source["headers"]:
            if name.lower() == binding["selector"].lower():
                return value
        return None
    extracted = _json_pointer(source["body"], binding["selector"])
    if extracted is MISSING or extracted is None or isinstance(extracted, (dict, list)):
        return None
    return extracted if isinstance(extracted, str) else str(extracted)


def _json_pointer(document: Any, pointer: str) -> Any:
    """RFC 6901. 경로가 없으면 MISSING을 돌려준다(JSON null과 구분한다)."""
    if pointer == "":
        return document
    current = document
    for raw in pointer.lstrip("/").split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            return MISSING
    return current


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _redirect_location(response: ReplayResponse) -> str | None:
    if not 300 <= response.status_code < 400:
        return None
    for name, value in response.headers:
        if name.lower() == LOCATION_HEADER:
            return value
    return None


def _not_sent(code: ErrorCode, message: str, step: dict[str, Any]) -> _TerminalError:
    return _TerminalError(make_error_item(code, message, item_ref=step["step_id"]), sent=False, request_url=None, request_ref=None)


def _sent(code: ErrorCode, message: str, step: dict[str, Any], url: str, request_ref: dict) -> _TerminalError:
    retryable = code in {ErrorCode.SESSION_EXPIRED, ErrorCode.TRANSPORT_ERROR}
    return _TerminalError(make_error_item(code, message, item_ref=step["step_id"], is_retryable=retryable), sent=True, request_url=url, request_ref=request_ref)


def _skipped_step(step_id: str) -> dict[str, Any]:
    return {
        "step_id": step_id, "status": "skipped", "request_url": None, "response_status": None,
        "request_ref": None, "response_ref": None, "check_results": [], "evidence_refs": [], "errors": [],
    }


def _errored_step(step_id: str, error: _TerminalError) -> dict[str, Any]:
    return {
        "step_id": step_id, "status": "error",
        # 보낸 단계만 request_url·request_ref를 남긴다(응답은 없음). 미전송이면 모두 null.
        "request_url": error.request_url, "response_status": None,
        "request_ref": error.request_ref, "response_ref": None,
        "check_results": [], "evidence_refs": [], "errors": [error.error_item],
    }
