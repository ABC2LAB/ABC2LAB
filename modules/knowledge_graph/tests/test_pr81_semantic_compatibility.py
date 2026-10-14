"""Independent fixtures for PR #81 shapes under the agreed 0.3 contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from modules.knowledge_graph.exceptions import ContractValidationError
from modules.knowledge_graph.models import (
    GraphSource,
    GraphState,
    IngestRequest,
    SemanticGraph,
)
from modules.knowledge_graph.query_results import graph_to_snapshot
from modules.knowledge_graph.service import (
    execute_ingest,
    prepare_ingest,
    prepare_ingest_operation,
)
from modules.knowledge_graph.utils.hashing import calculate_sha256
from modules.knowledge_graph.utils.validation import load_json

PR81_FIXTURE = (
    Path(__file__).parent
    / "fixtures/semantic_analyzer_pr81/semantic_analysis.json"
)
DISCOVERED_ENDPOINT_ID = "opaque-discovered-endpoint"


@pytest.fixture
def pr81_artifact() -> dict[str, Any]:
    return load_json(PR81_FIXTURE)


def _write_artifact(tmp_path: Path, artifact: dict[str, Any]) -> Path:
    path = tmp_path / "semantic_analysis.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    return path


def test_pr81_fixture_preserves_nodes_edges_and_workflows() -> None:
    artifact, graph = prepare_ingest(PR81_FIXTURE)

    snapshot = graph_to_snapshot(graph, include_evidence_refs=True)

    assert artifact["schema_version"] == "0.3.0"
    assert snapshot["nodes"] == artifact["data"]["nodes"]
    assert snapshot["relationships"] == artifact["data"]["relationships"]
    assert snapshot["workflows"] == artifact["data"]["workflows"]


def test_discovery_never_creates_request_observations_or_access() -> None:
    artifact, graph = prepare_ingest(PR81_FIXTURE)
    snapshot = graph_to_snapshot(graph, include_evidence_refs=True)
    node_by_id = {node["node_id"]: node for node in snapshot["nodes"]}

    assert node_by_id[DISCOVERED_ENDPOINT_ID]["basis"] == "inferred"
    parameter = node_by_id["opaque-discovered-parameter"]
    assert parameter["basis"] == "inferred"
    assert parameter["properties"]["value_type"] == "unknown"
    assert all(
        observation.endpoint_id != DISCOVERED_ENDPOINT_ID
        for observation in graph.request_observations
    )
    assert {item.request_id for item in graph.request_observations} == {
        item["request_id"] for item in artifact["data"]["normalized_requests"]
    }
    assert not any(
        edge["relation_type"] == "ACCESS"
        and edge["target_id"] == DISCOVERED_ENDPOINT_ID
        for edge in snapshot["relationships"]
    )
    discovered_edges = [
        edge for edge in snapshot["relationships"]
        if edge["source_id"] == DISCOVERED_ENDPOINT_ID
        or edge["target_id"] == DISCOVERED_ENDPOINT_ID
    ]
    assert {edge["relation_type"] for edge in discovered_edges} == {"CALL", "USE"}
    assert all(edge["basis"] == "inferred" for edge in discovered_edges)


def test_observed_ownership_basis_and_resource_targets_are_preserved() -> None:
    _, graph = prepare_ingest(PR81_FIXTURE)
    snapshot = graph_to_snapshot(graph, include_evidence_refs=True)
    node_by_id = {node["node_id"]: node for node in snapshot["nodes"]}
    ownerships = [
        edge for edge in snapshot["relationships"]
        if edge["relation_type"] == "OWNS"
    ]

    assert ownerships
    for edge in ownerships:
        assert edge["basis"] == "observed"
        assert node_by_id[edge["source_id"]]["node_type"] == "User"
        resource = node_by_id[edge["target_id"]]
        assert resource["node_type"] == "Resource"
        assert resource["properties"]["resource_scope"] == "instance"


@pytest.mark.parametrize("location", ["query", "body"])
def test_non_path_instance_identity_is_preserved(
    pr81_artifact: dict[str, Any],
    location: str,
) -> None:
    _, graph = prepare_ingest(PR81_FIXTURE)
    resource_id = f"opaque-{location}-resource"
    request = next(
        item for item in pr81_artifact["data"]["normalized_requests"]
        if item["resource_ids"] == [resource_id]
    )
    original = next(
        item for item in pr81_artifact["data"]["nodes"]
        if item["node_id"] == resource_id
    )
    snapshot = graph_to_snapshot(graph, include_evidence_refs=True)
    stored = next(
        node for node in snapshot["nodes"] if node["node_id"] == resource_id
    )
    observation = next(
        item for item in graph.request_observations
        if item.request_id == request["request_id"]
    )

    assert {
        parameter["location"] for parameter in request["parameters"]
    } == {location}
    assert stored == original
    assert observation.resource_ids == (resource_id,)
    assert all(
        isinstance(identifier["value"], str)
        for identifier in stored["properties"]["match_key"]["identifiers"]
    )


def test_composite_identifier_order_is_not_rewritten(
    pr81_artifact: dict[str, Any],
    tmp_path: Path,
) -> None:
    resource = next(
        node for node in pr81_artifact["data"]["nodes"]
        if node["node_id"] == "opaque-body-resource"
    )
    identifiers = resource["properties"]["match_key"]["identifiers"]
    assert len(identifiers) > 1
    identifiers.reverse()

    _, graph = prepare_ingest(_write_artifact(tmp_path, pr81_artifact))
    stored = next(node for node in graph.nodes if node.node_id == resource["node_id"])

    assert stored.properties["match_key"]["identifiers"] == identifiers


@pytest.mark.parametrize("field", ["source_id", "target_id"])
def test_discovered_edges_reject_dangling_references(
    pr81_artifact: dict[str, Any],
    tmp_path: Path,
    field: str,
) -> None:
    edge = next(
        edge for edge in pr81_artifact["data"]["relationships"]
        if edge["relationship_id"] == "discovered-use"
    )
    edge[field] = "missing-node"

    with pytest.raises(ContractValidationError, match=f"관계 {field}"):
        prepare_ingest(_write_artifact(tmp_path, pr81_artifact))


@pytest.mark.parametrize("schema_version", ["0.1.0", "0.2.0", "0.4.0"])
def test_pr81_shape_does_not_bypass_runtime_version_validation(
    pr81_artifact: dict[str, Any],
    tmp_path: Path,
    schema_version: str,
) -> None:
    pr81_artifact["schema_version"] = schema_version

    with pytest.raises(ContractValidationError, match="Schema"):
        prepare_ingest(_write_artifact(tmp_path, pr81_artifact))


class RecordingRepository:
    def __init__(self) -> None:
        self.graph: SemanticGraph | None = None
        self.source: GraphSource | None = None

    def ingest(
        self,
        graph_id: str,
        run_id: str,
        graph: SemanticGraph,
        source: GraphSource,
    ) -> GraphState:
        self.graph = graph
        self.source = source
        return GraphState(graph_id, 1, True)


def test_ingest_service_passes_pr81_graph_to_owned_repository_stub(
    pr81_artifact: dict[str, Any],
) -> None:
    prepared = prepare_ingest_operation(
        IngestRequest(
            input_path=PR81_FIXTURE,
            expected_sha256=calculate_sha256(PR81_FIXTURE),
            run_id=pr81_artifact["run_id"],
            iteration=pr81_artifact["iteration"],
            mode=pr81_artifact["mode"],
        )
    )
    repository = RecordingRepository()

    response = execute_ingest(
        prepared, repository,  # type: ignore[arg-type]
        graph_id_factory=lambda: "graph-pr81-fixture",
    )

    assert response.status == "completed"
    assert response.is_ready is True
    assert response.graph_revision == 1
    assert repository.source == prepared.source
    assert repository.graph is not None
    snapshot = graph_to_snapshot(repository.graph, include_evidence_refs=True)
    assert snapshot["nodes"] == pr81_artifact["data"]["nodes"]
    assert snapshot["relationships"] == pr81_artifact["data"]["relationships"]
