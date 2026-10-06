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
