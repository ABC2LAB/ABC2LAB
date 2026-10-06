from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from modules.knowledge_graph import entrypoint
from modules.knowledge_graph.utils.hashing import calculate_sha256


INPUT_PATH = "artifacts/iteration-000/access_analyzer/graph_query.json"
OUTPUT_DIR = "artifacts/iteration-000/knowledge_graph"


class FakeQueryRepository:
    def __enter__(self) -> "FakeQueryRepository":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def get_revision(self, graph_id: str, run_id: str) -> int | None:
        return 1

    def query(
        self,
        graph_id: str,
        run_id: str,
        query_key: str,
        parameters: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if query_key == "structure_snapshot":
            return [{"nodes": [], "relationships": [], "workflows": []}]
        return []


def test_run_query_writes_valid_artifact(
    query_run_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_environment(monkeypatch)
    monkeypatch.setattr(
        entrypoint,
        "Neo4jGraphRepository",
        lambda settings: FakeQueryRepository(),
    )

    response = entrypoint.run(
        "query",
        _input_paths(query_run_root),
        OUTPUT_DIR,
        _context(query_run_root),
    )

    output_path = query_run_root / OUTPUT_DIR / "graph_query_result.json"
    artifact = json.loads(output_path.read_text(encoding="utf-8"))
    assert response["status"] == "completed"
    assert response["output_path"].endswith("graph_query_result.json")
    assert len(response["sha256"]) == 64
    assert artifact["artifact_type"] == "graph_query_result"
    assert artifact["input_refs"][0]["artifact_id"] == "graph_query_demo_001"


def test_run_query_publishes_failed_artifact_for_missing_configuration(
    query_run_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "NEO4J_URI",
        "NEO4J_USERNAME",
        "NEO4J_PASSWORD",
        "NEO4J_DATABASE",
    ):
        monkeypatch.delenv(name, raising=False)

    response = entrypoint.run(
        "query",
        _input_paths(query_run_root),
        OUTPUT_DIR,
        _context(query_run_root),
    )

    output_path = query_run_root / OUTPUT_DIR / "graph_query_result.json"
    artifact = json.loads(output_path.read_text(encoding="utf-8"))
    assert response["status"] == "failed"
    assert response["output_path"] is not None
    assert artifact["data"] is None
    assert artifact["errors"][0]["code"] == "CONFIG_INVALID"


def test_run_query_rejects_hash_mismatch_without_output(
    query_run_root: Path,
) -> None:
    input_paths = _input_paths(query_run_root)
    input_paths["graph_query"]["sha256"] = "0" * 64

    response = entrypoint.run(
        "query",
        input_paths,
        OUTPUT_DIR,
        _context(query_run_root),
    )

    assert response["status"] == "failed"
    assert response["output_path"] is None
    assert response["errors"][0]["code"] == "INPUT_HASH_MISMATCH"


def test_run_query_never_overwrites_output(
    query_run_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_environment(monkeypatch)
    monkeypatch.setattr(
        entrypoint,
        "Neo4jGraphRepository",
        lambda settings: FakeQueryRepository(),
    )
    output_path = query_run_root / OUTPUT_DIR / "graph_query_result.json"
    output_path.parent.mkdir(parents=True)
    output_path.write_text("existing", encoding="utf-8")

    response = entrypoint.run(
        "query",
        _input_paths(query_run_root),
        OUTPUT_DIR,
        _context(query_run_root),
    )

    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "OUTPUT_EXISTS"
    assert output_path.read_text(encoding="utf-8") == "existing"


def _input_paths(run_root: Path) -> dict[str, dict[str, str]]:
    input_path = run_root / INPUT_PATH
    return {
        "graph_query": {
            "path": INPUT_PATH,
            "sha256": calculate_sha256(input_path),
        }
    }


def _context(run_root: Path) -> dict[str, object]:
    return {
        "run_id": "run_demo_001",
        "iteration": 0,
        "mode": "development",
        "run_root": run_root,
    }


def _configure_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "test-password")
    monkeypatch.setenv("NEO4J_DATABASE", "neo4j")
