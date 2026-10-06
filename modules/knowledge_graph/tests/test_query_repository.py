from __future__ import annotations

from typing import Any

import pytest

from modules.knowledge_graph.exceptions import ContractValidationError
from modules.knowledge_graph.neo4j_repository import Neo4jGraphRepository
from modules.knowledge_graph.settings import Neo4jSettings
from modules.knowledge_graph.storage import encode_json


class FakeQueryTransaction:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def run(self, query: str, **parameters: Any) -> list[dict[str, Any]]:
        self.calls.append((query, parameters))
        return self.records


class FakeQuerySession:
    def __init__(self, transaction: FakeQueryTransaction) -> None:
        self.transaction = transaction

    def __enter__(self) -> "FakeQuerySession":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute_read(self, callback: Any, *args: Any) -> Any:
        return callback(self.transaction, *args)


class FakeQueryDriver:
    def __init__(self, transaction: FakeQueryTransaction) -> None:
        self.transaction = transaction

    def session(self, *, database: str) -> FakeQuerySession:
        return FakeQuerySession(self.transaction)

    def close(self) -> None:
        return None


def test_resource_ownership_uses_parameterized_template() -> None:
    transaction = FakeQueryTransaction(
        [
            {
                "resource_id": "resource_001",
                "owner_account_id": "account_001",
                "basis": "observed",
                "evidence_refs_json": "[]",
            }
        ]
    )
    repository = _repository(transaction)

    rows = repository.query(
        "graph_001",
        "run_001",
        "resource_ownership",
        {"account_ids": ["account_001"], "resource_ids": []},
    )

    assert rows[0]["owner_account_id"] == "account_001"
    query, parameters = transaction.calls[0]
    assert "account_001" not in query
    assert parameters["account_ids"] == ["account_001"]
    assert "edge:OWNS" in query


def test_role_resource_access_maps_stored_properties() -> None:
    transaction = FakeQueryTransaction(
        [
            {
                "account_id": "account_001",
                "role_id": "role_001",
                "endpoint_id": "endpoint_001",
                "resource_id": None,
                "access_type": "ACCESS",
                "basis": "observed",
                "properties_json": encode_json({"action": "read_item"}),
                "endpoint_properties_json": "{}",
                "evidence_refs_json": "[]",
            }
        ]
    )
    repository = _repository(transaction)

    rows = repository.query(
        "graph_001",
        "run_001",
        "role_resource_access",
        {"role_ids": []},
    )

    assert rows == [
        {
            "account_id": "account_001",
            "role_id": "role_001",
            "endpoint_id": "endpoint_001",
            "resource_id": None,
            "action": "read_item",
            "access_observed": True,
            "evidence_refs": [],
        }
    ]
    query, _ = transaction.calls[0]
    assert "MATCH (account)-[access]->" in query
    assert "actor = account OR actor = role" not in query


def test_workflow_dependencies_returns_typed_rows() -> None:
    transaction = FakeQueryTransaction(
        [
            {
                "workflow_id": "workflow_001",
                "before_step_id": "step_001",
                "after_step_id": "step_002",
                "condition": "payment completed",
                "basis": "inferred",
                "evidence_refs_json": "[]",
            }
        ]
    )
    repository = _repository(transaction)

    rows = repository.query(
        "graph_001",
        "run_001",
        "workflow_dependencies",
        {"workflow_ids": ["workflow_001"]},
    )

    assert rows[0]["before_step_id"] == "step_001"
    assert rows[0]["basis"] == "inferred"


def test_query_rejects_raw_or_unknown_query_key() -> None:
    repository = _repository(FakeQueryTransaction([]))

    with pytest.raises(ContractValidationError, match="query_key"):
        repository.query(
            "graph_001",
            "run_001",
            "MATCH (node) RETURN node",
            {},
        )


@pytest.mark.parametrize(
    ("query_key", "parameters"),
    [
        ("resource_ownership", {"account_ids": [], "extra": []}),
        ("role_resource_access", {"role_ids": "role_001"}),
        ("structure_snapshot", {"include_evidence_refs": "true"}),
    ],
)
def test_query_rejects_invalid_parameters(
    query_key: str,
    parameters: dict[str, Any],
) -> None:
    repository = _repository(FakeQueryTransaction([]))

    with pytest.raises(ContractValidationError):
        repository.query("graph_001", "run_001", query_key, parameters)


def _repository(transaction: FakeQueryTransaction) -> Neo4jGraphRepository:
    settings = Neo4jSettings(
        uri="bolt://localhost:7687",
        username="neo4j",
        password="test-password",
        database="neo4j",
    )
    return Neo4jGraphRepository(  # type: ignore[arg-type]
        settings,
        driver=FakeQueryDriver(transaction),
    )
