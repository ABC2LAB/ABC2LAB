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
    GraphRevisionMismatchError,
    GraphStorageVerificationError,
    GraphUpdateConflictError,
    GraphUpdateReferenceError,
    QueryResultValidationError,
    RepositoryError,
    SourceArtifactConflictError,
    VerificationConflictError,
)
from modules.knowledge_graph.models import (
    GraphSource,
    GraphState,
    SemanticGraph,
    VerificationInputUpdate,
    VerificationState,
    VerificationUpdate,
)
from modules.knowledge_graph.query_results import graph_to_snapshot
from modules.knowledge_graph.settings import Neo4jSettings
from modules.knowledge_graph.storage import (
    decode_json,
    deserialize_edge,
    deserialize_node,
    deserialize_request_observation,
    deserialize_workflow,
    encode_json,
    serialize_edge,
    serialize_node,
    serialize_request_observation,
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

ALLOWED_QUERY_KEYS = frozenset(
    {
        "resource_ownership",
        "role_resource_access",
        "workflow_dependencies",
        "structure_snapshot",
    }
)

ALLOWED_VERIFICATION_RELATIONSHIP_TYPES = frozenset(
    {"VERIFIED_ACCESS", "VERIFIED_DENIAL"}
)

SCHEMA_QUERIES = (
    "CREATE CONSTRAINT abc2_graph_identity IF NOT EXISTS "
    "FOR (graph:ABC2Graph) REQUIRE (graph.run_id, graph.graph_id) IS UNIQUE",
    "CREATE CONSTRAINT abc2_graph_source_identity IF NOT EXISTS "
    "FOR (graph:ABC2Graph) "
    "REQUIRE (graph.run_id, graph.source_artifact_id) IS UNIQUE",
    "CREATE CONSTRAINT abc2_entity_identity IF NOT EXISTS "
    "FOR (entity:ABC2Entity) "
    "REQUIRE (entity.run_id, entity.graph_id, entity.node_id) IS UNIQUE",
    "CREATE CONSTRAINT abc2_request_observation_identity IF NOT EXISTS "
    "FOR (observation:ABC2RequestObservation) "
    "REQUIRE (observation.run_id, observation.graph_id, "
    "observation.request_id) IS UNIQUE",
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
    request_observations: int
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

    def ingest(
        self,
        graph_id: str,
        run_id: str,
        graph: SemanticGraph,
        source: GraphSource,
    ) -> GraphState:
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
                    source,
                )
        except (
            GraphAlreadyExistsError,
            GraphStorageVerificationError,
            SourceArtifactConflictError,
        ):
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

    def query(
        self,
        graph_id: str,
        run_id: str,
        query_key: str,
        parameters: dict[str, Any],
    ) -> list[dict[str, Any]]:
        self._validate_query_parameters(query_key, parameters)
        if query_key == "structure_snapshot":
            graph = self.load_graph(graph_id, run_id)
            return [
                graph_to_snapshot(
                    graph,
                    include_evidence_refs=parameters["include_evidence_refs"],
                )
            ]
        try:
            with self._driver.session(database=self._settings.database) as session:
                return session.execute_read(
                    self._query_transaction,
                    graph_id,
                    run_id,
                    query_key,
                    parameters,
                )
        except (DriverError, Neo4jError) as error:
            raise RepositoryError("Neo4j 읽기 질의 실패") from error

    def apply_verification(
        self,
        graph_id: str,
        run_id: str,
        update: VerificationInputUpdate | VerificationUpdate,
    ) -> VerificationState:
        if not graph_id or not run_id:
            raise ContractValidationError("graph_id와 run_id는 비어 있을 수 없음")
        if len(update.verification_ids) != len(set(update.verification_ids)):
            raise ContractValidationError("verification_id가 중복됨")
        if any(node.node_type == "Resource" for node in update.nodes):
            raise ContractValidationError(
                "verifier는 Resource 노드를 새로 생성할 수 없음"
            )
        invalid_types = {
            edge.relation_type
            for edge in update.relationships
            if edge.relation_type not in ALLOWED_VERIFICATION_RELATIONSHIP_TYPES
        }
        if invalid_types:
            raise ContractValidationError("허용되지 않은 verification 관계 유형")
        if isinstance(update, VerificationInputUpdate):
            if update.relationships:
                # TODO(이동찬): 2단계에서 같은 트랜잭션의 User 노드로 해석한다.
                raise GraphUpdateReferenceError(
                    "source_account_id의 User 노드 변환이 아직 구현되지 않음"
                )
            update = VerificationUpdate(
                source=update.source,
                source_graph_revision=update.source_graph_revision,
                verification_ids=update.verification_ids,
                nodes=update.nodes,
                relationships=(),
            )
        self.initialize_schema()
        try:
            with self._driver.session(database=self._settings.database) as session:
                return session.execute_write(
                    self._apply_verification_transaction,
                    graph_id,
                    run_id,
                    update,
                )
        except (
            GraphNotFoundError,
            GraphRevisionMismatchError,
            GraphUpdateConflictError,
            GraphUpdateReferenceError,
            VerificationConflictError,
        ):
            raise
        except (DriverError, Neo4jError) as error:
            raise RepositoryError("Neo4j 검증 결과 반영 실패") from error

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

    @staticmethod
    def _validate_query_parameters(
        query_key: str,
        parameters: dict[str, Any],
    ) -> None:
        if query_key not in ALLOWED_QUERY_KEYS:
            raise ContractValidationError("허용되지 않은 query_key")
        expected_keys_by_query = {
            "resource_ownership": {"account_ids", "resource_ids"},
            "role_resource_access": {"role_ids"},
            "workflow_dependencies": {"workflow_ids"},
            "structure_snapshot": {"include_evidence_refs"},
        }
        if set(parameters) != expected_keys_by_query[query_key]:
            raise ContractValidationError("query parameters 필드가 올바르지 않음")
        if query_key == "structure_snapshot":
            if not isinstance(parameters["include_evidence_refs"], bool):
                raise ContractValidationError("include_evidence_refs는 boolean이어야 함")
            return
        for value in parameters.values():
            if not isinstance(value, list) or any(
                not isinstance(item, str) or not item for item in value
            ):
                raise ContractValidationError("query ID 필터는 문자열 배열이어야 함")

    @classmethod
    def _query_transaction(
        cls,
        transaction: ManagedTransaction,
        graph_id: str,
        run_id: str,
        query_key: str,
        parameters: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if query_key == "resource_ownership":
            records = transaction.run(
                "MATCH (owner:ABC2Entity {graph_id: $graph_id, run_id: $run_id})"
                "-[edge:OWNS]->(resource:ABC2Entity {"
                "graph_id: $graph_id, run_id: $run_id}) "
                "MATCH (observation:ABC2RequestObservation {"
                "graph_id: $graph_id, run_id: $run_id}) "
                "WHERE owner.node_type = 'User' AND resource.node_type = 'Resource' "
                "AND resource.resource_scope = 'instance' "
                "AND observation.user_node_id = owner.node_id "
                "AND (size($account_ids) = 0 "
                "OR observation.account_id IN $account_ids) "
                "AND (size($resource_ids) = 0 OR resource.node_id IN $resource_ids) "
                "RETURN DISTINCT resource.node_id AS resource_id, "
                "resource.resource_key AS resource_key, "
                "resource.resource_scope AS resource_scope, "
                "resource.resource_match_key_json AS resource_match_key_json, "
                "observation.account_id AS owner_account_id, edge.basis AS basis, "
                "edge.evidence_refs_json AS evidence_refs_json "
                "ORDER BY resource.node_id, owner_account_id",
                graph_id=graph_id,
                run_id=run_id,
                **parameters,
            )
            return [cls._ownership_row(record) for record in records]
        if query_key == "role_resource_access":
            records = list(
                transaction.run(
                    "MATCH (observation:ABC2RequestObservation {"
                    "graph_id: $graph_id, run_id: $run_id}) "
                    "MATCH (account:ABC2Entity {graph_id: $graph_id, "
                    "run_id: $run_id, node_type: 'User'}) "
                    "MATCH (role:ABC2Entity {graph_id: $graph_id, run_id: $run_id, "
                    "node_type: 'Role'}) "
                    "MATCH (endpoint:ABC2Entity {graph_id: $graph_id, "
                    "run_id: $run_id, node_type: 'Endpoint'}) "
                    "WHERE account.node_id = observation.user_node_id "
                    "AND role.node_id = observation.role_node_id "
                    "AND endpoint.node_id = observation.endpoint_id "
                    "AND (size($role_ids) = 0 OR observation.role_id IN $role_ids) "
                    "AND EXISTS { "
                    "MATCH (account)-[has_role:HAS_ROLE]->(role) "
                    "WHERE has_role.graph_id = $graph_id "
                    "AND has_role.run_id = $run_id "
                    "} "
                    "AND EXISTS { "
                    "MATCH (account)-[access]->(endpoint) "
                    "WHERE access.graph_id = $graph_id "
                    "AND access.run_id = $run_id "
                    "AND type(access) = 'ACCESS' "
                    "} "
                    "RETURN observation.request_id AS request_id, "
                    "observation.account_id AS account_id, "
                    "observation.role_id AS role_id, "
                    "endpoint.node_id AS endpoint_id, "
                    "observation.action AS action, "
                    "observation.resource_ids_json AS resource_ids_json, "
                    "observation.evidence_refs_json AS evidence_refs_json "
                    "ORDER BY observation.account_id, observation.role_id, "
                    "endpoint.node_id, observation.request_id",
                    graph_id=graph_id,
                    run_id=run_id,
                    **parameters,
                )
            )
            resource_ids = {
                resource_id
                for record in records
                for resource_id in _decode_string_list(record["resource_ids_json"])
            }
            resource_by_id = cls._load_resource_identities(
                transaction,
                graph_id,
                run_id,
                resource_ids,
            )
            return cls._access_rows(records, resource_by_id)
        records = transaction.run(
            "MATCH (workflow:ABC2Workflow {graph_id: $graph_id, run_id: $run_id})"
            "-[:ABC2_HAS_DEPENDENCY]->(dependency:ABC2WorkflowDependency {"
            "graph_id: $graph_id, run_id: $run_id}) "
            "WHERE size($workflow_ids) = 0 "
            "OR workflow.workflow_id IN $workflow_ids "
            "RETURN workflow.workflow_id AS workflow_id, "
            "dependency.before_step_id AS before_step_id, "
            "dependency.after_step_id AS after_step_id, "
            "dependency.condition AS condition, dependency.basis AS basis, "
            "dependency.evidence_refs_json AS evidence_refs_json "
            "ORDER BY workflow.workflow_id, dependency.dependency_order",
            graph_id=graph_id,
            run_id=run_id,
            **parameters,
        )
        return [cls._flow_row(record) for record in records]

    @staticmethod
    def _ownership_row(record: Any) -> dict[str, Any]:
        resource = _resource_identity_from_record(record)
        if resource["resource_scope"] != "instance":
            raise QueryResultValidationError(
                "소유 관계의 Resource가 instance가 아님"
            )
        return {
            **resource,
            "owner_account_id": record["owner_account_id"],
            "basis": record["basis"],
            "evidence_refs": _decode_list(record["evidence_refs_json"]),
        }

    @classmethod
    def _access_rows(
        cls,
        records: Iterable[Any],
        resource_by_id: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        row_by_identity: dict[tuple[Any, ...], dict[str, Any]] = {}
        for record in records:
            for row in cls._access_rows_for_record(record, resource_by_id):
                identity = (
                    row["account_id"],
                    row["role_id"],
                    row["endpoint_id"],
                    row["resource_id"],
                    row["action"],
                )
                existing = row_by_identity.get(identity)
                if existing is None:
                    row_by_identity[identity] = row
                    continue
                existing["evidence_refs"] = _merge_evidence_refs(
                    existing["evidence_refs"],
                    row["evidence_refs"],
                )
                
                for request_id in row["request_ids"]:
                    if request_id not in existing["request_ids"]:
                        existing["request_ids"].append(request_id)
        return list(row_by_identity.values())

    @staticmethod
    def _access_rows_for_record(
        record: Any,
        resource_by_id: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        request_id = record["request_id"]
        if not isinstance(request_id, str) or not request_id:
            raise QueryResultValidationError(
                "요청 관찰 레코드의 request_id가 올바르지 않음"
            )
        action = record["action"]
        if not isinstance(action, str) or not action:
            raise QueryResultValidationError("요청 관찰 레코드의 action이 올바르지 않음")
        resource_ids = _decode_string_list(record["resource_ids_json"])
        resources: list[dict[str, Any]] = [
            resource_by_id[resource_id] for resource_id in resource_ids
        ] or [_empty_resource_identity()]
        evidence_refs = _decode_list(record["evidence_refs_json"])
        return [
            {
                "account_id": record["account_id"],
                "role_id": record["role_id"],
                "endpoint_id": record["endpoint_id"],
                **resource,
                "action": action,
                "request_ids": [request_id],
                "access_observed": True,
                "evidence_refs": list(evidence_refs),
            }
            for resource in resources
        ]

    @staticmethod
    def _load_resource_identities(
        transaction: ManagedTransaction,
        graph_id: str,
        run_id: str,
        resource_ids: set[str],
    ) -> dict[str, dict[str, Any]]:
        if not resource_ids:
            return {}
        records = transaction.run(
            "MATCH (resource:ABC2Entity {graph_id: $graph_id, run_id: $run_id, "
            "node_type: 'Resource'}) "
            "WHERE resource.node_id IN $resource_ids "
            "RETURN resource.node_id AS resource_id, "
            "resource.resource_key AS resource_key, "
            "resource.resource_scope AS resource_scope, "
            "resource.resource_match_key_json AS resource_match_key_json",
            graph_id=graph_id,
            run_id=run_id,
            resource_ids=sorted(resource_ids),
        )
        resource_by_id: dict[str, dict[str, Any]] = {}
        for record in records:
            resource = _resource_identity_from_record(record)
            resource_id = resource["resource_id"]
            if resource_id in resource_by_id:
                raise QueryResultValidationError("중복 Resource node_id가 조회됨")
            resource_by_id[resource_id] = resource
        if set(resource_by_id) != resource_ids:
            raise QueryResultValidationError(
                "요청 관찰의 Resource node_id를 그래프에서 찾을 수 없음"
            )
        return resource_by_id

    @staticmethod
    def _flow_row(record: Any) -> dict[str, Any]:
        return {
            "workflow_id": record["workflow_id"],
            "before_step_id": record["before_step_id"],
            "after_step_id": record["after_step_id"],
            "condition": record["condition"],
            "basis": record["basis"],
            "evidence_refs": _decode_list(record["evidence_refs_json"]),
        }

    @classmethod
    def _apply_verification_transaction(
        cls,
        transaction: ManagedTransaction,
        graph_id: str,
        run_id: str,
        update: VerificationUpdate,
    ) -> VerificationState:
        graph_record = transaction.run(
            "MATCH (graph:ABC2Graph {graph_id: $graph_id, run_id: $run_id}) "
            "SET graph.revision = graph.revision "
            "RETURN graph.revision AS revision",
            graph_id=graph_id,
            run_id=run_id,
        ).single()
        if graph_record is None:
            raise GraphNotFoundError("요청한 run 범위에 graph_id가 존재하지 않음")
        current_revision = int(graph_record["revision"])

        applied_records = list(
            transaction.run(
                "MATCH (verification:ABC2AppliedVerification {"
                "graph_id: $graph_id, run_id: $run_id}) "
                "WHERE verification.verification_id IN $verification_ids "
                "RETURN verification.verification_id AS verification_id, "
                "verification.source_artifact_id AS source_artifact_id, "
                "verification.source_sha256 AS source_sha256",
                graph_id=graph_id,
                run_id=run_id,
                verification_ids=list(update.verification_ids),
            )
        )
        if applied_records:
            if len(applied_records) != len(update.verification_ids):
                raise VerificationConflictError(
                    "일부 verification_id만 이미 반영되어 있음"
                )
            if any(
                record["source_artifact_id"] != update.source.artifact_id
                or record["source_sha256"] != update.source.sha256
                for record in applied_records
            ):
                raise VerificationConflictError(
                    "verification_id가 다른 산출물에서 재사용됨"
                )
            return VerificationState(
                graph_id=graph_id,
                previous_graph_revision=current_revision,
                graph_revision=current_revision,
                applied_verification_ids=update.verification_ids,
                is_applied=False,
            )

        if not update.verification_ids:
            return VerificationState(
                graph_id=graph_id,
                previous_graph_revision=current_revision,
                graph_revision=current_revision,
                applied_verification_ids=(),
                is_applied=False,
            )
        if current_revision != update.source_graph_revision:
            raise GraphRevisionMismatchError("verification의 기준 revision이 오래됨")

        cls._validate_update_identities(transaction, graph_id, run_id, update)
        cls._upsert_verified_nodes(transaction, graph_id, run_id, update)
        cls._upsert_verified_relationships(transaction, graph_id, run_id, update)
        next_revision = current_revision + 1
        transaction.run(
            "UNWIND $verification_ids AS verification_id "
            "CREATE (:ABC2AppliedVerification {"
            "graph_id: $graph_id, run_id: $run_id, "
            "verification_id: verification_id, "
            "source_artifact_id: $source_artifact_id, "
            "source_sha256: $source_sha256, "
            "source_iteration: $source_iteration, "
            "source_status: $source_status, "
            "source_graph_revision: $source_graph_revision, "
            "applied_revision: $applied_revision})",
            graph_id=graph_id,
            run_id=run_id,
            verification_ids=list(update.verification_ids),
            source_artifact_id=update.source.artifact_id,
            source_sha256=update.source.sha256,
            source_iteration=update.source.iteration,
            source_status=update.source.status,
            source_graph_revision=update.source_graph_revision,
            applied_revision=next_revision,
        ).consume()
        revision_record = transaction.run(
            "MATCH (graph:ABC2Graph {graph_id: $graph_id, run_id: $run_id}) "
            "WHERE graph.revision = $current_revision "
            "SET graph.revision = $next_revision "
            "RETURN graph.revision AS revision",
            graph_id=graph_id,
            run_id=run_id,
            current_revision=current_revision,
            next_revision=next_revision,
        ).single()
        if revision_record is None:
            raise GraphRevisionMismatchError("검증 반영 중 graph revision이 변경됨")
        return VerificationState(
            graph_id=graph_id,
            previous_graph_revision=current_revision,
            graph_revision=int(revision_record["revision"]),
            applied_verification_ids=update.verification_ids,
            is_applied=True,
        )

    @classmethod
    def _validate_update_identities(
        cls,
        transaction: ManagedTransaction,
        graph_id: str,
        run_id: str,
        update: VerificationUpdate,
    ) -> None:
        node_records = [serialize_node(node) for node in update.nodes]
        conflicting_nodes = list(
            transaction.run(
                "UNWIND $records AS record "
                "MATCH (node:ABC2Entity {graph_id: $graph_id, run_id: $run_id, "
                "node_id: record.node_id}) "
                "WHERE node.node_type <> record.node_type "
                "RETURN node.node_id AS node_id",
                graph_id=graph_id,
                run_id=run_id,
                records=node_records,
            )
        )
        if conflicting_nodes:
            raise GraphUpdateConflictError(
                "기존 node_id가 다른 node_type으로 갱신됨"
            )

        referenced_node_ids = {
            node_id
            for edge in update.relationships
            for node_id in (edge.source_id, edge.target_id)
        }
        stored_node_by_id = {
            record["node_id"]: record
            for record in transaction.run(
                "MATCH (node:ABC2Entity {graph_id: $graph_id, run_id: $run_id}) "
                "WHERE node.node_id IN $node_ids "
                "RETURN node.node_id AS node_id, node.node_type AS node_type, "
                "node.resource_scope AS resource_scope",
                graph_id=graph_id,
                run_id=run_id,
                node_ids=list(referenced_node_ids),
            )
        }
        if set(stored_node_by_id) != referenced_node_ids:
            raise GraphUpdateReferenceError(
                "검증 관계가 존재하지 않는 node_id를 참조함"
            )
        for edge in update.relationships:
            source = stored_node_by_id[edge.source_id]
            target = stored_node_by_id[edge.target_id]
            if source["node_type"] != "User":
                raise GraphUpdateReferenceError(
                    "검증 관계의 source는 기존 User 노드여야 함"
                )
            if target["node_type"] != "Resource":
                raise GraphUpdateReferenceError(
                    "검증 관계의 target은 기존 Resource 노드여야 함"
                )
            if target["resource_scope"] != "instance":
                raise GraphUpdateReferenceError(
                    "검증 관계의 target은 Resource instance여야 함"
                )

        edge_by_id = {edge.relationship_id: edge for edge in update.relationships}
        stored_edges = list(
            transaction.run(
                "MATCH (source:ABC2Entity {graph_id: $graph_id, run_id: $run_id})"
                "-[edge]->(target:ABC2Entity {graph_id: $graph_id, run_id: $run_id}) "
                "WHERE edge.relationship_id IN $relationship_ids "
                "RETURN edge.relationship_id AS relationship_id, "
                "source.node_id AS source_id, target.node_id AS target_id, "
                "type(edge) AS relation_type",
                graph_id=graph_id,
                run_id=run_id,
                relationship_ids=list(edge_by_id),
            )
        )
        seen_ids: set[str] = set()
        for record in stored_edges:
            relationship_id = record["relationship_id"]
            edge = edge_by_id[relationship_id]
            if relationship_id in seen_ids or (
                record["source_id"] != edge.source_id
                or record["target_id"] != edge.target_id
                or record["relation_type"] != edge.relation_type
            ):
                raise GraphUpdateConflictError(
                    "기존 relationship_id의 연결 정보가 갱신 입력과 다름"
                )
            seen_ids.add(relationship_id)

    @staticmethod
    def _upsert_verified_nodes(
        transaction: ManagedTransaction,
        graph_id: str,
        run_id: str,
        update: VerificationUpdate,
    ) -> None:
        transaction.run(
            "UNWIND $records AS record "
            "MERGE (node:ABC2Entity {graph_id: $graph_id, run_id: $run_id, "
            "node_id: record.node_id}) "
            "SET node.node_type = record.node_type, "
            "node.properties_json = record.properties_json, "
            "node.resource_key = record.resource_key, "
            "node.resource_scope = record.resource_scope, "
            "node.resource_match_key_json = record.resource_match_key_json, "
            "node.basis = record.basis, "
            "node.evidence_refs_json = record.evidence_refs_json",
            graph_id=graph_id,
            run_id=run_id,
            records=[serialize_node(node) for node in update.nodes],
        ).consume()

    @staticmethod
    def _upsert_verified_relationships(
        transaction: ManagedTransaction,
        graph_id: str,
        run_id: str,
        update: VerificationUpdate,
    ) -> None:
        records_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for edge in update.relationships:
            records_by_type[edge.relation_type].append(serialize_edge(edge))
        for relationship_type, records in records_by_type.items():
            query = (
                "UNWIND $records AS record "
                "MATCH (source:ABC2Entity {graph_id: $graph_id, run_id: $run_id, "
                "node_id: record.source_id}) "
                "MATCH (target:ABC2Entity {graph_id: $graph_id, run_id: $run_id, "
                "node_id: record.target_id}) "
                f"MERGE (source)-[stored:{relationship_type} {{"
                "graph_id: $graph_id, run_id: $run_id, "
                "relationship_id: record.relationship_id}]->(target) "
                "SET stored.properties_json = record.properties_json, "
                "stored.basis = record.basis, "
                "stored.evidence_refs_json = record.evidence_refs_json "
                "RETURN count(stored) AS updated_count"
            )
            record = transaction.run(
                query,
                graph_id=graph_id,
                run_id=run_id,
                records=records,
            ).single(strict=True)
            if int(record["updated_count"]) != len(records):
                raise GraphUpdateReferenceError(
                    "검증 관계의 source 또는 target 노드가 없음"
                )

    @classmethod
    def _ingest_transaction(
        cls,
        transaction: ManagedTransaction,
        graph_id: str,
        run_id: str,
        graph: SemanticGraph,
        source: GraphSource,
    ) -> GraphState:
        existing_source = transaction.run(
            "MATCH (graph:ABC2Graph {"
            "run_id: $run_id, source_artifact_id: $source_artifact_id}) "
            "RETURN graph.graph_id AS graph_id, graph.revision AS revision, "
            "graph.source_sha256 AS source_sha256",
            run_id=run_id,
            source_artifact_id=source.artifact_id,
        ).single()
        if existing_source is not None:
            if existing_source["source_sha256"] != source.sha256:
                raise SourceArtifactConflictError(
                    "같은 semantic artifact_id가 다른 SHA-256으로 재사용됨"
                )
            cls._verify_graph_counts(transaction, existing_source["graph_id"], run_id, graph)
            return GraphState(
                graph_id=existing_source["graph_id"],
                graph_revision=int(existing_source["revision"]),
                is_created=False,
            )

        existing_graph = transaction.run(
            "MATCH (graph:ABC2Graph {graph_id: $graph_id, run_id: $run_id}) "
            "RETURN graph.graph_id AS graph_id",
            graph_id=graph_id,
            run_id=run_id,
        ).single()
        if existing_graph is not None:
            raise GraphAlreadyExistsError("같은 run_id와 graph_id의 그래프가 이미 존재함")

        transaction.run(
            "CREATE (:ABC2Graph {"
            "graph_id: $graph_id, run_id: $run_id, revision: 1, "
            "source_artifact_id: $source_artifact_id, "
            "source_sha256: $source_sha256, source_iteration: $source_iteration, "
            "source_status: $source_status})",
            graph_id=graph_id,
            run_id=run_id,
            source_artifact_id=source.artifact_id,
            source_sha256=source.sha256,
            source_iteration=source.iteration,
            source_status=source.status,
        ).consume()
        cls._create_nodes(transaction, graph_id, run_id, graph)
        cls._create_relationships(transaction, graph_id, run_id, graph)
        cls._create_request_observations(transaction, graph_id, run_id, graph)
        cls._create_workflows(transaction, graph_id, run_id, graph)
        cls._verify_graph_counts(transaction, graph_id, run_id, graph)
        return GraphState(graph_id=graph_id, graph_revision=1, is_created=True)

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
            "resource_key: record.resource_key, "
            "resource_scope: record.resource_scope, "
            "resource_match_key_json: record.resource_match_key_json, "
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
    def _create_request_observations(
        transaction: ManagedTransaction,
        graph_id: str,
        run_id: str,
        graph: SemanticGraph,
    ) -> None:
        records = [
            serialize_request_observation(observation)
            for observation in graph.request_observations
        ]
        transaction.run(
            "UNWIND $records AS record "
            "CREATE (:ABC2RequestObservation {"
            "graph_id: $graph_id, run_id: $run_id, "
            "request_id: record.request_id, account_id: record.account_id, "
            "role_id: record.role_id, user_node_id: record.user_node_id, "
            "role_node_id: record.role_node_id, endpoint_id: record.endpoint_id, "
            "action: record.action, resource_ids_json: record.resource_ids_json, "
            "basis: record.basis, evidence_refs_json: record.evidence_refs_json})",
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
        request_observation_values = _record_values(
            transaction.run(
                "MATCH (observation:ABC2RequestObservation {"
                "graph_id: $graph_id, run_id: $run_id}) "
                "RETURN observation {.*} AS value ORDER BY observation.request_id",
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
            request_observations=tuple(
                deserialize_request_observation(item)
                for item in request_observation_values
            ),
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
            ("ABC2RequestObservation", "request_observations"),
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
            request_observations=counts["request_observations"],
            nodes=counts["nodes"],
            relationships=int(relationship_record["count"]),
            workflows=counts["workflows"],
            workflow_steps=counts["workflow_steps"],
            workflow_dependencies=counts["workflow_dependencies"],
        )

    @classmethod
    def _verify_graph_counts(
        cls,
        transaction: ManagedTransaction,
        graph_id: str,
        run_id: str,
        graph: SemanticGraph,
    ) -> None:
        actual = cls._count_transaction(transaction, graph_id, run_id)
        expected = GraphCounts(
            request_observations=len(graph.request_observations),
            nodes=len(graph.nodes),
            relationships=len(graph.relationships),
            workflows=len(graph.workflows),
            workflow_steps=sum(len(item.steps) for item in graph.workflows),
            workflow_dependencies=sum(
                len(item.dependencies) for item in graph.workflows
            ),
        )
        if actual != expected:
            raise GraphStorageVerificationError("Neo4j 적재 건수가 입력과 다름")


def _record_values(records: Iterable[Any]) -> list[dict[str, Any]]:
    return [dict(record["value"]) for record in records]


def _group_by_workflow(
    values: list[dict[str, Any]],
) -> defaultdict[str, list[dict[str, Any]]]:
    values_by_workflow: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for value in values:
        values_by_workflow[value["workflow_id"]].append(value)
    return values_by_workflow


def _decode_list(value: str) -> list[dict[str, Any]]:
    try:
        decoded = decode_json(value)
    except (TypeError, ValueError) as error:
        raise QueryResultValidationError(
            "저장된 evidence_refs JSON을 읽을 수 없음"
        ) from error
    if not isinstance(decoded, list) or any(
        not isinstance(item, dict) for item in decoded
    ):
        raise QueryResultValidationError("저장된 evidence_refs 형식이 올바르지 않음")
    return decoded


def _decode_string_list(value: str) -> list[str]:
    try:
        decoded = decode_json(value)
    except (TypeError, ValueError) as error:
        raise QueryResultValidationError(
            "저장된 request resource_ids JSON을 읽을 수 없음"
        ) from error
    if not isinstance(decoded, list) or any(
        not isinstance(item, str) or not item for item in decoded
    ):
        raise QueryResultValidationError(
            "저장된 request resource_ids 형식이 올바르지 않음"
        )
    return decoded


def _empty_resource_identity() -> dict[str, Any]:
    return {
        "resource_id": None,
        "resource_key": None,
        "resource_scope": None,
        "match_key": None,
    }


def _resource_identity_from_record(record: Any) -> dict[str, Any]:
    resource_id = record["resource_id"]
    resource_key = record["resource_key"]
    resource_scope = record["resource_scope"]
    if not isinstance(resource_id, str) or not resource_id:
        raise QueryResultValidationError("Resource node_id가 올바르지 않음")
    if not isinstance(resource_key, str) or not resource_key:
        raise QueryResultValidationError("Resource resource_key가 올바르지 않음")
    if resource_scope not in {"type", "instance"}:
        raise QueryResultValidationError("Resource resource_scope가 올바르지 않음")
    match_key = _decode_resource_match_key(record["resource_match_key_json"])
    if resource_scope == "type" and match_key is not None:
        raise QueryResultValidationError("Resource type의 match_key는 null이어야 함")
    if resource_scope == "instance" and match_key is None:
        raise QueryResultValidationError("Resource instance의 match_key가 없음")
    if match_key is not None and match_key["resource_key"] != resource_key:
        raise QueryResultValidationError(
            "Resource match_key의 resource_key가 일치하지 않음"
        )
    return {
        "resource_id": resource_id,
        "resource_key": resource_key,
        "resource_scope": resource_scope,
        "match_key": match_key,
    }


def _decode_resource_match_key(value: str | None) -> dict[str, Any] | None:
    if value is None:
        return None
    try:
        decoded = decode_json(value)
    except (TypeError, ValueError) as error:
        raise QueryResultValidationError(
            "저장된 Resource match_key JSON을 읽을 수 없음"
        ) from error
    if not isinstance(decoded, dict) or set(decoded) != {
        "resource_key",
        "identifiers",
    }:
        raise QueryResultValidationError("저장된 Resource match_key 형식이 올바르지 않음")
    resource_key = decoded["resource_key"]
    identifiers = decoded["identifiers"]
    if not isinstance(resource_key, str) or not resource_key:
        raise QueryResultValidationError("저장된 Resource match_key 형식이 올바르지 않음")
    if not isinstance(identifiers, list) or not identifiers:
        raise QueryResultValidationError("저장된 Resource match_key 형식이 올바르지 않음")
    identifier_keys: set[str] = set()
    for identifier in identifiers:
        if not isinstance(identifier, dict) or set(identifier) != {
            "key",
            "value",
        }:
            raise QueryResultValidationError(
                "저장된 Resource match_key 식별값 형식이 올바르지 않음"
            )
        key = identifier["key"]
        item_value = identifier["value"]
        if (
            not isinstance(key, str)
            or not key
            or not isinstance(item_value, str)
            or not item_value
            or key in identifier_keys
        ):
            raise QueryResultValidationError(
                "저장된 Resource match_key 식별값 형식이 올바르지 않음"
            )
        identifier_keys.add(key)
    return decoded


def _merge_evidence_refs(
    existing_refs: list[dict[str, Any]],
    new_refs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged_refs = list(existing_refs)
    identities = {encode_json(item) for item in existing_refs}
    for evidence_ref in new_refs:
        identity = encode_json(evidence_ref)
        if identity in identities:
            continue
        merged_refs.append(evidence_ref)
        identities.add(identity)
    return merged_refs
