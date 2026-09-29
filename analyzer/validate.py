"""넘기기 전 자체 검증. 계약 위반을 잡아 리스트로 돌려준다(비면 통과)."""
from __future__ import annotations

from common.schemas import AnalysisResult, Source


def validate(result: AnalysisResult) -> list[str]:
    errors: list[str] = []
    n = result.nodes
    node_ids = {x.id for group in (n.roles, n.pages, n.api_operations, n.parameters,
                                   n.resources, n.features, n.business_flows, n.flow_steps)
                for x in group}
    ev_ids = {e.id for e in result.evidence}

    for r in result.relationships:
        if r.from_id not in node_ids:
            errors.append(f"관계 {r.id}: from_id 미선언 → {r.from_id}")
        if r.to_id not in node_ids:
            errors.append(f"관계 {r.id}: to_id 미선언 → {r.to_id}")
        if r.source is Source.LLM and r.confidence is None:
            errors.append(f"관계 {r.id}: llm_inferred인데 confidence 없음")

    # evidence_ids 참조 무결성
    for group in (n.roles, n.pages, n.api_operations, n.parameters, n.resources):
        for node in group:
            for eid in node.evidence_ids:
                if eid not in ev_ids:
                    errors.append(f"노드 {node.id}: 없는 evidence 참조 → {eid}")
    return errors
