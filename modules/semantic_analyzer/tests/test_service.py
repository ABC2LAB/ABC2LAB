import json
from pathlib import Path

import pytest

from modules.semantic_analyzer.llm.adapter import FakeClient
from modules.semantic_analyzer.service import analyze_data

_FIXTURE = (
    Path(__file__).parent
    / "fixtures/runs/run_demo_001/artifacts/iteration-000/collector/crawl_result.json"
)


@pytest.fixture
def crawl_data() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))["data"]


@pytest.fixture
def result(crawl_data) -> dict:
    return analyze_data(crawl_data, FakeClient())


def _nodes_by_type(result: dict, node_type: str) -> list[dict]:
    return [n for n in result["nodes"] if n["node_type"] == node_type]


def _edges_by_type(result: dict, relation_type: str) -> list[dict]:
    return [e for e in result["relationships"] if e["relation_type"] == relation_type]


def test_one_normalized_request_per_input_request(result, crawl_data):
    assert len(result["normalized_requests"]) == len(crawl_data["requests"])


def test_original_ids_preserved(result):
    by_id = {r["request_id"]: r for r in result["normalized_requests"]}
    alice_order = by_id["req_order_detail_alice"]
    assert alice_order["account_id"] == "acc_alice"
    assert alice_order["role_id"] == "role_user"
    assert alice_order["path_template"] == "/orders/{id}"


def test_endpoint_dedup_across_same_template(result):
    # alice/bob 둘 다 /orders/{id}를 호출 → Endpoint 노드는 하나로 합쳐진다.
    order_endpoints = [
        n for n in _nodes_by_type(result, "Endpoint")
        if n["properties"]["path_template"] == "/orders/{id}"
    ]
    assert len(order_endpoints) == 1


def test_sensitive_parameter_value_type_is_null(result):
    login = next(r for r in result["normalized_requests"] if r["request_id"] == "req_login_alice")
    password = next(p for p in login["parameters"] if p["name"] == "password")
    assert password["is_sensitive"] is True
    assert password["value_type"] == "null"


def test_structural_edges_are_observed(result):
    for relation in ("HAS_ROLE", "ACCESS", "CALL", "USE"):
        edges = _edges_by_type(result, relation)
        assert edges, f"{relation} 관계가 없음"
        assert all(e["basis"] == "observed" for e in edges)


def test_access_edges_cover_role_and_account(result):
    # ACCESS는 역할 단위(집계)와 계정 단위 둘 다 만든다. KG의 계정→Endpoint 질의용.
    sources = {e["source_id"] for e in _edges_by_type(result, "ACCESS")}
    assert any(s.startswith("role:") for s in sources), "역할 단위 ACCESS가 없음"
    assert "user:acc_alice" in sources, "계정 단위 ACCESS가 없음"


def test_resource_nodes_and_owns_are_inferred(result):
    resources = _nodes_by_type(result, "Resource")
    # 계약 0.2: Resource properties는 resource_key·resource_scope·match_key
    assert any(n["properties"]["resource_key"] == "order" for n in resources)
    assert all(n["basis"] == "inferred" for n in resources)
    assert all(n["properties"]["resource_scope"] in ("type", "instance") for n in resources)
    owns = _edges_by_type(result, "OWNS")
    # alice·bob이 각자 경로 id로 주문에 접근 → 둘 다 order 인스턴스 자원 OWNS(추론)
    owners = {e["source_id"] for e in owns}
    assert "user:acc_alice" in owners and "user:acc_bob" in owners
    assert all(e["basis"] == "inferred" for e in owns)


def test_instance_resource_has_match_key(result):
    # 경로 id로 접근한 자원은 instance 범위 + match_key(식별자) 를 가진다(IDOR 판정 재료).
    instances = [n for n in _nodes_by_type(result, "Resource")
                 if n["properties"]["resource_scope"] == "instance"]
    assert instances, "instance 범위 Resource가 없음"
    mk = instances[0]["properties"]["match_key"]
    assert mk is not None and mk["identifiers"] and "key" in mk["identifiers"][0]


def test_action_meaning_from_fake(result):
    order = next(r for r in result["normalized_requests"] if r["request_id"] == "req_order_detail_alice")
    assert order["action_meaning"] == "read_order"


def test_workflows_one_per_account_with_ordered_steps(result):
    workflows = {w["workflow_id"]: w for w in result["workflows"]}
    assert set(workflows) == {"workflow:acc_alice", "workflow:acc_bob", "workflow:acc_admin"}
    alice = workflows["workflow:acc_alice"]
    orders = [s["order"] for s in alice["steps"]]
    assert orders == sorted(orders) == list(range(len(orders)))
    assert alice["basis"] == "inferred"


def test_fake_llm_reports_no_model_info(result):
    assert result["model_info"] is None
