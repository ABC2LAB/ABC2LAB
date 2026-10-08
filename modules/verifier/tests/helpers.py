"""verifier 테스트 공용 도구. 커밋 테스트는 우리 폴더 Schema만 읽는다(명세: 타 모듈 Schema 참조 금지).

세션 창구는 CountingExecutor 대역으로 쓰고 send 호출 수를 센다(PR1은 0이어야 한다).
해시 결합(safety_decisions.scenarios_sha256 == test_scenarios 파일 해시)은 write_triple이 실제로 계산해 맞춘다.
"""

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from modules.verifier.executor import ReplayRequest, ReplayResponse, SessionTransportError

MODULE_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = MODULE_ROOT / "schemas"
FIXTURE_RUN = MODULE_ROOT / "tests/fixtures/runs/run_demo_001/artifacts/iteration-000"
RUN_ID = "run_demo_001"
INPUT_RELS = {
    "test_scenarios": "artifacts/iteration-000/scenario_generator/test_scenarios.json",
    "safety_decisions": "artifacts/iteration-000/safety_policy/safety_decisions.json",
    "crawl_result": "artifacts/iteration-000/collector/crawl_result.json",
}


class FakeClock:
    """주입 시계(초 단위, time.monotonic처럼). 실제 시간 대신 테스트가 진행시킨다."""

    def __init__(self) -> None:
        self.milliseconds = 0

    def __call__(self) -> float:
        return self.milliseconds / 1000

    def advance(self, milliseconds: int) -> None:
        self.milliseconds += milliseconds


class UnscriptedRequestError(AssertionError):
    """대역에 정하지 않은 (method, url)로 전송했다. 테스트의 URL 오타가 failure 판정으로 조용히 묻히지 않게 바로 터뜨린다.

    엔진은 창구 오류(SessionTransportError·SessionExpiredError)만 잡으므로 이 예외는 삼켜지지 않는다.
    """


class ScriptedExecutor:
    """프로그래머블 세션 창구 대역.

    (method, url)별 응답 또는 예외를 정하고, 무효 세션 계정을 지정하고, 실제로 보낸 요청을 모두 기록한다.
    정하지 않은 (method, url)은 UnscriptedRequestError로 터진다(404가 필요하면 명시적으로 지정).
    clock을 주면 send마다 latency_ms만큼 시계를 진행시킨다(max_duration 테스트용).
    """

    def __init__(
        self,
        responses: dict[tuple[str, str], ReplayResponse | Exception] | None = None,
        invalid_accounts: tuple[str, ...] = (),
        clock: FakeClock | None = None,
        latency_ms: int = 0,
    ) -> None:
        self.responses = dict(responses or {})
        self.invalid_accounts = set(invalid_accounts)
        self.clock = clock
        self.latency_ms = latency_ms
        self.sent: list[tuple[str, ReplayRequest]] = []
        self.lease_calls = 0
        self.release_calls = 0

    @property
    def send_calls(self) -> int:
        return len(self.sent)

    def sent_urls(self) -> list[str]:
        return [request.url for _, request in self.sent]

    def lease(self, account_id: str) -> "ScriptedLease":
        self.lease_calls += 1
        return ScriptedLease(self, account_id)


class ScriptedLease:
    def __init__(self, parent: ScriptedExecutor, account_id: str) -> None:
        self._parent = parent
        self._account_id = account_id

    def is_valid(self) -> bool:
        return self._account_id not in self._parent.invalid_accounts

    def send(self, request: ReplayRequest) -> ReplayResponse:
        # 보낸 시도는 응답·예외와 무관하게 먼저 기록한다(전송 0건 검사의 기준).
        self._parent.sent.append((self._account_id, request))
        if self._parent.clock is not None:
            self._parent.clock.advance(self._parent.latency_ms)
        key = (request.method, request.url)
        if key not in self._parent.responses:
            raise UnscriptedRequestError(f"대역에 정하지 않은 요청: {request.method} {request.url}")
        outcome = self._parent.responses[key]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def release(self) -> None:
        self._parent.release_calls += 1


def transport_error() -> SessionTransportError:
    return SessionTransportError("connection refused")


def schema_errors(relative_name: str, document: Any) -> list[str]:
    schema = json.loads((SCHEMA_DIR / relative_name).read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    return [f"{list(e.absolute_path)}: {e.message}" for e in validator.iter_errors(document)]


def make_context(run_root: Path, **overrides: Any) -> dict[str, Any]:
    context = {
        "run_id": run_root.name,
        "iteration": 0,
        "mode": "development",
        "run_root": str(run_root),
        "source_graph_revision": 1,
    }
    context.update(overrides)
    return context


def output_dir_for(run_root: Path) -> Path:
    return run_root / "artifacts" / "iteration-000" / "verifier"


def copy_committed_triple(run_root: Path) -> list[str]:
    """커밋된 정상 fixture 3종을 run_root로 복사하고 상대 경로를 돌려준다."""
    for rel in INPUT_RELS.values():
        target = run_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        source = FIXTURE_RUN / Path(rel).relative_to("artifacts/iteration-000")
        shutil.copyfile(source, target)
    return list(INPUT_RELS.values())


def _envelope(artifact_type: str, producer: str, data: Any, status: str = "completed") -> dict[str, Any]:
    return {
        "schema_version": "0.1.0", "artifact_type": artifact_type, "artifact_id": f"{artifact_type}_1",
        "run_id": RUN_ID, "iteration": 0, "producer": producer, "mode": "development",
        "created_at": "2026-10-07T00:00:00.000000Z", "status": status, "input_refs": [], "errors": [],
        "runtime_metrics": None, "data": data,
    }


def scenario(
    scenario_id: str, candidate_id: str, steps: list[dict[str, Any]],
    preconditions: list[dict[str, Any]] | None = None, assertions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "scenario_id": scenario_id, "candidate_id": candidate_id, "expected_basis": "inferred",
        "preconditions": preconditions or [], "steps": steps, "assertions": assertions or [],
    }


def check(check_id: str, kind: str, subject_ref: str, operator: str, expected: Any, selector: str | None = None) -> dict[str, Any]:
    return {"check_id": check_id, "kind": kind, "subject_ref": subject_ref, "selector": selector, "operator": operator, "expected": expected}


def step(
    step_id: str, account_id: str, role_id: str, session_ref: str | None,
    url_template: str = "http://127.0.0.1:8001/orders/7", order: int = 0,
) -> dict[str, Any]:
    # 기본 url_template은 치환자 없는 실행 가능한 GET이다. 바인딩이 필요한 테스트는 url_template을 직접 준다.
    return {
        "step_id": step_id, "order": order, "source_request_id": "req_x", "account_id": account_id,
        "role_id": role_id, "session_ref": session_ref,
        "request": {"method": "GET", "url_template": url_template, "parameters": [], "body_ref": None},
        "bindings": [], "state_change": "none",
    }


def decision(decision_id: str, scenario_id: str, verdict: str, effective_account_ids: list[str]) -> dict[str, Any]:
    item = {"status": "pass", "rule_id": "r", "reason": "ok"}
    return {
        "decision_id": decision_id, "scenario_id": scenario_id, "decision": verdict,
        "reason_codes": ["X"], "reason": "x",
        "assessment": {k: item for k in ["target_scope", "test_accounts", "request_budget", "state_change", "data_impact", "service_impact"]},
        "effective_origins": ["http://127.0.0.1:8001"], "effective_account_ids": effective_account_ids,
        "limits": {"max_requests": 4, "max_duration_ms": 10000, "allow_state_change": False}, "approval_ref": None,
    }


def account(account_id: str = "acc_user", role_id: str = "role_user", session_ref: str | None = "sess_user") -> dict[str, Any]:
    return {"account_id": account_id, "role_id": role_id, "alias": "user_a", "session_ref": session_ref}


def write_triple(
    run_root: Path,
    scenarios: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    accounts: list[dict[str, Any]],
    sha_override: str | None = None,
) -> list[str]:
    """test_scenarios를 쓰고 그 파일 해시를 safety_decisions.scenarios_sha256에 맞춘다(override면 일부러 틀린 해시)."""
    scen_doc = _envelope("test_scenarios", "scenario_generator", {"scenarios": scenarios, "model_info": None})
    scen_path = run_root / INPUT_RELS["test_scenarios"]
    scen_path.parent.mkdir(parents=True, exist_ok=True)
    scen_path.write_text(json.dumps(scen_doc, ensure_ascii=False), encoding="utf-8")
    sha = sha_override or hashlib.sha256(scen_path.read_bytes()).hexdigest()
    dec_doc = _envelope("safety_decisions", "safety_policy", {
        "policy_id": "p", "policy_version": "0.1.0", "scenarios_sha256": sha, "decisions": decisions,
    })
    dec_path = run_root / INPUT_RELS["safety_decisions"]
    dec_path.parent.mkdir(parents=True, exist_ok=True)
    dec_path.write_text(json.dumps(dec_doc, ensure_ascii=False), encoding="utf-8")
    crawl_doc = _envelope("crawl_result", "collector", {
        "target_url": "http://127.0.0.1:8001", "roles": [{"role_id": "role_user", "name": "user"}],
        "accounts": accounts, "pages": [], "actions": [], "requests": [],
    })
    crawl_path = run_root / INPUT_RELS["crawl_result"]
    crawl_path.parent.mkdir(parents=True, exist_ok=True)
    crawl_path.write_text(json.dumps(crawl_doc, ensure_ascii=False), encoding="utf-8")
    return list(INPUT_RELS.values())
