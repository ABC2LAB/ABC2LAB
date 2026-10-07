"""semantic_analyzer 핵심: crawl_result data → semantic_analysis data.

경계: 정규화와 구조 그래프(노드·관계)는 규칙으로 만든다(basis=observed).
      자원·행위 의미와 업무 흐름은 LLM(이번엔 fake)으로 추론한다(basis=inferred).
원본 request_id·account_id·role_id는 절대 새 ID로 바꾸지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from modules.semantic_analyzer.llm.adapter import LlmClient, RequestFeatures
from modules.semantic_analyzer.utils import ids
from modules.semantic_analyzer.utils.normalize import (
    json_value_type,
    normalize_method,
    normalize_path_template,
)

BASIS_OBSERVED = "observed"
BASIS_INFERRED = "inferred"


@dataclass
class _Graph:
    """노드는 node_id로, 관계는 relationship_id로 중복 제거하며 근거를 모은다."""
    nodes: dict[str, dict] = field(default_factory=dict)
    edges: dict[str, dict] = field(default_factory=dict)

    def add_node(self, node_id: str, node_type: str, properties: dict, basis: str,
                 evidence_refs: list[dict]) -> str:
        node = self.nodes.get(node_id)
        if node is None:
            self.nodes[node_id] = {
                "node_id": node_id,
                "node_type": node_type,
                "properties": properties,
                "basis": basis,
                "evidence_refs": list(evidence_refs),
            }
        else:
            _merge_evidence(node["evidence_refs"], evidence_refs)
        return node_id

    def add_edge(self, source_id: str, relation_type: str, target_id: str, properties: dict,
                 basis: str, evidence_refs: list[dict]) -> None:
        edge_id = ids.relationship_id(source_id, relation_type, target_id)
        edge = self.edges.get(edge_id)
        if edge is None:
            self.edges[edge_id] = {
                "relationship_id": edge_id,
                "source_id": source_id,
                "target_id": target_id,
                "relation_type": relation_type,
                "properties": properties,
                "basis": basis,
                "evidence_refs": list(evidence_refs),
            }
        else:
            _merge_evidence(edge["evidence_refs"], evidence_refs)


def analyze_data(crawl_data: dict, client: LlmClient) -> dict:
    """검증된 crawl_result.data를 semantic_analysis.data로 변환한다."""
    graph = _Graph()
    _add_roles(graph, crawl_data["roles"])
    _add_accounts(graph, crawl_data["accounts"])
    page_ids = _add_pages(graph, crawl_data["pages"])
    action_ids = _add_actions(graph, crawl_data["actions"])

    normalized_requests = [
        _process_request(graph, request, client, page_ids, action_ids)
        for request in crawl_data["requests"]
    ]
    workflows = _build_workflows(crawl_data["requests"], normalized_requests, crawl_data["accounts"])

    return {
        "normalized_requests": normalized_requests,
        "nodes": list(graph.nodes.values()),
        "relationships": list(graph.edges.values()),
        "workflows": workflows,
        "model_info": client.model_info(),
    }


def _add_roles(graph: _Graph, roles: list[dict]) -> None:
    for role in roles:
        graph.add_node(ids.role_node_id(role["role_id"]), "Role",
                       {"name": role["name"], "role_id": role["role_id"]}, BASIS_OBSERVED, [])


def _add_accounts(graph: _Graph, accounts: list[dict]) -> None:
    for account in accounts:
        user_id = graph.add_node(
            ids.user_node_id(account["account_id"]), "User",
            {"account_id": account["account_id"], "alias": account["alias"],
             "role_id": account["role_id"]},
            BASIS_OBSERVED, [])
        # User-HAS_ROLE->Role: 계정이 선언한 역할은 관찰 사실이다.
        graph.add_edge(user_id, "HAS_ROLE", ids.role_node_id(account["role_id"]), {},
                       BASIS_OBSERVED, [])


def _add_pages(graph: _Graph, pages: list[dict]) -> set[str]:
    seen: set[str] = set()
    for page in pages:
        node_id = graph.add_node(
            ids.page_node_id(page["page_id"]), "Page",
            {"url": page["url"], "title": page["title"], "page_id": page["page_id"]},
            BASIS_OBSERVED, page["evidence_refs"])
        seen.add(page["page_id"])
    return seen


def _add_actions(graph: _Graph, actions: list[dict]) -> set[str]:
    seen: set[str] = set()
    for action in actions:
        graph.add_node(
            ids.action_node_id(action["action_id"]), "Action",
            {"kind": action["kind"], "label": action["label"], "page_id": action["page_id"]},
            BASIS_OBSERVED, action["evidence_refs"])
        seen.add(action["action_id"])
    return seen


def _process_request(graph: _Graph, request: dict, client: LlmClient,
                     page_ids: set[str], action_ids: set[str]) -> dict:
    method = normalize_method(request["method"])
    path_template = normalize_path_template(request["url"])
    endpoint_id = ids.endpoint_node_id(method, path_template)
    evidence = request["evidence_refs"]

    # ── 구조(관찰) ──
    graph.add_node(endpoint_id, "Endpoint",
                   {"method": method, "path_template": path_template}, BASIS_OBSERVED, evidence)
    graph.add_edge(ids.role_node_id(request["role_id"]), "ACCESS", endpoint_id, {},
                   BASIS_OBSERVED, evidence)
    _link_source(graph, request, endpoint_id, page_ids, action_ids, evidence)
    parameters = _process_parameters(graph, endpoint_id, request["parameters"], evidence)

    # ── 의미(추론, LLM) ──
    meaning = client.infer_request_meaning(RequestFeatures(
        method=method, path_template=path_template,
        parameter_names=tuple(p["name"] for p in request["parameters"])))
    resource_node_ids = _apply_meaning(graph, request, endpoint_id, meaning, evidence)

    return {
        "request_id": request["request_id"],
        "endpoint_id": endpoint_id,
        "method": method,
        "path_template": path_template,
        "account_id": request["account_id"],
        "role_id": request["role_id"],
        "action_meaning": meaning.action_meaning,
        "resource_ids": resource_node_ids,
        "parameters": parameters,
        "basis": BASIS_OBSERVED,
        "evidence_refs": list(evidence),
    }


def _link_source(graph: _Graph, request: dict, endpoint_id: str,
                 page_ids: set[str], action_ids: set[str], evidence: list[dict]) -> None:
    """요청을 유발한 Page·Action → Endpoint (CALL, 관찰). 관찰 못 한 연결은 잇지 않는다."""
    page_id = request["page_id"]
    if page_id in page_ids:
        graph.add_edge(ids.page_node_id(page_id), "CALL", endpoint_id, {}, BASIS_OBSERVED, evidence)
    action_id = request["action_id"]
    if action_id in action_ids:
        graph.add_edge(ids.action_node_id(action_id), "CALL", endpoint_id, {}, BASIS_OBSERVED, evidence)


def _process_parameters(graph: _Graph, endpoint_id: str, parameters: list[dict],
                        evidence: list[dict]) -> list[dict]:
    definitions: list[dict] = []
    for parameter in parameters:
        parameter_id = ids.parameter_node_id(endpoint_id, parameter["location"], parameter["name"])
        value_type = "null" if parameter["is_sensitive"] else json_value_type(parameter["value"])
        graph.add_node(parameter_id, "Parameter",
                       {"name": parameter["name"], "location": parameter["location"],
                        "value_type": value_type, "is_sensitive": parameter["is_sensitive"]},
                       BASIS_OBSERVED, evidence)
        graph.add_edge(endpoint_id, "USE", parameter_id, {}, BASIS_OBSERVED, evidence)
        definitions.append({
            "parameter_id": parameter_id,
            "name": parameter["name"],
            "location": parameter["location"],
            "value_type": value_type,
            "is_sensitive": parameter["is_sensitive"],
        })
    return definitions


def _apply_meaning(graph: _Graph, request: dict, endpoint_id: str, meaning, evidence: list[dict]) -> list[dict]:
    """추론한 자원을 Resource 노드·REFERENCE/OWNS 관계로 얹는다(basis=inferred)."""
    has_path_id = any(p["location"] == "path" for p in request["parameters"])
    resource_node_ids: list[str] = []
    for resource_key in meaning.resource_keys:
        resource_id = ids.resource_node_id(resource_key)
        graph.add_node(resource_id, "Resource", {"name": resource_key}, BASIS_INFERRED, evidence)
        graph.add_edge(endpoint_id, "REFERENCE", resource_id, {}, BASIS_INFERRED, evidence)
        # 경로 id로 특정 인스턴스에 접근했으면 소유 관계를 추정한다(IDOR 분석의 재료). 추론이다.
        if has_path_id:
            graph.add_edge(ids.user_node_id(request["account_id"]), "OWNS", resource_id, {},
                           BASIS_INFERRED, evidence)
        resource_node_ids.append(resource_id)
    return resource_node_ids


def _build_workflows(requests: list[dict], normalized_requests: list[dict],
                     accounts: list[dict]) -> list[dict]:
    """계정별 관찰 요청 순서를 업무 흐름 후보로 추론한다(basis=inferred).

    관찰한 순서일 뿐 '반드시 지켜야 하는 업무 정책'으로 단정하지 않는다(명세 주의사항).
    """
    role_by_account = {a["account_id"]: a["role_id"] for a in accounts}
    meaning_by_request = {n["request_id"]: n["action_meaning"] for n in normalized_requests}
    evidence_by_request = {n["request_id"]: n["evidence_refs"] for n in normalized_requests}

    by_account: dict[str, list[dict]] = {}
    for request in requests:
        by_account.setdefault(request["account_id"], []).append(request)

    workflows: list[dict] = []
    for account_id in sorted(by_account):
        ordered = sorted(by_account[account_id], key=lambda r: (r["observed_at"], r["request_id"]))
        if not ordered:
            continue
        steps: list[dict] = []
        dependencies: list[dict] = []
        evidence_all: list[dict] = []
        for order, request in enumerate(ordered):
            request_id = request["request_id"]
            step_id = f"step:{account_id}:{order}"
            steps.append({
                "step_id": step_id,
                "order": order,
                "action": meaning_by_request.get(request_id, "access_resource"),
                "request_ids": [request_id],
            })
            _merge_evidence(evidence_all, evidence_by_request.get(request_id, []))
            if order > 0:
                dependencies.append({
                    "before_step_id": steps[order - 1]["step_id"],
                    "after_step_id": step_id,
                    "condition": "observed_sequence",
                    "basis": BASIS_INFERRED,
                    "evidence_refs": list(evidence_by_request.get(request_id, [])),
                })
        role_id = role_by_account.get(account_id)
        workflows.append({
            "workflow_id": f"workflow:{account_id}",
            "name": f"observed_flow_{account_id}",
            "role_ids": [role_id] if role_id else [],
            "steps": steps,
            "dependencies": dependencies,
            "basis": BASIS_INFERRED,
            "evidence_refs": evidence_all,
        })
    return workflows


def _merge_evidence(existing: list[dict], incoming: list[dict]) -> None:
    """evidence_id 기준으로 중복 없이 이어 붙인다(순서 보존)."""
    seen = {item["evidence_id"] for item in existing}
    for item in incoming:
        if item["evidence_id"] not in seen:
            existing.append(item)
            seen.add(item["evidence_id"])
