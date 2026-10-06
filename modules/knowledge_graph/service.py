"""Contract preparation for future knowledge graph operations."""

from pathlib import Path
from typing import Any

from modules.knowledge_graph.models import SemanticGraph
from modules.knowledge_graph.utils.validation import (
    load_and_validate_artifact,
    validate_graph_query_semantics,
    validate_semantic_analysis_semantics,
    validate_verification_results_semantics,
)


SCHEMA_DIRECTORY = Path(__file__).parent / "schemas"


def prepare_ingest(input_path: Path) -> tuple[dict[str, Any], SemanticGraph]:
    artifact = load_and_validate_artifact(
        input_path,
        SCHEMA_DIRECTORY / "input" / "semantic_analysis.schema.json",
    )
    validate_semantic_analysis_semantics(artifact)
    return artifact, SemanticGraph.from_artifact(artifact)


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
