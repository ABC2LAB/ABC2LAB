"""Neo4j persistence adapter for knowledge graph state."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from neo4j import Driver, GraphDatabase, ManagedTransaction
from neo4j.exceptions import DriverError, Neo4jError

from modules.knowledge_graph.exceptions import (
    ContractValidationError,
    GraphAlreadyExistsError,
    GraphNotFoundError,
    RepositoryError,
)
from modules.knowledge_graph.models import SemanticGraph
from modules.knowledge_graph.settings import Neo4jSettings
from modules.knowledge_graph.storage import (
    deserialize_edge,
    deserialize_node,
    deserialize_workflow,
    serialize_edge,
    serialize_node,
    serialize_workflow,
    serialize_workflow_dependency,
    serialize_workflow_step,
)


ALLOWED_RELATIONSHIP_TYPES = frozenset(
    {
        "HAS_ROLE",
        "ACCESS",
        "CALL",
        "USE",
        "REFERENCE",
        "OWNS",
        "PRECEDES",
        "REQUIRES",
        "VERIFIED_ACCESS",
        "VERIFIED_DENIAL",
    }
)

SCHEMA_QUERIES = (
    "CREATE CONSTRAINT abc2_graph_identity IF NOT EXISTS "
    "FOR (graph:ABC2Graph) REQUIRE (graph.run_id, graph.graph_id) IS UNIQUE",
    "CREATE CONSTRAINT abc2_entity_identity IF NOT EXISTS "
    "FOR (entity:ABC2Entity) "
    "REQUIRE (entity.run_id, entity.graph_id, entity.node_id) IS UNIQUE",
    "CREATE CONSTRAINT abc2_workflow_identity IF NOT EXISTS "
    "FOR (workflow:ABC2Workflow) "
    "REQUIRE (workflow.run_id, workflow.graph_id, workflow.workflow_id) IS UNIQUE",
    "CREATE CONSTRAINT abc2_workflow_step_identity IF NOT EXISTS "
    "FOR (step:ABC2WorkflowStep) "
    "REQUIRE (step.run_id, step.graph_id, step.workflow_id, step.step_id) IS UNIQUE",
    "CREATE CONSTRAINT abc2_workflow_dependency_identity IF NOT EXISTS "
    "FOR (dependency:ABC2WorkflowDependency) "
    "REQUIRE (dependency.run_id, dependency.graph_id, dependency.dependency_id) IS UNIQUE",
    "CREATE CONSTRAINT abc2_verification_identity IF NOT EXISTS "
    "FOR (verification:ABC2AppliedVerification) "
    "REQUIRE (verification.run_id, verification.graph_id, "
    "verification.verification_id) IS UNIQUE",
)


@dataclass(frozen=True)
class GraphCounts:
    nodes: int
    relationships: int
    workflows: int
    workflow_steps: int
    workflow_dependencies: int


class Neo4jGraphRepository:
    def __init__(self, settings: Neo4jSettings, driver: Driver | None = None) -> None:
        self._settings = settings
        self._driver = driver or GraphDatabase.driver(
            settings.uri,
            auth=(settings.username, settings.password),
        )

    def __enter__(self) -> "Neo4jGraphRepository":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._driver.close()

    def verify_connectivity(self) -> None:
        try:
            self._driver.execute_query(
                "RETURN 1 AS value",
                database_=self._settings.database,
            )
        except (DriverError, Neo4jError) as error:
            raise RepositoryError("Neo4j 연결 확인 실패") from error

    def initialize_schema(self) -> None:
        try:
            for query in SCHEMA_QUERIES:
                self._driver.execute_query(query, database_=self._settings.database)
        except (DriverError, Neo4jError) as error:
            raise RepositoryError("Neo4j 제약조건 생성 실패") from error

    def ingest(self, graph_id: str, run_id: str, graph: SemanticGraph) -> int:
        if not graph_id or not run_id:
            raise ContractValidationError("graph_id와 run_id는 비어 있을 수 없음")
        self._validate_relationship_types(graph)
        self.initialize_schema()
        try:
            with self._driver.session(database=self._settings.database) as session:
                return session.execute_write(
                    self._ingest_transaction,
                    graph_id,
                    run_id,
                    graph,
                )
        except GraphAlreadyExistsError:
            raise
        except (DriverError, Neo4jError) as error:
            raise RepositoryError("Neo4j 그래프 적재 실패") from error

    def load_graph(self, graph_id: str, run_id: str) -> SemanticGraph:
        try:
            with self._driver.session(database=self._settings.database) as session:
                return session.execute_read(
                    self._load_graph_transaction,
                    graph_id,
                    run_id,
                )
        except (DriverError, Neo4jError) as error:
            raise RepositoryError("Neo4j 그래프 조회 실패") from error

    def get_revision(self, graph_id: str, run_id: str) -> int | None:
        try:
            records, _, _ = self._driver.execute_query(
                "MATCH (graph:ABC2Graph {graph_id: $graph_id, run_id: $run_id}) "
                "RETURN graph.revision AS revision",
                graph_id=graph_id,
                run_id=run_id,
                database_=self._settings.database,
            )
        except (DriverError, Neo4jError) as error:
            raise RepositoryError("Neo4j revision 조회 실패") from error
        if not records:
            return None
        return int(records[0]["revision"])

    def get_counts(self, graph_id: str, run_id: str) -> GraphCounts:
        try:
            with self._driver.session(database=self._settings.database) as session:
                return session.execute_read(
                    self._count_transaction,
                    graph_id,
                    run_id,
                )
        except (DriverError, Neo4jError) as error:
            raise RepositoryError("Neo4j 적재 건수 조회 실패") from error

    @staticmethod
    def _validate_relationship_types(graph: SemanticGraph) -> None:
        invalid_types = {
            edge.relation_type
            for edge in graph.relationships
            if edge.relation_type not in ALLOWED_RELATIONSHIP_TYPES
        }
        if invalid_types:
            raise ContractValidationError("허용되지 않은 relationship type")

    @classmethod
    def _ingest_transaction(
        cls,
        transaction: ManagedTransaction,
        graph_id: str,
        run_id: str,
        graph: SemanticGraph,
    ) -> int:
        existing = transaction.run(
            "MATCH (graph:ABC2Graph {graph_id: $graph_id, run_id: $run_id}) "
            "RETURN graph.graph_id AS graph_id",
            graph_id=graph_id,
            run_id=run_id,
        ).single()
        if existing is not None:
            raise GraphAlreadyExistsError("같은 run_id와 graph_id의 그래프가 이미 존재함")

        transaction.run(
            "CREATE (:ABC2Graph {graph_id: $graph_id, run_id: $run_id, revision: 1})",
            graph_id=graph_id,
            run_id=run_id,
        ).consume()
        cls._create_nodes(transaction, graph_id, run_id, graph)
        cls._create_relationships(transaction, graph_id, run_id, graph)
        cls._create_workflows(transaction, graph_id, run_id, graph)
        return 1

    @staticmethod
    def _create_nodes(
        transaction: ManagedTransaction,
        graph_id: str,
        run_id: str,
        graph: SemanticGraph,
    ) -> None:
        records = [serialize_node(node) for node in graph.nodes]
        transaction.run(
            "UNWIND $records AS record "
            "CREATE (:ABC2Entity {"
            "graph_id: $graph_id, run_id: $run_id, node_id: record.node_id, "
            "node_type: record.node_type, properties_json: record.properties_json, "
            "basis: record.basis, evidence_refs_json: record.evidence_refs_json})",
            graph_id=graph_id,
            run_id=run_id,
            records=records,
        ).consume()

    @staticmethod
    def _create_relationships(
        transaction: ManagedTransaction,
        graph_id: str,
        run_id: str,
        graph: SemanticGraph,
    ) -> None:
        records_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for edge in graph.relationships:
            records_by_type[edge.relation_type].append(serialize_edge(edge))

        for relationship_type, records in records_by_type.items():
            query = (
                "UNWIND $records AS record "
                "MATCH (source:ABC2Entity {"
                "graph_id: $graph_id, run_id: $run_id, node_id: record.source_id}) "
                "MATCH (target:ABC2Entity {"
                "graph_id: $graph_id, run_id: $run_id, node_id: record.target_id}) "
                f"CREATE (source)-[stored:{relationship_type} {{"
                "graph_id: $graph_id, run_id: $run_id, "
                "relationship_id: record.relationship_id, "
                "properties_json: record.properties_json, basis: record.basis, "
                "evidence_refs_json: record.evidence_refs_json}]->(target)"
            )
            transaction.run(
                query,
                graph_id=graph_id,
                run_id=run_id,
                records=records,
            ).consume()

    @staticmethod
    def _create_workflows(
        transaction: ManagedTransaction,
        graph_id: str,
        run_id: str,
        graph: SemanticGraph,
    ) -> None:
        workflows = [serialize_workflow(workflow) for workflow in graph.workflows]
        steps = [
            serialize_workflow_step(workflow.workflow_id, step)
            for workflow in graph.workflows
            for step in workflow.steps
        ]
        dependencies = [
            serialize_workflow_dependency(
                workflow.workflow_id,
                dependency,
                dependency_order,
            )
            for workflow in graph.workflows
            for dependency_order, dependency in enumerate(workflow.dependencies)
        ]
        transaction.run(
            "UNWIND $records AS record "
            "CREATE (:ABC2Workflow {"
            "graph_id: $graph_id, run_id: $run_id, workflow_id: record.workflow_id, "
            "name: record.name, role_ids_json: record.role_ids_json, "
            "basis: record.basis, evidence_refs_json: record.evidence_refs_json})",
            graph_id=graph_id,
            run_id=run_id,
            records=workflows,
        ).consume()
        transaction.run(
            "UNWIND $records AS record "
            "MATCH (workflow:ABC2Workflow {"
            "graph_id: $graph_id, run_id: $run_id, "
            "workflow_id: record.workflow_id}) "
            "CREATE (step:ABC2WorkflowStep {"
            "graph_id: $graph_id, run_id: $run_id, workflow_id: record.workflow_id, "
            "step_id: record.step_id, step_order: record.step_order, "
            "action: record.action, request_ids_json: record.request_ids_json}) "
            "CREATE (workflow)-[:ABC2_HAS_STEP]->(step)",
            graph_id=graph_id,
            run_id=run_id,
            records=steps,
        ).consume()
        transaction.run(
            "UNWIND $records AS record "
            "MATCH (workflow:ABC2Workflow {"
            "graph_id: $graph_id, run_id: $run_id, "
            "workflow_id: record.workflow_id}) "
            "CREATE (dependency:ABC2WorkflowDependency {"
            "graph_id: $graph_id, run_id: $run_id, workflow_id: record.workflow_id, "
            "dependency_id: record.dependency_id, "
            "dependency_order: record.dependency_order, "
            "before_step_id: record.before_step_id, "
            "after_step_id: record.after_step_id, condition: record.condition, "
            "basis: record.basis, evidence_refs_json: record.evidence_refs_json}) "
            "CREATE (workflow)-[:ABC2_HAS_DEPENDENCY]->(dependency)",
            graph_id=graph_id,
            run_id=run_id,
            records=dependencies,
        ).consume()

    @staticmethod
    def _load_graph_transaction(
        transaction: ManagedTransaction,
        graph_id: str,
        run_id: str,
    ) -> SemanticGraph:
        graph_record = transaction.run(
            "MATCH (graph:ABC2Graph {graph_id: $graph_id, run_id: $run_id}) "
            "RETURN graph.graph_id AS graph_id",
            graph_id=graph_id,
            run_id=run_id,
        ).single()
        if graph_record is None:
            raise GraphNotFoundError("요청한 run 범위에 graph_id가 존재하지 않음")
        node_values = _record_values(
            transaction.run(
                "MATCH (node:ABC2Entity {graph_id: $graph_id, run_id: $run_id}) "
                "RETURN node {.*} AS value ORDER BY node.node_id",
                graph_id=graph_id,
                run_id=run_id,
            )
        )
        edge_values = _record_values(
            transaction.run(
                "MATCH (source:ABC2Entity {graph_id: $graph_id, run_id: $run_id})"
                "-[edge]->(target:ABC2Entity {graph_id: $graph_id, run_id: $run_id}) "
                "RETURN edge {.*, source_id: source.node_id, target_id: target.node_id, "
                "relation_type: type(edge)} AS value ORDER BY edge.relationship_id",
                graph_id=graph_id,
                run_id=run_id,
            )
        )
        workflow_values = _record_values(
            transaction.run(
                "MATCH (workflow:ABC2Workflow {graph_id: $graph_id, run_id: $run_id}) "
                "RETURN workflow {.*} AS value ORDER BY workflow.workflow_id",
                graph_id=graph_id,
                run_id=run_id,
            )
        )
        step_values = _record_values(
            transaction.run(
                "MATCH (step:ABC2WorkflowStep {graph_id: $graph_id, run_id: $run_id}) "
                "RETURN step {.*} AS value ORDER BY step.workflow_id, step.step_order",
                graph_id=graph_id,
                run_id=run_id,
            )
        )
        dependency_values = _record_values(
            transaction.run(
                "MATCH (dependency:ABC2WorkflowDependency {"
                "graph_id: $graph_id, run_id: $run_id}) "
                "RETURN dependency {.*} AS value "
                "ORDER BY dependency.workflow_id, dependency.dependency_id",
                graph_id=graph_id,
                run_id=run_id,
            )
        )
        steps_by_workflow = _group_by_workflow(step_values)
        dependencies_by_workflow = _group_by_workflow(dependency_values)
        return SemanticGraph(
            nodes=tuple(deserialize_node(item) for item in node_values),
            relationships=tuple(deserialize_edge(item) for item in edge_values),
            workflows=tuple(
                deserialize_workflow(
                    item,
                    steps_by_workflow[item["workflow_id"]],
                    dependencies_by_workflow[item["workflow_id"]],
                )
                for item in workflow_values
            ),
        )

    @staticmethod
    def _count_transaction(
        transaction: ManagedTransaction,
        graph_id: str,
        run_id: str,
    ) -> GraphCounts:
        labels = (
            ("ABC2Entity", "nodes"),
            ("ABC2Workflow", "workflows"),
            ("ABC2WorkflowStep", "workflow_steps"),
            ("ABC2WorkflowDependency", "workflow_dependencies"),
        )
        counts: dict[str, int] = {}
        for label, key in labels:
            record = transaction.run(
                f"MATCH (item:{label} {{graph_id: $graph_id, run_id: $run_id}}) "
                "RETURN count(item) AS count",
                graph_id=graph_id,
                run_id=run_id,
            ).single(strict=True)
            counts[key] = int(record["count"])
        relationship_record = transaction.run(
            "MATCH (:ABC2Entity {graph_id: $graph_id, run_id: $run_id})"
            "-[edge]->(:ABC2Entity {graph_id: $graph_id, run_id: $run_id}) "
            "RETURN count(edge) AS count",
            graph_id=graph_id,
            run_id=run_id,
        ).single(strict=True)
        return GraphCounts(
            nodes=counts["nodes"],
            relationships=int(relationship_record["count"]),
            workflows=counts["workflows"],
            workflow_steps=counts["workflow_steps"],
            workflow_dependencies=counts["workflow_dependencies"],
        )


def _record_values(records: Iterable[Any]) -> list[dict[str, Any]]:
    return [dict(record["value"]) for record in records]


def _group_by_workflow(
    values: list[dict[str, Any]],
) -> defaultdict[str, list[dict[str, Any]]]:
    values_by_workflow: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for value in values:
        values_by_workflow[value["workflow_id"]].append(value)
    return values_by_workflow
