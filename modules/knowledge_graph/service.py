"""Contract preparation and orchestration for knowledge graph operations."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from modules.knowledge_graph.exceptions import (
    ContractValidationError,
    InputArtifactFailedError,
    InputHashMismatchError,
)
from modules.knowledge_graph.models import (
    ControlError,
    GraphSource,
    IngestControlResponse,
    IngestRequest,
    SemanticGraph,
)
from modules.knowledge_graph.repositories import GraphRepository
from modules.knowledge_graph.utils.hashing import calculate_sha256
from modules.knowledge_graph.utils.validation import (
    load_and_validate_artifact,
    validate_graph_query_semantics,
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
    validate_graph_query_semantics(artifact)
    return artifact


def prepare_verification(input_path: Path) -> dict[str, Any]:
    artifact = load_and_validate_artifact(
        input_path,
        SCHEMA_DIRECTORY / "input" / "verification_results.schema.json",
    )
    validate_verification_results_semantics(artifact)
    return artifact


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


def _create_graph_id() -> str:
    return f"graph_{uuid4().hex}"
