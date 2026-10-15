"""semantic_analyzer 핵심: crawl_result data → semantic_analysis data.

경계: 정규화와 구조 그래프(노드·관계)는 규칙으로 만든다(basis=observed).
      자원·행위 의미와 업무 흐름은 LLM(이번엔 fake)으로 추론한다(basis=inferred).
원본 request_id·account_id·role_id는 절대 새 ID로 바꾸지 않는다.
"""
from __future__ import annotations

import re
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
HTTP_OK_MIN = 200
HTTP_OK_MAX = 300
# query·body 파라미터를 인스턴스 식별자로 볼 기준(규칙 2 identifiers와 같음): 이름이 id형 + 값이 숫자/UUID.
_ID_NAME_PATTERN = re.compile(r"(?:^id$|_id$|Id$)")
_UUID_PATTERN = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
# LLM 의미 추론에 힌트로 넘길 파라미터 위치. 쿠키·헤더는 자원 힌트가 아니라 LLM이 session 등을 자원으로 오인시킨다.
_MEANING_PARAM_LOCATIONS = frozenset({"path", "query", "body"})


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
    # 관찰 요청을 먼저 처리한 뒤, 요청하지 않고 발견만 한 엔드포인트를 추론 노드로 얹는다.
    _add_discovered_endpoints(graph, crawl_data.get("discovered_endpoints", []), page_ids, action_ids)
    # 접근이 아니라 관찰 근거로 OWNS를 세운다(모든 요청을 본 뒤에 단일 접근·경로 식별자+2xx를 따진다).
    _add_ownership(graph, crawl_data["requests"], normalized_requests)
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
    # 역할 단위(집계)와 계정 단위 접근을 둘 다 남긴다.
    # KG가 계정→Endpoint 접근을 조회하므로(role_resource_access) 계정 엣지도 있어야 비지 않는다.
    graph.add_edge(ids.role_node_id(request["role_id"]), "ACCESS", endpoint_id, {},
                   BASIS_OBSERVED, evidence)
    graph.add_edge(ids.user_node_id(request["account_id"]), "ACCESS", endpoint_id, {},
                   BASIS_OBSERVED, evidence)
    _link_source(graph, request, endpoint_id, page_ids, action_ids, evidence)
    parameters = _process_parameters(graph, endpoint_id, request["parameters"], evidence)

    # ── 의미(추론, LLM) ──
    # 쿠키·헤더·민감 파라미터는 의미 힌트가 아니라 LLM이 자원(resource:session 등)으로 오인하므로 제외한다.
    # 구조 그래프의 Parameter 노드·USE·normalized_requests에는 그대로 남는다(관찰 사실).
    meaning = client.infer_request_meaning(RequestFeatures(
        method=method, path_template=path_template,
        parameter_names=tuple(
            p["name"] for p in request["parameters"]
            if not p["is_sensitive"] and p["location"] in _MEANING_PARAM_LOCATIONS)))
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


def _add_discovered_endpoints(graph: _Graph, discovered_endpoints: list[dict],
                              page_ids: set[str], action_ids: set[str]) -> None:
    """화면에서 발견했지만 요청은 보내지 않은 엔드포인트를 추론 노드로 얹는다.

    collector가 폼·JS·스펙에서 찾았을 뿐 호출하지 않았으므로(규칙 5·6) 전부 basis=inferred다.
    접근을 관찰한 게 아니라서 ACCESS 관계·normalized_requests는 만들지 않고, 값을 못 봤으니 value_type은 unknown이다.
    """
    for discovered in discovered_endpoints:
        method = normalize_method(discovered["method"])
        path_template = normalize_path_template(discovered["url_template"])
        endpoint_id = ids.endpoint_node_id(method, path_template)
        evidence = discovered.get("evidence_refs", [])
        graph.add_node(endpoint_id, "Endpoint",
                       {"method": method, "path_template": path_template}, BASIS_INFERRED, evidence)
        for parameter in discovered["parameters"]:
            parameter_id = ids.parameter_node_id(endpoint_id, parameter["location"], parameter["name"])
            graph.add_node(parameter_id, "Parameter",
                           {"name": parameter["name"], "location": parameter["location"],
                            "value_type": "unknown", "is_sensitive": parameter["is_sensitive"]},
                           BASIS_INFERRED, evidence)
            graph.add_edge(endpoint_id, "USE", parameter_id, {}, BASIS_INFERRED, evidence)
        for origin in discovered["discovered_from"]:
            if origin["page_id"] in page_ids:
                graph.add_edge(ids.page_node_id(origin["page_id"]), "CALL", endpoint_id, {},
                               BASIS_INFERRED, evidence)
            if origin["action_id"] in action_ids:
                graph.add_edge(ids.action_node_id(origin["action_id"]), "CALL", endpoint_id, {},
                               BASIS_INFERRED, evidence)


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


def _looks_like_id(name: str, value: object) -> bool:
    """query·body 파라미터가 자원 인스턴스 ID로 쓸 만한지(이름 id형 + 값 숫자/UUID). 규칙 2 identifiers와 같은 기준."""
    if not _ID_NAME_PATTERN.search(name):
        return False
    if isinstance(value, bool):
        return False
    if isinstance(value, int) and value >= 0:
        return True
    return isinstance(value, str) and (value.isdigit() or bool(_UUID_PATTERN.fullmatch(value)))


def _instance_identifiers(request: dict) -> list[dict]:
    """인스턴스 식별자 {key, value}를 만든다. path는 그대로, query·body는 id형(이름+값)만 쓴다.

    민감 파라미터(value=null)·빈 값은 제외한다. 값은 문자열로 맞춘다(ResourceIdentifier.value는 비어있지 않은 문자열).
    쓸 식별자가 없으면 type 범위로 떨어진다. (path 외 query·body의 자원 ID도 잡으려는 확장.)
    """
    identifiers: list[dict] = []
    for parameter in request["parameters"]:
        if parameter["is_sensitive"]:
            continue
        value = parameter["value"]
        if value is None or str(value) == "":
            continue
        location = parameter["location"]
        if location == "path" or (location in ("query", "body") and _looks_like_id(parameter["name"], value)):
            identifiers.append({"key": parameter["name"], "value": str(value)})
    return identifiers


def _apply_meaning(graph: _Graph, request: dict, endpoint_id: str, meaning, evidence: list[dict]) -> list[dict]:
    """추론한 자원을 Resource 노드·REFERENCE 관계로 얹는다(basis=inferred).

    Resource properties는 계약 0.2: resource_key·resource_scope(type|instance)·match_key.
    path·id형 query·body 식별자가 있으면 instance 범위(식별자 포함)로 만든다.
    소유(OWNS)는 여기서 접근만으로 만들지 않는다 — _add_ownership이 관찰 근거로 따로 세운다.
    """
    identifiers = _instance_identifiers(request)
    is_instance = bool(identifiers)
    identifier_values = tuple(item["value"] for item in identifiers)
    resource_node_ids: list[str] = []
    for resource_key in meaning.resource_keys:
        if is_instance:
            resource_id = ids.resource_instance_node_id(resource_key, identifier_values)
            properties = {
                "resource_key": resource_key,
                "resource_scope": "instance",
                "match_key": {"resource_key": resource_key, "identifiers": identifiers},
            }
        else:
            resource_id = ids.resource_node_id(resource_key)
            properties = {"resource_key": resource_key, "resource_scope": "type", "match_key": None}
        graph.add_node(resource_id, "Resource", properties, BASIS_INFERRED, evidence)
        graph.add_edge(endpoint_id, "REFERENCE", resource_id, {}, BASIS_INFERRED, evidence)
        resource_node_ids.append(resource_id)
    return resource_node_ids


def _has_path_identifier(request: dict) -> bool:
    """요청이 경로 식별자(인스턴스 주소)를 가졌는지: location=path·비민감·비어있지 않은 값."""
    for parameter in request["parameters"]:
        if parameter["location"] != "path" or parameter["is_sensitive"]:
            continue
        value = parameter["value"]
        if value is not None and str(value) != "":
            return True
    return False


def _access_is_2xx(request: dict) -> bool:
    """요청 응답이 2xx인지(접근이 허용·성공했는지)."""
    response = request.get("response") or {}
    status = response.get("status_code")
    return isinstance(status, int) and HTTP_OK_MIN <= status < HTTP_OK_MAX


def _add_ownership(graph: _Graph, requests: list[dict], normalized_requests: list[dict]) -> None:
    """관찰 근거로만 OWNS를 세운다(접근만으로 만들지 않는다).

    규칙: 인스턴스 자원을 접근한 계정이 **단 하나**이고(크롤러는 자기 레인만 도므로 단일=소유, 다계정=공개),
    그 계정이 자원을 **경로 식별자(/x/{id})로 지목해 2xx**로 받았을 때만 OWNS(basis=observed).
    경로 식별자(인스턴스 주소)가 핵심 근거라, 응답이 HTML이라 본문 id를 못 돌려줘도 소유를 세운다
    (JSON 본문 id는 더 강한 신호일 뿐 필수 아님). 공개 카탈로그처럼 여러 계정이 본 자원과
    경로 식별자 없는 body-id 자원은 제외해 과대귀속(접근=소유)을 막는다.
    """
    request_by_id = {request["request_id"]: request for request in requests}
    accessors_by_resource: dict[str, set[str]] = {}
    evidence_by_pair: dict[tuple[str, str], list[dict]] = {}
    for normalized in normalized_requests:
        account_id = normalized["account_id"]
        raw_request = request_by_id.get(normalized["request_id"], {})
        grants_ownership = _access_is_2xx(raw_request) and _has_path_identifier(raw_request)
        for resource_id in normalized["resource_ids"]:
            node = graph.nodes.get(resource_id)
            if node is None or node["properties"].get("resource_scope") != "instance":
                continue
            accessors_by_resource.setdefault(resource_id, set()).add(account_id)
            if grants_ownership:
                evidence_by_pair[(resource_id, account_id)] = normalized["evidence_refs"]
    for resource_id, accounts in accessors_by_resource.items():
        if len(accounts) != 1:
            continue
        account_id = next(iter(accounts))
        evidence = evidence_by_pair.get((resource_id, account_id))
        if evidence is None:
            continue
        graph.add_edge(ids.user_node_id(account_id), "OWNS", resource_id, {}, BASIS_OBSERVED, evidence)


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
