from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from modules.knowledge_graph.exceptions import (
    GraphAlreadyExistsError,
    GraphStorageVerificationError,
    SourceArtifactConflictError,
)
from modules.knowledge_graph.models import GraphSource
from modules.knowledge_graph.neo4j_repository import (
    SCHEMA_QUERIES,
    Neo4jGraphRepository,
)
from modules.knowledge_graph.service import prepare_ingest
from modules.knowledge_graph.settings import Neo4jSettings
from modules.knowledge_graph.storage import decode_json


class FakeResult:
    def __init__(self, single_value: dict[str, Any] | None = None) -> None:
        self.single_value = single_value
        self.was_consumed = False

    def single(self, strict: bool = False) -> dict[str, Any] | None:
        if strict and self.single_value is None:
            raise ValueError("record required")
        return self.single_value

    def consume(self) -> None:
        self.was_consumed = True


class FakeTransaction:
    def __init__(
        self,
        existing_source: dict[str, Any] | None = None,
        has_graph_collision: bool = False,
        counts: dict[str, int] | None = None,
    ) -> None:
        self.existing_source = existing_source
        self.has_graph_collision = has_graph_collision
        self.counts = counts or {
            "request_observations": 1,
            "nodes": 4,
            "relationships": 2,
            "workflows": 1,
            "workflow_steps": 1,
            "workflow_dependencies": 0,
        }
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def run(self, query: str, **parameters: Any) -> FakeResult:
        self.calls.append((query, parameters))
        if "source_artifact_id: $source_artifact_id" in query:
            return FakeResult(self.existing_source)
        if "RETURN graph.graph_id AS graph_id" in query:
            if not self.has_graph_collision:
                return FakeResult()
            return FakeResult({"graph_id": "existing_graph"})
        count_key_by_label = {
            "ABC2RequestObservation": "request_observations",
            "ABC2Entity": "nodes",
            "ABC2Workflow ": "workflows",
            "ABC2WorkflowStep": "workflow_steps",
            "ABC2WorkflowDependency": "workflow_dependencies",
        }
        if "RETURN count(item) AS count" in query:
            key = next(
                count_key
                for label, count_key in count_key_by_label.items()
                if f"item:{label}" in query
            )
            return FakeResult({"count": self.counts[key]})
        if "RETURN count(edge) AS count" in query:
            return FakeResult({"count": self.counts["relationships"]})
        return FakeResult()


class FakeSession:
    def __init__(self, transaction: FakeTransaction) -> None:
        self.transaction = transaction

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute_write(self, callback: Any, *args: Any) -> Any:
        return callback(self.transaction, *args)


class FakeDriver:
    def __init__(self, transaction: FakeTransaction) -> None:
        self.transaction = transaction
        self.schema_queries: list[str] = []
        self.database_names: list[str] = []
        self.is_closed = False

    def execute_query(self, query: str, **parameters: Any) -> tuple[list[Any], None, None]:
        self.schema_queries.append(query)
        self.database_names.append(parameters["database_"])
        return [], None, None

    def session(self, *, database: str) -> FakeSession:
        self.database_names.append(database)
        return FakeSession(self.transaction)

    def verify_connectivity(self, **_: Any) -> None:
        return None

    def close(self) -> None:
        self.is_closed = True


def test_ingest_writes_scoped_graph_records(fixture_root: Path) -> None:
    _, graph = prepare_ingest(
        fixture_root / "semantic_analyzer" / "semantic_analysis.json"
    )
    transaction = FakeTransaction()
    driver = FakeDriver(transaction)
    repository = Neo4jGraphRepository(_settings(), driver=driver)  # type: ignore[arg-type]

    state = repository.ingest(
        "graph_demo_001",
        "run_demo_001",
        graph,
        _source(),
    )

    assert state.graph_id == "graph_demo_001"
    assert state.graph_revision == 1
    assert state.is_created is True
    assert len(driver.schema_queries) == len(SCHEMA_QUERIES)
    assert set(driver.database_names) == {"neo4j"}
    assert len(transaction.calls) == 16
    for _, parameters in transaction.calls:
        assert parameters["run_id"] == "run_demo_001"

    node_call = next(
        call
        for call in transaction.calls
        if "ABC2Entity" in call[0] and "UNWIND" in call[0] and "MATCH" not in call[0]
    )
    relationship_call = next(
        call for call in transaction.calls if "relationship_id" in call[0]
    )
    observation_call = next(
        call
        for call in transaction.calls
        if "CREATE (:ABC2RequestObservation" in call[0]
    )
    assert len(node_call[1]["records"]) == 4
    resource_record = next(
        record
        for record in node_call[1]["records"]
        if record["node_id"] == "resource_order_001"
    )
    assert resource_record["resource_key"] == "order"
    assert resource_record["resource_scope"] == "instance"
    assert decode_json(resource_record["resource_match_key_json"]) == {
        "resource_key": "order",
        "identifiers": [{"key": "order_id", "value": "001"}],
    }
    assert "resource_scope: record.resource_scope" in node_call[0]
    assert "resource_match_key_json: record.resource_match_key_json" in node_call[0]
    assert len(relationship_call[1]["records"]) == 1
    assert "run_id: $run_id" in relationship_call[0]
    assert observation_call[1]["records"] == [
        {
            "request_id": "request_001",
            "account_id": "account_user",
            "role_id": "role_user",
            "user_node_id": "account_user",
            "role_node_id": "role_user",
            "endpoint_id": "endpoint_orders",
            "action": "read_order",
            "resource_ids_json": '["resource_order_001"]',
            "basis": "observed",
            "evidence_refs_json": "[]",
        }
    ]
    metadata_call = next(
        call for call in transaction.calls if "CREATE (:ABC2Graph" in call[0]
    )
    assert metadata_call[1]["source_artifact_id"] == "semantic_demo_001"
    assert metadata_call[1]["source_sha256"] == "a" * 64


def test_ingest_reuses_identical_source_artifact(fixture_root: Path) -> None:
    _, graph = prepare_ingest(
        fixture_root / "semantic_analyzer" / "semantic_analysis.json"
    )
    driver = FakeDriver(
        FakeTransaction(
            existing_source={
                "graph_id": "graph_existing",
                "revision": 3,
                "source_sha256": "a" * 64,
            }
        )
    )
    repository = Neo4jGraphRepository(_settings(), driver=driver)  # type: ignore[arg-type]

    state = repository.ingest("graph_new", "run_demo_001", graph, _source())

    assert state.graph_id == "graph_existing"
    assert state.graph_revision == 3
    assert state.is_created is False


def test_ingest_rejects_source_hash_conflict(fixture_root: Path) -> None:
    _, graph = prepare_ingest(
        fixture_root / "semantic_analyzer" / "semantic_analysis.json"
    )
    transaction = FakeTransaction(
        existing_source={
            "graph_id": "graph_existing",
            "revision": 1,
            "source_sha256": "b" * 64,
        }
    )
    repository = Neo4jGraphRepository(  # type: ignore[arg-type]
        _settings(),
        driver=FakeDriver(transaction),
    )

    with pytest.raises(SourceArtifactConflictError):
        repository.ingest("graph_new", "run_demo_001", graph, _source())


def test_ingest_rejects_generated_graph_collision(fixture_root: Path) -> None:
    _, graph = prepare_ingest(
        fixture_root / "semantic_analyzer" / "semantic_analysis.json"
    )
    repository = Neo4jGraphRepository(  # type: ignore[arg-type]
        _settings(),
        driver=FakeDriver(FakeTransaction(has_graph_collision=True)),
    )

    with pytest.raises(GraphAlreadyExistsError):
        repository.ingest("graph_existing", "run_demo_001", graph, _source())


def test_ingest_rolls_back_when_counts_differ(fixture_root: Path) -> None:
    _, graph = prepare_ingest(
        fixture_root / "semantic_analyzer" / "semantic_analysis.json"
    )
    transaction = FakeTransaction(
        counts={
            "request_observations": 0,
            "nodes": 3,
            "relationships": 1,
            "workflows": 1,
            "workflow_steps": 1,
            "workflow_dependencies": 0,
        }
    )
    repository = Neo4jGraphRepository(  # type: ignore[arg-type]
        _settings(),
        driver=FakeDriver(transaction),
    )

    with pytest.raises(GraphStorageVerificationError):
        repository.ingest("graph_demo_001", "run_demo_001", graph, _source())


def test_repository_context_closes_driver() -> None:
    driver = FakeDriver(FakeTransaction())

    with Neo4jGraphRepository(_settings(), driver=driver):  # type: ignore[arg-type]
        pass

    assert driver.is_closed is True


def _settings() -> Neo4jSettings:
    return Neo4jSettings(
        uri="bolt://localhost:7687",
        username="neo4j",
        password="test-password",
        database="neo4j",
    )


def _source() -> GraphSource:
    return GraphSource(
        artifact_id="semantic_demo_001",
        sha256="a" * 64,
        iteration=0,
        status="completed",
    )
