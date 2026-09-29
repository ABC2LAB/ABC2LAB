"""
kg 모듈 (재상 담당): 크롤러 세션(JSON) -> KG 후보.

'확장'의 의미: 크롤러가 준 role 별 pages/requests 를, 그래프의
노드(Role/Endpoint/Resource/Page)와 엣지(ACCESSED/VISITED/INSTANCE_OF/
LINKS_TO/EXPOSES)로 펼친다. 판단(접근통제 위반 추론)은 analyzer 담당이므로
여기서는 '누가 무엇을 어떤 status 로 접근했나'를 사실 그대로, 근거와 함께 기록한다.
"""
from __future__ import annotations

from kg.schemas import (
    CrawlResult,
    EdgeCandidate,
    Evidence,
    KGCandidates,
    NodeCandidate,
    NodeLabel,
    RelType,
    RoleResult,
)


def _role_id(role: str) -> str:
    return f"role:{role}"


def _endpoint_id(method: str, endpoint: str) -> str:
    return f"ep:{method.upper()}:{endpoint}"


def _resource_id(endpoint: str, rid: str) -> str:
    return f"res:{endpoint}#{rid}"


def _page_id(endpoint: str) -> str:
    return f"page:{endpoint}"


class _Graph:
    """노드는 id 로 중복 제거, 엣지는 (src,type,tgt) 로 중복 제거하며 근거를 모은다."""
    def __init__(self) -> None:
        self.nodes: dict[str, NodeCandidate] = {}
        self.edges: dict[tuple, EdgeCandidate] = {}

    def node(self, id_: str, label: NodeLabel, props: dict, ev: Evidence) -> None:
        n = self.nodes.get(id_)
        if n is None:
            self.nodes[id_] = NodeCandidate(id=id_, label=label, properties=props, evidence=[ev])
        elif ev not in n.evidence:
            n.evidence.append(ev)

    def edge(self, s: str, t: str, rtype: RelType, props: dict, ev: Evidence) -> None:
        key = (s, rtype.value, t)
        e = self.edges.get(key)
        if e is None:
            self.edges[key] = EdgeCandidate(source=s, target=t, type=rtype,
                                            properties=props, evidence=[ev])
        elif ev not in e.evidence:
            e.evidence.append(ev)


def _add_role(g: _Graph, rc: RoleResult, base_url: str) -> None:
    role = rc.role
    rev = Evidence(source_url=base_url, locator="crawl.role", evidence_id=rc.id,
                   role=role, confidence=1.0)
    g.node(_role_id(role), NodeLabel.ROLE, {"role": role}, rev)

    # 요청을 발생시킨 페이지를 되짚기 위한 맵. 크롤 레코드 id 가 우선, 없으면 url
    ep_by_page_id = {p.id: p.endpoint for p in rc.pages}
    ep_by_url = {p.url: p.endpoint for p in rc.pages}

    # ── 페이지 ──
    for p in rc.pages:
        pev = Evidence(source_url=p.url, locator="discovered_page", evidence_id=p.id,
                       role=role, snippet=f"{p.title} ({p.status})", confidence=0.9)
        g.node(_page_id(p.endpoint), NodeLabel.PAGE,
               {"endpoint": p.endpoint, "title": p.title, "sample_url": p.url}, pev)
        g.edge(_role_id(role), _page_id(p.endpoint), RelType.VISITED,
               {"status": p.status, "depth": p.depth}, pev)
        for ln in p.links:
            # 목적지를 모르는 링크(javascript: 등)는 그래프로 이을 수 없다
            if ln.endpoint is None:
                continue
            lev = Evidence(source_url=p.url, locator="link", evidence_id=p.id, role=role,
                           snippet=(ln.text or "")[:100], confidence=0.7)
            g.node(_page_id(ln.endpoint), NodeLabel.PAGE, {"endpoint": ln.endpoint}, lev)
            g.edge(_page_id(p.endpoint), _page_id(ln.endpoint), RelType.LINKS_TO,
                   {"text": ln.text, "is_state_changing": ln.is_state_changing,
                    "outcome": ln.outcome}, lev)

    # ── 요청 ──
    for r in rc.requests:
        ep_id = _endpoint_id(r.method, r.endpoint)
        rev2 = Evidence(source_url=r.url, locator="captured_request", evidence_id=r.id,
                        role=role, snippet=f"{r.method} {r.status}", confidence=0.9)
        g.node(ep_id, NodeLabel.ENDPOINT,
               {"method": r.method.upper(), "template": r.endpoint,
                "resource_type": r.resource_type}, rev2)

        # 요청을 발생시킨 페이지 -> 엔드포인트 (EXPOSES)
        src_ep = ep_by_page_id.get(r.source_page_id or "") or ep_by_url.get(r.source_page or "")
        if src_ep:
            g.edge(_page_id(src_ep), ep_id, RelType.EXPOSES,
                   {"method": r.method.upper(), "status": r.status,
                    "source_action": r.source_action or ""}, rev2)

        if r.resource_ids:
            for rid in r.resource_ids:
                res_id = _resource_id(r.endpoint, rid)
                g.node(res_id, NodeLabel.RESOURCE,
                       {"endpoint": r.endpoint, "resource_id": rid}, rev2)
                g.edge(res_id, ep_id, RelType.INSTANCE_OF, {}, rev2)
                g.edge(_role_id(role), res_id, RelType.ACCESSED,
                       {"method": r.method.upper(), "status": r.status,
                        "captured_at": r.captured_at}, rev2)
        else:
            g.edge(_role_id(role), ep_id, RelType.ACCESSED,
                   {"method": r.method.upper(), "status": r.status,
                    "captured_at": r.captured_at}, rev2)


def build_kg(crawl: CrawlResult) -> KGCandidates:
    """크롤 결과(crawl_result.json)를 근거가 달린 KG 후보로 펼친다."""
    g = _Graph()
    for rc in crawl.roles:
        _add_role(g, rc, crawl.target_base_url)
    return KGCandidates(
        target_base_url=crawl.target_base_url,
        crawl_run_id=crawl.run_id,
        nodes=list(g.nodes.values()),
        edges=list(g.edges.values()),
        meta={"role_count": len(crawl.roles),
              "node_count": len(g.nodes), "edge_count": len(g.edges)},
    )
