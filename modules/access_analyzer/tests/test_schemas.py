"""세 계약 Schema를 우리 폴더 사본으로만 검증한다(다른 모듈 Schema를 읽지 않는다).

docs는 명세 의미대로 손으로 만든다. graph_query_result는 입력 사본이라 KG 현재 구현이 아니라 명세 모양을 담는다.
"""

import copy
import json

import pytest

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
        "schema_version": "0.2.0", "artifact_type": "graph_query_result", "artifact_id": "r1",
        "run_id": "run_demo_001", "iteration": 0, "producer": "knowledge_graph", "mode": "development",
        "created_at": "2026-10-07T00:03:00Z", "status": status, "input_refs": [], "errors": errors,
        "runtime_metrics": None, "data": data,
    }


ITEM_1_MATCH_KEY = {"resource_key": "item", "identifiers": [{"key": "item_id", "value": "1"}]}


def _completed_result_data() -> dict:
    """KG 0.2.0의 행 종류를 다 담는다: instance 소유, instance·type·자원 없음 접근, Resource type·instance와 비Resource 노드."""
    ownership = {"query_id": "q_own", "query_key": "resource_ownership", "status": "completed", "errors": [], "rows": [
        {"resource_id": "resource:item_1", "resource_key": "item", "resource_scope": "instance", "match_key": ITEM_1_MATCH_KEY,
         "owner_account_id": "account_a", "basis": "inferred", "evidence_refs": [EVIDENCE]},
    ]}
    access = {"query_id": "q_acc", "query_key": "role_resource_access", "status": "completed", "errors": [], "rows": [
        {"account_id": "account_a", "role_id": "role_user", "endpoint_id": "endpoint:GET:/x/{id}",
         "resource_id": "resource:item_1", "resource_key": "item", "resource_scope": "instance", "match_key": ITEM_1_MATCH_KEY,
         "action": "read", "request_ids": ["req_a"], "access_observed": True, "evidence_refs": [EVIDENCE]},
        {"account_id": "account_a", "role_id": "role_user", "endpoint_id": "endpoint:GET:/x",
         "resource_id": "resource:item", "resource_key": "item", "resource_scope": "type", "match_key": None,
         "action": "list", "request_ids": ["req_a_list"], "access_observed": True, "evidence_refs": []},
        {"account_id": "account_b", "role_id": "role_user", "endpoint_id": "endpoint:GET:/y",
         "resource_id": None, "resource_key": None, "resource_scope": None, "match_key": None,
         "action": "list", "request_ids": ["req_b"], "access_observed": False, "evidence_refs": []},
    ]}
    flow = {"query_id": "q_flow", "query_key": "workflow_dependencies", "status": "completed", "errors": [], "rows": [
        {"workflow_id": "workflow_a", "before_step_id": "s0", "after_step_id": "s1",
         "condition": "observed_sequence", "basis": "inferred", "evidence_refs": []},
    ]}
    snapshot = {"query_id": "q_snap", "query_key": "structure_snapshot", "status": "completed", "errors": [], "rows": [
        {"nodes": [
            {"node_id": "resource:item", "node_type": "Resource",
             "properties": {"resource_key": "item", "resource_scope": "type", "match_key": None},
             "basis": "inferred", "evidence_refs": []},
            {"node_id": "resource:item_1", "node_type": "Resource",
             "properties": {"resource_key": "item", "resource_scope": "instance", "match_key": ITEM_1_MATCH_KEY, "label": "x"},
             "basis": "inferred", "evidence_refs": []},
            # Resource 규칙은 Resource 노드에만 걸린다. 다른 노드 properties는 자유 형식.
            {"node_id": "user:account_a", "node_type": "User", "properties": {"account_id": "account_a"},
             "basis": "observed", "evidence_refs": []},
        ], "relationships": [], "workflows": []},
    ]}
    return {"graph_id": "graph_demo_001", "graph_revision": 1, "results": [ownership, access, flow, snapshot]}


def _ownership_row(data: dict) -> dict:
    return data["results"][0]["rows"][0]


def _access_row(data: dict, index: int) -> dict:
    return data["results"][1]["rows"][index]


def _resource_node(data: dict, index: int) -> dict:
    return data["results"][3]["rows"][0]["nodes"][index]


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


def test_result_access_row_requires_request_ids() -> None:
    # #32에서 AccessRow에 request_ids가 required로 추가됐다. 미러링이 빠지면 이 테스트가 red.
    data = _completed_result_data()
    access_rows = data["results"][1]["rows"]
    del access_rows[0]["request_ids"]
    assert schema_errors(RESULT_SCHEMA, _result_envelope("completed", [], data))


def test_result_access_row_rejects_empty_request_ids() -> None:
    data = _completed_result_data()
    data["results"][1]["rows"][0]["request_ids"] = []
    assert schema_errors(RESULT_SCHEMA, _result_envelope("completed", [], data))


def test_result_rejects_legacy_version() -> None:
    # #41로 0.2.0이 됐다. 0.1.0 산출물은 묵시 변환 없이 거절한다(KG README). const를 안 바꾼 미러면 red.
    document = _result_envelope("completed", [], _completed_result_data())
    document["schema_version"] = "0.1.0"
    assert schema_errors(RESULT_SCHEMA, document)


def _set_ownership_scope_type(data: dict) -> None:
    # match_key는 그대로 둬서 scope 규칙 하나만 어긴다.
    _ownership_row(data)["resource_scope"] = "type"


def _drop_ownership_match_key(data: dict) -> None:
    _ownership_row(data)["match_key"] = None


def _drop_instance_access_match_key(data: dict) -> None:
    _access_row(data, 0)["match_key"] = None


def _add_type_access_match_key(data: dict) -> None:
    _access_row(data, 1)["match_key"] = ITEM_1_MATCH_KEY


def _add_resource_key_without_resource(data: dict) -> None:
    _access_row(data, 2)["resource_key"] = "item"


def _drop_scope_with_resource(data: dict) -> None:
    _access_row(data, 1)["resource_scope"] = None


def _drop_resource_node_scope(data: dict) -> None:
    del _resource_node(data, 0)["properties"]["resource_scope"]


def _empty_match_key_identifiers(data: dict) -> None:
    _ownership_row(data)["match_key"] = {"resource_key": "item", "identifiers": []}


@pytest.mark.parametrize(
    "break_contract",
    [
        # 소유 행은 instance만(const). enum으로 느슨하게 미러링하면 type 자원이 "주인 있는 자원"으로 들어온다.
        _set_ownership_scope_type,
        _drop_ownership_match_key,
        # KG test_contracts와 같은 거절 예시: instance인데 match_key 없음.
        _drop_instance_access_match_key,
        _add_type_access_match_key,
        # resource_id가 null이면 key·scope·match_key도 모두 null.
        _add_resource_key_without_resource,
        # resource_id가 있으면 scope도 있어야 한다(else 절).
        _drop_scope_with_resource,
        # structure_snapshot의 Resource 노드 properties 규칙.
        _drop_resource_node_scope,
        _empty_match_key_identifiers,
    ],
    ids=lambda break_contract: break_contract.__name__,
)
def test_result_rejects_broken_resource_identity(break_contract) -> None:
    data = _completed_result_data()
    break_contract(data)
    assert schema_errors(RESULT_SCHEMA, _result_envelope("completed", [], data))


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
