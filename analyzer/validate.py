"""출력 자체 검증 (명세 10.3). 계약 위반을 리스트로 돌려준다(비면 통과)."""
from __future__ import annotations

from analyzer.schemas import AnalysisResult, DerivedBy


def validate(result: AnalysisResult) -> list[str]:
    errors: list[str] = []
    node_ids = [n.id for n in result.nodes]
    id_set = set(node_ids)

    # 중복 Node id
    if len(node_ids) != len(id_set):
        seen, dup = set(), set()
        for nid in node_ids:
            (dup if nid in seen else seen).add(nid)
        for nid in dup:
            errors.append(f"중복 Node id → {nid}")

    # 관계: 양 끝 존재 + 중복 tuple
    seen_tuples = set()
    for r in result.relationships:
        if r.from_id not in id_set:
            errors.append(f"관계 from_id 미선언 → {r.from_id}")
        if r.to_id not in id_set:
            errors.append(f"관계 to_id 미선언 → {r.to_id}")
        key = (r.from_id, r.type, r.to_id)
        if key in seen_tuples:
            errors.append(f"중복 관계 → {key}")
        seen_tuples.add(key)
        if r.derived_by is DerivedBy.LLM and r.confidence is None:
            errors.append(f"llm 관계인데 confidence 없음 → {key}")
    return errors
