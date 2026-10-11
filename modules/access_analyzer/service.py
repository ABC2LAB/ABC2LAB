"""access_analyzer 처리 로직: prepare_queries(질의 계획)와 analyze(후보 판정).

prepare_queries: KG에 보낼 읽기 전용 질의 계획(graph_query.json의 data)을 만든다. 허용된 네 개의
query_key(resource_ownership·role_resource_access·workflow_dependencies·structure_snapshot)를 각각 한 번씩
담는다. 지금은 run 범위 전체를 조회한다(필터 빈 배열). 실제 Cypher 실행·DB 연결은 knowledge_graph가 한다.

structure_snapshot은 명세상 평가·감사용(reporter)이라 analyze 규칙 입력으로 쓰지 않지만, 질의 작성 주체는
access_analyzer다(명세 m4). 그래서 계획에는 포함해 KG가 reporter용 snapshot을 함께 내도록 한다.

analyze: graph_query_result를 받아 revision·상태와 질의 계획 대응을 판정하고 rules로 vulnerability_candidates의 data를
만든다. 질의 실패·partial·오래된 revision·계획과 다른 결과를 "후보 0개"와 구분하는 것이 먼저다(명세 m4 완료 기준).
model_info는 LLM을 쓰지 않아 null.
"""

from collections import Counter
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from modules.access_analyzer import rules
from modules.access_analyzer.utils.config import RuleConfig, load_rule_config
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

# 입력 결과가 가리켜야 하는 질의 계획: 우리가 쓴 graph_query.json. ArtifactRef에 producer 필드가 없어 경로의
# producer 폴더(runs/<run_id>/artifacts/iteration-<NNN>/<producer>/, 명세 03)로 본다.
GRAPH_QUERY_ARTIFACT_TYPE = "graph_query"
PRODUCER_DIRECTORY = "access_analyzer"


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
    config: RuleConfig | None = None,
) -> AnalyzeOutcome:
    """graph_query_result로 vulnerability_candidates의 status·errors·data를 정한다.

    규칙(Rule A)으로 후보를 만든다. 실패·partial·stale·계획과 다른 결과를 정상 빈 결과와 구분하는 것이 먼저다.
    입력은 이미 Schema·의미 검증을 통과했다고 본다(entrypoint가 먼저 검증).
    """
    rule_config = config or load_rule_config()
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

    # 받은 결과가 우리 계획의 질의와 정확히 대응해야 한다(명세 m4). 빠지거나 바뀐 질의로 후보를 만들지 않는다.
    # 계획한 질의가 다 있고 그중 일부가 failed인 경우는 아래 partial로 남긴다.
    plan_errors = find_query_plan_mismatches(input_artifact, graph_id, expected_graph_revision)
    if plan_errors:
        return AnalyzeOutcome(Status.FAILED, plan_errors, None)

    # 완료된 분석 쿼리 결과에 규칙을 적용한다. LLM은 쓰지 않아 model_info=null.
    candidates, rule_errors = rules.apply_rules(data, rule_config)
    candidates_data = {
        "source_graph_revision": graph_revision,
        "candidates": candidates,
        "model_info": None,
    }
    # 입력 partial(일부 질의 실패)은 분석 못 한 범위를 errors로 남겨 "후보 0개 완료"와 구분한다.
    partial_errors = (
        [
            make_error_item(
                ErrorCode.QUERY_RESULT_PARTIAL,
                f"입력 질의 일부 실패: {item['code']}",
                item_ref=item["item_ref"],
                is_retryable=item["retryable"],
            )
            for item in input_artifact["errors"]
        ]
        if input_status == "partial"
        else []
    )
    errors = partial_errors + rule_errors
    status = Status.PARTIAL if errors else Status.COMPLETED
    return AnalyzeOutcome(status, errors, candidates_data)


def find_query_plan_mismatches(
    input_artifact: dict[str, Any], graph_id: str, expected_graph_revision: int | None
) -> list[dict[str, Any]]:
    """입력이 우리 graph_query 하나에서 왔고 결과 (query_id, query_key) 쌍이 계획과 정확히 같은지. 어긋남마다 오류 하나."""
    planned_queries = build_graph_query_data(graph_id, expected_graph_revision)["queries"]
    planned_pairs = [(query["query_id"], query["query_key"]) for query in planned_queries]
    result_pairs = [(result["query_id"], result["query_key"]) for result in input_artifact["data"]["results"]]
    return [*_check_graph_query_ref(input_artifact["input_refs"]), *_compare_query_pairs(planned_pairs, result_pairs)]


def _check_graph_query_ref(input_refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs = [ref for ref in input_refs if ref["artifact_type"] == GRAPH_QUERY_ARTIFACT_TYPE]
    if len(refs) == 1 and PurePosixPath(refs[0]["path"]).parent.name == PRODUCER_DIRECTORY:
        return []
    return [_plan_error(f"입력 input_refs에 access_analyzer가 쓴 graph_query 참조가 정확히 하나여야 함 (graph_query 참조 {len(refs)}개)")]


def _compare_query_pairs(
    planned_pairs: list[tuple[str, str]], result_pairs: list[tuple[str, str]]
) -> list[dict[str, Any]]:
    """메시지에는 query_id·query_key만 넣는다(rows 내용은 넣지 않는다)."""
    planned_key_by_id = dict(planned_pairs)
    received = set(result_pairs)
    errors = [
        _plan_error(f"계획한 질의 결과가 없음: {query_id}({query_key})", query_id)
        for query_id, query_key in planned_pairs
        if (query_id, query_key) not in received
    ]
    for query_id, query_key in dict.fromkeys(result_pairs):
        planned_key = planned_key_by_id.get(query_id)
        if planned_key is None:
            errors.append(_plan_error(f"계획에 없는 질의 결과: {query_id}({query_key})", query_id))
        elif planned_key != query_key:
            errors.append(_plan_error(f"질의 키가 계획과 다름: {query_id}는 {planned_key} 계획인데 {query_key} 결과", query_id))
    for query_key, count in Counter(key for _, key in result_pairs).items():
        if count > 1:
            errors.append(_plan_error(f"같은 query_key 결과가 {count}개: {query_key}"))
    return errors


def _plan_error(message: str, item_ref: str | None = None) -> dict[str, Any]:
    # 계획과 다른 결과는 KG·계획 계약이 어긋난 것이라 같은 입력으로 다시 돌려도 같다.
    return make_error_item(ErrorCode.QUERY_PLAN_MISMATCH, message, item_ref=item_ref, is_retryable=False)
