"""
관찰 그래프 (LLM 없음). CrawlResult의 사실만으로 노드·관계·evidence를 만든다.
Website는 KG Builder(③)가 만들므로 여기서 만들지 않는다.
"""
from __future__ import annotations

from common.schemas import (
    ApiOperationNode, CrawlResult, Evidence, Nodes, PageNode, ParameterNode,
    Relationship, RelType, RoleNode, Source,
)
from analyzer.normalize import api_id, page_id, param_id, rel_id, role_id


def build_observed(crawl: CrawlResult) -> tuple[Nodes, list[Relationship], list[Evidence]]:
    nodes = Nodes()
    rels: dict[tuple, Relationship] = {}
    evid: dict[str, Evidence] = {}
    seen = {"role": set(), "page": set(), "api": set(), "param": set()}

    def add_ev(eid: str, stype: str, role: str | None = None) -> str:
        evid.setdefault(eid, Evidence(id=eid, source_type=stype, source_ref=eid, role=role))
        return eid

    def add_rel(frm: str, t: RelType, to: str, ev: str) -> None:
        key = (frm, t.value, to)
        r = rels.get(key)
        if r is None:
            rels[key] = Relationship(id=rel_id(frm, t.value, to), type=t, from_id=frm,
                                     to_id=to, source=Source.OBSERVED, evidence_ids=[ev])
        elif ev not in r.evidence_ids:
            r.evidence_ids.append(ev)

    for rc in crawl.roles:
        rid = role_id(rc.role)
        rev = add_ev(rc.id or rid, "RoleContext", rc.role)
        if rc.role not in seen["role"]:
            seen["role"].add(rc.role)
            nodes.roles.append(RoleNode(id=rid, name=rc.role, evidence_ids=[rev]))

        # crawl 페이지 id → 정규화 page 노드 id (요청을 페이지에 잇기 위해)
        page_by_crawlid = {p.id: page_id(p.endpoint) for p in rc.pages}

        for p in rc.pages:
            pid = page_id(p.endpoint)
            pev = add_ev(p.id, "DiscoveredPage", rc.role)
            if pid not in seen["page"]:
                seen["page"].add(pid)
                nodes.pages.append(PageNode(id=pid, name=p.title or "", path_pattern=p.endpoint,
                                            observed_urls=[p.url] if p.url else [], evidence_ids=[pev]))
            add_rel(rid, RelType.CAN_ACCESS, pid, pev)

        for r in rc.requests:
            aid = api_id(r.method, r.endpoint)
            qev = add_ev(r.id, "CapturedRequest", rc.role)
            if aid not in seen["api"]:
                seen["api"].add(aid)
                nodes.api_operations.append(ApiOperationNode(id=aid, method=r.method.upper(),
                                                             endpoint=r.endpoint, evidence_ids=[qev]))
            add_rel(rid, RelType.CAN_CALL, aid, qev)

            # 요청을 발생시킨 페이지 → API (CALLS)
            src_pid = page_by_crawlid.get(r.source_page_id or "")
            if src_pid:
                add_rel(src_pid, RelType.CALLS, aid, qev)

            # 쿼리 파라미터 → Parameter + ACCEPTS
            for qname in r.query_params:
                prm = param_id(r.method, r.endpoint, "query", qname)
                if prm not in seen["param"]:
                    seen["param"].add(prm)
                    nodes.parameters.append(ParameterNode(id=prm, name=qname, location="query",
                                                          evidence_ids=[qev]))
                add_rel(aid, RelType.ACCEPTS, prm, qev)

    return nodes, list(rels.values()), list(evid.values())
