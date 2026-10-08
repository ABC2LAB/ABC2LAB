"""allow 재현 실행 엔진. 전송 직전 scope 재검사·리다이렉트·limits·state_change·body_ref·세션 오류를 본다.

전송 0건은 ScriptedExecutor.send_calls로 고정한다. 각 테스트는 '이 검사를 빼면 통과해 버리는' 버그 하나를 겨눈다.
Check 평가(success/failure 분류) 자체는 test_checks.py에서 본다.
"""

from pathlib import Path

from modules.verifier.execution import (
    ExecutionContext,
    ExecutionOutcome,
    classify_scenario,
    execute_scenario,
    execution_status_for,
)
from modules.verifier.executor import ReplayResponse, SessionExpiredError
from modules.verifier.tests.helpers import (
    FakeClock,
    ScriptedExecutor,
    check,
    decision,
    scenario,
    step,
    transport_error,
)
from modules.verifier.utils.config import ReplayConfig
from modules.verifier.utils.evidence import EvidenceWriter

ORIGIN = "http://127.0.0.1:8001"


def _context(executor, run_root: Path, clock=None, max_redirects: int = 0) -> ExecutionContext:
    return ExecutionContext(executor, EvidenceWriter(run_root, 0), clock or (lambda: 0.0), ReplayConfig(max_redirects))


def _decision(origins=(ORIGIN,), max_requests=4, max_duration_ms=10000, allow_state_change=False) -> dict:
    verdict = decision("d1", "s1", "allow", ["acc_user"])
    verdict["effective_origins"] = list(origins)
    verdict["limits"] = {"max_requests": max_requests, "max_duration_ms": max_duration_ms, "allow_state_change": allow_state_change}
    return verdict


def _one_step(
    url_template: str = f"{ORIGIN}/orders/7", state_change: str = "none", body_ref=None,
    preconditions=None, assertions=None,
) -> dict:
    s = step("st1", "acc_user", "role_user", "sess_user", url_template=url_template)
    s["state_change"] = state_change
    s["request"]["body_ref"] = body_ref
    return scenario("s1", "c1", [s], preconditions=preconditions, assertions=assertions)


def _run(scn, executor, run_root, dec=None, clock=None, max_redirects=0):
    dec = dec or _decision()
    outcome = execute_scenario(scn, dec, _context(executor, run_root, clock, max_redirects))
    return classify_scenario(scn, dec, outcome), outcome


def _codes(item) -> list[str]:
    return [e["code"] for e in item["errors"]]


def test_normal_get_executes_and_is_success(tmp_path: Path) -> None:
    executor = ScriptedExecutor({("GET", f"{ORIGIN}/orders/7"): ReplayResponse(200, body={"owner_id": 3})})
    # 상태 + 응답 내용(owner_id)을 함께 확인해야 success.
    scn = _one_step(assertions=[
        check("a1", "response_status", "st1", "eq", 200),
        check("a2", "response_json", "st1", "eq", 3, selector="/owner_id"),
    ])
    item, outcome = _run(scn, executor, tmp_path / "run_demo_001")
    assert (item["result"], item["execution_status"]) == ("success", "completed")
    assert outcome.executed_steps[0]["status"] == "completed"
    assert outcome.executed_steps[0]["response_status"] == 200
    assert executor.send_calls == 1


def test_origin_out_of_scope_no_send(tmp_path: Path) -> None:
    executor = ScriptedExecutor({})  # 어떤 전송이든 UnscriptedRequestError로 터진다
    item, _ = _run(_one_step(), executor, tmp_path / "run_demo_001", dec=_decision(origins=("http://127.0.0.1:9999",)))
    assert (item["result"], item["execution_status"]) == ("indeterminate", "not_executed")
    assert _codes(item) == ["ORIGIN_OUT_OF_SCOPE"]
    assert executor.send_calls == 0


def test_302_not_followed_by_default(tmp_path: Path) -> None:
    executor = ScriptedExecutor({("GET", f"{ORIGIN}/orders/7"): ReplayResponse(302, headers=((("Location"), f"{ORIGIN}/login"),))})
    _, outcome = _run(_one_step(), executor, tmp_path / "run_demo_001")
    assert executor.send_calls == 1
    assert outcome.executed_steps[0]["response_status"] == 302


def test_redirect_followed_when_enabled(tmp_path: Path) -> None:
    executor = ScriptedExecutor({
        ("GET", f"{ORIGIN}/a"): ReplayResponse(302, headers=(("Location", f"{ORIGIN}/b"),)),
        ("GET", f"{ORIGIN}/b"): ReplayResponse(200, body={}),
    })
    _, outcome = _run(_one_step(url_template=f"{ORIGIN}/a"), executor, tmp_path / "run_demo_001", max_redirects=1)
    step_record = outcome.executed_steps[0]
    assert executor.send_calls == 2
    assert step_record["request_url"] == f"{ORIGIN}/b"  # 마지막 hop 기준
    assert step_record["response_status"] == 200
    assert len(step_record["evidence_refs"]) == 2  # 앞 hop 요청·응답


def test_redirect_location_out_of_scope_not_sent(tmp_path: Path) -> None:
    # (a) 허용 origin으로 시작했는데 Location이 외부 → 그 hop은 보내지 않는다(첫 요청만 전송).
    executor = ScriptedExecutor({("GET", f"{ORIGIN}/a"): ReplayResponse(302, headers=(("Location", "http://evil.example/x"),))})
    _, outcome = _run(_one_step(url_template=f"{ORIGIN}/a"), executor, tmp_path / "run_demo_001", max_redirects=3)
    assert executor.send_calls == 1
    assert executor.sent_urls() == [f"{ORIGIN}/a"]
    assert outcome.executed_steps[0]["response_status"] == 302


def test_binding_substituted_host_out_of_scope_not_sent(tmp_path: Path) -> None:
    # (b) 앞 단계 응답값이 host 자리에 치환돼 외부 origin이 되면, 생산자 규칙과 무관하게 verifier가 재검사해 보내지 않는다.
    first = step("a", "acc_user", "role_user", "sess_user", url_template=f"{ORIGIN}/me")
    second = step("b", "acc_user", "role_user", "sess_user", url_template="http://{h}/orders/7", order=1)
    second["bindings"] = [{"binding_id": "h", "source_step_id": "a", "source_part": "response_body", "selector": "/host"}]
    scn = scenario("s1", "c1", [first, second])
    executor = ScriptedExecutor({("GET", f"{ORIGIN}/me"): ReplayResponse(200, body={"host": "evil.example"})})
    item, _ = _run(scn, executor, tmp_path / "run_demo_001")
    assert executor.sent_urls() == [f"{ORIGIN}/me"]  # 두 번째(외부) 단계는 전송 0
    assert _codes(item) == ["ORIGIN_OUT_OF_SCOPE"]


def test_binding_value_encoded_as_path_segment(tmp_path: Path) -> None:
    first = step("a", "acc_user", "role_user", "sess_user", url_template=f"{ORIGIN}/me")
    second = step("b", "acc_user", "role_user", "sess_user", url_template=f"{ORIGIN}/orders/{{oid}}", order=1)
    second["bindings"] = [{"binding_id": "oid", "source_step_id": "a", "source_part": "response_body", "selector": "/oid"}]
    scn = scenario("s1", "c1", [first, second])
    executor = ScriptedExecutor({
        ("GET", f"{ORIGIN}/me"): ReplayResponse(200, body={"oid": "a/b 7"}),
        ("GET", f"{ORIGIN}/orders/a%2Fb%207"): ReplayResponse(200, body={}),
    })
    _run(scn, executor, tmp_path / "run_demo_001")
    assert executor.sent_urls()[1] == f"{ORIGIN}/orders/a%2Fb%207"


def test_limit_max_requests_skips_rest(tmp_path: Path) -> None:
    # (c) 첫 단계 전송 후 한도 초과 → 둘째 단계 전송 0, skipped.
    first = step("a", "acc_user", "role_user", "sess_user", url_template=f"{ORIGIN}/a")
    second = step("b", "acc_user", "role_user", "sess_user", url_template=f"{ORIGIN}/b", order=1)
    scn = scenario("s1", "c1", [first, second])
    executor = ScriptedExecutor({("GET", f"{ORIGIN}/a"): ReplayResponse(200, body={})})
    item, outcome = _run(scn, executor, tmp_path / "run_demo_001", dec=_decision(max_requests=1))
    assert executor.send_calls == 1
    assert executor.sent_urls() == [f"{ORIGIN}/a"]
    assert outcome.executed_steps[1]["status"] == "skipped"
    assert (item["result"], item["execution_status"]) == ("indeterminate", "error")
    assert "LIMIT_EXCEEDED" in _codes(item)


def test_limit_max_requests_zero_not_executed(tmp_path: Path) -> None:
    executor = ScriptedExecutor({})
    item, _ = _run(_one_step(url_template=f"{ORIGIN}/a"), executor, tmp_path / "run_demo_001", dec=_decision(max_requests=0))
    assert executor.send_calls == 0
    assert item["execution_status"] == "not_executed"
    assert "LIMIT_EXCEEDED" in _codes(item)


def test_limit_max_duration_skips_rest(tmp_path: Path) -> None:
    clock = FakeClock()
    first = step("a", "acc_user", "role_user", "sess_user", url_template=f"{ORIGIN}/a")
    second = step("b", "acc_user", "role_user", "sess_user", url_template=f"{ORIGIN}/b", order=1)
    scn = scenario("s1", "c1", [first, second])
    # send마다 600ms 진행, 한도 500ms → 첫 전송(600ms 경과) 후 둘째 단계 전엔 이미 초과.
    executor = ScriptedExecutor({("GET", f"{ORIGIN}/a"): ReplayResponse(200, body={})}, clock=clock, latency_ms=600)
    item, outcome = _run(scn, executor, tmp_path / "run_demo_001", dec=_decision(max_duration_ms=500), clock=clock)
    assert executor.send_calls == 1
    assert outcome.executed_steps[1]["status"] == "skipped"
    assert "LIMIT_EXCEEDED" in _codes(item)


def test_state_change_two_codes(tmp_path: Path) -> None:
    executor = ScriptedExecutor({})
    item_not_allowed, _ = _run(_one_step(state_change="expected"), executor, tmp_path / "r1" / "run_demo_001",
                               dec=_decision(allow_state_change=False))
    item_no_hook, _ = _run(_one_step(state_change="expected"), ScriptedExecutor({}), tmp_path / "r2" / "run_demo_001",
                           dec=_decision(allow_state_change=True))
    assert _codes(item_not_allowed) == ["STATE_CHANGE_NOT_ALLOWED"]
    assert _codes(item_no_hook) == ["STATE_RESET_UNAVAILABLE"]
    assert executor.send_calls == 0


def test_body_ref_not_sent(tmp_path: Path) -> None:
    body_ref = {"evidence_id": "e", "kind": "request", "path": "p", "sha256": "0" * 64, "redacted": True}
    executor = ScriptedExecutor({})
    item, _ = _run(_one_step(body_ref=body_ref), executor, tmp_path / "run_demo_001")
    assert _codes(item) == ["BODY_REF_UNAVAILABLE"]
    assert executor.send_calls == 0


def test_session_invalid_is_indeterminate_not_failure(tmp_path: Path) -> None:
    # (d) 세션 무효 → indeterminate, failure 아님.
    executor = ScriptedExecutor({}, invalid_accounts=("acc_user",))
    item, _ = _run(_one_step(), executor, tmp_path / "run_demo_001")
    assert item["result"] == "indeterminate"
    assert _codes(item) == ["SESSION_INVALID"]
    assert executor.send_calls == 0


def test_transport_error_is_indeterminate_not_failure(tmp_path: Path) -> None:
    # (d) 통신 오류 → indeterminate(≠failure). 전송은 시도됨.
    executor = ScriptedExecutor({("GET", f"{ORIGIN}/orders/7"): transport_error()})
    item, _ = _run(_one_step(), executor, tmp_path / "run_demo_001")
    assert item["result"] == "indeterminate"
    assert item["execution_status"] == "error"
    assert _codes(item) == ["TRANSPORT_ERROR"]
    assert executor.send_calls == 1


def test_session_expired_mid_execution_is_indeterminate(tmp_path: Path) -> None:
    executor = ScriptedExecutor({("GET", f"{ORIGIN}/orders/7"): SessionExpiredError("expired")})
    item, _ = _run(_one_step(), executor, tmp_path / "run_demo_001")
    assert item["result"] == "indeterminate"
    assert _codes(item) == ["SESSION_EXPIRED"]


def test_execution_status_rule_is_independent_of_result() -> None:
    # execution_status는 전송 수로만 정한다(result 분류 step4가 바뀌어도 이 규칙은 그대로여야 한다).
    err = {"code": "X", "message": "x", "item_ref": None, "retryable": False}
    assert execution_status_for(ExecutionOutcome([], {}, send_count=0, terminal_error=None)) == "completed"
    assert execution_status_for(ExecutionOutcome([], {}, send_count=3, terminal_error=None)) == "completed"
    assert execution_status_for(ExecutionOutcome([], {}, send_count=0, terminal_error=err)) == "not_executed"
    assert execution_status_for(ExecutionOutcome([], {}, send_count=1, terminal_error=err)) == "error"


def test_lease_released(tmp_path: Path) -> None:
    executor = ScriptedExecutor({("GET", f"{ORIGIN}/orders/7"): ReplayResponse(200, body={})})
    _run(_one_step(), executor, tmp_path / "run_demo_001")
    assert executor.release_calls == executor.lease_calls == 1
