"""세 계약 Schema를 우리 폴더 사본으로만 검증한다(다른 모듈 Schema를 읽지 않는다).

docs는 명세 의미대로 손으로 만든다. graph_query_result는 입력 사본이라 KG 현재 구현이 아니라 명세 모양을 담는다.
"""

import copy
import json

from modules.access_analyzer.tests.helpers import GRAPH_QUERY_FIXTURE, schema_errors

GRAPH_QUERY_SCHEMA = "output/graph_query.schema.json"
RESULT_SCHEMA = "input/graph_query_result.schema.json"
CANDIDATES_SCHEMA = "output/vulnerability_candidates.schema.json"

EVIDENCE = {
    "evidence_id": "ev1", "kind": "response",
    "path": "evidence/collector/response/1.json", "sha256": "0" * 64, "redacted": True,
}


def _result_envelope(status: str, errors: list, data) -> dict:
    return {
        "schema_version": "0.1.0", "artifact_type": "graph_query_result", "artifact_id": "r1",
        "run_id": "run_demo_001", "iteration": 0, "producer": "knowledge_graph", "mode": "development",
        "created_at": "2026-10-07T00:03:00Z", "status": status, "input_refs": [], "errors": errors,
        "runtime_metrics": None, "data": data,
    }


def _completed_result_data() -> dict:
    ownership = {"query_id": "q_own", "query_key": "resource_ownership", "status": "completed", "errors": [], "rows": [
        {"resource_id": "resource:item_1", "owner_account_id": "account_a", "basis": "inferred", "evidence_refs": [EVIDENCE]},
    ]}
    access = {"query_id": "q_acc", "query_key": "role_resource_access", "status": "completed", "errors": [], "rows": [
        {"account_id": "account_a", "role_id": "role_user", "endpoint_id": "endpoint:GET:/x/{id}",
         "resource_id": "resource:item_1", "action": "read", "access_observed": True, "evidence_refs": [EVIDENCE]},
        {"account_id": "account_b", "role_id": "role_user", "endpoint_id": "endpoint:GET:/y",
         "resource_id": None, "action": "list", "access_observed": False, "evidence_refs": []},
    ]}
    flow = {"query_id": "q_flow", "query_key": "workflow_dependencies", "status": "completed", "errors": [], "rows": [
        {"workflow_id": "workflow_a", "before_step_id": "s0", "after_step_id": "s1",
         "condition": "observed_sequence", "basis": "inferred", "evidence_refs": []},
    ]}
    return {"graph_id": "graph_demo_001", "graph_revision": 1, "results": [ownership, access, flow]}


def _candidate() -> dict:
    return {
        "candidate_id": "cand_1", "category": "authorization", "vulnerability_type": "horizontal_access",
        "rule_id": "rule_same_role_other_owner", "actor_account_id": "account_a", "actor_role_id": "role_user",
        "reference_account_id": "account_b", "resource_ids": ["resource:item_1"], "source_request_ids": ["req_1"],
        "workflow_id": None, "hypothesis": "X가 Y 소유 자원에 접근 가능할 수 있다",
        "expected_behavior": "소유자 아닌 접근은 거절되어야 한다", "expected_basis": "inferred", "evidence_refs": [EVIDENCE],
    }


def _candidates_envelope(candidates: list) -> dict:
    return {
        "schema_version": "0.1.0", "artifact_type": "vulnerability_candidates", "artifact_id": "c1",
        "run_id": "run_demo_001", "iteration": 0, "producer": "access_analyzer", "mode": "development",
        "created_at": "2026-10-07T00:04:00Z", "status": "completed", "input_refs": [], "errors": [],
        "runtime_metrics": None, "data": {"source_graph_revision": 1, "candidates": candidates, "model_info": None},
    }


# --- graph_query 출력 ---

def test_graph_query_fixture_valid() -> None:
    document = json.loads(GRAPH_QUERY_FIXTURE.read_bytes())
    assert schema_errors(GRAPH_QUERY_SCHEMA, document) == []


def test_graph_query_rejects_undefined_key() -> None:
    document = json.loads(GRAPH_QUERY_FIXTURE.read_bytes())
    document["data"]["surprise"] = "x"
    assert schema_errors(GRAPH_QUERY_SCHEMA, document)


# --- graph_query_result 입력(명세 모양) ---

def test_result_completed_all_row_types_valid() -> None:
    assert schema_errors(RESULT_SCHEMA, _result_envelope("completed", [], _completed_result_data())) == []


def test_result_partial_valid() -> None:
    err = {"code": "QUERY_EXECUTION_FAILED", "message": "x", "item_ref": "q_acc", "retryable": True}
    data = {"graph_id": "graph_demo_001", "graph_revision": 2, "results": [
        {"query_id": "q_own", "query_key": "resource_ownership", "status": "completed", "errors": [], "rows": []},
        {"query_id": "q_acc", "query_key": "role_resource_access", "status": "failed", "errors": [err], "rows": []},
    ]}
    assert schema_errors(RESULT_SCHEMA, _result_envelope("partial", [err], data)) == []


def test_result_failed_data_null_valid() -> None:
    err = {"code": "GRAPH_REVISION_MISMATCH", "message": "x", "item_ref": None, "retryable": False}
    assert schema_errors(RESULT_SCHEMA, _result_envelope("failed", [err], None)) == []


def test_result_completed_with_errors_rejected() -> None:
    err = {"code": "X", "message": "x", "item_ref": None, "retryable": False}
    assert schema_errors(RESULT_SCHEMA, _result_envelope("completed", [err], _completed_result_data()))


# --- vulnerability_candidates 출력 ---

def test_candidate_valid() -> None:
    assert schema_errors(CANDIDATES_SCHEMA, _candidates_envelope([_candidate()])) == []


def test_candidate_empty_source_request_ids_rejected() -> None:
    bad = copy.deepcopy(_candidate())
    bad["source_request_ids"] = []
    assert schema_errors(CANDIDATES_SCHEMA, _candidates_envelope([bad]))


def test_candidate_empty_resource_ids_rejected() -> None:
    bad = copy.deepcopy(_candidate())
    bad["resource_ids"] = []
    assert schema_errors(CANDIDATES_SCHEMA, _candidates_envelope([bad]))


def test_candidate_empty_reference_account_id_rejected() -> None:
    bad = copy.deepcopy(_candidate())
    bad["reference_account_id"] = ""
    assert schema_errors(CANDIDATES_SCHEMA, _candidates_envelope([bad]))


def test_candidate_null_reference_and_workflow_allowed() -> None:
    ok = copy.deepcopy(_candidate())
    ok["reference_account_id"] = None
    ok["workflow_id"] = None
    assert schema_errors(CANDIDATES_SCHEMA, _candidates_envelope([ok])) == []


def test_candidate_undefined_key_rejected() -> None:
    bad = copy.deepcopy(_candidate())
    bad["surprise"] = "x"
    assert schema_errors(CANDIDATES_SCHEMA, _candidates_envelope([bad]))
