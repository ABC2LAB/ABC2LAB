import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from neo4j import GraphDatabase

from modules.knowledge_graph.entrypoint import run
from modules.knowledge_graph.exceptions import SourceArtifactConflictError
from modules.knowledge_graph.models import GraphSource
from modules.knowledge_graph.neo4j_repository import Neo4jGraphRepository
from modules.knowledge_graph.service import prepare_ingest
from modules.knowledge_graph.settings import Neo4jSettings
from modules.knowledge_graph.utils.hashing import calculate_sha256


CURRENT_SEMANTIC_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "semantic_analyzer_current"
    / "semantic_analysis.json"
)


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

    try:
        ingest_response = _run_public_ingest(run_root, semantic_relative)
        assert isinstance(ingest_response["graph_id"], str)
        graph_id = ingest_response["graph_id"]
        initial_query = _run_public_query(run_root, graph_id, iteration=0)
        _add_verified_denial_update(run_root)
        applied = _run_public_verification(run_root, graph_id)
        repeated = _run_public_verification(run_root, graph_id)
        updated_query = _run_public_query(run_root, graph_id, iteration=1)
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
    assert verified["target_id"] == "resource_order_001"
    verified_denial = next(
        item
        for item in relationships
        if item["relationship_id"] == "relationship_verified_denial_001"
    )
    assert verified_denial["target_id"] == "resource_order_001"


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
) -> dict[str, object]:
    source_path = run_root / (
        "artifacts/iteration-000/access_analyzer/graph_query.json"
    )
    artifact = json.loads(source_path.read_text(encoding="utf-8"))
    artifact["artifact_id"] = f"graph_query_integration_{iteration}"
    artifact["iteration"] = iteration
    artifact["data"]["graph_id"] = graph_id
    artifact["data"]["expected_graph_revision"] = iteration + 1
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
