"""JSON Schema and cross-reference validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator, FormatChecker

from modules.knowledge_graph.exceptions import ContractValidationError


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractValidationError(f"중복 JSON 키: {key}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise ContractValidationError(f"유한하지 않은 JSON 숫자: {value}")


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as source:
            value = json.load(
                source,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_finite,
            )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ContractValidationError(f"JSON 읽기 실패: {path}: {error}") from error
    if not isinstance(value, dict):
        raise ContractValidationError("산출물 최상위 값은 object여야 함")
    return value


def validate_schema(value: dict[str, Any], schema_path: Path) -> None:
    schema = load_json(schema_path)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(value), key=lambda item: list(item.absolute_path))
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "$"
        raise ContractValidationError(f"Schema 위반 ({location}): {error.message}")


def load_and_validate_artifact(path: Path, schema_path: Path) -> dict[str, Any]:
    artifact = load_json(path)
    validate_schema(artifact, schema_path)
    return artifact


def _require_unique(values: Iterable[str], label: str) -> set[str]:
    values_list = list(values)
    values_set = set(values_list)
    if len(values_list) != len(values_set):
        raise ContractValidationError(f"중복 {label}")
    return values_set


def validate_semantic_analysis_semantics(artifact: dict[str, Any]) -> None:
    data = artifact["data"]
    nodes = data["nodes"]
    relationships = data["relationships"]
    requests = data["normalized_requests"]
    workflows = data["workflows"]

    node_ids = _require_unique((item["node_id"] for item in nodes), "node_id")
    node_type_by_id = {item["node_id"]: item["node_type"] for item in nodes}
    _require_unique(
        (item["relationship_id"] for item in relationships),
        "relationship_id",
    )
    request_ids = _require_unique((item["request_id"] for item in requests), "request_id")
    _require_unique((item["workflow_id"] for item in workflows), "workflow_id")

    for relationship in relationships:
        if relationship["source_id"] not in node_ids:
            raise ContractValidationError("관계 source_id가 nodes에 존재하지 않음")
        if relationship["target_id"] not in node_ids:
            raise ContractValidationError("관계 target_id가 nodes에 존재하지 않음")

    for request in requests:
        referenced_ids = [
            request["account_id"],
            request["role_id"],
            request["endpoint_id"],
            *request["resource_ids"],
        ]
        if any(item not in node_ids for item in referenced_ids):
            raise ContractValidationError("정규화 요청이 존재하지 않는 node_id를 참조함")
        expected_types = {
            request["account_id"]: "User",
            request["role_id"]: "Role",
            request["endpoint_id"]: "Endpoint",
            **{resource_id: "Resource" for resource_id in request["resource_ids"]},
        }
        if any(
            node_type_by_id[node_id] != node_type
            for node_id, node_type in expected_types.items()
        ):
            raise ContractValidationError("정규화 요청의 node_type 참조가 올바르지 않음")
        _require_unique(
            (item["parameter_id"] for item in request["parameters"]),
            f"parameter_id ({request['request_id']})",
        )

    for workflow in workflows:
        if any(role_id not in node_ids for role_id in workflow["role_ids"]):
            raise ContractValidationError("workflow가 없는 role_id를 참조함")
        if any(node_type_by_id[role_id] != "Role" for role_id in workflow["role_ids"]):
            raise ContractValidationError("workflow role_id가 Role 노드가 아님")
        step_ids = _require_unique(
            (item["step_id"] for item in workflow["steps"]),
            f"step_id ({workflow['workflow_id']})",
        )
        orders = [item["order"] for item in workflow["steps"]]
        if sorted(orders) != list(range(len(orders))):
            raise ContractValidationError("workflow step order는 0부터 연속이어야 함")
        for step in workflow["steps"]:
            if any(request_id not in request_ids for request_id in step["request_ids"]):
                raise ContractValidationError("workflow step이 없는 request_id를 참조함")
        for dependency in workflow["dependencies"]:
            if dependency["before_step_id"] not in step_ids:
                raise ContractValidationError("dependency before_step_id가 없음")
            if dependency["after_step_id"] not in step_ids:
                raise ContractValidationError("dependency after_step_id가 없음")


def validate_graph_query_semantics(artifact: dict[str, Any]) -> None:
    queries = artifact["data"]["queries"]
    _require_unique((item["query_id"] for item in queries), "query_id")


def validate_verification_results_semantics(artifact: dict[str, Any]) -> None:
    data = artifact["data"]
    results = data["results"]
    graph_updates = data["graph_updates"]
    result_ids = _require_unique(
        (item["verification_id"] for item in results),
        "verification_id",
    )
    source_ids = _require_unique(
        graph_updates["source_verification_ids"],
        "source_verification_id",
    )
    if not source_ids.issubset(result_ids):
        raise ContractValidationError("graph_updates가 없는 verification_id를 참조함")

    result_by_id = {item["verification_id"]: item for item in results}
    for result in results:
        if (
            result["result"] in {"success", "failure"}
            and result["execution_status"] != "completed"
        ):
            raise ContractValidationError("success 또는 failure 결과는 completed 실행이어야 함")
        if result["result"] == "blocked":
            if result["execution_status"] != "not_executed" or result["steps"]:
                raise ContractValidationError("blocked 결과는 미실행이며 steps가 비어 있어야 함")
            if result["policy_decision"] not in {"block", "require_approval"}:
                raise ContractValidationError("blocked 결과의 Policy 판정이 올바르지 않음")
        if result["execution_status"] == "not_executed" and result["steps"]:
            raise ContractValidationError("미실행 결과의 steps는 비어 있어야 함")
    for source_id in source_ids:
        result = result_by_id[source_id]
        if result["policy_decision"] != "allow":
            raise ContractValidationError("차단 또는 승인 대기 결과는 KG 갱신 근거가 될 수 없음")
        if result["execution_status"] != "completed":
            raise ContractValidationError("미완료 실행은 KG 갱신 근거가 될 수 없음")
        if result["result"] not in {"success", "failure"}:
            raise ContractValidationError("판단불가 결과는 KG 갱신 근거가 될 수 없음")
        if not result["evidence_refs"]:
            raise ContractValidationError("KG 갱신 근거에 EvidenceRef가 없음")

    nodes = graph_updates["nodes"]
    relationships = graph_updates["relationships"]
    _require_unique((item["node_id"] for item in nodes), "graph update node_id")
    _require_unique(
        (item["relationship_id"] for item in relationships),
        "graph update relationship_id",
    )
    for item in [*nodes, *relationships]:
        if item["basis"] != "verified":
            raise ContractValidationError("graph update의 basis는 verified여야 함")
        if not item["evidence_refs"]:
            raise ContractValidationError("graph update에 EvidenceRef가 없음")


def validate_graph_query_result_semantics(artifact: dict[str, Any]) -> None:
    if artifact["status"] == "failed":
        return
    results = artifact["data"]["results"]
    _require_unique((item["query_id"] for item in results), "result query_id")
    for result in results:
        if result["status"] == "completed" and result["errors"]:
            raise ContractValidationError("완료 질의 결과의 errors는 비어 있어야 함")
        if result["status"] == "failed" and not result["errors"]:
            raise ContractValidationError("실패 질의 결과에는 error가 필요함")


def validate_graph_query_result_against_input(
    artifact: dict[str, Any],
    source_artifact_id: str,
    expected_queries: tuple[tuple[str, str], ...],
) -> None:
    matching_refs = [
        item
        for item in artifact["input_refs"]
        if item["artifact_id"] == source_artifact_id
        and item["artifact_type"] == "graph_query"
    ]
    if len(matching_refs) != 1:
        raise ContractValidationError("graph_query input_ref 대응이 올바르지 않음")
    if artifact["status"] == "failed":
        return
    actual_queries = tuple(
        (item["query_id"], item["query_key"])
        for item in artifact["data"]["results"]
    )
    if actual_queries != expected_queries:
        raise ContractValidationError("질의 결과가 입력 query_id·query_key 순서와 다름")
