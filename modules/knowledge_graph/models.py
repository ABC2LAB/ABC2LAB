"""Internal immutable models for knowledge graph contract data."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
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
class RequestObservation:
    request_id: str
    account_id: str
    role_id: str
    user_node_id: str
    role_node_id: str
    endpoint_id: str
    action: str
    resource_ids: tuple[str, ...]
    basis: str
    evidence_refs: tuple[EvidenceReference, ...]

    @classmethod
    def from_mapping(
        cls,
        value: dict[str, Any],
        user_node_id_by_account_id: dict[str, str],
        role_node_id_by_role_id: dict[str, str],
    ) -> "RequestObservation":
        return cls(
            request_id=value["request_id"],
            account_id=value["account_id"],
            role_id=value["role_id"],
            user_node_id=user_node_id_by_account_id[value["account_id"]],
            role_node_id=role_node_id_by_role_id[value["role_id"]],
            endpoint_id=value["endpoint_id"],
            action=value["action_meaning"],
            resource_ids=tuple(value["resource_ids"]),
            basis=value["basis"],
            evidence_refs=tuple(
                EvidenceReference.from_mapping(item) for item in value["evidence_refs"]
            ),
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
class VerificationRelationship:
    """Verifier input whose source is an account, not a graph node ID."""

    relationship_id: str
    source_account_id: str
    target_id: str
    relation_type: str
    properties: dict[str, JsonValue]
    basis: str
    evidence_refs: tuple[EvidenceReference, ...]

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "VerificationRelationship":
        return cls(
            relationship_id=value["relationship_id"],
            source_account_id=value["source_account_id"],
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
    request_observations: tuple[RequestObservation, ...]
    nodes: tuple[GraphNode, ...]
    relationships: tuple[GraphEdge, ...]
    workflows: tuple[Workflow, ...]

    @classmethod
    def from_artifact(cls, artifact: dict[str, Any]) -> "SemanticGraph":
        data = artifact["data"]
        user_node_id_by_account_id = {
            item["properties"]["account_id"]: item["node_id"]
            for item in data["nodes"]
            if item["node_type"] == "User"
        }
        role_node_id_by_role_id = {
            item["properties"]["role_id"]: item["node_id"]
            for item in data["nodes"]
            if item["node_type"] == "Role"
        }
        return cls(
            request_observations=tuple(
                RequestObservation.from_mapping(
                    item,
                    user_node_id_by_account_id,
                    role_node_id_by_role_id,
                )
                for item in data["normalized_requests"]
            ),
            nodes=tuple(GraphNode.from_mapping(item) for item in data["nodes"]),
            relationships=tuple(
                GraphEdge.from_mapping(item) for item in data["relationships"]
            ),
            workflows=tuple(Workflow.from_mapping(item) for item in data["workflows"]),
        )


@dataclass(frozen=True)
class IngestRequest:
    input_path: Path
    expected_sha256: str
    run_id: str
    iteration: int
    mode: str


@dataclass(frozen=True)
class GraphSource:
    artifact_id: str
    sha256: str
    iteration: int
    status: str


@dataclass(frozen=True)
class GraphState:
    graph_id: str
    graph_revision: int
    is_created: bool


@dataclass(frozen=True)
class ControlError:
    code: str
    message: str
    item_ref: str | None
    retryable: bool

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "ControlError":
        return cls(
            code=value["code"],
            message=value["message"],
            item_ref=value["item_ref"],
            retryable=value["retryable"],
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "item_ref": self.item_ref,
            "retryable": self.retryable,
        }


@dataclass(frozen=True)
class IngestControlResponse:
    status: str
    graph_id: str | None
    graph_revision: int | None
    is_ready: bool
    errors: tuple[ControlError, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "operation": "ingest",
            "status": self.status,
            "graph_id": self.graph_id,
            "graph_revision": self.graph_revision,
            "is_ready": self.is_ready,
            "errors": [error.to_mapping() for error in self.errors],
        }


@dataclass(frozen=True)
class QueryRequest:
    input_path: Path
    input_relative_path: str
    expected_sha256: str
    output_path: Path
    output_relative_path: str
    run_id: str
    iteration: int
    mode: str


@dataclass(frozen=True)
class QueryDefinition:
    query_id: str
    query_key: str
    parameters: dict[str, Any]

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "QueryDefinition":
        return cls(
            query_id=value["query_id"],
            query_key=value["query_key"],
            parameters=value["parameters"],
        )


@dataclass(frozen=True)
class QueryControlResponse:
    status: str
    artifact_id: str | None
    output_path: str | None
    sha256: str | None
    graph_id: str | None
    graph_revision: int | None
    errors: tuple[ControlError, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "operation": "query",
            "status": self.status,
            "artifact_id": self.artifact_id,
            "output_path": self.output_path,
            "sha256": self.sha256,
            "graph_id": self.graph_id,
            "graph_revision": self.graph_revision,
            "errors": [error.to_mapping() for error in self.errors],
        }


@dataclass(frozen=True)
class VerificationRequest:
    input_path: Path
    expected_sha256: str
    graph_id: str
    run_id: str
    iteration: int
    mode: str


@dataclass(frozen=True)
class VerificationSource:
    artifact_id: str
    sha256: str
    iteration: int
    status: str


@dataclass(frozen=True)
class VerificationInputUpdate:
    """Validated input before account IDs are resolved in the graph scope."""

    source: VerificationSource
    source_graph_revision: int
    verification_ids: tuple[str, ...]
    nodes: tuple[GraphNode, ...]
    relationships: tuple[VerificationRelationship, ...]


@dataclass(frozen=True)
class VerificationUpdate:
    """Storage update whose relationship endpoints are graph node IDs."""

    source: VerificationSource
    source_graph_revision: int
    verification_ids: tuple[str, ...]
    nodes: tuple[GraphNode, ...]
    relationships: tuple[GraphEdge, ...]


@dataclass(frozen=True)
class VerificationState:
    graph_id: str
    previous_graph_revision: int
    graph_revision: int
    applied_verification_ids: tuple[str, ...]
    is_applied: bool


@dataclass(frozen=True)
class VerificationControlResponse:
    status: str
    graph_id: str | None
    previous_graph_revision: int | None
    graph_revision: int | None
    applied_verification_ids: tuple[str, ...]
    is_applied: bool
    errors: tuple[ControlError, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "operation": "apply_verification",
            "status": self.status,
            "graph_id": self.graph_id,
            "previous_graph_revision": self.previous_graph_revision,
            "graph_revision": self.graph_revision,
            "applied_verification_ids": list(self.applied_verification_ids),
            "is_applied": self.is_applied,
            "errors": [error.to_mapping() for error in self.errors],
        }
