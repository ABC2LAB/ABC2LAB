"""access_analyzer 처리 로직: prepare_queries(질의 계획)와 analyze(후보 판정).

prepare_queries: KG에 보낼 읽기 전용 질의 계획(graph_query.json의 data)을 만든다. 허용된 네 개의
query_key(resource_ownership·role_resource_access·workflow_dependencies·structure_snapshot)를 각각 한 번씩
담는다. 지금은 run 범위 전체를 조회한다(필터 빈 배열). 실제 Cypher 실행·DB 연결은 knowledge_graph가 한다.

structure_snapshot은 명세상 평가·감사용(reporter)이라 analyze 규칙 입력으로 쓰지 않지만, 질의 작성 주체는
access_analyzer다(명세 m4). 그래서 계획에는 포함해 KG가 reporter용 snapshot을 함께 내도록 한다.

analyze: graph_query_result를 받아 revision·상태를 판정하고 vulnerability_candidates의 data를 만든다.
이 PR은 규칙이 없어 candidates는 빈 배열이다(Rule A는 다음 PR). 핵심은 질의 실패·partial·오래된 revision을
"후보 0개"와 구분하는 것이다(명세 m4 완료 기준). model_info는 LLM을 쓰지 않아 null.
"""

from dataclasses import dataclass
from typing import Any

from modules.access_analyzer.utils.envelope import ErrorCode, Status, make_error_item

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


@dataclass(frozen=True)
class AnalyzeOutcome:
    """analyze 판정 결과. entrypoint가 duration을 더해 envelope으로 만든다."""

    status: Status
    errors: list[dict[str, Any]]
    # status=failed면 None
    data: dict[str, Any] | None


def build_candidates_result(
    input_artifact: dict[str, Any],
    graph_id: str,
    expected_graph_revision: int | None,
    run_id: str,
) -> AnalyzeOutcome:
    """graph_query_result로 vulnerability_candidates의 status·errors·data를 정한다.

    규칙이 없는 이 PR은 candidates=[]다. 중요한 건 실패·partial·stale을 정상 빈 결과와 구분하는 것.
    입력은 이미 Schema·의미 검증을 통과했다고 본다(entrypoint가 먼저 검증).
    """
    input_status = input_artifact["status"]
    if input_status == "failed":
        # KG가 결과를 못 냈다(data=null). 후보 0개로 단정하지 않고 우리도 failed로 둔다.
        count = len(input_artifact["errors"])
        error = make_error_item(
            ErrorCode.INPUT_RESULT_FAILED,
            f"입력 graph_query_result가 failed라 후보를 만들 수 없음 (입력 오류 {count}건)",
            is_retryable=True,
        )
        return AnalyzeOutcome(Status.FAILED, [error], None)

    data = input_artifact["data"]
    if input_artifact["run_id"] != run_id:
        error = make_error_item(ErrorCode.RUN_ID_MISMATCH, "입력 결과의 run_id가 실행 run_id와 다름", is_retryable=False)
        return AnalyzeOutcome(Status.FAILED, [error], None)
    if data["graph_id"] != graph_id:
        error = make_error_item(ErrorCode.GRAPH_ID_MISMATCH, "입력 결과의 graph_id가 조회 graph_id와 다름", is_retryable=False)
        return AnalyzeOutcome(Status.FAILED, [error], None)

    graph_revision = data["graph_revision"]
    if expected_graph_revision is not None and expected_graph_revision != graph_revision:
        # 계획한 revision과 다른 결과다. 오래된 결과로 후보를 만들지 않는다(명세 m4).
        error = make_error_item(
            ErrorCode.GRAPH_REVISION_STALE,
            f"입력 결과 revision({graph_revision})이 계획 revision({expected_graph_revision})과 다름",
            is_retryable=True,
        )
        return AnalyzeOutcome(Status.FAILED, [error], None)

    # 규칙은 다음 PR. 지금은 빈 후보. source_graph_revision은 실제 조회 revision.
    candidates_data = {
        "source_graph_revision": graph_revision,
        "candidates": [],
        "model_info": None,
    }
    if input_status == "partial":
        # 일부 질의가 실패한 결과다. 분석 못 한 범위를 errors로 남겨 "후보 0개 완료"와 구분한다.
        errors = [
            make_error_item(
                ErrorCode.QUERY_RESULT_PARTIAL,
                f"입력 질의 일부 실패: {item['code']}",
                item_ref=item["item_ref"],
                is_retryable=item["retryable"],
            )
            for item in input_artifact["errors"]
        ]
        return AnalyzeOutcome(Status.PARTIAL, errors, candidates_data)
    return AnalyzeOutcome(Status.COMPLETED, [], candidates_data)
