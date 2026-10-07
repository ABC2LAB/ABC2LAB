"""Lossless conversion between contract models and Neo4j property records."""

from __future__ import annotations

import json
from dataclasses import asdict
from hashlib import sha256
from typing import Any

from modules.knowledge_graph.models import (
    EvidenceReference,
    GraphEdge,
    GraphNode,
    RequestObservation,
    Workflow,
    WorkflowDependency,
    WorkflowStep,
)


def encode_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def decode_json(value: str) -> Any:
    return json.loads(value)


def serialize_request_observation(
    observation: RequestObservation,
) -> dict[str, Any]:
    return {
        "request_id": observation.request_id,
        "account_id": observation.account_id,
        "role_id": observation.role_id,
        "user_node_id": observation.user_node_id,
        "role_node_id": observation.role_node_id,
        "endpoint_id": observation.endpoint_id,
        "action": observation.action,
        "resource_ids_json": encode_json(list(observation.resource_ids)),
        "basis": observation.basis,
        "evidence_refs_json": encode_json(
            [asdict(item) for item in observation.evidence_refs]
        ),
    }


def deserialize_request_observation(value: dict[str, Any]) -> RequestObservation:
    return RequestObservation(
        request_id=value["request_id"],
        account_id=value["account_id"],
        role_id=value["role_id"],
        user_node_id=value["user_node_id"],
        role_node_id=value["role_node_id"],
        endpoint_id=value["endpoint_id"],
        action=value["action"],
        resource_ids=tuple(decode_json(value["resource_ids_json"])),
        basis=value["basis"],
        evidence_refs=tuple(
            EvidenceReference.from_mapping(item)
            for item in decode_json(value["evidence_refs_json"])
        ),
    )


def serialize_node(node: GraphNode) -> dict[str, Any]:
    return {
        "node_id": node.node_id,
        "node_type": node.node_type,
        "properties_json": encode_json(node.properties),
        "basis": node.basis,
        "evidence_refs_json": encode_json([asdict(item) for item in node.evidence_refs]),
    }


def deserialize_node(value: dict[str, Any]) -> GraphNode:
    evidence_values = decode_json(value["evidence_refs_json"])
    return GraphNode(
        node_id=value["node_id"],
        node_type=value["node_type"],
        properties=decode_json(value["properties_json"]),
        basis=value["basis"],
        evidence_refs=tuple(EvidenceReference.from_mapping(item) for item in evidence_values),
    )


def serialize_edge(edge: GraphEdge) -> dict[str, Any]:
    return {
        "relationship_id": edge.relationship_id,
        "source_id": edge.source_id,
        "target_id": edge.target_id,
        "relation_type": edge.relation_type,
        "properties_json": encode_json(edge.properties),
        "basis": edge.basis,
        "evidence_refs_json": encode_json([asdict(item) for item in edge.evidence_refs]),
    }


def deserialize_edge(value: dict[str, Any]) -> GraphEdge:
    evidence_values = decode_json(value["evidence_refs_json"])
    return GraphEdge(
        relationship_id=value["relationship_id"],
        source_id=value["source_id"],
        target_id=value["target_id"],
        relation_type=value["relation_type"],
        properties=decode_json(value["properties_json"]),
        basis=value["basis"],
        evidence_refs=tuple(EvidenceReference.from_mapping(item) for item in evidence_values),
    )


def serialize_workflow(workflow: Workflow) -> dict[str, Any]:
    return {
        "workflow_id": workflow.workflow_id,
        "name": workflow.name,
        "role_ids_json": encode_json(list(workflow.role_ids)),
        "basis": workflow.basis,
        "evidence_refs_json": encode_json(
            [asdict(item) for item in workflow.evidence_refs]
        ),
    }


def serialize_workflow_step(
    workflow_id: str,
    step: WorkflowStep,
) -> dict[str, Any]:
    return {
        "workflow_id": workflow_id,
        "step_id": step.step_id,
        "step_order": step.order,
        "action": step.action,
        "request_ids_json": encode_json(list(step.request_ids)),
    }


def serialize_workflow_dependency(
    workflow_id: str,
    dependency: WorkflowDependency,
    dependency_order: int = 0,
) -> dict[str, Any]:
    identity_source = encode_json(
        {
            "workflow_id": workflow_id,
            "before_step_id": dependency.before_step_id,
            "after_step_id": dependency.after_step_id,
            "condition": dependency.condition,
        }
    )
    return {
        "workflow_id": workflow_id,
        "dependency_id": sha256(identity_source.encode("utf-8")).hexdigest(),
        "dependency_order": dependency_order,
        "before_step_id": dependency.before_step_id,
        "after_step_id": dependency.after_step_id,
        "condition": dependency.condition,
        "basis": dependency.basis,
        "evidence_refs_json": encode_json(
            [asdict(item) for item in dependency.evidence_refs]
        ),
    }


def deserialize_workflow(
    workflow_value: dict[str, Any],
    step_values: list[dict[str, Any]],
    dependency_values: list[dict[str, Any]],
) -> Workflow:
    steps = tuple(
        WorkflowStep(
            step_id=value["step_id"],
            order=value["step_order"],
            action=value["action"],
            request_ids=tuple(decode_json(value["request_ids_json"])),
        )
        for value in sorted(step_values, key=lambda item: item["step_order"])
    )
    dependencies = tuple(
        WorkflowDependency(
            before_step_id=value["before_step_id"],
            after_step_id=value["after_step_id"],
            condition=value["condition"],
            basis=value["basis"],
            evidence_refs=tuple(
                EvidenceReference.from_mapping(item)
                for item in decode_json(value["evidence_refs_json"])
            ),
        )
        for value in sorted(
            dependency_values,
            key=lambda item: item["dependency_order"],
        )
    )
    return Workflow(
        workflow_id=workflow_value["workflow_id"],
        name=workflow_value["name"],
        role_ids=tuple(decode_json(workflow_value["role_ids_json"])),
        steps=steps,
        dependencies=dependencies,
        basis=workflow_value["basis"],
        evidence_refs=tuple(
            EvidenceReference.from_mapping(item)
            for item in decode_json(workflow_value["evidence_refs_json"])
        ),
    )
