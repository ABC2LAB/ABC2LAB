from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator

import pytest

from modules.knowledge_graph.exceptions import (
    ContractValidationError,
    GraphNotFoundError,
    GraphRevisionMismatchError,
    GraphUpdateConflictError,
    GraphUpdateReferenceError,
    VerificationConflictError,
)
from modules.knowledge_graph.models import (
    GraphNode,
    VerificationInputUpdate,
    VerificationRelationship,
    VerificationSource,
)
from modules.knowledge_graph.neo4j_repository import Neo4jGraphRepository
from modules.knowledge_graph.service import prepare_verification
from modules.knowledge_graph.settings import Neo4jSettings
from modules.knowledge_graph.storage import encode_json


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
        revision: int | None = 1,
        applied_records: list[dict[str, Any]] | None = None,
        stored_nodes: dict[str, dict[str, Any]] | None = None,
        conflicting_nodes: list[dict[str, Any]] | None = None,
        stored_edges: list[dict[str, Any]] | None = None,
    ) -> None:
        self.revision = revision
        self.applied_records = applied_records or []
        self.stored_nodes = (
            stored_nodes
            if stored_nodes is not None
            else {
                "kg-user-B": _stored_node("kg-user-B", "User", account_id="account_user"),
                "resource_order_001": _stored_node("resource_order_001", "Resource", "instance"),
            }
        )
        self.conflicting_nodes = conflicting_nodes or []
        self.stored_edges = stored_edges or []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def run(self, query: str, **parameters: Any) -> FakeResult:
        self.calls.append((query, parameters))
        if "SET graph.revision = graph.revision" in query:
            if self.revision is None:
                return FakeResult()
            return FakeResult(single_value={"revision": self.revision})
        if "MATCH (verification:ABC2AppliedVerification" in query:
            return FakeResult(records=self.applied_records)
        if "node.node_type <> record.node_type" in query:
            return FakeResult(records=self.conflicting_nodes)
        scoped_nodes = {
            node_id: node
            for node_id, node in self.stored_nodes.items()
            if node.get("graph_id", parameters.get("graph_id")) == parameters.get("graph_id")
            and node.get("run_id", parameters.get("run_id")) == parameters.get("run_id")
        }
        if "WHERE node.node_type = 'User'" in query:
            return FakeResult(
                records=[node for node in scoped_nodes.values() if node["node_type"] == "User"]
            )
        if "WHERE node.node_id IN $node_ids" in query:
            requested = set(parameters["node_ids"])
            return FakeResult(
                records=[
                    scoped_nodes[node_id]
                    for node_id in sorted(requested & set(scoped_nodes))
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
        self.is_closed = False

    def execute_query(self, query: str, **parameters: Any) -> tuple[list[Any], None, None]:
        return [], None, None

    def session(self, *, database: str) -> FakeSession:
        return FakeSession(self.transaction)

    def close(self) -> None:
        self.is_closed = True


def _stored_node(
    node_id: str,
    node_type: str,
    resource_scope: str | None = None,
    account_id: str | None = None,
) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "node_type": node_type,
        "resource_scope": resource_scope,
        "properties_json": encode_json({"account_id": account_id or node_id}),
    }


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


@pytest.mark.parametrize(
    "account_id",
    ["account_user", "opaque-B", "user:B", "Case.Account", " B ", "resource_order_001"],
)
@pytest.mark.parametrize("relation_type", ["VERIFIED_ACCESS", "VERIFIED_DENIAL"])
def test_account_source_is_resolved_without_rewriting_input(
    fixture_root: Path,
    account_id: str,
    relation_type: str,
) -> None:
    update = _update(fixture_root)
    relationship = replace(
        update.relationships[0],
        source_account_id=account_id,
        relation_type=relation_type,
    )
    update = replace(update, relationships=(relationship,))
    original_update = deepcopy(update)
    transaction = FakeVerificationTransaction()
    transaction.stored_nodes["kg-user-B"]["properties_json"] = encode_json(
        {"account_id": account_id}
    )

    state = _repository(transaction).apply_verification("graph_demo_001", "run_demo_001", update)

    records = _written_relationships(transaction)
    assert state.graph_revision == 2
    assert len(records) == 1
    assert records[0] == {
        "relationship_id": relationship.relationship_id,
        "source_id": "kg-user-B",
        "target_id": relationship.target_id,
        "relation_type": relation_type,
        "properties_json": encode_json(relationship.properties),
        "basis": "verified",
        "evidence_refs_json": encode_json([
            {
                "evidence_id": reference.evidence_id,
                "kind": reference.kind,
                "path": reference.path,
                "sha256": reference.sha256,
                "redacted": reference.redacted,
            }
            for reference in relationship.evidence_refs
        ]),
    }
    assert update.relationships == (relationship,)
    assert update == original_update
    assert relationship.source_account_id == account_id
    assert not hasattr(relationship, "source_id")
    verification_records = [
        parameters
        for query, parameters in transaction.calls
        if "CREATE (:ABC2AppliedVerification" in query
    ]
    assert len(verification_records) == 1
    assert verification_records[0]["source_artifact_id"] == update.source.artifact_id
    assert verification_records[0]["source_sha256"] == update.source.sha256
    assert verification_records[0]["source_graph_revision"] == update.source_graph_revision


def test_account_lookup_and_relationship_writes_share_locked_transaction(
    fixture_root: Path,
) -> None:
    transaction = FakeVerificationTransaction()
    _repository(transaction).apply_verification(
        "graph_demo_001", "run_demo_001", _update(fixture_root)
    )

    lookup_calls = [
        (index, query, parameters)
        for index, (query, parameters) in enumerate(transaction.calls)
        if "WHERE node.node_type = 'User'" in query
    ]
    assert len(lookup_calls) == 1
    lookup_index, query, parameters = lookup_calls[0]
    assert "graph_id: $graph_id, run_id: $run_id" in query
    assert parameters == {"graph_id": "graph_demo_001", "run_id": "run_demo_001"}
    assert "SET graph.revision = graph.revision" in transaction.calls[0][0]
    assert all(
        index > lookup_index
        for index, (query, _) in enumerate(transaction.calls)
        if "MERGE " in query or "CREATE (:ABC2AppliedVerification" in query
    )


def test_actor_account_does_not_resolve_to_resource_owner(fixture_root: Path) -> None:
    transaction = FakeVerificationTransaction()
    transaction.stored_nodes = {
        "kg-owner-A": _stored_node("kg-owner-A", "User", account_id="account_A"),
        **transaction.stored_nodes,
    }

    _repository(transaction).apply_verification(
        "graph_demo_001", "run_demo_001", _update(fixture_root)
    )

    assert _written_relationships(transaction)[0]["source_id"] == "kg-user-B"


def test_multiple_account_sources_use_one_user_index(fixture_root: Path) -> None:
    update = _update(fixture_root)
    second_relationship = replace(
        update.relationships[0],
        relationship_id="another-verified-relation",
        source_account_id="account_A",
        relation_type="VERIFIED_DENIAL",
    )
    update = replace(update, relationships=(*update.relationships, second_relationship))
    transaction = FakeVerificationTransaction()
    transaction.stored_nodes["kg-user-A"] = _stored_node(
        "kg-user-A", "User", account_id="account_A"
    )

    _repository(transaction).apply_verification("graph_demo_001", "run_demo_001", update)

    assert {record["source_id"] for record in _written_relationships(transaction)} == {
        "kg-user-A", "kg-user-B"
    }
    assert sum("WHERE node.node_type = 'User'" in query for query, _ in transaction.calls) == 1


@pytest.mark.parametrize("account_id", ["missing-B", "user:account_user", "kg-user-B"])
def test_unresolved_account_source_is_rejected_before_graph_updates(
    fixture_root: Path,
    account_id: str,
) -> None:
    update = _update(fixture_root)
    relationship = next(iter(update.relationships))
    update = replace(
        update,
        relationships=(replace(relationship, source_account_id=account_id),),
    )
    transaction = FakeVerificationTransaction()
    with pytest.raises(GraphUpdateReferenceError, match="source_account_id"):
        _repository(transaction).apply_verification("graph_demo_001", "run_demo_001", update)

    _assert_no_graph_updates(transaction)


@pytest.mark.parametrize("account_id", ["Account_User", " account_user", "account_user "])
def test_account_lookup_requires_exact_original_id(fixture_root: Path, account_id: str) -> None:
    update = _update(fixture_root)
    update = replace(
        update,
        relationships=(replace(update.relationships[0], source_account_id=account_id),),
    )
    transaction = FakeVerificationTransaction()

    with pytest.raises(GraphUpdateReferenceError, match="source_account_id"):
        _repository(transaction).apply_verification("graph_demo_001", "run_demo_001", update)

    _assert_no_graph_updates(transaction)


@pytest.mark.parametrize(
    "other_scope",
    [
        {"run_id": "different-run"},
        {"graph_id": "different-graph"},
        {"run_id": "different-run", "graph_id": "different-graph"},
    ],
)
def test_account_source_cannot_use_user_from_other_scope(
    fixture_root: Path,
    other_scope: dict[str, str],
) -> None:
    transaction = FakeVerificationTransaction()
    transaction.stored_nodes["kg-user-B"].update(other_scope)

    with pytest.raises(GraphUpdateReferenceError, match="source_account_id"):
        _repository(transaction).apply_verification(
            "graph_demo_001", "run_demo_001", _update(fixture_root)
        )

    _assert_no_graph_updates(transaction)


@pytest.mark.parametrize(
    "other_scope",
    [
        {"run_id": "different-run"},
        {"graph_id": "different-graph"},
        {"run_id": "different-run", "graph_id": "different-graph"},
    ],
)
def test_foreign_scope_account_does_not_conflict_with_local_user(
    fixture_root: Path,
    other_scope: dict[str, str],
) -> None:
    transaction = FakeVerificationTransaction()
    transaction.stored_nodes["foreign-user"] = {
        **transaction.stored_nodes["kg-user-B"],
        "node_id": "foreign-user",
        **other_scope,
    }

    state = _repository(transaction).apply_verification(
        "graph_demo_001", "run_demo_001", _update(fixture_root)
    )

    assert state.is_applied is True
    assert _written_relationships(transaction)[0]["source_id"] == "kg-user-B"


@pytest.mark.parametrize("has_duplicate_node_id", [False, True])
def test_duplicate_user_identity_is_rejected(
    fixture_root: Path,
    has_duplicate_node_id: bool,
) -> None:
    transaction = FakeVerificationTransaction()
    transaction.stored_nodes["other-row"] = {
        **transaction.stored_nodes["kg-user-B"],
        "node_id": "kg-user-B" if has_duplicate_node_id else "kg-user-other",
        "properties_json": encode_json({
            "account_id": "account_A" if has_duplicate_node_id else "account_user",
        }),
    }

    with pytest.raises(GraphUpdateReferenceError, match="중복"):
        _repository(transaction).apply_verification(
            "graph_demo_001", "run_demo_001", _update(fixture_root)
        )

    _assert_no_graph_updates(transaction)


@pytest.mark.parametrize(
    "properties_json",
    [
        None,
        "{",
        "null",
        "[]",
        "{}",
        '{"account_id":null}',
        '{"account_id":false}',
        '{"account_id":12}',
        '{"account_id":""}',
    ],
)
def test_invalid_stored_user_account_is_rejected(
    fixture_root: Path,
    properties_json: str | None,
) -> None:
    transaction = FakeVerificationTransaction()
    transaction.stored_nodes["kg-user-B"]["properties_json"] = properties_json

    with pytest.raises(GraphUpdateReferenceError, match="기존 User"):
        _repository(transaction).apply_verification(
            "graph_demo_001", "run_demo_001", _update(fixture_root)
        )

    _assert_no_graph_updates(transaction)


@pytest.mark.parametrize("node_id", [None, "", 12])
def test_invalid_stored_user_node_id_is_rejected(fixture_root: Path, node_id: Any) -> None:
    transaction = FakeVerificationTransaction()
    transaction.stored_nodes["kg-user-B"]["node_id"] = node_id

    with pytest.raises(GraphUpdateReferenceError, match="node_id"):
        _repository(transaction).apply_verification(
            "graph_demo_001", "run_demo_001", _update(fixture_root)
        )

    _assert_no_graph_updates(transaction)


def test_missing_source_is_not_created_by_verifier_nodes(fixture_root: Path) -> None:
    update = _update(fixture_root)
    update = replace(
        update,
        nodes=(_verified_user(update, "kg-new-user", "new-account"),),
        relationships=(replace(update.relationships[0], source_account_id="new-account"),),
    )
    transaction = FakeVerificationTransaction()

    with pytest.raises(GraphUpdateReferenceError, match="source_account_id"):
        _repository(transaction).apply_verification("graph_demo_001", "run_demo_001", update)

    _assert_no_graph_updates(transaction)


@pytest.mark.parametrize("account_id", ["account_A", None])
def test_user_update_cannot_replace_source_account_identity(
    fixture_root: Path,
    account_id: str | None,
) -> None:
    update = _update(fixture_root)
    update = replace(update, nodes=(_verified_user(update, "kg-user-B", account_id),))
    transaction = FakeVerificationTransaction()
    error_type = GraphUpdateConflictError if account_id is not None else GraphUpdateReferenceError

    with pytest.raises(error_type, match="account_id"):
        _repository(transaction).apply_verification("graph_demo_001", "run_demo_001", update)

    _assert_no_graph_updates(transaction)


def test_user_update_cannot_create_duplicate_account(fixture_root: Path) -> None:
    update = _update(fixture_root)
    update = replace(update, nodes=(_verified_user(update, "kg-user-other", "account_user"),))
    transaction = FakeVerificationTransaction()

    with pytest.raises(GraphUpdateConflictError, match="중복"):
        _repository(transaction).apply_verification("graph_demo_001", "run_demo_001", update)

    _assert_no_graph_updates(transaction)


def test_user_updates_cannot_duplicate_each_other(fixture_root: Path) -> None:
    update = _update(fixture_root)
    update = replace(
        update,
        nodes=(
            _verified_user(update, "kg-new-user-A", "new-account"),
            _verified_user(update, "kg-new-user-B", "new-account"),
        ),
    )
    transaction = FakeVerificationTransaction()

    with pytest.raises(GraphUpdateConflictError, match="중복"):
        _repository(transaction).apply_verification("graph_demo_001", "run_demo_001", update)

    _assert_no_graph_updates(transaction)


def test_user_metadata_update_preserves_account_identity(fixture_root: Path) -> None:
    update = _update(fixture_root)
    user = _verified_user(update, "kg-user-B", "account_user")
    update = replace(
        update,
        nodes=(replace(user, properties={**user.properties, "verified_note": "test"}),),
    )
    transaction = FakeVerificationTransaction()

    state = _repository(transaction).apply_verification("graph_demo_001", "run_demo_001", update)

    assert state.is_applied is True
    assert _written_relationships(transaction)[0]["source_id"] == "kg-user-B"


def test_user_nodes_only_update_still_checks_duplicate_accounts(fixture_root: Path) -> None:
    update = _update(fixture_root)
    update = replace(
        update,
        nodes=(_verified_user(update, "kg-user-other", "account_user"),),
        relationships=(),
    )
    transaction = FakeVerificationTransaction()

    with pytest.raises(GraphUpdateConflictError, match="중복"):
        _repository(transaction).apply_verification("graph_demo_001", "run_demo_001", update)

    _assert_no_graph_updates(transaction)


def test_new_user_nodes_only_update_is_allowed_with_unique_account(fixture_root: Path) -> None:
    update = _update(fixture_root)
    update = replace(
        update,
        nodes=(_verified_user(update, "kg-new-user", "new-account"),),
        relationships=(),
    )
    transaction = FakeVerificationTransaction()

    state = _repository(transaction).apply_verification(
        "graph_demo_001", "run_demo_001", update
    )

    assert state.is_applied is True
    assert state.graph_revision == 2
    assert _written_relationships(transaction) == []


def test_non_user_nodes_only_update_does_not_load_user_index(fixture_root: Path) -> None:
    update = _update(fixture_root)
    node = _verified_user(update, "kg-new-page", "unused")
    update = replace(
        update,
        nodes=(replace(node, node_type="Page", properties={}),),
        relationships=(),
    )
    transaction = FakeVerificationTransaction()

    state = _repository(transaction).apply_verification("graph_demo_001", "run_demo_001", update)

    assert state.graph_revision == 2
    assert not any("WHERE node.node_type = 'User'" in query for query, _ in transaction.calls)


def test_existing_relationship_conflict_uses_resolved_user_node(fixture_root: Path) -> None:
    transaction = FakeVerificationTransaction(stored_edges=[{
        "relationship_id": "relationship_verified_access_001",
        "source_id": "different-kg-user",
        "target_id": "resource_order_001",
        "relation_type": "VERIFIED_ACCESS",
    }])

    with pytest.raises(GraphUpdateConflictError, match="relationship_id"):
        _repository(transaction).apply_verification(
            "graph_demo_001", "run_demo_001", _update(fixture_root)
        )

    _assert_no_graph_updates(transaction)


def test_existing_relationship_with_same_resolved_identity_is_allowed(
    fixture_root: Path,
) -> None:
    transaction = FakeVerificationTransaction(stored_edges=[{
        "relationship_id": "relationship_verified_access_001",
        "source_id": "kg-user-B",
        "target_id": "resource_order_001",
        "relation_type": "VERIFIED_ACCESS",
    }])

    state = _repository(transaction).apply_verification(
        "graph_demo_001", "run_demo_001", _update(fixture_root)
    )

    assert state.is_applied is True


def test_existing_relationship_target_cannot_be_changed(fixture_root: Path) -> None:
    transaction = FakeVerificationTransaction(
        stored_edges=[{
            "relationship_id": "relationship_verified_access_001",
            "source_id": "kg-user-B",
            "target_id": "another-resource",
            "relation_type": "VERIFIED_ACCESS",
        }],
    )

    with pytest.raises(GraphUpdateConflictError, match="relationship_id"):
        _repository(transaction).apply_verification(
            "graph_demo_001", "run_demo_001", _update(fixture_root)
        )

    _assert_no_graph_updates(transaction)


def test_empty_account_input_keeps_existing_no_op_behavior(fixture_root: Path) -> None:
    update = replace(
        _update(fixture_root),
        verification_ids=(),
        nodes=(),
        relationships=(),
    )
    transaction = FakeVerificationTransaction()

    state = _repository(transaction).apply_verification(
        "graph_demo_001", "run_demo_001", update
    )

    assert state.graph_revision == 1
    assert state.is_applied is False
    assert not any("MERGE (source)-[stored:" in query for query, _ in transaction.calls)
    assert not any("WHERE node.node_type = 'User'" in query for query, _ in transaction.calls)


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
    assert not any("WHERE node.node_type = 'User'" in query for query, _ in transaction.calls)


def test_apply_verification_keeps_revision_for_empty_update(
    fixture_root: Path,
) -> None:
    source_update = _update(fixture_root)
    empty_update = VerificationInputUpdate(
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
    transaction = FakeVerificationTransaction(revision=2)
    repository = _repository(transaction)

    with pytest.raises(GraphRevisionMismatchError):
        repository.apply_verification(
            "graph_demo_001",
            "run_demo_001",
            _update(fixture_root),
        )

    assert not any("WHERE node.node_type = 'User'" in query for query, _ in transaction.calls)
    _assert_no_graph_updates(transaction)


def test_missing_graph_is_rejected_before_account_lookup(fixture_root: Path) -> None:
    transaction = FakeVerificationTransaction(revision=None)

    with pytest.raises(GraphNotFoundError):
        _repository(transaction).apply_verification(
            "graph_demo_001", "run_demo_001", _update(fixture_root)
        )

    assert not any("WHERE node.node_type = 'User'" in query for query, _ in transaction.calls)
    _assert_no_graph_updates(transaction)


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


def test_partial_verification_replay_is_rejected_before_account_lookup(fixture_root: Path) -> None:
    update = _update(fixture_root)
    update = replace(update, verification_ids=(*update.verification_ids, "another-verification"))
    transaction = FakeVerificationTransaction(
        applied_records=[{
            "verification_id": "verification_001",
            "source_artifact_id": update.source.artifact_id,
            "source_sha256": update.source.sha256,
        }],
    )

    with pytest.raises(VerificationConflictError, match="일부"):
        _repository(transaction).apply_verification(
            "graph_demo_001", "run_demo_001", update
        )

    assert not any("WHERE node.node_type = 'User'" in query for query, _ in transaction.calls)
    _assert_no_graph_updates(transaction)


def test_apply_verification_rejects_missing_relationship_node(
    fixture_root: Path,
) -> None:
    transaction = FakeVerificationTransaction(
        stored_nodes={
            "account_user": _stored_node("account_user", "User"),
        }
    )

    with pytest.raises(GraphUpdateReferenceError):
        _repository(transaction).apply_verification(
            "graph_demo_001",
            "run_demo_001",
            _update(fixture_root),
        )


@pytest.mark.parametrize(
    ("stored_nodes", "error_pattern"),
    [
        (
            {
                "account_user": _stored_node("account_user", "Role"),
                "resource_order_001": _stored_node(
                    "resource_order_001",
                    "Resource",
                    "instance",
                ),
            },
            "source_account_id",
        ),
        (
            {
                "account_user": _stored_node("account_user", "User"),
                "resource_order_001": _stored_node(
                    "resource_order_001",
                    "Endpoint",
                ),
            },
            "Resource",
        ),
        (
            {
                "account_user": _stored_node("account_user", "User"),
                "resource_order_001": _stored_node(
                    "resource_order_001",
                    "Resource",
                    "type",
                ),
            },
            "instance",
        ),
    ],
)
def test_apply_verification_requires_user_to_resource_instance(
    fixture_root: Path,
    stored_nodes: dict[str, dict[str, Any]],
    error_pattern: str,
) -> None:
    transaction = FakeVerificationTransaction(stored_nodes=stored_nodes)

    with pytest.raises(GraphUpdateReferenceError, match=error_pattern):
        _repository(transaction).apply_verification(
            "graph_demo_001",
            "run_demo_001",
            _update(fixture_root),
        )


def test_apply_verification_rejects_new_resource_node(fixture_root: Path) -> None:
    source_update = _update(fixture_root)
    update = VerificationInputUpdate(
        source=source_update.source,
        source_graph_revision=source_update.source_graph_revision,
        verification_ids=source_update.verification_ids,
        nodes=(
            GraphNode(
                node_id="resource_new_001",
                node_type="Resource",
                properties={
                    "resource_key": "order",
                    "resource_scope": "instance",
                    "match_key": {
                        "resource_key": "order",
                        "identifiers": [
                            {"key": "order_id", "value": "new-001"},
                        ],
                    },
                },
                basis="verified",
                evidence_refs=(),
            ),
        ),
        relationships=source_update.relationships,
    )

    with pytest.raises(ContractValidationError, match="Resource 노드"):
        _repository(FakeVerificationTransaction()).apply_verification(
            "graph_demo_001",
            "run_demo_001",
            update,
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


def _update(fixture_root: Path) -> VerificationInputUpdate:
    artifact = prepare_verification(
        fixture_root / "verifier" / "verification_results.json"
    )
    graph_updates = artifact["data"]["graph_updates"]
    return VerificationInputUpdate(
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
            VerificationRelationship.from_mapping(item)
            for item in graph_updates["relationships"]
        ),
    )


def _assert_no_graph_updates(transaction: FakeVerificationTransaction) -> None:
    assert not any(
        "MERGE " in query
        or "CREATE (:ABC2AppliedVerification" in query
        or "WHERE graph.revision = $current_revision" in query
        for query, _ in transaction.calls
    )


def _written_relationships(transaction: FakeVerificationTransaction) -> list[dict[str, Any]]:
    return [
        record
        for query, parameters in transaction.calls
        if "RETURN count(stored) AS updated_count" in query
        for record in parameters["records"]
    ]


def _verified_user(
    update: VerificationInputUpdate,
    node_id: str,
    account_id: str | None,
) -> GraphNode:
    return GraphNode(
        node_id=node_id,
        node_type="User",
        properties={"account_id": account_id},
        basis="verified",
        evidence_refs=update.relationships[0].evidence_refs,
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
