import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from neo4j import GraphDatabase, ManagedTransaction

from modules.knowledge_graph.entrypoint import run
from modules.knowledge_graph.exceptions import (
    GraphUpdateReferenceError,
    SourceArtifactConflictError,
)
from modules.knowledge_graph.models import (
    GraphSource,
    SemanticGraph,
    VerificationInputUpdate,
    VerificationState,
)
from modules.knowledge_graph.neo4j_repository import GraphCounts, Neo4jGraphRepository
from modules.knowledge_graph.service import prepare_ingest
from modules.knowledge_graph.settings import Neo4jSettings
from modules.knowledge_graph.utils.hashing import calculate_sha256


CURRENT_SEMANTIC_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "semantic_analyzer_current"
    / "semantic_analysis.json"
)
VERIFICATION_RELATIVE_PATH = (
    "artifacts/iteration-000/verifier/verification_results.json"
)
SEMANTIC_RELATIVE_PATH = (
    "artifacts/iteration-000/semantic_analyzer/semantic_analysis.json"
)
ACTOR_NODE_ID = "opaque-user-B"
OWNER_NODE_ID = "opaque-owner-A"
NEW_USER_NODE_ID = "opaque-new-user-C"


@dataclass(frozen=True)
class StoredVerificationGraph:
    revision: int | None
    graph: SemanticGraph
    counts: GraphCounts
    applied_verifications: tuple[dict[str, Any], ...]


@pytest.mark.skipif(
    os.getenv("KG_RUN_NEO4J_INTEGRATION") != "1",
    reason="KG_RUN_NEO4J_INTEGRATION=1일 때만 실제 Neo4j 통합 테스트 실행",
)
def test_ingest_round_trip_with_real_neo4j(fixture_root: Path) -> None:
    input_path = fixture_root / "semantic_analyzer" / "semantic_analysis.json"
    artifact, graph = prepare_ingest(input_path)
    graph_id = f"graph_integration_{uuid4().hex}"
    run_id = f"run_integration_{uuid4().hex}"
    source = GraphSource(
        artifact_id=artifact["artifact_id"],
        sha256=calculate_sha256(input_path),
        iteration=artifact["iteration"],
        status=artifact["status"],
    )
    settings = Neo4jSettings.from_environment()
    is_created = False

    try:
        with Neo4jGraphRepository(settings) as repository:
            repository.verify_connectivity()
            state = repository.ingest(graph_id, run_id, graph, source)
            is_created = True
            repeated_state = repository.ingest(
                f"graph_unused_{uuid4().hex}",
                run_id,
                graph,
                source,
            )
            with pytest.raises(SourceArtifactConflictError):
                repository.ingest(
                    f"graph_unused_{uuid4().hex}",
                    run_id,
                    graph,
                    GraphSource(
                        artifact_id=source.artifact_id,
                        sha256="0" * 64,
                        iteration=source.iteration,
                        status=source.status,
                    ),
                )
            restored = repository.load_graph(graph_id, run_id)
            counts = repository.get_counts(graph_id, run_id)
    finally:
        if is_created:
            with GraphDatabase.driver(
                settings.uri,
                auth=(settings.username, settings.password),
            ) as driver:
                driver.execute_query(
                    "MATCH (item {graph_id: $graph_id, run_id: $run_id}) "
                    "DETACH DELETE item",
                    graph_id=graph_id,
                    run_id=run_id,
                    database_=settings.database,
                )

    assert state.graph_id == graph_id
    assert state.graph_revision == 1
    assert state.is_created is True
    assert repeated_state.graph_id == graph_id
    assert repeated_state.graph_revision == 1
    assert repeated_state.is_created is False
    assert restored.request_observations == graph.request_observations
    assert {node.node_id: node for node in restored.nodes} == {
        node.node_id: node for node in graph.nodes
    }
    assert {
        relationship.relationship_id: relationship
        for relationship in restored.relationships
    } == {
        relationship.relationship_id: relationship
        for relationship in graph.relationships
    }
    assert {workflow.workflow_id: workflow for workflow in restored.workflows} == {
        workflow.workflow_id: workflow for workflow in graph.workflows
    }
    assert counts.request_observations == len(graph.request_observations)
    assert counts.nodes == len(graph.nodes)
    assert counts.relationships == len(graph.relationships)
    assert counts.workflows == len(graph.workflows)
    assert counts.workflow_steps == sum(len(item.steps) for item in graph.workflows)
    assert counts.workflow_dependencies == sum(
        len(item.dependencies) for item in graph.workflows
    )


@pytest.mark.skipif(
    os.getenv("KG_RUN_NEO4J_INTEGRATION") != "1",
    reason="KG_RUN_NEO4J_INTEGRATION=1일 때만 실제 Neo4j 통합 테스트 실행",
)
def test_public_ingest_is_idempotent_with_real_neo4j(
    ingest_run_root: Path,
) -> None:
    relative_path = (
        "artifacts/iteration-000/semantic_analyzer/semantic_analysis.json"
    )
    input_path = ingest_run_root / relative_path
    settings = Neo4jSettings.from_environment()
    graph_id: str | None = None

    try:
        first = _run_public_ingest(ingest_run_root, relative_path)
        graph_id = first["graph_id"]
        repeated = _run_public_ingest(ingest_run_root, relative_path)
        input_path.write_text(
            input_path.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        conflicted = _run_public_ingest(ingest_run_root, relative_path)
    finally:
        if graph_id is not None:
            with GraphDatabase.driver(
                settings.uri,
                auth=(settings.username, settings.password),
            ) as driver:
                driver.execute_query(
                    "MATCH (item {graph_id: $graph_id, run_id: $run_id}) "
                    "DETACH DELETE item",
                    graph_id=graph_id,
                    run_id="run_demo_001",
                    database_=settings.database,
                )

    assert first["status"] == "completed"
    assert first["graph_revision"] == 1
    assert first["is_ready"] is True
    assert repeated == first
    assert conflicted["status"] == "failed"
    assert conflicted["errors"][0]["code"] == "ARTIFACT_CONFLICT"


@pytest.mark.skipif(
    os.getenv("KG_RUN_NEO4J_INTEGRATION") != "1",
    reason="KG_RUN_NEO4J_INTEGRATION=1일 때만 실제 Neo4j 통합 테스트 실행",
)
def test_public_query_returns_all_typed_rows_with_real_neo4j(
    ingest_run_root: Path,
    query_run_root: Path,
) -> None:
    run_root = ingest_run_root
    semantic_relative = (
        "artifacts/iteration-000/semantic_analyzer/semantic_analysis.json"
    )
    semantic_path = run_root / semantic_relative
    _replace_with_current_semantic_fixture(semantic_path)
    settings = Neo4jSettings.from_environment()
    graph_id: str | None = None

    try:
        ingest_response = _run_public_ingest(run_root, semantic_relative)
        assert isinstance(ingest_response["graph_id"], str)
        graph_id = ingest_response["graph_id"]
        query_path = query_run_root / (
            "artifacts/iteration-000/access_analyzer/graph_query.json"
        )
        query_artifact = json.loads(query_path.read_text(encoding="utf-8"))
        query_artifact["data"]["graph_id"] = graph_id
        query_path.write_text(
            json.dumps(query_artifact, ensure_ascii=False),
            encoding="utf-8",
        )
        query_response = run(
            operation="query",
            input_paths={
                "graph_query": {
                    "path": (
                        "artifacts/iteration-000/access_analyzer/graph_query.json"
                    ),
                    "sha256": calculate_sha256(query_path),
                }
            },
            output_dir="artifacts/iteration-000/knowledge_graph",
            context={
                "run_id": "run_demo_001",
                "iteration": 0,
                "mode": "development",
                "run_root": run_root,
            },
        )
        result_path = run_root / str(query_response["output_path"])
        result = json.loads(result_path.read_text(encoding="utf-8"))
    finally:
        if graph_id is not None:
            with GraphDatabase.driver(
                settings.uri,
                auth=(settings.username, settings.password),
            ) as driver:
                driver.execute_query(
                    "MATCH (item {graph_id: $graph_id, run_id: $run_id}) "
                    "DETACH DELETE item",
                    graph_id=graph_id,
                    run_id="run_demo_001",
                    database_=settings.database,
                )

    results_by_key = {
        item["query_key"]: item for item in result["data"]["results"]
    }
    assert query_response["status"] == "completed"
    assert len(results_by_key["resource_ownership"]["rows"]) == 1
    ownership_row = results_by_key["resource_ownership"]["rows"][0]
    assert ownership_row["owner_account_id"] == "acc_alice"
    assert ownership_row["resource_id"] == "resource:order"
    assert ownership_row["resource_key"] == "order"
    assert ownership_row["resource_scope"] == "instance"
    assert ownership_row["match_key"]["identifiers"] == [
        {"key": "order_id", "value": "example-order"},
    ]
    assert len(results_by_key["role_resource_access"]["rows"]) == 1
    access_row = results_by_key["role_resource_access"]["rows"][0]
    assert access_row["account_id"] == "acc_alice"
    assert access_row["role_id"] == "role_user"
    assert access_row["action"] == "read_order"
    assert access_row["request_ids"] == ["request_order_alice"]
    assert access_row["resource_id"] == "resource:order"
    assert access_row["resource_key"] == "order"
    assert access_row["resource_scope"] == "instance"
    assert access_row["match_key"] == ownership_row["match_key"]
    assert results_by_key["workflow_dependencies"]["rows"] == []
    snapshot = results_by_key["structure_snapshot"]["rows"][0]
    assert len(snapshot["nodes"]) == 5
    assert len(snapshot["relationships"]) == 6
    assert len(snapshot["workflows"]) == 1
    resource_node = next(
        node for node in snapshot["nodes"] if node["node_id"] == "resource:order"
    )
    assert resource_node["properties"] == {
        "resource_key": "order",
        "resource_scope": "instance",
        "match_key": {
            "resource_key": "order",
            "identifiers": [
                {"key": "order_id", "value": "example-order"},
            ],
        },
    }


@pytest.mark.skipif(
    os.getenv("KG_RUN_NEO4J_INTEGRATION") != "1",
    reason="KG_RUN_NEO4J_INTEGRATION=1일 때만 실제 Neo4j 통합 테스트 실행",
)
def test_public_verification_updates_revision_once_with_real_neo4j(
    ingest_run_root: Path,
    query_run_root: Path,
    verification_run_root: Path,
) -> None:
    run_root = ingest_run_root
    semantic_relative = (
        "artifacts/iteration-000/semantic_analyzer/semantic_analysis.json"
    )
    settings = Neo4jSettings.from_environment()
    graph_id: str | None = None
    _prepare_verification_semantic(run_root)

    try:
        ingest_response = _run_public_ingest(run_root, semantic_relative)
        assert isinstance(ingest_response["graph_id"], str)
        graph_id = ingest_response["graph_id"]
        initial_query = _run_public_query(run_root, graph_id, iteration=0)
        _add_verified_denial_update(run_root)
        input_sha256 = calculate_sha256(run_root / VERIFICATION_RELATIVE_PATH)
        applied = _run_public_verification(run_root, graph_id)
        repeated = _run_public_verification(run_root, graph_id)
        updated_query = _run_public_query(run_root, graph_id, iteration=1)
        with Neo4jGraphRepository(settings) as repository:
            stored = _capture_verification_graph(repository, graph_id)
        assert calculate_sha256(run_root / VERIFICATION_RELATIVE_PATH) == input_sha256
    finally:
        if graph_id is not None:
            with GraphDatabase.driver(
                settings.uri,
                auth=(settings.username, settings.password),
            ) as driver:
                driver.execute_query(
                    "MATCH (item {graph_id: $graph_id, run_id: $run_id}) "
                    "DETACH DELETE item",
                    graph_id=graph_id,
                    run_id="run_demo_001",
                    database_=settings.database,
                )

    assert initial_query["data"]["graph_revision"] == 1
    initial_snapshot = _snapshot_from_query(initial_query)
    actor = next(
        node for node in initial_snapshot["nodes"] if node["node_id"] == ACTOR_NODE_ID
    )
    assert actor["properties"]["account_id"] == "account_user"
    ownership = next(
        edge for edge in initial_snapshot["relationships"]
        if edge["relation_type"] == "OWNS"
    )
    assert ownership["source_id"] == OWNER_NODE_ID
    assert applied["status"] == "completed"
    assert applied["previous_graph_revision"] == 1
    assert applied["graph_revision"] == 2
    assert applied["is_applied"] is True
    assert repeated["graph_revision"] == 2
    assert repeated["is_applied"] is False
    assert updated_query["data"]["graph_revision"] == 2
    snapshot_result = next(
        item
        for item in updated_query["data"]["results"]
        if item["query_key"] == "structure_snapshot"
    )
    relationships = snapshot_result["rows"][0]["relationships"]
    verified = next(
        item
        for item in relationships
        if item["relationship_id"] == "relationship_verified_access_001"
    )
    assert verified["basis"] == "verified"
    assert verified["relation_type"] == "VERIFIED_ACCESS"
    assert verified["source_id"] == ACTOR_NODE_ID
    assert "source_account_id" not in verified
    assert verified["target_id"] == "resource_order_001"
    verified_denial = next(
        item
        for item in relationships
        if item["relationship_id"] == "relationship_verified_denial_001"
    )
    assert verified_denial["source_id"] == ACTOR_NODE_ID
    assert verified_denial["relation_type"] == "VERIFIED_DENIAL"
    assert "source_account_id" not in verified_denial
    assert verified_denial["target_id"] == "resource_order_001"
    assert stored.revision == 2
    assert stored.counts.nodes == len(initial_snapshot["nodes"])
    assert stored.counts.relationships == len(initial_snapshot["relationships"]) + 2
    assert len(stored.applied_verifications) == 1
    assert stored.applied_verifications[0]["verification_id"] == "verification_001"
    assert stored.applied_verifications[0]["source_sha256"] == input_sha256


@pytest.mark.skipif(
    os.getenv("KG_RUN_NEO4J_INTEGRATION") != "1",
    reason="KG_RUN_NEO4J_INTEGRATION=1일 때만 실제 Neo4j 통합 테스트 실행",
)
@pytest.mark.parametrize("invalid_account", ["missing", "duplicate"])
def test_invalid_account_leaves_real_graph_unchanged(
    ingest_run_root: Path,
    query_run_root: Path,
    verification_run_root: Path,
    invalid_account: str,
) -> None:
    run_root = ingest_run_root
    with _verification_graph(run_root) as (graph_id, repository):
        artifact = _read_verification(run_root)
        _add_verified_user_updates(artifact)
        if invalid_account == "missing":
            artifact["data"]["graph_updates"]["relationships"][0][
                "source_account_id"
            ] = "missing-account"
        else:
            _execute_test_query(
                "MATCH (node:ABC2Entity {graph_id: $graph_id, run_id: $run_id, "
                "node_id: $node_id}) SET node.properties_json = $properties_json",
                {
                    "graph_id": graph_id,
                    "run_id": "run_demo_001",
                    "node_id": OWNER_NODE_ID,
                    "properties_json": json.dumps(
                        {"account_id": "account_user", "alias": "owner", "role_id": "role_user"}
                    ),
                },
            )
        _write_verification(run_root, artifact)
        before = _capture_verification_graph(repository, graph_id)
        initial_query = _run_public_query(run_root, graph_id, iteration=0)

        response = _run_public_verification(run_root, graph_id)

        assert response["status"] == "failed"
        assert response["errors"][0]["code"] == "GRAPH_UPDATE_REFERENCE_INVALID"
        assert _capture_verification_graph(repository, graph_id) == before
        _assert_snapshot_unchanged(run_root, graph_id, initial_query, revision=1)


@pytest.mark.skipif(
    os.getenv("KG_RUN_NEO4J_INTEGRATION") != "1",
    reason="KG_RUN_NEO4J_INTEGRATION=1일 때만 실제 Neo4j 통합 테스트 실행",
)
@pytest.mark.parametrize("foreign_scope", ["different_run", "different_graph"])
def test_account_resolution_is_scoped_with_real_neo4j(
    ingest_run_root: Path,
    query_run_root: Path,
    verification_run_root: Path,
    foreign_scope: str,
) -> None:
    run_root = ingest_run_root
    with _verification_graph(run_root) as (graph_id, repository):
        foreign_graph_id = graph_id if foreign_scope == "different_run" else uuid4().hex
        foreign_run_id = uuid4().hex if foreign_scope == "different_run" else "run_demo_001"
        with _foreign_verification_graph(
            repository, run_root, (foreign_graph_id, foreign_run_id)
        ):
            foreign_before = _capture_verification_graph(
                repository, foreign_graph_id, foreign_run_id
            )
            applied = _run_public_verification(run_root, graph_id)
            assert applied["status"] == "completed"
            assert applied["graph_revision"] == 2
            assert _capture_verification_graph(
                repository, foreign_graph_id, foreign_run_id
            ) == foreign_before
            _assert_foreign_account_rejected(run_root, graph_id, repository)
            assert _capture_verification_graph(
                repository, foreign_graph_id, foreign_run_id
            ) == foreign_before


@pytest.mark.skipif(
    os.getenv("KG_RUN_NEO4J_INTEGRATION") != "1",
    reason="KG_RUN_NEO4J_INTEGRATION=1일 때만 실제 Neo4j 통합 테스트 실행",
)
@pytest.mark.parametrize(
    "conflict_case",
    [
        ("stale_revision", "GRAPH_REVISION_MISMATCH"),
        ("relationship_identity", "GRAPH_UPDATE_CONFLICT"),
        ("verification_reuse", "VERIFICATION_CONFLICT"),
    ],
)
def test_verification_conflict_leaves_real_graph_unchanged(
    ingest_run_root: Path,
    query_run_root: Path,
    verification_run_root: Path,
    conflict_case: tuple[str, str],
) -> None:
    conflict, error_code = conflict_case
    run_root = ingest_run_root
    with _verification_graph(run_root) as (graph_id, repository):
        applied = _run_public_verification(run_root, graph_id)
        assert applied["status"] == "completed"
        artifact = _read_verification(run_root)
        if conflict != "verification_reuse":
            _set_verification_id(artifact, "verification_conflict_002")
            artifact["data"]["source_graph_revision"] = 2
        if conflict == "stale_revision":
            artifact["data"]["source_graph_revision"] = 1
        elif conflict == "relationship_identity":
            artifact["data"]["graph_updates"]["relationships"][0][
                "source_account_id"
            ] = "account_owner"
        _add_verified_user_updates(artifact)
        _write_verification(run_root, artifact)
        before = _capture_verification_graph(repository, graph_id)
        initial_query = _run_public_query(
            run_root, graph_id, iteration=0, expected_graph_revision=2
        )

        response = _run_public_verification(run_root, graph_id)

        assert response["status"] == "failed"
        assert response["errors"][0]["code"] == error_code
        assert _capture_verification_graph(repository, graph_id) == before
        _assert_snapshot_unchanged(run_root, graph_id, initial_query, revision=2)


@pytest.mark.skipif(
    os.getenv("KG_RUN_NEO4J_INTEGRATION") != "1",
    reason="KG_RUN_NEO4J_INTEGRATION=1일 때만 실제 Neo4j 통합 테스트 실행",
)
def test_verification_transaction_rolls_back_real_writes(
    ingest_run_root: Path,
    query_run_root: Path,
    verification_run_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_root = ingest_run_root
    with _verification_graph(run_root) as (graph_id, repository):
        artifact = _read_verification(run_root)
        _add_verified_user_updates(artifact)
        _write_verification(run_root, artifact)
        before = _capture_verification_graph(repository, graph_id)
        initial_query = _run_public_query(run_root, graph_id, iteration=0)

        with monkeypatch.context() as patch:
            _inject_failure_after_verification_writes(patch)
            response = _run_public_verification(run_root, graph_id)

        assert response["status"] == "failed"
        assert response["errors"][0]["code"] == "GRAPH_UPDATE_REFERENCE_INVALID"
        assert _capture_verification_graph(repository, graph_id) == before
        _assert_snapshot_unchanged(run_root, graph_id, initial_query, revision=1)

        retried = _run_public_verification(run_root, graph_id)
        repeated = _run_public_verification(run_root, graph_id)
        after = _capture_verification_graph(repository, graph_id)
        assert retried["status"] == "completed"
        assert retried["is_applied"] is True
        assert retried["graph_revision"] == 2
        assert repeated["is_applied"] is False
        assert repeated["graph_revision"] == 2
        assert after.counts.nodes == before.counts.nodes + 1
        assert after.counts.relationships == before.counts.relationships + 1
        assert len(after.applied_verifications) == 1
        actor = next(node for node in after.graph.nodes if node.node_id == ACTOR_NODE_ID)
        assert actor.properties["alias"] == "verified-actor"


def _run_public_ingest(run_root: Path, relative_path: str) -> dict[str, object]:
    input_path = run_root / relative_path
    return run(
        operation="ingest",
        input_paths={
            "semantic_analysis": {
                "path": relative_path,
                "sha256": calculate_sha256(input_path),
            }
        },
        output_dir="artifacts/iteration-000/knowledge_graph",
        context={
            "run_id": "run_demo_001",
            "iteration": 0,
            "mode": "development",
            "run_root": run_root,
        },
    )


def _run_public_query(
    run_root: Path,
    graph_id: str,
    iteration: int,
    expected_graph_revision: int | None = None,
) -> dict[str, object]:
    source_path = run_root / (
        "artifacts/iteration-000/access_analyzer/graph_query.json"
    )
    artifact = json.loads(source_path.read_text(encoding="utf-8"))
    artifact["artifact_id"] = f"graph_query_integration_{iteration}"
    artifact["iteration"] = iteration
    artifact["data"]["graph_id"] = graph_id
    artifact["data"]["expected_graph_revision"] = (
        iteration + 1 if expected_graph_revision is None else expected_graph_revision
    )
    relative_path = (
        f"artifacts/iteration-{iteration:03d}/access_analyzer/graph_query.json"
    )
    input_path = run_root / relative_path
    input_path.parent.mkdir(parents=True, exist_ok=True)
    input_path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
    response = run(
        operation="query",
        input_paths={
            "graph_query": {
                "path": relative_path,
                "sha256": calculate_sha256(input_path),
            }
        },
        output_dir=f"artifacts/iteration-{iteration:03d}/knowledge_graph",
        context={
            "run_id": "run_demo_001",
            "iteration": iteration,
            "mode": "development",
            "run_root": run_root,
        },
    )
    output_path = run_root / str(response["output_path"])
    return json.loads(output_path.read_text(encoding="utf-8"))


def _run_public_verification(
    run_root: Path,
    graph_id: str,
) -> dict[str, object]:
    relative_path = (
        "artifacts/iteration-000/verifier/verification_results.json"
    )
    input_path = run_root / relative_path
    return run(
        operation="apply_verification",
        input_paths={
            "verification_results": {
                "path": relative_path,
                "sha256": calculate_sha256(input_path),
            }
        },
        output_dir="artifacts/iteration-000/knowledge_graph",
        context={
            "run_id": "run_demo_001",
            "iteration": 0,
            "mode": "development",
            "run_root": run_root,
            "graph_id": graph_id,
        },
    )


def _add_verified_denial_update(run_root: Path) -> None:
    input_path = run_root / (
        "artifacts/iteration-000/verifier/verification_results.json"
    )
    artifact = json.loads(input_path.read_text(encoding="utf-8"))
    evidence_refs = artifact["data"]["results"][0]["evidence_refs"]
    graph_updates = artifact["data"]["graph_updates"]
    graph_updates["relationships"].append(
        {
            "relationship_id": "relationship_verified_denial_001",
            "source_account_id": "account_user",
            "target_id": "resource_order_001",
            "relation_type": "VERIFIED_DENIAL",
            "properties": {"action": "read_verified_resource"},
            "basis": "verified",
            "evidence_refs": evidence_refs,
        }
    )
    input_path.write_text(
        json.dumps(artifact, ensure_ascii=False),
        encoding="utf-8",
    )


def _replace_with_current_semantic_fixture(semantic_path: Path) -> None:
    artifact = json.loads(CURRENT_SEMANTIC_FIXTURE.read_text(encoding="utf-8"))
    artifact["artifact_id"] = "semantic_integration_current_001"
    artifact["run_id"] = "run_demo_001"
    artifact["input_refs"] = []
    semantic_path.write_text(
        json.dumps(artifact, ensure_ascii=False),
        encoding="utf-8",
    )


def _prepare_verification_semantic(run_root: Path) -> None:
    input_path = run_root / SEMANTIC_RELATIVE_PATH
    artifact = json.loads(input_path.read_text(encoding="utf-8"))
    data = artifact["data"]
    actor = next(node for node in data["nodes"] if node["node_type"] == "User")
    original_node_id = actor["node_id"]
    actor["node_id"] = ACTOR_NODE_ID
    for edge in data["relationships"]:
        for field in ("source_id", "target_id"):
            if edge[field] == original_node_id:
                edge[field] = ACTOR_NODE_ID
    owner = deepcopy(actor)
    owner["node_id"] = OWNER_NODE_ID
    owner["properties"]["account_id"] = "account_owner"
    owner["properties"]["alias"] = "owner"
    data["nodes"].append(owner)
    owner_role = deepcopy(
        next(edge for edge in data["relationships"] if edge["relation_type"] == "HAS_ROLE")
    )
    owner_role["relationship_id"] = "relationship_owner_has_role"
    owner_role["source_id"] = OWNER_NODE_ID
    data["relationships"].append(owner_role)
    ownership = next(edge for edge in data["relationships"] if edge["relation_type"] == "OWNS")
    ownership["source_id"] = OWNER_NODE_ID
    input_path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")


@contextmanager
def _verification_graph(
    run_root: Path,
) -> Iterator[tuple[str, Neo4jGraphRepository]]:
    _prepare_verification_semantic(run_root)
    response = _run_public_ingest(run_root, SEMANTIC_RELATIVE_PATH)
    assert response["status"] == "completed"
    graph_id = response["graph_id"]
    assert isinstance(graph_id, str)
    try:
        with Neo4jGraphRepository(Neo4jSettings.from_environment()) as repository:
            yield graph_id, repository
    finally:
        _delete_test_graph(graph_id, "run_demo_001")


@contextmanager
def _foreign_verification_graph(
    repository: Neo4jGraphRepository,
    run_root: Path,
    identity: tuple[str, str],
) -> Iterator[None]:
    graph_id, run_id = identity
    graph, source = _prepare_foreign_ingest(run_root, run_id)
    state = repository.ingest(graph_id, run_id, graph, source)
    try:
        assert state.is_created is True
        yield
    finally:
        _delete_test_graph(graph_id, run_id)


def _prepare_foreign_ingest(
    run_root: Path,
    run_id: str,
) -> tuple[SemanticGraph, GraphSource]:
    artifact = json.loads((run_root / SEMANTIC_RELATIVE_PATH).read_text(encoding="utf-8"))
    artifact["artifact_id"] = f"semantic_foreign_{uuid4().hex}"
    artifact["run_id"] = run_id
    data = artifact["data"]
    foreign_user = deepcopy(
        next(node for node in data["nodes"] if node["node_id"] == ACTOR_NODE_ID)
    )
    foreign_user["node_id"] = "opaque-foreign-only"
    foreign_user["properties"]["account_id"] = "foreign-only-account"
    data["nodes"].append(foreign_user)
    foreign_role = deepcopy(
        next(edge for edge in data["relationships"] if edge["relation_type"] == "HAS_ROLE")
    )
    foreign_role["relationship_id"] = "relationship_foreign_has_role"
    foreign_role["source_id"] = foreign_user["node_id"]
    data["relationships"].append(foreign_role)
    input_path = run_root / "private" / "knowledge_graph" / f"{artifact['artifact_id']}.json"
    input_path.parent.mkdir(parents=True, exist_ok=True)
    input_path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
    _, graph = prepare_ingest(input_path)
    return graph, GraphSource(
        artifact_id=artifact["artifact_id"],
        sha256=calculate_sha256(input_path),
        iteration=artifact["iteration"],
        status=artifact["status"],
    )


def _assert_foreign_account_rejected(
    run_root: Path,
    graph_id: str,
    repository: Neo4jGraphRepository,
) -> None:
    artifact = _read_verification(run_root)
    _set_verification_id(artifact, "verification_foreign_002")
    artifact["artifact_id"] = "verification_foreign_002"
    artifact["data"]["source_graph_revision"] = 2
    edge = artifact["data"]["graph_updates"]["relationships"][0]
    edge["source_account_id"] = "foreign-only-account"
    edge["relationship_id"] = "relationship_foreign_002"
    _add_verified_user_updates(artifact)
    _write_verification(run_root, artifact)
    before = _capture_verification_graph(repository, graph_id)
    initial_query = _run_public_query(
        run_root, graph_id, iteration=0, expected_graph_revision=2
    )
    verified = next(
        edge for edge in _snapshot_from_query(initial_query)["relationships"]
        if edge["relation_type"] == "VERIFIED_ACCESS"
    )
    assert verified["source_id"] == ACTOR_NODE_ID
    response = _run_public_verification(run_root, graph_id)
    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "GRAPH_UPDATE_REFERENCE_INVALID"
    assert _capture_verification_graph(repository, graph_id) == before
    _assert_snapshot_unchanged(run_root, graph_id, initial_query, revision=2)


def _capture_verification_graph(
    repository: Neo4jGraphRepository,
    graph_id: str,
    run_id: str = "run_demo_001",
) -> StoredVerificationGraph:
    records = _execute_test_query(
        "MATCH (verification:ABC2AppliedVerification "
        "{graph_id: $graph_id, run_id: $run_id}) "
        "RETURN properties(verification) AS properties "
        "ORDER BY verification.verification_id",
        {"graph_id": graph_id, "run_id": run_id},
    )
    return StoredVerificationGraph(
        revision=repository.get_revision(graph_id, run_id),
        graph=repository.load_graph(graph_id, run_id),
        counts=repository.get_counts(graph_id, run_id),
        applied_verifications=tuple(record["properties"] for record in records),
    )


def _execute_test_query(
    query: str,
    parameters: dict[str, Any],
) -> list[dict[str, Any]]:
    settings = Neo4jSettings.from_environment()
    with GraphDatabase.driver(
        settings.uri, auth=(settings.username, settings.password)
    ) as driver:
        records, _, _ = driver.execute_query(
            query, parameters_=parameters, database_=settings.database
        )
    return [record.data() for record in records]


def _delete_test_graph(graph_id: str, run_id: str) -> None:
    _execute_test_query(
        "MATCH (item {graph_id: $graph_id, run_id: $run_id}) DETACH DELETE item",
        {"graph_id": graph_id, "run_id": run_id},
    )


def _read_verification(run_root: Path) -> dict[str, Any]:
    return json.loads((run_root / VERIFICATION_RELATIVE_PATH).read_text(encoding="utf-8"))


def _write_verification(run_root: Path, artifact: dict[str, Any]) -> None:
    (run_root / VERIFICATION_RELATIVE_PATH).write_text(
        json.dumps(artifact, ensure_ascii=False), encoding="utf-8"
    )


def _set_verification_id(artifact: dict[str, Any], verification_id: str) -> None:
    artifact["data"]["results"][0]["verification_id"] = verification_id
    artifact["data"]["graph_updates"]["source_verification_ids"] = [verification_id]


def _add_verified_user_updates(artifact: dict[str, Any]) -> None:
    graph_updates = artifact["data"]["graph_updates"]
    evidence_refs = deepcopy(artifact["data"]["results"][0]["evidence_refs"])
    for node_id, account_id in (
        (NEW_USER_NODE_ID, "new-account"),
        (ACTOR_NODE_ID, "account_user"),
    ):
        graph_updates["nodes"].append(
            {
                "node_id": node_id,
                "node_type": "User",
                "properties": {
                    "account_id": account_id,
                    "role_id": "role_user",
                    "alias": "verified-actor",
                },
                "basis": "verified",
                "evidence_refs": deepcopy(evidence_refs),
            }
        )


def _snapshot_from_query(artifact: dict[str, Any]) -> dict[str, Any]:
    assert artifact["status"] == "completed"
    result = next(
        item for item in artifact["data"]["results"]
        if item["query_key"] == "structure_snapshot"
    )
    assert result["status"] == "completed"
    assert len(result["rows"]) == 1
    return result["rows"][0]


def _assert_snapshot_unchanged(
    run_root: Path,
    graph_id: str,
    initial_query: dict[str, Any],
    revision: int,
) -> None:
    after_query = _run_public_query(
        run_root, graph_id, iteration=1, expected_graph_revision=revision
    )
    assert after_query["data"]["graph_revision"] == revision
    assert _snapshot_from_query(after_query) == _snapshot_from_query(initial_query)


def _inject_failure_after_verification_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    original_apply = Neo4jGraphRepository._apply_verification_transaction

    def apply_then_fail(
        repository_class: type[Neo4jGraphRepository],
        transaction: ManagedTransaction,
        graph_id: str,
        run_id: str,
        update: VerificationInputUpdate,
    ) -> VerificationState:
        state = original_apply(transaction, graph_id, run_id, update)
        assert state.graph_revision == 2
        record = transaction.run(
            "MATCH (graph:ABC2Graph {graph_id: $graph_id, run_id: $run_id}) "
            "MATCH (node:ABC2Entity {graph_id: $graph_id, run_id: $run_id, "
            "node_id: $node_id}) "
            "MATCH (verification:ABC2AppliedVerification "
            "{graph_id: $graph_id, run_id: $run_id}) "
            "MATCH ()-[edge:VERIFIED_ACCESS {graph_id: $graph_id, run_id: $run_id}]->() "
            "RETURN graph.revision AS revision, node.node_id AS node_id, "
            "count(verification) AS verification_count, count(edge) AS relationship_count",
            graph_id=graph_id,
            run_id=run_id,
            node_id=NEW_USER_NODE_ID,
        ).single()
        assert record is not None
        assert record["revision"] == 2
        assert record["node_id"] == NEW_USER_NODE_ID
        assert record["verification_count"] == 1
        assert record["relationship_count"] == 1
        raise GraphUpdateReferenceError("통합 테스트: commit 전 오류를 주입해 rollback 확인")

    monkeypatch.setattr(
        Neo4jGraphRepository,
        "_apply_verification_transaction",
        classmethod(apply_then_fail),
    )
