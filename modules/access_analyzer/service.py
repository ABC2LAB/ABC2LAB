"""access_analyzer 처리 로직. 이 PR은 prepare_queries만 구현한다(analyze는 다음 PR).

prepare_queries: KG에 보낼 읽기 전용 질의 계획(graph_query.json의 data)을 만든다. 허용된 네 개의
query_key(resource_ownership·role_resource_access·workflow_dependencies·structure_snapshot)를 각각 한 번씩
담는다. 지금은 run 범위 전체를 조회한다(필터 빈 배열). 실제 Cypher 실행·DB 연결은 knowledge_graph가 한다.

structure_snapshot은 명세상 평가·감사용(reporter)이라 analyze 규칙 입력으로 쓰지 않지만, 질의 작성 주체는
access_analyzer다(명세 m4). 그래서 계획에는 포함해 KG가 reporter용 snapshot을 함께 내도록 한다.
"""

from typing import Any

# 명세 m4·KG configs/default.json의 supported_query_keys와 같은 집합·순서.
QUERY_KEY_OWNERSHIP = "resource_ownership"
QUERY_KEY_ACCESS = "role_resource_access"
QUERY_KEY_FLOW = "workflow_dependencies"
QUERY_KEY_SNAPSHOT = "structure_snapshot"

# query_id는 질의 왕복 연결 ID. 결과의 (query_id, query_key) 순서가 이 순서와 같아야 한다(KG가 강제).
QUERY_ID_OWNERSHIP = "q_resource_ownership"
QUERY_ID_ACCESS = "q_role_resource_access"
QUERY_ID_FLOW = "q_workflow_dependencies"
QUERY_ID_SNAPSHOT = "q_structure_snapshot"


def build_graph_query_data(graph_id: str, expected_graph_revision: int | None) -> dict[str, Any]:
    """graph_query.json의 data를 만든다. run 범위 전체를 조회하는 기본 계획."""
    return {
        "graph_id": graph_id,
        "expected_graph_revision": expected_graph_revision,
        "queries": [
            {
                "query_id": QUERY_ID_OWNERSHIP,
                "query_key": QUERY_KEY_OWNERSHIP,
                "parameters": {"account_ids": [], "resource_ids": []},
            },
            {
                "query_id": QUERY_ID_ACCESS,
                "query_key": QUERY_KEY_ACCESS,
                "parameters": {"role_ids": []},
            },
            {
                "query_id": QUERY_ID_FLOW,
                "query_key": QUERY_KEY_FLOW,
                "parameters": {"workflow_ids": []},
            },
            {
                "query_id": QUERY_ID_SNAPSHOT,
                "query_key": QUERY_KEY_SNAPSHOT,
                "parameters": {"include_evidence_refs": True},
            },
        ],
    }
