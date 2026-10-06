"""Conversion of stored graph models into public query result rows."""

from __future__ import annotations

from modules.knowledge_graph.models import (
    EvidenceReference,
    GraphEdge,
    GraphNode,
    SemanticGraph,
    Workflow,
    WorkflowDependency,
    WorkflowStep,
)


def graph_to_snapshot(
    graph: SemanticGraph,
    include_evidence_refs: bool,
) -> dict[str, object]:
    return {
        "nodes": [
            _node_to_mapping(node, include_evidence_refs) for node in graph.nodes
        ],
        "relationships": [
            _edge_to_mapping(edge, include_evidence_refs)
            for edge in graph.relationships
        ],
        "workflows": [
            _workflow_to_mapping(workflow, include_evidence_refs)
            for workflow in graph.workflows
        ],
    }


def evidence_to_mappings(
    evidence_refs: tuple[EvidenceReference, ...],
) -> list[dict[str, object]]:
    return [
        {
            "evidence_id": item.evidence_id,
            "kind": item.kind,
            "path": item.path,
            "sha256": item.sha256,
            "redacted": item.redacted,
        }
        for item in evidence_refs
    ]


def _node_to_mapping(
    node: GraphNode,
    include_evidence_refs: bool,
) -> dict[str, object]:
    return {
        "node_id": node.node_id,
        "node_type": node.node_type,
        "properties": node.properties,
        "basis": node.basis,
        "evidence_refs": (
            evidence_to_mappings(node.evidence_refs) if include_evidence_refs else []
        ),
    }


def _edge_to_mapping(
    edge: GraphEdge,
    include_evidence_refs: bool,
) -> dict[str, object]:
    return {
        "relationship_id": edge.relationship_id,
        "source_id": edge.source_id,
        "target_id": edge.target_id,
        "relation_type": edge.relation_type,
        "properties": edge.properties,
        "basis": edge.basis,
        "evidence_refs": (
            evidence_to_mappings(edge.evidence_refs) if include_evidence_refs else []
        ),
    }


def _workflow_to_mapping(
    workflow: Workflow,
    include_evidence_refs: bool,
) -> dict[str, object]:
    return {
        "workflow_id": workflow.workflow_id,
        "name": workflow.name,
        "role_ids": list(workflow.role_ids),
        "steps": [_step_to_mapping(step) for step in workflow.steps],
        "dependencies": [
            _dependency_to_mapping(dependency, include_evidence_refs)
            for dependency in workflow.dependencies
        ],
        "basis": workflow.basis,
        "evidence_refs": (
            evidence_to_mappings(workflow.evidence_refs)
            if include_evidence_refs
            else []
        ),
    }


def _step_to_mapping(step: WorkflowStep) -> dict[str, object]:
    return {
        "step_id": step.step_id,
        "order": step.order,
        "action": step.action,
        "request_ids": list(step.request_ids),
    }


def _dependency_to_mapping(
    dependency: WorkflowDependency,
    include_evidence_refs: bool,
) -> dict[str, object]:
    return {
        "before_step_id": dependency.before_step_id,
        "after_step_id": dependency.after_step_id,
        "condition": dependency.condition,
        "basis": dependency.basis,
        "evidence_refs": (
            evidence_to_mappings(dependency.evidence_refs)
            if include_evidence_refs
            else []
        ),
    }
