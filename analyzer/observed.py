"""
관찰(rule) 단계: crawl_result 의 요청에서 ApiOperation·Parameter 노드와
HAS_PARAMETER 관계를 만든다. LLM 없음. derived_by="rule".

명세대로 canonical ID 를 쓰고, evidence_ids 에는 request:N 을 그대로 넣는다.
Page/Website 노드는 V1 canonical ID 가 미정이므로 여기서 만들지 않는다
(명세 예시 B.2 와 동일하게 ApiOperation·Parameter·Resource 에 집중).
"""
from __future__ import annotations

from analyzer.normalize import api_id, param_id
from analyzer.schemas import CrawlResult, DerivedBy, Node, NodeType, Relationship


def build_observed(crawl: CrawlResult) -> tuple[list[Node], list[Relationship]]:
    nodes: dict[str, Node] = {}
    rels: dict[tuple, Relationship] = {}

    def add_evidence(node: Node, ev: str) -> None:
        if ev not in node.evidence_ids:
            node.evidence_ids.append(ev)

    for role in crawl.roles:
        for req in role.requests:
            aid = api_id(req.method, req.endpoint)
            ev = req.id  # 예: request:31
            # ── ApiOperation 노드 ──
            api = nodes.get(aid)
            if api is None:
                api = Node(
                    id=aid, type=NodeType.API_OPERATION,
                    name=f"{req.method.upper()} {req.endpoint}",
                    derived_by=DerivedBy.RULE, evidence_ids=[ev],
                    properties={"method": req.method.upper(), "endpoint": req.endpoint},
                )
                nodes[aid] = api
            else:
                add_evidence(api, ev)

            # ── Parameter 노드 + HAS_PARAMETER (query, body) ──
            for location, params in (("query", req.query_params), ("body", req.body_params)):
                for pname in params:
                    pid = param_id(aid, location, pname)
                    p = nodes.get(pid)
                    if p is None:
                        nodes[pid] = Node(
                            id=pid, type=NodeType.PARAMETER, name=pname,
                            derived_by=DerivedBy.RULE, evidence_ids=[ev],
                            properties={"location": location, "parameter_path": pname,
                                        "data_type": "string"},
                        )
                    else:
                        add_evidence(p, ev)
                    key = (aid, "HAS_PARAMETER", pid)
                    r = rels.get(key)
                    if r is None:
                        rels[key] = Relationship(
                            from_id=aid, type="HAS_PARAMETER", to_id=pid,
                            derived_by=DerivedBy.RULE, evidence_ids=[ev])
                    elif ev not in r.evidence_ids:
                        r.evidence_ids.append(ev)

    return list(nodes.values()), list(rels.values())
