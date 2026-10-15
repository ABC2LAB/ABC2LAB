"""후보 근거(EvidenceRef) 병합. 규칙마다 같은 방식으로 쓴다."""

from typing import Any


def merge_evidence(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> None:
    """evidence_id 기준 중복 없이 이어 붙인다(순서 보존)."""
    seen = {item["evidence_id"] for item in existing}
    for item in incoming:
        if item["evidence_id"] not in seen:
            existing.append(item)
            seen.add(item["evidence_id"])
