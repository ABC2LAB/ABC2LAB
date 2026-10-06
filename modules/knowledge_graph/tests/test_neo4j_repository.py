from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from modules.knowledge_graph.exceptions import GraphAlreadyExistsError
from modules.knowledge_graph.neo4j_repository import (
    SCHEMA_QUERIES,
    Neo4jGraphRepository,
)
from modules.knowledge_graph.service import prepare_ingest
from modules.knowledge_graph.settings import Neo4jSettings


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
    def __init__(self, existing_revision: int | None = None) -> None:
        self.existing_revision = existing_revision
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def run(self, query: str, **parameters: Any) -> FakeResult:
        self.calls.append((query, parameters))
        if "RETURN graph.graph_id AS graph_id" in query:
            if self.existing_revision is None:
                return FakeResult()
            return FakeResult({"graph_id": "existing_graph"})
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

    revision = repository.ingest("graph_demo_001", "run_demo_001", graph)

    assert revision == 1
    assert len(driver.schema_queries) == len(SCHEMA_QUERIES)
    assert set(driver.database_names) == {"neo4j"}
    assert len(transaction.calls) == 7
    for _, parameters in transaction.calls[1:]:
        assert parameters["graph_id"] == "graph_demo_001"
        assert parameters["run_id"] == "run_demo_001"

    node_call = next(
        call
        for call in transaction.calls
        if "ABC2Entity" in call[0] and "UNWIND" in call[0] and "MATCH" not in call[0]
    )
    relationship_call = next(
        call for call in transaction.calls if "relationship_id" in call[0]
    )
    assert len(node_call[1]["records"]) == 4
    assert len(relationship_call[1]["records"]) == 1
    assert "run_id: $run_id" in relationship_call[0]


def test_ingest_rejects_existing_graph(fixture_root: Path) -> None:
    _, graph = prepare_ingest(
        fixture_root / "semantic_analyzer" / "semantic_analysis.json"
    )
    driver = FakeDriver(FakeTransaction(existing_revision=1))
    repository = Neo4jGraphRepository(_settings(), driver=driver)  # type: ignore[arg-type]

    with pytest.raises(GraphAlreadyExistsError):
        repository.ingest("graph_demo_001", "run_demo_001", graph)


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
