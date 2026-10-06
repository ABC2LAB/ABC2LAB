"""Internal immutable models for knowledge graph contract data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True)
class EvidenceReference:
    evidence_id: str
    kind: str
    path: str
    sha256: str
    redacted: bool

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "EvidenceReference":
        return cls(
            evidence_id=value["evidence_id"],
            kind=value["kind"],
            path=value["path"],
            sha256=value["sha256"],
            redacted=value["redacted"],
        )


@dataclass(frozen=True)
class GraphNode:
    node_id: str
    node_type: str
    properties: dict[str, JsonValue]
    basis: str
    evidence_refs: tuple[EvidenceReference, ...]

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "GraphNode":
        return cls(
            node_id=value["node_id"],
            node_type=value["node_type"],
            properties=value["properties"],
            basis=value["basis"],
            evidence_refs=tuple(
                EvidenceReference.from_mapping(item) for item in value["evidence_refs"]
            ),
        )


@dataclass(frozen=True)
class GraphEdge:
    relationship_id: str
    source_id: str
    target_id: str
    relation_type: str
    properties: dict[str, JsonValue]
    basis: str
    evidence_refs: tuple[EvidenceReference, ...]

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "GraphEdge":
        return cls(
            relationship_id=value["relationship_id"],
            source_id=value["source_id"],
            target_id=value["target_id"],
            relation_type=value["relation_type"],
            properties=value["properties"],
            basis=value["basis"],
            evidence_refs=tuple(
                EvidenceReference.from_mapping(item) for item in value["evidence_refs"]
            ),
        )


@dataclass(frozen=True)
class WorkflowStep:
    step_id: str
    order: int
    action: str
    request_ids: tuple[str, ...]

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "WorkflowStep":
        return cls(
            step_id=value["step_id"],
            order=value["order"],
            action=value["action"],
            request_ids=tuple(value["request_ids"]),
        )


@dataclass(frozen=True)
class WorkflowDependency:
    before_step_id: str
    after_step_id: str
    condition: str
    basis: str
    evidence_refs: tuple[EvidenceReference, ...]

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "WorkflowDependency":
        return cls(
            before_step_id=value["before_step_id"],
            after_step_id=value["after_step_id"],
            condition=value["condition"],
            basis=value["basis"],
            evidence_refs=tuple(
                EvidenceReference.from_mapping(item) for item in value["evidence_refs"]
            ),
        )


@dataclass(frozen=True)
class Workflow:
    workflow_id: str
    name: str
    role_ids: tuple[str, ...]
    steps: tuple[WorkflowStep, ...]
    dependencies: tuple[WorkflowDependency, ...]
    basis: str
    evidence_refs: tuple[EvidenceReference, ...]

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "Workflow":
        return cls(
            workflow_id=value["workflow_id"],
            name=value["name"],
            role_ids=tuple(value["role_ids"]),
            steps=tuple(WorkflowStep.from_mapping(item) for item in value["steps"]),
            dependencies=tuple(
                WorkflowDependency.from_mapping(item) for item in value["dependencies"]
            ),
            basis=value["basis"],
            evidence_refs=tuple(
                EvidenceReference.from_mapping(item) for item in value["evidence_refs"]
            ),
        )


@dataclass(frozen=True)
class SemanticGraph:
    nodes: tuple[GraphNode, ...]
    relationships: tuple[GraphEdge, ...]
    workflows: tuple[Workflow, ...]

    @classmethod
    def from_artifact(cls, artifact: dict[str, Any]) -> "SemanticGraph":
        data = artifact["data"]
        return cls(
            nodes=tuple(GraphNode.from_mapping(item) for item in data["nodes"]),
            relationships=tuple(
                GraphEdge.from_mapping(item) for item in data["relationships"]
            ),
            workflows=tuple(Workflow.from_mapping(item) for item in data["workflows"]),
        )
