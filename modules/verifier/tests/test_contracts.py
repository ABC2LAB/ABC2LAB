"""입력 사본 Schema와 출력 검증(특히 result×execution_status·policy_decision↔result·graph_updates)을 고정한다.

우리 폴더 Schema·검증기만 쓴다. 각 음성 케이스는 '이 검사를 빼면 통과해 버리는' 버그 하나를 겨눈다.
"""

import copy
import hashlib
import json
from pathlib import Path

from modules.verifier.tests.helpers import FIXTURE_RUN, schema_errors
from modules.verifier.utils import validation as v

RESULT_SCHEMA = "output/verification_results.schema.json"
# verify를 자기 데모 입력으로 실제 실행(세션 창구 없음)해 만든 생산자 샘플. 2단계에서 소비자가 입력 사본 검증에 쓴다.
VERIFICATION_RESULTS_FIXTURE = FIXTURE_RUN / "verifier" / "verification_results.json"
EVIDENCE = {"evidence_id": "ev1", "kind": "execution_log", "path": "evidence/verifier/x.json", "sha256": "0" * 64, "redacted": True}


def _item(result: str, execution_status: str, policy_decision: str = "allow", steps=None, errors=None, vid: str = "v1") -> dict:
    return {
        "verification_id": vid, "candidate_id": "c1", "scenario_id": "s1", "decision_id": "d1",
        "policy_decision": policy_decision, "execution_status": execution_status, "result": result,
        "reason": "x", "steps": steps or [], "evidence_refs": [], "errors": errors or [],
    }


def _doc(results, graph_updates=None) -> dict:
    return {
        "schema_version": "0.2.0", "artifact_type": "verification_results", "artifact_id": "r1",
        "run_id": "run_demo_001", "iteration": 0, "producer": "verifier", "mode": "development",
        "created_at": "2026-10-07T00:00:00Z", "status": "completed", "input_refs": [], "errors": [],
        "runtime_metrics": None,
        "data": {
            "source_graph_revision": 1, "scenarios_sha256": "0" * 64, "results": results,
            "graph_updates": graph_updates or {"source_verification_ids": [], "nodes": [], "relationships": []},
        },
    }


def _codes(doc) -> set[str]:
    return {i.code.value for i in v.validate_verification_results_bytes(json.dumps(doc).encode())}


# ── 입력 사본 Schema (생산자 fixture 수용) ──

def test_input_copies_accept_producer_fixtures() -> None:
    assert v.validate_test_scenarios_file(FIXTURE_RUN / "scenario_generator/test_scenarios.json") == []
    assert v.validate_safety_decisions_file(FIXTURE_RUN / "safety_policy/safety_decisions.json") == []
    assert v.validate_crawl_result_file(FIXTURE_RUN / "collector/crawl_result.json") == []


def test_crawl_result_requires_page_account_id() -> None:
    # Page.account_id는 collector가 추가한 필드(미러링). 빠지면 거절되어야 한다.
    document = json.loads((FIXTURE_RUN / "collector/crawl_result.json").read_bytes())
    document["data"]["pages"] = [{"page_id": "p1", "url": "u", "title": None, "evidence_refs": []}]  # account_id 없음
    assert schema_errors("input/crawl_result.schema.json", document)


# ── 출력 검증: pairing ──

def test_valid_results_pass() -> None:
    assert v.validate_verification_results_bytes(json.dumps(_doc([
        _item("indeterminate", "not_executed", vid="v1"),
        _item("blocked", "not_executed", policy_decision="block", vid="v2"),
    ])).encode()) == []


def test_block_decision_with_success_rejected() -> None:
    assert "RESULT_STATUS_INVALID" in _codes(_doc([_item("success", "completed", policy_decision="block")]))


def test_allow_with_blocked_rejected() -> None:
    assert "RESULT_STATUS_INVALID" in _codes(_doc([_item("blocked", "not_executed", policy_decision="allow")]))


def test_success_with_not_executed_rejected() -> None:
    assert "RESULT_STATUS_INVALID" in _codes(_doc([_item("success", "not_executed")]))


def test_blocked_with_steps_rejected() -> None:
    step = {"step_id": "s", "status": "skipped", "request_url": None, "response_status": None,
            "request_ref": None, "response_ref": None, "check_results": [], "evidence_refs": [], "errors": []}
    assert "RESULT_STATUS_INVALID" in _codes(_doc([_item("blocked", "not_executed", policy_decision="block", steps=[step])]))


def test_duplicate_verification_id_rejected() -> None:
    doc = _doc([_item("indeterminate", "not_executed"), _item("indeterminate", "not_executed")])
    assert "DUPLICATE_ID" in _codes(doc)


# ── 출력 검증: graph_updates 근거 ──

def test_graph_update_source_must_be_success() -> None:
    # source_verification_id가 성공 결과가 아니면 거절.
    node = {"node_id": "user:a", "node_type": "User", "properties": {}, "basis": "verified", "evidence_refs": [EVIDENCE]}
    doc = _doc([_item("failure", "completed")], graph_updates={"source_verification_ids": ["v1"], "nodes": [node], "relationships": []})
    assert "GRAPH_UPDATE_INVALID" in _codes(doc)


def test_graph_update_non_verified_basis_rejected() -> None:
    # 스키마가 basis=verified를 강제한다(m7).
    node = {"node_id": "user:a", "node_type": "User", "properties": {}, "basis": "observed", "evidence_refs": [EVIDENCE]}
    doc = _doc([_item("success", "completed")], graph_updates={"source_verification_ids": ["v1"], "nodes": [node], "relationships": []})
    assert "SCHEMA_INVALID" in _codes(doc)


def test_graph_update_requires_source_when_present() -> None:
    node = {"node_id": "user:a", "node_type": "User", "properties": {}, "basis": "verified", "evidence_refs": [EVIDENCE]}
    doc = _doc([_item("success", "completed")], graph_updates={"source_verification_ids": [], "nodes": [node], "relationships": []})
    assert "GRAPH_UPDATE_INVALID" in _codes(doc)


def test_producer_sample_validates_against_output_schema() -> None:
    # 커밋된 생산자 샘플(실제 verify 출력, 세션 창구 없음)이 자기 출력 스키마(0.2.0)를 통과하는지 고정한다.
    doc = json.loads(VERIFICATION_RESULTS_FIXTURE.read_bytes())
    assert doc["schema_version"] == "0.2.0"
    assert doc["run_id"] == "run_demo_001"
    # 데모 입력 2개 시나리오가 다 들어간다(세션 창구 없어 allow는 미실행 indeterminate, block은 blocked).
    assert {it["scenario_id"] for it in doc["data"]["results"]} == {"scenario_allow", "scenario_block"}
    assert schema_errors(RESULT_SCHEMA, doc) == []


def test_producer_sample_input_refs_resolve_in_run_tree() -> None:
    # 2단계 소비자가 이 run 트리로 검증할 수 있게: input_ref가 상대경로이고 실제 파일·해시가 맞는지 고정.
    run_root = FIXTURE_RUN.parents[1]
    doc = json.loads(VERIFICATION_RESULTS_FIXTURE.read_bytes())
    assert len(doc["input_refs"]) == 3
    for ref in doc["input_refs"]:
        assert not ref["path"].startswith("/")
        target = run_root / ref["path"]
        assert target.is_file()
        assert hashlib.sha256(target.read_bytes()).hexdigest() == ref["sha256"]
