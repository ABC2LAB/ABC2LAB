"""결정적 ID 생성 (순수 함수). '같은 입력 = 같은 ID'를 이 한 곳에서 보장한다.

다른 모듈과 ID 문자열을 맞추려는 목적이 아니다. 이 모듈 안에서 노드·관계를 중복 없이
모으고, reporter 평가가 문자열 일치가 아니라 정규화 키로 매칭하므로(ID 자유) 여기 규칙은 내부 안정성용이다.
"""
from __future__ import annotations


def user_node_id(account_id: str) -> str:
    return f"user:{account_id}"


def role_node_id(role_id: str) -> str:
    return f"role:{role_id}"


def page_node_id(page_id: str) -> str:
    return f"page:{page_id}"


def action_node_id(action_id: str) -> str:
    return f"action:{action_id}"


def endpoint_node_id(method: str, path_template: str) -> str:
    return f"endpoint:{method}:{path_template}"


def parameter_node_id(endpoint_id: str, location: str, name: str) -> str:
    return f"param:{endpoint_id}:{location}:{name}"


def resource_node_id(resource_key: str) -> str:
    """type 범위 자원 노드(특정 인스턴스가 아닌 자원 종류)."""
    return f"resource:{resource_key}"


def resource_instance_node_id(resource_key: str, identifier_values: tuple[str, ...]) -> str:
    """instance 범위 자원 노드. 인스턴스 식별 값으로 구분한다(예: resource:order:001)."""
    return f"resource:{resource_key}:{':'.join(identifier_values)}"


def relationship_id(source_id: str, relation_type: str, target_id: str) -> str:
    return f"rel:{source_id}|{relation_type}|{target_id}"
