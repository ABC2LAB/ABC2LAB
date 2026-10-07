"""Cross-artifact semantic validation for reporter inputs."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from modules.reporter.exceptions import ContractValidationError
from modules.reporter.models import (
    Candidate,
    CrawlAccount,
    CrawlIndex,
    CrawlRequest,
    GraphSnapshot,
    InputArtifact,
    ReportInputs,
    SafetyDecision,
    Scenario,
    VerificationResult,
)
from modules.reporter.utils.hashing import calculate_sha256
from modules.reporter.utils.validation import require_unique

_BINDING_PLACEHOLDER_PATTERN = re.compile(r"\{([^{}]+)\}")


def build_report_models(
    artifact_by_type: Mapping[str, InputArtifact],
    scenarios_path: Path,
) -> tuple[
    tuple[Candidate, ...],
    tuple[Scenario, ...],
    tuple[SafetyDecision, ...],
    tuple[VerificationResult, ...],
]:
    candidates = _build_candidates(artifact_by_type["vulnerability_candidates"])
    scenarios = _build_scenarios(artifact_by_type["test_scenarios"])
    decisions = _build_decisions(artifact_by_type["safety_decisions"])
    results = _build_verification_results(
        artifact_by_type["verification_results"]
    )
    candidates_available = artifact_by_type["vulnerability_candidates"].data is not None
    scenarios_available = artifact_by_type["test_scenarios"].data is not None
    decisions_available = artifact_by_type["safety_decisions"].data is not None
    _validate_scenario_candidate_links(
        candidates,
        scenarios,
        candidates_available,
    )
    _validate_decision_links(
        scenarios,
        decisions,
        scenarios_available and decisions_available,
    )
    _validate_verification_links(
        candidates,
        scenarios,
        decisions,
        results,
        candidates_available and scenarios_available and decisions_available,
    )
    _validate_scenario_hashes(artifact_by_type, scenarios_path)
    _validate_source_revisions(artifact_by_type)
    return candidates, scenarios, decisions, results


def extract_graph_snapshot(artifact: InputArtifact) -> GraphSnapshot | None:
    data = artifact.data
    if data is None:
        return None
    results = data["results"]
    require_unique((item["query_id"] for item in results), "query result ID")
    for item in results:
        if item["status"] == "completed" and item["errors"]:
            raise ContractValidationError("완료 KG 질의 결과에 errors가 있음")
        if item["status"] == "failed" and not item["errors"]:
            raise ContractValidationError("실패 KG 질의 결과에 errors가 없음")

    snapshot_results = [
        item
        for item in results
        if item["query_key"] == "structure_snapshot"
        and item["status"] == "completed"
    ]
    if not snapshot_results:
        return None
    if len(snapshot_results) != 1 or len(snapshot_results[0]["rows"]) != 1:
        raise ContractValidationError("structure_snapshot은 정확히 한 행이어야 함")
    snapshot = snapshot_results[0]["rows"][0]
    _validate_graph_structure(snapshot)
    return GraphSnapshot(
        graph_id=data["graph_id"],
        graph_revision=data["graph_revision"],
        nodes=tuple(snapshot["nodes"]),
        relationships=tuple(snapshot["relationships"]),
        workflows=tuple(snapshot["workflows"]),
    )


def validate_semantic_analysis(artifact: InputArtifact) -> None:
    data = artifact.data
    if data is None:
        return
    graph = {
        "nodes": data["nodes"],
        "relationships": data["relationships"],
        "workflows": data["workflows"],
    }
    node_type_by_id = _validate_graph_structure(graph)
    request_ids = require_unique(
        (item["request_id"] for item in data["normalized_requests"]),
        "normalized request ID",
    )
    for request in data["normalized_requests"]:
        expected_types = {
            request["account_id"]: "User",
            request["role_id"]: "Role",
            request["endpoint_id"]: "Endpoint",
            **{
                resource_id: "Resource"
                for resource_id in request["resource_ids"]
            },
        }
        has_invalid_type = any(
            node_type_by_id.get(node_id) != node_type
            for node_id, node_type in expected_types.items()
        )
        if has_invalid_type:
            raise ContractValidationError(
                "normalized request의 node 참조가 올바르지 않음"
            )
    _validate_workflow_request_refs(data["workflows"], request_ids)


def build_crawl_index(artifact: InputArtifact) -> CrawlIndex | None:
    data = artifact.data
    if data is None:
        return None
    role_ids = require_unique(
        (item["role_id"] for item in data["roles"]),
        "crawl role_id",
    )
    account_values = data["accounts"]
    require_unique(
        (item["account_id"] for item in account_values),
        "crawl account_id",
    )
    accounts = tuple(
        CrawlAccount(
            account_id=item["account_id"],
            role_id=item["role_id"],
            session_ref=item["session_ref"],
        )
        for item in account_values
    )
    if any(item.role_id not in role_ids for item in accounts):
        raise ContractValidationError("crawl account가 없는 role_id를 참조함")
    page_ids = require_unique(
        (item["page_id"] for item in data["pages"]),
        "crawl page_id",
    )
    action_page_by_id = _validate_crawl_actions(data["actions"], page_ids)
    requests = _build_crawl_requests(
        data["requests"],
        accounts,
        page_ids,
        action_page_by_id,
    )
    return CrawlIndex(
        role_ids=frozenset(role_ids),
        accounts=accounts,
        page_ids=frozenset(page_ids),
        action_page_by_id=action_page_by_id,
        requests=requests,
    )


def validate_evaluation_links(
    crawl_index: CrawlIndex | None,
    semantic_analysis: InputArtifact,
    report_inputs: ReportInputs,
) -> None:
    if crawl_index is None:
        return
    _validate_semantic_crawl_links(semantic_analysis, crawl_index)
    _validate_candidate_crawl_links(report_inputs.candidates, crawl_index)
    scenario_artifact = report_inputs.artifact_by_type("test_scenarios")
    if scenario_artifact.data is not None:
        _validate_scenario_crawl_links(
            scenario_artifact.data["scenarios"],
            crawl_index,
        )


def validate_ground_truth(value: Mapping[str, Any], dataset_name: str) -> None:
    if value["dataset_id"] != dataset_name:
        raise ContractValidationError("ground_truth 경로와 dataset_id가 일치하지 않음")
    data = value["data"]
    entity_ids = require_unique(
        (item["gt_id"] for item in data["entities"]),
        "ground truth entity ID",
    )
    require_unique(
        (item["gt_relation_id"] for item in data["relationships"]),
        "ground truth relation ID",
    )
    require_unique(
        (item["gt_workflow_id"] for item in data["workflows"]),
        "ground truth workflow ID",
    )
    require_unique(
        (item["case_id"] for item in data["cases"]),
        "ground truth case ID",
    )
    for relationship in data["relationships"]:
        if relationship["source_gt_id"] not in entity_ids:
            raise ContractValidationError("ground truth relation source가 없음")
        if relationship["target_gt_id"] not in entity_ids:
            raise ContractValidationError("ground truth relation target이 없음")


def validate_evaluation_revisions(
    artifact_by_type: Mapping[str, InputArtifact],
    snapshot: GraphSnapshot | None,
) -> None:
    if snapshot is None:
        return
    for artifact_type in ("vulnerability_candidates", "verification_results"):
        data = artifact_by_type[artifact_type].data
        if data is None:
            continue
        if data["source_graph_revision"] != snapshot.graph_revision:
            raise ContractValidationError(
                f"{artifact_type}의 graph revision이 snapshot과 다름"
            )


def _build_candidates(artifact: InputArtifact) -> tuple[Candidate, ...]:
    data = artifact.data
    if data is None:
        return ()
    values = data["candidates"]
    require_unique((item["candidate_id"] for item in values), "candidate_id")
    return tuple(Candidate.from_mapping(item) for item in values)


def _build_scenarios(artifact: InputArtifact) -> tuple[Scenario, ...]:
    data = artifact.data
    if data is None:
        return ()
    values = data["scenarios"]
    require_unique((item["scenario_id"] for item in values), "scenario_id")
    for scenario in values:
        _validate_scenario_steps(scenario)
    return tuple(Scenario.from_mapping(item) for item in values)


def _validate_scenario_steps(scenario: Mapping[str, Any]) -> None:
    steps = scenario["steps"]
    require_unique(
        (item["step_id"] for item in steps),
        f"step_id ({scenario['scenario_id']})",
    )
    orders = sorted(item["order"] for item in steps)
    if orders != list(range(len(steps))):
        raise ContractValidationError("scenario step order는 0부터 연속이어야 함")
    check_ids = [
        item["check_id"]
        for item in (*scenario["preconditions"], *scenario["assertions"])
    ]
    require_unique(check_ids, f"check_id ({scenario['scenario_id']})")
    order_by_step_id = {item["step_id"]: item["order"] for item in steps}
    binding_ids = require_unique(
        (
            binding["binding_id"]
            for step in steps
            for binding in step["bindings"]
        ),
        f"binding_id ({scenario['scenario_id']})",
    )
    for step in steps:
        _validate_step_bindings(step, order_by_step_id, binding_ids)


def _validate_step_bindings(
    step: Mapping[str, Any],
    order_by_step_id: Mapping[str, int],
    scenario_binding_ids: set[str],
) -> None:
    current_binding_ids = {item["binding_id"] for item in step["bindings"]}
    for binding in step["bindings"]:
        source_order = order_by_step_id.get(binding["source_step_id"])
        if source_order is None:
            raise ContractValidationError("binding source_step_id가 scenario에 없음")
        if source_order >= step["order"]:
            raise ContractValidationError("binding은 앞선 단계만 참조해야 함")
    for parameter in step["request"]["parameters"]:
        binding_ref = parameter["binding_ref"]
        if binding_ref is None:
            continue
        if parameter["value"] is not None:
            raise ContractValidationError("binding parameter value는 null이어야 함")
        if binding_ref not in current_binding_ids:
            raise ContractValidationError("parameter binding_ref가 현재 step에 없음")
    placeholders = _BINDING_PLACEHOLDER_PATTERN.findall(
        step["request"]["url_template"]
    )
    if any(item not in scenario_binding_ids for item in placeholders):
        raise ContractValidationError("url_template이 없는 binding을 참조함")


def _build_decisions(artifact: InputArtifact) -> tuple[SafetyDecision, ...]:
    data = artifact.data
    if data is None:
        return ()
    values = data["decisions"]
    require_unique((item["decision_id"] for item in values), "decision_id")
    require_unique(
        (item["scenario_id"] for item in values),
        "decision scenario_id",
    )
    for item in values:
        _validate_decision_state(item)
    return tuple(SafetyDecision.from_mapping(item) for item in values)


def _validate_decision_state(value: Mapping[str, Any]) -> None:
    require_unique(value["reason_codes"], "decision reason_code")
    require_unique(value["effective_origins"], "effective origin")
    require_unique(value["effective_account_ids"], "effective account_id")
    statuses = [item["status"] for item in value["assessment"].values()]
    decision = value["decision"]
    if decision == "allow" and any(status != "pass" for status in statuses):
        raise ContractValidationError("allow 판정은 모든 assessment가 pass여야 함")
    if "block" in statuses and decision != "block":
        raise ContractValidationError("block assessment의 최종 판정이 block이 아님")
    if decision != "allow":
        if value["approval_ref"] is not None:
            raise ContractValidationError("미허용 판정에 approval_ref가 있음")
        if value["effective_origins"] or value["effective_account_ids"]:
            raise ContractValidationError("미허용 판정에 실행 범위가 있음")


def _build_verification_results(
    artifact: InputArtifact,
) -> tuple[VerificationResult, ...]:
    data = artifact.data
    if data is None:
        return ()
    values = data["results"]
    require_unique(
        (item["verification_id"] for item in values),
        "verification_id",
    )
    for item in values:
        _validate_verification_state(item)
    _validate_graph_updates(data, values)
    return tuple(VerificationResult.from_mapping(item) for item in values)


def _validate_verification_state(value: Mapping[str, Any]) -> None:
    if value["result"] in {"success", "failure"}:
        if value["execution_status"] != "completed":
            raise ContractValidationError("재현 성공·실패는 completed 실행이어야 함")
    if value["result"] == "blocked":
        if value["execution_status"] != "not_executed" or value["steps"]:
            raise ContractValidationError("blocked 결과는 미실행이고 steps가 비어야 함")
        if value["policy_decision"] not in {"block", "require_approval"}:
            raise ContractValidationError("blocked 결과의 Policy 판정이 올바르지 않음")
    if value["execution_status"] == "not_executed" and value["steps"]:
        raise ContractValidationError("미실행 verification의 steps는 비어야 함")


def _validate_graph_updates(
    data: Mapping[str, Any],
    results: list[Mapping[str, Any]],
) -> None:
    updates = data["graph_updates"]
    source_ids = require_unique(
        updates["source_verification_ids"],
        "source_verification_id",
    )
    result_by_id = {item["verification_id"]: item for item in results}
    if not source_ids.issubset(result_by_id):
        raise ContractValidationError("graph_updates가 없는 verification을 참조함")
    has_updates = bool(updates["nodes"] or updates["relationships"])
    if has_updates != bool(source_ids):
        raise ContractValidationError("graph update와 근거 ID 존재 여부가 다름")
    require_unique((item["node_id"] for item in updates["nodes"]), "update node_id")
    require_unique(
        (item["relationship_id"] for item in updates["relationships"]),
        "update relationship_id",
    )
    for source_id in source_ids:
        source = result_by_id[source_id]
        if source["policy_decision"] != "allow":
            raise ContractValidationError("미허용 결과는 graph update 근거가 될 수 없음")
        if source["execution_status"] != "completed":
            raise ContractValidationError("미완료 결과는 graph update 근거가 될 수 없음")
        if not source["evidence_refs"]:
            raise ContractValidationError("graph update 실행 근거가 없음")
    allowed_evidence = {
        _evidence_identity(evidence)
        for source_id in source_ids
        for evidence in _verification_evidence(result_by_id[source_id])
    }
    for item in (*updates["nodes"], *updates["relationships"]):
        if item["basis"] != "verified" or not item["evidence_refs"]:
            raise ContractValidationError("graph update는 verified 근거가 필요함")
        if any(
            _evidence_identity(evidence) not in allowed_evidence
            for evidence in item["evidence_refs"]
        ):
            raise ContractValidationError("graph update 근거가 verification과 다름")


def _verification_evidence(
    result: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    values = list(result["evidence_refs"])
    for step in result["steps"]:
        values.extend(step["evidence_refs"])
        if step["request_ref"] is not None:
            values.append(step["request_ref"])
        if step["response_ref"] is not None:
            values.append(step["response_ref"])
    return values


def _evidence_identity(value: Mapping[str, Any]) -> tuple[object, ...]:
    return (
        value["evidence_id"],
        value["kind"],
        value["path"],
        value["sha256"],
        value["redacted"],
    )


def _validate_scenario_candidate_links(
    candidates: tuple[Candidate, ...],
    scenarios: tuple[Scenario, ...],
    is_available: bool,
) -> None:
    if not is_available:
        return
    candidate_by_id = {item.candidate_id: item for item in candidates}
    for scenario in scenarios:
        candidate = candidate_by_id.get(scenario.candidate_id)
        if candidate is None:
            raise ContractValidationError("scenario가 없는 candidate_id를 참조함")
        if candidate.expected_basis != scenario.expected_basis:
            raise ContractValidationError("scenario expected_basis가 후보와 다름")


def _validate_decision_links(
    scenarios: tuple[Scenario, ...],
    decisions: tuple[SafetyDecision, ...],
    is_available: bool,
) -> None:
    if not is_available:
        return
    scenario_ids = {item.scenario_id for item in scenarios}
    decision_scenario_ids = {item.scenario_id for item in decisions}
    if scenario_ids != decision_scenario_ids:
        raise ContractValidationError("모든 scenario에 정확히 하나의 판정이 필요함")


def _validate_verification_links(
    candidates: tuple[Candidate, ...],
    scenarios: tuple[Scenario, ...],
    decisions: tuple[SafetyDecision, ...],
    results: tuple[VerificationResult, ...],
    is_available: bool,
) -> None:
    if not is_available:
        return
    candidate_ids = {item.candidate_id for item in candidates}
    scenario_by_id = {item.scenario_id: item for item in scenarios}
    decision_by_id = {item.decision_id: item for item in decisions}
    for result in results:
        scenario = scenario_by_id.get(result.scenario_id)
        decision = decision_by_id.get(result.decision_id)
        if result.candidate_id not in candidate_ids or scenario is None:
            raise ContractValidationError("verification의 후보·시나리오 참조가 없음")
        if scenario.candidate_id != result.candidate_id:
            raise ContractValidationError("verification candidate 연결이 다름")
        if decision is None or decision.scenario_id != result.scenario_id:
            raise ContractValidationError("verification decision 연결이 다름")
        if decision.decision != result.policy_decision:
            raise ContractValidationError("verification Policy 판정이 원본과 다름")


def _validate_scenario_hashes(
    artifact_by_type: Mapping[str, InputArtifact],
    scenarios_path: Path,
) -> None:
    scenario_artifact = artifact_by_type["test_scenarios"]
    if scenario_artifact.data is None:
        return
    actual_hash = calculate_sha256(scenarios_path)
    for artifact_type in ("safety_decisions", "verification_results"):
        data = artifact_by_type[artifact_type].data
        if data is not None and data["scenarios_sha256"] != actual_hash:
            raise ContractValidationError(
                f"{artifact_type}의 scenarios_sha256이 실제 계획과 다름"
            )


def _validate_source_revisions(
    artifact_by_type: Mapping[str, InputArtifact],
) -> None:
    candidates = artifact_by_type["vulnerability_candidates"].data
    verification = artifact_by_type["verification_results"].data
    if candidates is None or verification is None:
        return
    if candidates["source_graph_revision"] != verification["source_graph_revision"]:
        raise ContractValidationError("후보와 검증 결과의 graph revision이 다름")


def _validate_graph_structure(
    graph: Mapping[str, Any],
) -> dict[str, str]:
    nodes = graph["nodes"]
    relationships = graph["relationships"]
    workflows = graph["workflows"]
    node_ids = require_unique((item["node_id"] for item in nodes), "node_id")
    require_unique(
        (item["relationship_id"] for item in relationships),
        "relationship_id",
    )
    require_unique((item["workflow_id"] for item in workflows), "workflow_id")
    for relationship in relationships:
        if relationship["source_id"] not in node_ids:
            raise ContractValidationError("relationship source_id가 없음")
        if relationship["target_id"] not in node_ids:
            raise ContractValidationError("relationship target_id가 없음")
    node_type_by_id = {
        item["node_id"]: item["node_type"] for item in nodes
    }
    _validate_workflows(workflows, node_type_by_id)
    return node_type_by_id


def _validate_workflows(
    workflows: list[Mapping[str, Any]],
    node_type_by_id: Mapping[str, str],
) -> None:
    for workflow in workflows:
        if any(
            node_type_by_id.get(role_id) != "Role"
            for role_id in workflow["role_ids"]
        ):
            raise ContractValidationError("workflow role_id가 Role 노드가 아님")
        step_ids = require_unique(
            (item["step_id"] for item in workflow["steps"]),
            f"workflow step_id ({workflow['workflow_id']})",
        )
        orders = sorted(item["order"] for item in workflow["steps"])
        if orders != list(range(len(orders))):
            raise ContractValidationError("workflow step order는 0부터 연속이어야 함")
        for dependency in workflow["dependencies"]:
            if dependency["before_step_id"] not in step_ids:
                raise ContractValidationError("workflow dependency 시작 단계가 없음")
            if dependency["after_step_id"] not in step_ids:
                raise ContractValidationError("workflow dependency 종료 단계가 없음")


def _validate_workflow_request_refs(
    workflows: list[Mapping[str, Any]],
    request_ids: set[str],
) -> None:
    for workflow in workflows:
        for step in workflow["steps"]:
            if any(item not in request_ids for item in step["request_ids"]):
                raise ContractValidationError("workflow step의 request_id가 없음")


def _validate_crawl_actions(
    actions: list[Mapping[str, Any]],
    page_ids: set[str],
) -> dict[str, str]:
    require_unique((item["action_id"] for item in actions), "crawl action_id")
    action_page_by_id = {item["action_id"]: item["page_id"] for item in actions}
    if any(page_id not in page_ids for page_id in action_page_by_id.values()):
        raise ContractValidationError("crawl action이 없는 page_id를 참조함")
    return action_page_by_id


def _build_crawl_requests(
    values: list[Mapping[str, Any]],
    accounts: tuple[CrawlAccount, ...],
    page_ids: set[str],
    action_page_by_id: Mapping[str, str],
) -> tuple[CrawlRequest, ...]:
    require_unique((item["request_id"] for item in values), "crawl request_id")
    account_by_id = {item.account_id: item for item in accounts}
    requests = tuple(
        CrawlRequest(
            request_id=item["request_id"],
            account_id=item["account_id"],
            role_id=item["role_id"],
            session_ref=item["session_ref"],
            page_id=item["page_id"],
            action_id=item["action_id"],
        )
        for item in values
    )
    for request in requests:
        _validate_crawl_request(
            request,
            account_by_id,
            page_ids,
            action_page_by_id,
        )
    return requests


def _validate_crawl_request(
    request: CrawlRequest,
    account_by_id: Mapping[str, CrawlAccount],
    page_ids: set[str],
    action_page_by_id: Mapping[str, str],
) -> None:
    account = account_by_id.get(request.account_id)
    if account is None or account.role_id != request.role_id:
        raise ContractValidationError("crawl request의 계정·역할 연결이 다름")
    if account.session_ref != request.session_ref:
        raise ContractValidationError("crawl request의 session_ref 연결이 다름")
    if request.page_id is not None and request.page_id not in page_ids:
        raise ContractValidationError("crawl request의 page_id가 없음")
    if request.action_id is None:
        return
    action_page = action_page_by_id.get(request.action_id)
    if action_page is None or action_page != request.page_id:
        raise ContractValidationError("crawl request의 action 연결이 다름")


def _validate_semantic_crawl_links(
    artifact: InputArtifact,
    crawl_index: CrawlIndex,
) -> None:
    data = artifact.data
    if data is None:
        return
    for item in data["normalized_requests"]:
        source = crawl_index.request_by_id(item["request_id"])
        if source is None:
            raise ContractValidationError("semantic request_id가 crawl에 없음")
        if source.account_id != item["account_id"] or source.role_id != item["role_id"]:
            raise ContractValidationError("semantic 요청의 계정·역할 연결이 다름")


def _validate_candidate_crawl_links(
    candidates: tuple[Candidate, ...],
    crawl_index: CrawlIndex,
) -> None:
    for candidate in candidates:
        actor = crawl_index.account_by_id(candidate.actor_account_id)
        if actor is None or actor.role_id != candidate.actor_role_id:
            raise ContractValidationError("candidate의 실행 계정·역할 연결이 다름")
        if (
            candidate.reference_account_id is not None
            and crawl_index.account_by_id(candidate.reference_account_id) is None
        ):
            raise ContractValidationError("candidate reference_account_id가 없음")
        if any(
            crawl_index.request_by_id(request_id) is None
            for request_id in candidate.source_request_ids
        ):
            raise ContractValidationError("candidate source_request_id가 crawl에 없음")


def _validate_scenario_crawl_links(
    scenarios: list[Mapping[str, Any]],
    crawl_index: CrawlIndex,
) -> None:
    for scenario in scenarios:
        for step in scenario["steps"]:
            source = crawl_index.request_by_id(step["source_request_id"])
            account = crawl_index.account_by_id(step["account_id"])
            if source is None or account is None:
                raise ContractValidationError("scenario 원본 요청·계정 참조가 없음")
            if account.role_id != step["role_id"]:
                raise ContractValidationError("scenario 계정·역할 연결이 다름")
            if account.session_ref != step["session_ref"]:
                raise ContractValidationError("scenario session_ref 연결이 다름")
            if source.account_id != step["account_id"]:
                raise ContractValidationError("scenario source_request 계정이 다름")
