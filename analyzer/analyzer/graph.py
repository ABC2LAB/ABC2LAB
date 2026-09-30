"""
그래프 조립
==========

CrawlRun을 Knowledge Graph의 씨앗 형태로 변환한다.
노드: Role, Page, Endpoint, Param
엣지:
  - OBSERVED   : Role -> Endpoint  (그 역할이 해당 엔드포인트를 호출, status/resource_ids 포함)
  - LINKS_TO   : Page -> Endpoint  (페이지 안에 그 엔드포인트로 가는 링크가 있음)
  - TRIGGERS   : Page -> Endpoint  (페이지 로드/액션이 fetch로 그 엔드포인트를 호출)
  - HAS_PARAM  : Endpoint -> Param

엔드포인트 노드는 (method, endpoint) 조합으로 유일하게 합쳐진다.
같은 엔드포인트를 여러 역할이 호출하면 OBSERVED 엣지가 역할 수만큼 생긴다.
이 "역할별 관측 상태" 가 뒤 단계 룰의 핵심 입력이다.
"""

from __future__ import annotations

from .models import CrawlRun, Graph, GraphNode, GraphEdge


def endpoint_key(method: str, endpoint: str) -> str:
    return f"endpoint:{method} {endpoint}"


def build_graph(run: CrawlRun) -> Graph:
    g = Graph()
    node_ids: set[str] = set()

    def add_node(nid: str, ntype: str, props: dict):
        if nid not in node_ids:
            node_ids.add(nid)
            g.nodes.append(GraphNode(id=nid, type=ntype, props=props))

    # Role 노드
    for role in run.roles:
        add_node(f"role:{role.role}", "Role", {"name": role.role})

    # Page 노드 + LINKS_TO 엣지
    for page in run.all_pages():
        add_node(page.id, "Page", {
            "role": page.role, "endpoint": page.endpoint,
            "title": page.title, "status": page.status, "depth": page.depth,
        })
        for link in page.links:
            if not link.endpoint:
                continue
            ep_id = endpoint_key("GET", link.endpoint)
            add_node(ep_id, "Endpoint", {"method": "GET", "endpoint": link.endpoint})
            g.edges.append(GraphEdge(
                src=page.id, dst=ep_id, relation="LINKS_TO",
                props={"text": link.text, "is_state_changing": link.is_state_changing,
                       "outcome": link.outcome},
            ))

    # Endpoint 노드 + OBSERVED 엣지 + HAS_PARAM + TRIGGERS
    for req in run.all_requests():
        ep_id = endpoint_key(req.method, req.endpoint)
        add_node(ep_id, "Endpoint", {
            "method": req.method, "endpoint": req.endpoint,
            "resource_type": req.resource_type,
        })
        # 역할 -> 엔드포인트 관측
        g.edges.append(GraphEdge(
            src=f"role:{req.role}", dst=ep_id, relation="OBSERVED",
            props={
                "status": req.status,
                "resource_type": req.resource_type,
                "resource_ids": req.resource_ids,
                "request_id": req.id,
            },
        ))
        # 파라미터 노드
        for p in list(req.query_params.keys()) + list(req.body_params.keys()):
            pid = f"param:{req.method} {req.endpoint}#{p}"
            add_node(pid, "Param", {"name": p, "endpoint": req.endpoint, "method": req.method})
            g.edges.append(GraphEdge(src=ep_id, dst=pid, relation="HAS_PARAM", props={}))
        # 페이지가 fetch로 트리거한 경우
        if req.resource_type in ("fetch", "xhr") and req.source_page_id:
            g.edges.append(GraphEdge(
                src=req.source_page_id, dst=ep_id, relation="TRIGGERS",
                props={"source_action": req.source_action},
            ))

    return g
