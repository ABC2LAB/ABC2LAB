import copy
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


def test_structural_edges_from_observed_requests_are_observed(result):
    # 관찰 요청에서 나온 구조 관계는 observed다. HAS_ROLE·ACCESS는 관찰 요청에서만 생긴다.
    for relation in ("HAS_ROLE", "ACCESS"):
        edges = _edges_by_type(result, relation)
        assert edges, f"{relation} 관계가 없음"
        assert all(e["basis"] == "observed" for e in edges)
    # CALL·USE는 관찰 요청(observed)과 discovered_endpoints(inferred)에서 모두 생길 수 있어, 관찰분 존재만 본다.
    for relation in ("CALL", "USE"):
        edges = _edges_by_type(result, relation)
        assert any(e["basis"] == "observed" for e in edges), f"관찰 {relation}가 없음"


def test_discovered_endpoint_adds_inferred_nodes_and_call(result):
    # discovered_endpoints는 요청하지 않고 발견만 한 것이라 Endpoint·Parameter·CALL을 inferred로 만든다.
    endpoint = next(n for n in _nodes_by_type(result, "Endpoint")
                    if n["properties"] == {"method": "POST", "path_template": "/items/{id}"})
    assert endpoint["basis"] == "inferred"
    endpoint_id = endpoint["node_id"]
    # Parameter 노드는 inferred, 값은 못 봤으니 value_type=unknown
    params = [n for n in _nodes_by_type(result, "Parameter")
              if n["node_id"].startswith(f"param:{endpoint_id}:")]
    assert params and all(p["basis"] == "inferred" and p["properties"]["value_type"] == "unknown" for p in params)
    # discovered_from의 Page·Action → Endpoint CALL은 inferred
    calls_in = [e for e in _edges_by_type(result, "CALL") if e["target_id"] == endpoint_id]
    assert {e["source_id"] for e in calls_in} == {"page:page_products", "action:act_add_cart"}
    assert all(e["basis"] == "inferred" for e in calls_in)
    # 접근을 관찰한 게 아니므로 ACCESS도 normalized_requests 항목도 없다
    assert not any(e["target_id"] == endpoint_id for e in _edges_by_type(result, "ACCESS"))
    assert not any(r["endpoint_id"] == endpoint_id for r in result["normalized_requests"])


def test_access_edges_cover_role_and_account(result):
    # ACCESS는 역할 단위(집계)와 계정 단위 둘 다 만든다. KG의 계정→Endpoint 질의용.
    sources = {e["source_id"] for e in _edges_by_type(result, "ACCESS")}
    assert any(s.startswith("role:") for s in sources), "역할 단위 ACCESS가 없음"
    assert "user:acc_alice" in sources, "계정 단위 ACCESS가 없음"


def test_resources_are_inferred_and_owns_is_observed(result):
    resources = _nodes_by_type(result, "Resource")
    # 계약 0.2: Resource properties는 resource_key·resource_scope·match_key. 자원 노드 자체는 LLM 추론.
    assert any(n["properties"]["resource_key"] == "order" for n in resources)
    assert all(n["basis"] == "inferred" for n in resources)
    assert all(n["properties"]["resource_scope"] in ("type", "instance") for n in resources)
    owns = _edges_by_type(result, "OWNS")
    # OWNS는 관찰 근거로만: alice·bob이 각자 자기 주문을 경로 식별자(/orders/{id})로 2xx 접근 → basis=observed.
    # alice 주문 응답엔 본문 id가 없어도(HTML류, fixture에서 identifiers=null) 경로 식별자+2xx로 소유를 세운다.
    assert owns and all(e["basis"] == "observed" for e in owns)
    assert {e["source_id"] for e in owns} == {"user:acc_alice", "user:acc_bob"}
    # 공개(type) 자원엔 OWNS가 붙지 않는다(C1·C4 과대귀속 방지).
    type_resource_ids = {n["node_id"] for n in resources if n["properties"]["resource_scope"] == "type"}
    assert not any(e["target_id"] in type_resource_ids for e in owns)
    # 경로 식별자 없는 body-id 인스턴스(cart:product_id)는 단일 접근·2xx여도 OWNS에서 제외한다.
    cart_instance_ids = {n["node_id"] for n in resources
                         if n["properties"]["resource_scope"] == "instance"
                         and n["properties"]["resource_key"] == "cart"}
    assert cart_instance_ids, "fixture의 /cart/add body product_id로 생긴 cart 인스턴스가 있어야 한다"
    assert not any(e["target_id"] in cart_instance_ids for e in owns)


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


def test_workflow_step_action_is_canonical_not_llm(result):
    # workflow step action은 결정적 토큰(canonical_action)이다. normalized_requests.action_meaning(LLM)과는 분리.
    # GET /products는 canonical "view_product"지만 FakeClient action_meaning은 "read_product" → 둘이 달라 전환을 확인한다.
    products = next(r for r in result["normalized_requests"] if r["request_id"] == "req_products_alice")
    assert products["action_meaning"] == "read_product"  # LLM 결과는 그대로
    alice = next(w for w in result["workflows"] if w["workflow_id"] == "workflow:acc_alice")
    step = next(s for s in alice["steps"] if "req_products_alice" in s["request_ids"])
    assert step["action"] == "view_product"  # workflow step은 규칙 토큰


def test_workflows_one_per_account_with_ordered_steps(result):
    workflows = {w["workflow_id"]: w for w in result["workflows"]}
    assert set(workflows) == {"workflow:acc_alice", "workflow:acc_bob", "workflow:acc_admin"}
    alice = workflows["workflow:acc_alice"]
    orders = [s["order"] for s in alice["steps"]]
    assert orders == sorted(orders) == list(range(len(orders)))
    assert alice["basis"] == "inferred"


def test_fake_llm_reports_no_model_info(result):
    assert result["model_info"] is None


class _RecordingClient(FakeClient):
    """FakeClient 결과는 그대로 쓰되, 넘어온 RequestFeatures를 기록한다(네트워크 0)."""

    def __init__(self) -> None:
        self.seen: list = []

    def infer_request_meaning(self, features):
        self.seen.append(features)
        return super().infer_request_meaning(features)


def test_cookie_header_and_sensitive_params_excluded_from_llm_features(crawl_data):
    # 쿠키·헤더·민감 파라미터는 LLM 의미 추론 입력(parameter_names)에서 빠진다(자원 오인 방지).
    # 단, 구조 그래프의 Parameter 노드에는 관찰 사실로 남는다.
    data = copy.deepcopy(crawl_data)
    target = next(r for r in data["requests"] if r["request_id"] == "req_order_detail_alice")
    target["parameters"].extend([
        {"name": "session", "location": "cookie", "value": None, "is_sensitive": True},
        {"name": "x-csrf", "location": "header", "value": "t", "is_sensitive": False},
        {"name": "token", "location": "query", "value": "s", "is_sensitive": True},
    ])
    client = _RecordingClient()
    result = analyze_data(data, client)
    features = next(f for f in client.seen if f.path_template == "/orders/{id}")
    # 의미 입력엔 비민감 path/query/body만: order_id는 남고 cookie/header/민감은 빠진다.
    assert "order_id" in features.parameter_names
    assert "session" not in features.parameter_names  # cookie
    assert "x-csrf" not in features.parameter_names   # header
    assert "token" not in features.parameter_names    # 민감 query
    # 구조 그래프엔 쿠키 파라미터가 관찰 노드로 남는다.
    param_names = {n["properties"]["name"] for n in _nodes_by_type(result, "Parameter")}
    assert "session" in param_names
