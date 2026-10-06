"""Contract preparation and orchestration for knowledge graph operations."""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable
from uuid import uuid4

from modules.knowledge_graph.exceptions import (
    ContractValidationError,
    InputArtifactFailedError,
    InputHashMismatchError,
    OutputArtifactExistsError,
    QueryResultValidationError,
    RepositoryError,
)
from modules.knowledge_graph.models import (
    ControlError,
    GraphEdge,
    GraphNode,
    GraphSource,
    IngestControlResponse,
    IngestRequest,
    QueryControlResponse,
    QueryDefinition,
    QueryRequest,
    SemanticGraph,
    VerificationControlResponse,
    VerificationRequest,
    VerificationSource,
    VerificationUpdate,
)
from modules.knowledge_graph.repositories import GraphRepository
from modules.knowledge_graph.utils.atomic_writer import write_json_atomically
from modules.knowledge_graph.utils.hashing import calculate_sha256
from modules.knowledge_graph.utils.validation import (
    load_and_validate_artifact,
    validate_graph_query_semantics,
    validate_graph_query_result_against_input,
    validate_graph_query_result_semantics,
    validate_schema,
    validate_semantic_analysis_semantics,
    validate_verification_results_semantics,
)


SCHEMA_DIRECTORY = Path(__file__).parent / "schemas"


@dataclass(frozen=True)
class PreparedIngest:
    run_id: str
    graph: SemanticGraph
    source: GraphSource
    errors: tuple[ControlError, ...]


@dataclass(frozen=True)
class PreparedQuery:
    request: QueryRequest
    source_artifact_id: str
    source_errors: tuple[ControlError, ...]
    graph_id: str
    expected_graph_revision: int | None
    queries: tuple[QueryDefinition, ...]


@dataclass(frozen=True)
class PreparedVerification:
    request: VerificationRequest
    update: VerificationUpdate
    errors: tuple[ControlError, ...]


def prepare_ingest(input_path: Path) -> tuple[dict[str, Any], SemanticGraph]:
    artifact = load_and_validate_artifact(
        input_path,
        SCHEMA_DIRECTORY / "input" / "semantic_analysis.schema.json",
    )
    if artifact["status"] == "failed":
        raise InputArtifactFailedError("semantic_analysis 상태가 failed임")
    validate_semantic_analysis_semantics(artifact)
    return artifact, SemanticGraph.from_artifact(artifact)


def prepare_ingest_operation(request: IngestRequest) -> PreparedIngest:
    actual_sha256 = calculate_sha256(request.input_path)
    if actual_sha256 != request.expected_sha256:
        raise InputHashMismatchError("semantic_analysis SHA-256이 일치하지 않음")
    artifact, graph = prepare_ingest(request.input_path)
    _validate_ingest_context(artifact, request)
    source = GraphSource(
        artifact_id=artifact["artifact_id"],
        sha256=actual_sha256,
        iteration=artifact["iteration"],
        status=artifact["status"],
    )
    return PreparedIngest(
        run_id=request.run_id,
        graph=graph,
        source=source,
        errors=tuple(ControlError.from_mapping(item) for item in artifact["errors"]),
    )


def execute_ingest(
    prepared: PreparedIngest,
    repository: GraphRepository,
    graph_id_factory: Callable[[], str] | None = None,
) -> IngestControlResponse:
    create_graph_id = graph_id_factory or _create_graph_id
    state = repository.ingest(
        create_graph_id(),
        prepared.run_id,
        prepared.graph,
        prepared.source,
    )
    return IngestControlResponse(
        status=prepared.source.status,
        graph_id=state.graph_id,
        graph_revision=state.graph_revision,
        is_ready=True,
        errors=prepared.errors,
    )


def prepare_query(input_path: Path) -> dict[str, Any]:
    artifact = load_and_validate_artifact(
        input_path,
        SCHEMA_DIRECTORY / "input" / "graph_query.schema.json",
    )
    if artifact["status"] == "failed":
        raise InputArtifactFailedError("graph_query 상태가 failed임")
    validate_graph_query_semantics(artifact)
    return artifact


def prepare_query_operation(request: QueryRequest) -> PreparedQuery:
    actual_sha256 = calculate_sha256(request.input_path)
    if actual_sha256 != request.expected_sha256:
        raise InputHashMismatchError("graph_query SHA-256이 일치하지 않음")
    artifact = prepare_query(request.input_path)
    _validate_query_context(artifact, request)
    data = artifact["data"]
    return PreparedQuery(
        request=request,
        source_artifact_id=artifact["artifact_id"],
        source_errors=tuple(
            ControlError.from_mapping(item) for item in artifact["errors"]
        ),
        graph_id=data["graph_id"],
        expected_graph_revision=data["expected_graph_revision"],
        queries=tuple(QueryDefinition.from_mapping(item) for item in data["queries"]),
    )


def execute_query(
    prepared: PreparedQuery,
    repository: GraphRepository,
    artifact_id_factory: Callable[[], str] | None = None,
    created_at_factory: Callable[[], str] | None = None,
) -> dict[str, Any]:
    started_at = perf_counter()
    create_artifact_id = artifact_id_factory or _create_query_artifact_id
    create_timestamp = created_at_factory or _create_timestamp
    artifact_id = create_artifact_id()
    graph_revision = repository.get_revision(prepared.graph_id, prepared.request.run_id)
    if graph_revision is None:
        error = _error("GRAPH_NOT_FOUND", "조회할 graph_id가 없음", None, False)
        return _failed_query_artifact(
            prepared,
            artifact_id,
            create_timestamp(),
            (*prepared.source_errors, error),
            started_at,
        )
    if (
        prepared.expected_graph_revision is not None
        and prepared.expected_graph_revision != graph_revision
    ):
        error = _error(
            "GRAPH_REVISION_MISMATCH",
            "요청 revision과 실제 graph revision이 다름",
            None,
            False,
        )
        return _failed_query_artifact(
            prepared,
            artifact_id,
            create_timestamp(),
            (*prepared.source_errors, error),
            started_at,
        )

    results, query_errors, completed_count = _execute_queries(
        prepared,
        repository,
    )
    ending_revision = repository.get_revision(
        prepared.graph_id,
        prepared.request.run_id,
    )
    if ending_revision != graph_revision:
        error = _error(
            "GRAPH_REVISION_CHANGED",
            "질의 실행 중 graph revision이 변경됨",
            None,
            True,
        )
        return _failed_query_artifact(
            prepared,
            artifact_id,
            create_timestamp(),
            (*prepared.source_errors, *query_errors, error),
            started_at,
        )
    if prepared.queries and completed_count == 0:
        return _failed_query_artifact(
            prepared,
            artifact_id,
            create_timestamp(),
            (*prepared.source_errors, *query_errors),
            started_at,
        )

    errors = (*prepared.source_errors, *query_errors)
    status = "partial" if errors else "completed"
    return _query_artifact(
        prepared=prepared,
        artifact_id=artifact_id,
        created_at=create_timestamp(),
        status=status,
        errors=errors,
        data={
            "graph_id": prepared.graph_id,
            "graph_revision": graph_revision,
            "results": results,
        },
        started_at=started_at,
    )


def build_query_dependency_failure(
    prepared: PreparedQuery,
    code: str,
    message: str,
    retryable: bool,
) -> dict[str, Any]:
    started_at = perf_counter()
    error = _error(code, message, None, retryable)
    return _failed_query_artifact(
        prepared,
        _create_query_artifact_id(),
        _create_timestamp(),
        (*prepared.source_errors, error),
        started_at,
    )


def publish_query_artifact(
    prepared: PreparedQuery,
    artifact: dict[str, Any],
) -> QueryControlResponse:
    validate_schema(
        artifact,
        SCHEMA_DIRECTORY / "output" / "graph_query_result.schema.json",
    )
    validate_graph_query_result_semantics(artifact)
    validate_graph_query_result_against_input(
        artifact,
        prepared.source_artifact_id,
        tuple((item.query_id, item.query_key) for item in prepared.queries),
    )
    if prepared.request.output_path.exists():
        raise OutputArtifactExistsError("graph_query_result 출력이 이미 존재함")
    write_json_atomically(prepared.request.output_path, artifact)
    output_sha256 = calculate_sha256(prepared.request.output_path)
    graph_revision = (
        artifact["data"]["graph_revision"] if artifact["data"] is not None else None
    )
    return QueryControlResponse(
        status=artifact["status"],
        artifact_id=artifact["artifact_id"],
        output_path=prepared.request.output_relative_path,
        sha256=output_sha256,
        graph_id=prepared.graph_id,
        graph_revision=graph_revision,
        errors=tuple(ControlError.from_mapping(item) for item in artifact["errors"]),
    )


def _execute_queries(
    prepared: PreparedQuery,
    repository: GraphRepository,
) -> tuple[list[dict[str, Any]], tuple[ControlError, ...], int]:
    results: list[dict[str, Any]] = []
    errors: list[ControlError] = []
    completed_count = 0
    for query in prepared.queries:
        try:
            rows = repository.query(
                prepared.graph_id,
                prepared.request.run_id,
                query.query_key,
                query.parameters,
            )
        except RepositoryError:
            error = _error(
                "QUERY_EXECUTION_FAILED",
                "Neo4j가 개별 읽기 질의를 완료하지 못함",
                query.query_id,
                True,
            )
            errors.append(error)
            results.append(_failed_query_result(query, error))
        except (ContractValidationError, QueryResultValidationError):
            error = _error(
                "QUERY_RESULT_INVALID",
                "저장된 그래프 데이터가 질의 결과 계약을 만족하지 않음",
                query.query_id,
                False,
            )
            errors.append(error)
            results.append(_failed_query_result(query, error))
        else:
            completed_count += 1
            results.append(
                {
                    "query_id": query.query_id,
                    "query_key": query.query_key,
                    "status": "completed",
                    "rows": rows,
                    "errors": [],
                }
            )
    return results, tuple(errors), completed_count


def _failed_query_result(
    query: QueryDefinition,
    error: ControlError,
) -> dict[str, Any]:
    return {
        "query_id": query.query_id,
        "query_key": query.query_key,
        "status": "failed",
        "rows": [],
        "errors": [error.to_mapping()],
    }


def _failed_query_artifact(
    prepared: PreparedQuery,
    artifact_id: str,
    created_at: str,
    errors: tuple[ControlError, ...],
    started_at: float,
) -> dict[str, Any]:
    return _query_artifact(
        prepared=prepared,
        artifact_id=artifact_id,
        created_at=created_at,
        status="failed",
        errors=errors,
        data=None,
        started_at=started_at,
    )


def _query_artifact(
    prepared: PreparedQuery,
    artifact_id: str,
    created_at: str,
    status: str,
    errors: tuple[ControlError, ...],
    data: dict[str, Any] | None,
    started_at: float,
) -> dict[str, Any]:
    duration_ms = max(0, round((perf_counter() - started_at) * 1000))
    return {
        "schema_version": "0.1.0",
        "artifact_type": "graph_query_result",
        "artifact_id": artifact_id,
        "run_id": prepared.request.run_id,
        "iteration": prepared.request.iteration,
        "producer": "knowledge_graph",
        "mode": prepared.request.mode,
        "created_at": created_at,
        "status": status,
        "input_refs": [
            {
                "artifact_id": prepared.source_artifact_id,
                "artifact_type": "graph_query",
                "iteration": prepared.request.iteration,
                "path": prepared.request.input_relative_path,
                "sha256": prepared.request.expected_sha256,
            }
        ],
        "errors": [error.to_mapping() for error in errors],
        "runtime_metrics": {
            "duration_ms": duration_ms,
            "llm_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "peak_memory_mb": None,
        },
        "data": data,
    }


def _error(
    code: str,
    message: str,
    item_ref: str | None,
    retryable: bool,
) -> ControlError:
    return ControlError(
        code=code,
        message=message,
        item_ref=item_ref,
        retryable=retryable,
    )


def _create_query_artifact_id() -> str:
    return f"graph_query_result_{uuid4().hex}"


def _create_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00",
        "Z",
    )


def prepare_verification(input_path: Path) -> dict[str, Any]:
    artifact = load_and_validate_artifact(
        input_path,
        SCHEMA_DIRECTORY / "input" / "verification_results.schema.json",
    )
    if artifact["status"] == "failed":
        raise InputArtifactFailedError("verification_results 상태가 failed임")
    validate_verification_results_semantics(artifact)
    return artifact


def prepare_verification_operation(
    request: VerificationRequest,
) -> PreparedVerification:
    actual_sha256 = calculate_sha256(request.input_path)
    if actual_sha256 != request.expected_sha256:
        raise InputHashMismatchError("verification_results SHA-256이 일치하지 않음")
    artifact = prepare_verification(request.input_path)
    _validate_verification_context(artifact, request)
    data = artifact["data"]
    graph_updates = data["graph_updates"]
    source = VerificationSource(
        artifact_id=artifact["artifact_id"],
        sha256=actual_sha256,
        iteration=artifact["iteration"],
        status=artifact["status"],
    )
    update = VerificationUpdate(
        source=source,
        source_graph_revision=data["source_graph_revision"],
        verification_ids=tuple(graph_updates["source_verification_ids"]),
        nodes=tuple(GraphNode.from_mapping(item) for item in graph_updates["nodes"]),
        relationships=tuple(
            GraphEdge.from_mapping(item) for item in graph_updates["relationships"]
        ),
    )
    return PreparedVerification(
        request=request,
        update=update,
        errors=tuple(ControlError.from_mapping(item) for item in artifact["errors"]),
    )


def execute_verification(
    prepared: PreparedVerification,
    repository: GraphRepository,
) -> VerificationControlResponse:
    state = repository.apply_verification(
        prepared.request.graph_id,
        prepared.request.run_id,
        prepared.update,
    )
    return VerificationControlResponse(
        status=prepared.update.source.status,
        graph_id=state.graph_id,
        previous_graph_revision=state.previous_graph_revision,
        graph_revision=state.graph_revision,
        applied_verification_ids=state.applied_verification_ids,
        is_applied=state.is_applied,
        errors=prepared.errors,
    )


def _validate_ingest_context(
    artifact: dict[str, Any],
    request: IngestRequest,
) -> None:
    if artifact["run_id"] != request.run_id:
        raise ContractValidationError("semantic_analysis run_id가 context와 다름")
    if artifact["iteration"] != request.iteration:
        raise ContractValidationError("semantic_analysis iteration이 context와 다름")
    if artifact["mode"] != request.mode:
        raise ContractValidationError("semantic_analysis mode가 context와 다름")


def _validate_query_context(
    artifact: dict[str, Any],
    request: QueryRequest,
) -> None:
    if artifact["run_id"] != request.run_id:
        raise ContractValidationError("graph_query run_id가 context와 다름")
    if artifact["iteration"] != request.iteration:
        raise ContractValidationError("graph_query iteration이 context와 다름")
    if artifact["mode"] != request.mode:
        raise ContractValidationError("graph_query mode가 context와 다름")


def _validate_verification_context(
    artifact: dict[str, Any],
    request: VerificationRequest,
) -> None:
    if artifact["run_id"] != request.run_id:
        raise ContractValidationError("verification_results run_id가 context와 다름")
    if artifact["iteration"] != request.iteration:
        raise ContractValidationError(
            "verification_results iteration이 context와 다름"
        )
    if artifact["mode"] != request.mode:
        raise ContractValidationError("verification_results mode가 context와 다름")


def _create_graph_id() -> str:
    return f"graph_{uuid4().hex}"
