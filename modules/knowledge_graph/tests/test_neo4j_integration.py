import os
from pathlib import Path
from uuid import uuid4

import pytest
from neo4j import GraphDatabase

from modules.knowledge_graph.neo4j_repository import Neo4jGraphRepository
from modules.knowledge_graph.service import prepare_ingest
from modules.knowledge_graph.settings import Neo4jSettings


@pytest.mark.skipif(
    os.getenv("KG_RUN_NEO4J_INTEGRATION") != "1",
    reason="KG_RUN_NEO4J_INTEGRATION=1일 때만 실제 Neo4j 통합 테스트 실행",
)
def test_ingest_round_trip_with_real_neo4j(fixture_root: Path) -> None:
    _, graph = prepare_ingest(
        fixture_root / "semantic_analyzer" / "semantic_analysis.json"
    )
    graph_id = f"graph_integration_{uuid4().hex}"
    settings = Neo4jSettings.from_environment()
    is_created = False

    try:
        with Neo4jGraphRepository(settings) as repository:
            repository.verify_connectivity()
            revision = repository.ingest(graph_id, "run_demo_001", graph)
            is_created = True
            restored = repository.load_graph(graph_id, "run_demo_001")
            counts = repository.get_counts(graph_id, "run_demo_001")
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
                    run_id="run_demo_001",
                    database_=settings.database,
                )

    assert revision == 1
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
