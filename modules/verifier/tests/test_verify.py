"""verify 안전 게이트 end-to-end. 핵심: allow만 통과시키고 어떤 게이트 실패에서도 요청을 보내지 않는다.

각 테스트는 '이 게이트를 빼면 통과해 버리는' 버그 하나를 겨눈다. 세션 창구 대역 send 호출 수로 '요청 0건'을 고정한다.
"""

import hashlib
import json
from pathlib import Path

from modules.verifier import entrypoint as ep
from modules.verifier.tests.helpers import (
    CountingExecutor,
    account,
    copy_committed_triple,
    decision,
    make_context,
    output_dir_for,
    scenario,
    schema_errors,
    step,
    write_triple,
)

OUTPUT_SCHEMA = "output/verification_results.schema.json"


def _run(run_root: Path, input_paths):
    executor = CountingExecutor()
    result = ep.run("verify", input_paths, output_dir_for(run_root), make_context(run_root), session_executor=executor)
    return result, executor


def _published(run_root: Path) -> dict:
    return json.loads((output_dir_for(run_root) / "verification_results.json").read_bytes())


def _item(doc: dict, scenario_id: str) -> dict:
    return next(it for it in doc["data"]["results"] if it["scenario_id"] == scenario_id)


# ── 정상 게이트(커밋 fixture) ──

def test_committed_triple_gate(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    paths = copy_committed_triple(run_root)
    result, executor = _run(run_root, paths)
    doc = _published(run_root)
    assert result["status"] == "completed"
    allow = _item(doc, "scenario_allow")
    assert (allow["result"], allow["execution_status"]) == ("indeterminate", "not_executed")
    assert [e["code"] for e in allow["errors"]] == ["EXECUTION_PENDING"]
    assert allow["steps"] == []
    block = _item(doc, "scenario_block")
    assert (block["policy_decision"], block["result"], block["execution_status"]) == ("require_approval", "blocked", "not_executed")
    assert block["steps"] == []
    assert schema_errors(OUTPUT_SCHEMA, doc) == []
    assert executor.send_calls == 0
    assert ep._exit_code(result) == ep.EXIT_COMPLETED


def test_committed_triple_sha_matches_file() -> None:
    # fixture 결합이 깨지면(해시 불일치) 게이트가 막아 게이트 테스트가 의미를 잃으므로 fixture 자체를 지킨다.
    from modules.verifier.tests.helpers import FIXTURE_RUN
    scen = (FIXTURE_RUN / "scenario_generator/test_scenarios.json").read_bytes()
    dec = json.loads((FIXTURE_RUN / "safety_policy/safety_decisions.json").read_bytes())
    assert dec["data"]["scenarios_sha256"] == hashlib.sha256(scen).hexdigest()


# ── 판정 분기 ──

def test_allow_passes_gate_but_defers_execution(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    paths = write_triple(
        run_root, [scenario("s1", "c1", [step("st1", "acc_user", "role_user", "sess_user")])],
        [decision("d1", "s1", "allow", ["acc_user"])], [account()],
    )
    result, executor = _run(run_root, paths)
    item = _item(_published(run_root), "s1")
    assert (item["result"], item["execution_status"]) == ("indeterminate", "not_executed")
    assert item["errors"][0]["code"] == "EXECUTION_PENDING"
    assert executor.send_calls == 0


def test_block_not_executed(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    paths = write_triple(
        run_root, [scenario("s1", "c1", [step("st1", "acc_user", "role_user", "sess_user")])],
        [decision("d1", "s1", "block", [])], [account()],
    )
    result, executor = _run(run_root, paths)
    item = _item(_published(run_root), "s1")
    assert (item["result"], item["execution_status"]) == ("blocked", "not_executed")
    assert item["steps"] == []
    assert executor.send_calls == 0


# ── 게이트 실패(요청 0건) ──

def test_hash_mismatch_fails_no_candidates(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    paths = write_triple(
        run_root, [scenario("s1", "c1", [step("st1", "acc_user", "role_user", "sess_user")])],
        [decision("d1", "s1", "allow", ["acc_user"])], [account()], sha_override="0" * 64,
    )
    result, executor = _run(run_root, paths)
    assert result["status"] == "failed"
    assert _published(run_root)["data"] is None
    assert result["errors"][0]["code"] == "SCENARIOS_HASH_MISMATCH"
    assert executor.send_calls == 0


def test_account_scope_mismatch(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    paths = write_triple(
        run_root, [scenario("s1", "c1", [step("st1", "acc_user", "role_user", "sess_user")])],
        [decision("d1", "s1", "allow", ["acc_other"])], [account()],
    )
    result, executor = _run(run_root, paths)
    item = _item(_published(run_root), "s1")
    assert (item["result"], item["execution_status"]) == ("indeterminate", "not_executed")
    assert item["errors"][0]["code"] == "ACCOUNT_SCOPE_MISMATCH"
    assert executor.send_calls == 0


def test_role_mismatch(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    paths = write_triple(
        run_root, [scenario("s1", "c1", [step("st1", "acc_user", "role_admin", "sess_user")])],
        [decision("d1", "s1", "allow", ["acc_user"])], [account(role_id="role_user")],
    )
    result, executor = _run(run_root, paths)
    item = _item(_published(run_root), "s1")
    assert item["errors"][0]["code"] == "ROLE_MISMATCH"
    assert executor.send_calls == 0


def test_session_mismatch(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    paths = write_triple(
        run_root, [scenario("s1", "c1", [step("st1", "acc_user", "role_user", "wrong_session")])],
        [decision("d1", "s1", "allow", ["acc_user"])], [account(session_ref="sess_user")],
    )
    result, executor = _run(run_root, paths)
    item = _item(_published(run_root), "s1")
    assert item["errors"][0]["code"] == "SESSION_INVALID"
    assert executor.send_calls == 0


def test_policy_decision_missing_partial(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    # 시나리오는 둘인데 판정은 하나뿐 → s2는 판정 누락.
    paths = write_triple(
        run_root,
        [scenario("s1", "c1", [step("st1", "acc_user", "role_user", "sess_user")]),
         scenario("s2", "c2", [step("st2", "acc_user", "role_user", "sess_user")])],
        [decision("d1", "s1", "allow", ["acc_user"])], [account()],
    )
    result, executor = _run(run_root, paths)
    doc = _published(run_root)
    assert result["status"] == "partial"
    missing = [e for e in doc["errors"] if e["code"] == "POLICY_DECISION_MISSING"]
    assert missing and missing[0]["item_ref"] == "s2"
    # 판정 없는 s2는 results에 없고(계약상 policy_decision 필수), 숨기지 않고 errors로 추적.
    assert {it["scenario_id"] for it in doc["data"]["results"]} == {"s1"}
    assert executor.send_calls == 0


# ── 경계: 파일 유무·종료코드 ──

def test_unsupported_operation_writes_failed_file(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    paths = copy_committed_triple(run_root)
    result = ep.run("reproduce", paths, output_dir_for(run_root), make_context(run_root))
    assert result["status"] == "failed"
    assert result["artifact_path"] is not None
    assert _published(run_root)["data"] is None
    assert result["errors"][0]["code"] == "OPERATION_UNSUPPORTED"
    assert ep._exit_code(result) == ep.EXIT_FAILED_WITH_FILE


def test_undefined_context_key_writes_no_file(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    paths = copy_committed_triple(run_root)
    context = make_context(run_root, surprise="x")
    result = ep.run("verify", paths, output_dir_for(run_root), context)
    assert result["artifact_path"] is None
    assert result["errors"][0]["code"] == "CONTEXT_INVALID"
    assert ep._exit_code(result) == ep.EXIT_NO_FILE


def test_too_few_inputs_failed_file(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    copy_committed_triple(run_root)
    two = [v for v in [
        "artifacts/iteration-000/scenario_generator/test_scenarios.json",
        "artifacts/iteration-000/safety_policy/safety_decisions.json",
    ]]
    result = ep.run("verify", two, output_dir_for(run_root), make_context(run_root))
    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "INPUT_MISSING"


def test_input_contract_invalid_failed_file(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    paths = copy_committed_triple(run_root)
    crawl = run_root / "artifacts/iteration-000/collector/crawl_result.json"
    document = json.loads(crawl.read_bytes())
    document["data"]["surprise"] = "x"  # 미정의 키
    crawl.write_text(json.dumps(document), encoding="utf-8")
    result = ep.run("verify", paths, output_dir_for(run_root), make_context(run_root))
    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "INPUT_CONTRACT_INVALID"


def test_republish_rejected(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    paths = copy_committed_triple(run_root)
    first, _ = _run(run_root, paths)
    assert first["status"] == "completed"
    second = ep.run("verify", paths, output_dir_for(run_root), make_context(run_root))
    assert second["artifact_path"] is None
    assert second["errors"][0]["code"] == "ARTIFACT_EXISTS"
