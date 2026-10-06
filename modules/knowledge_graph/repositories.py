"""Persistence boundary owned by the knowledge graph module."""

from __future__ import annotations

from typing import Any, Protocol

from modules.knowledge_graph.models import SemanticGraph


class GraphRepository(Protocol):
    """Interface implemented by the Neo4j adapter in the next stage."""

    def ingest(self, graph_id: str, run_id: str, graph: SemanticGraph) -> int:
        """Persist a semantic graph and return the resulting revision."""

    def query(
        self,
        graph_id: str,
        run_id: str,
        query_key: str,
        parameters: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Execute one allowlisted query template."""

    def apply_verification(
        self,
        graph_id: str,
        run_id: str,
        artifact: dict[str, Any],
    ) -> int:
        """Apply verified graph updates and return the resulting revision."""
