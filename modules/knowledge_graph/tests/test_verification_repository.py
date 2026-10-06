from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import pytest

from modules.knowledge_graph.exceptions import (
    GraphRevisionMismatchError,
    GraphUpdateConflictError,
    GraphUpdateReferenceError,
    VerificationConflictError,
)
from modules.knowledge_graph.models import (
    GraphEdge,
    GraphNode,
    VerificationSource,
    VerificationUpdate,
)
from modules.knowledge_graph.neo4j_repository import Neo4jGraphRepository
from modules.knowledge_graph.service import prepare_verification
from modules.knowledge_graph.settings import Neo4jSettings


class FakeResult:
    def __init__(
        self,
        records: list[dict[str, Any]] | None = None,
        single_value: dict[str, Any] | None = None,
    ) -> None:
        self.records = records or []
        self.single_value = single_value

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self.records)

    def single(self, strict: bool = False) -> dict[str, Any] | None:
        if strict and self.single_value is None:
            raise ValueError("record required")
        return self.single_value

    def consume(self) -> None:
        return None


class FakeVerificationTransaction:
    def __init__(
        self,
        revision: int = 1,
        applied_records: list[dict[str, Any]] | None = None,
        stored_node_ids: set[str] | None = None,
        conflicting_nodes: list[dict[str, Any]] | None = None,
        stored_edges: list[dict[str, Any]] | None = None,
    ) -> None:
        self.revision = revision
        self.applied_records = applied_records or []
        self.stored_node_ids = stored_node_ids or {
            "account_user",
            "resource_order_001",
        }
        self.conflicting_nodes = conflicting_nodes or []
        self.stored_edges = stored_edges or []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def run(self, query: str, **parameters: Any) -> FakeResult:
        self.calls.append((query, parameters))
        if "SET graph.revision = graph.revision" in query:
            return FakeResult(single_value={"revision": self.revision})
        if "MATCH (verification:ABC2AppliedVerification" in query:
            return FakeResult(records=self.applied_records)
        if "node.node_type <> record.node_type" in query:
            return FakeResult(records=self.conflicting_nodes)
        if "WHERE node.node_id IN $node_ids" in query:
            requested = set(parameters["node_ids"])
            return FakeResult(
                records=[
                    {"node_id": node_id}
                    for node_id in sorted(requested & self.stored_node_ids)
                ]
            )
        if "WHERE edge.relationship_id IN $relationship_ids" in query:
            return FakeResult(records=self.stored_edges)
        if "RETURN count(stored) AS updated_count" in query:
            return FakeResult(
                single_value={"updated_count": len(parameters["records"])}
            )
        if "WHERE graph.revision = $current_revision" in query:
            return FakeResult(single_value={"revision": parameters["next_revision"]})
        return FakeResult()


class FakeSession:
    def __init__(self, transaction: FakeVerificationTransaction) -> None:
        self.transaction = transaction

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute_write(self, callback: Any, *args: Any) -> Any:
        return callback(self.transaction, *args)


class FakeDriver:
    def __init__(self, transaction: FakeVerificationTransaction) -> None:
        self.transaction = transaction

    def execute_query(self, query: str, **parameters: Any) -> tuple[list[Any], None, None]:
        return [], None, None

    def session(self, *, database: str) -> FakeSession:
        return FakeSession(self.transaction)

    def close(self) -> None:
        return None


def test_apply_verification_updates_graph_once(fixture_root: Path) -> None:
    transaction = FakeVerificationTransaction()
    repository = _repository(transaction)

    state = repository.apply_verification(
        "graph_demo_001",
        "run_demo_001",
        _update(fixture_root),
    )

    assert state.previous_graph_revision == 1
    assert state.graph_revision == 2
    assert state.is_applied is True
    assert state.applied_verification_ids == ("verification_001",)
    assert any("ABC2AppliedVerification" in query for query, _ in transaction.calls)
    assert any("MERGE (source)-[stored:VERIFIED_ACCESS" in query for query, _ in transaction.calls)


def test_apply_verification_is_idempotent_for_same_source(fixture_root: Path) -> None:
    update = _update(fixture_root)
    transaction = FakeVerificationTransaction(
        revision=2,
        applied_records=[
            {
                "verification_id": "verification_001",
                "source_artifact_id": update.source.artifact_id,
                "source_sha256": update.source.sha256,
            }
        ],
    )

    state = _repository(transaction).apply_verification(
        "graph_demo_001",
        "run_demo_001",
        update,
    )

    assert state.graph_revision == 2
    assert state.is_applied is False
    assert not any("MERGE (source)-[stored:" in query for query, _ in transaction.calls)


def test_apply_verification_keeps_revision_for_empty_update(
    fixture_root: Path,
) -> None:
    source_update = _update(fixture_root)
    empty_update = VerificationUpdate(
        source=source_update.source,
        source_graph_revision=source_update.source_graph_revision,
        verification_ids=(),
        nodes=(),
        relationships=(),
    )

    state = _repository(FakeVerificationTransaction()).apply_verification(
        "graph_demo_001",
        "run_demo_001",
        empty_update,
    )

    assert state.graph_revision == 1
    assert state.is_applied is False


def test_apply_verification_rejects_stale_revision(fixture_root: Path) -> None:
    repository = _repository(FakeVerificationTransaction(revision=2))

    with pytest.raises(GraphRevisionMismatchError):
        repository.apply_verification(
            "graph_demo_001",
            "run_demo_001",
            _update(fixture_root),
        )


def test_apply_verification_rejects_reused_verification_id(
    fixture_root: Path,
) -> None:
    transaction = FakeVerificationTransaction(
        revision=2,
        applied_records=[
            {
                "verification_id": "verification_001",
                "source_artifact_id": "different_artifact",
                "source_sha256": "0" * 64,
            }
        ],
    )

    with pytest.raises(VerificationConflictError):
        _repository(transaction).apply_verification(
            "graph_demo_001",
            "run_demo_001",
            _update(fixture_root),
        )


def test_apply_verification_rejects_missing_relationship_node(
    fixture_root: Path,
) -> None:
    transaction = FakeVerificationTransaction(stored_node_ids={"account_user"})

    with pytest.raises(GraphUpdateReferenceError):
        _repository(transaction).apply_verification(
            "graph_demo_001",
            "run_demo_001",
            _update(fixture_root),
        )


def test_apply_verification_rejects_relationship_identity_conflict(
    fixture_root: Path,
) -> None:
    transaction = FakeVerificationTransaction(
        stored_edges=[
            {
                "relationship_id": "relationship_verified_access_001",
                "source_id": "account_user",
                "target_id": "resource_order_001",
                "relation_type": "VERIFIED_DENIAL",
            }
        ]
    )

    with pytest.raises(GraphUpdateConflictError):
        _repository(transaction).apply_verification(
            "graph_demo_001",
            "run_demo_001",
            _update(fixture_root),
        )


def _update(fixture_root: Path) -> VerificationUpdate:
    artifact = prepare_verification(
        fixture_root / "verifier" / "verification_results.json"
    )
    graph_updates = artifact["data"]["graph_updates"]
    return VerificationUpdate(
        source=VerificationSource(
            artifact_id=artifact["artifact_id"],
            sha256="a" * 64,
            iteration=artifact["iteration"],
            status=artifact["status"],
        ),
        source_graph_revision=artifact["data"]["source_graph_revision"],
        verification_ids=tuple(graph_updates["source_verification_ids"]),
        nodes=tuple(GraphNode.from_mapping(item) for item in graph_updates["nodes"]),
        relationships=tuple(
            GraphEdge.from_mapping(item) for item in graph_updates["relationships"]
        ),
    )


def _repository(
    transaction: FakeVerificationTransaction,
) -> Neo4jGraphRepository:
    settings = Neo4jSettings(
        uri="bolt://localhost:7687",
        username="neo4j",
        password="test-password",
        database="neo4j",
    )
    return Neo4jGraphRepository(  # type: ignore[arg-type]
        settings,
        driver=FakeDriver(transaction),
    )
