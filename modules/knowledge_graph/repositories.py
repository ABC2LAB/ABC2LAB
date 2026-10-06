"""Persistence boundary owned by the knowledge graph module."""

from __future__ import annotations

from typing import Any, Protocol

from modules.knowledge_graph.models import (
    GraphSource,
    GraphState,
    SemanticGraph,
    VerificationState,
    VerificationUpdate,
)


class GraphRepository(Protocol):
    """Persistence and query boundary implemented by the Neo4j adapter."""

    def ingest(
        self,
        graph_id: str,
        run_id: str,
        graph: SemanticGraph,
        source: GraphSource,
    ) -> GraphState:
        """Persist or reuse a graph for one immutable semantic artifact."""

    def query(
        self,
        graph_id: str,
        run_id: str,
        query_key: str,
        parameters: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Execute one allowlisted query template."""

    def get_revision(self, graph_id: str, run_id: str) -> int | None:
        """Return the current graph revision in one run scope."""

    def apply_verification(
        self,
        graph_id: str,
        run_id: str,
        update: VerificationUpdate,
    ) -> VerificationState:
        """Apply each verified update once and return the graph state."""
