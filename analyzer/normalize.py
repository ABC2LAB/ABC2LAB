"""결정적 ID·정규화 (순수 함수). '같은 입력=같은 ID'를 이 한 곳에서 보장한다."""
from __future__ import annotations


def role_id(name: str) -> str:
    return f"role:{name}"


def page_id(endpoint: str) -> str:
    return f"page:{endpoint}"


def api_id(method: str, endpoint: str) -> str:
    return f"api:{method.upper()}:{endpoint}"


def param_id(method: str, endpoint: str, location: str, name: str) -> str:
    return f"param:{method.upper()}:{endpoint}:{location}:{name}"


def rel_id(from_id: str, rel_type: str, to_id: str) -> str:
    # 관계 type은 대문자로 (통합 명세 부록 A 기준)
    return f"rel:{from_id}:{rel_type.upper()}:{to_id}"
