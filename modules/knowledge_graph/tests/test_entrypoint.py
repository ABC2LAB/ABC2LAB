from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from modules.knowledge_graph import entrypoint
from modules.knowledge_graph.exceptions import (
    RepositoryError,
    SourceArtifactConflictError,
)
from modules.knowledge_graph.models import GraphSource, GraphState, SemanticGraph
from modules.knowledge_graph.utils.hashing import calculate_sha256


INPUT_RELATIVE_PATH = (
    "artifacts/iteration-000/semantic_analyzer/semantic_analysis.json"
)
OUTPUT_RELATIVE_PATH = "artifacts/iteration-000/knowledge_graph"


class FakeRepository:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.is_connectivity_checked = False
        self.is_closed = False

    def __enter__(self) -> "FakeRepository":
        return self

    def __exit__(self, *_: object) -> None:
        self.is_closed = True

    def verify_connectivity(self) -> None:
        self.is_connectivity_checked = True

    def ingest(
        self,
        graph_id: str,
        run_id: str,
        graph: SemanticGraph,
        source: GraphSource,
    ) -> GraphState:
        if self.error is not None:
            raise self.error
        return GraphState(
            graph_id="graph_public_001",
            graph_revision=1,
            is_created=True,
        )


def test_run_ingest_returns_public_control_response(
    ingest_run_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = FakeRepository()
    _configure_environment(monkeypatch)
    monkeypatch.setattr(
        entrypoint,
        "Neo4jGraphRepository",
        lambda settings: repository,
    )

    response = entrypoint.run(
        "ingest",
        _input_paths(ingest_run_root),
        OUTPUT_RELATIVE_PATH,
        _context(ingest_run_root),
    )

    assert response == {
        "operation": "ingest",
        "status": "completed",
        "graph_id": "graph_public_001",
        "graph_revision": 1,
        "is_ready": True,
        "errors": [],
    }
    assert repository.is_connectivity_checked is True
    assert repository.is_closed is True


@pytest.mark.parametrize(
    ("error", "expected_code", "is_retryable"),
    [
        (
            SourceArtifactConflictError("conflict"),
            "ARTIFACT_CONFLICT",
            False,
        ),
        (RepositoryError("unavailable"), "NEO4J_UNAVAILABLE", True),
    ],
)
def test_run_ingest_maps_repository_errors(
    ingest_run_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_code: str,
    is_retryable: bool,
) -> None:
    _configure_environment(monkeypatch)
    monkeypatch.setattr(
        entrypoint,
        "Neo4jGraphRepository",
        lambda settings: FakeRepository(error),
    )

    response = entrypoint.run(
        "ingest",
        _input_paths(ingest_run_root),
        OUTPUT_RELATIVE_PATH,
        _context(ingest_run_root),
    )

    assert response["status"] == "failed"
    assert response["is_ready"] is False
    assert response["errors"][0]["code"] == expected_code
    assert response["errors"][0]["retryable"] is is_retryable


def test_run_ingest_rejects_hash_mismatch_before_connection(
    ingest_run_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_created(settings: object) -> None:
        raise AssertionError("repository must not be created")

    monkeypatch.setattr(entrypoint, "Neo4jGraphRepository", fail_if_created)
    input_paths = _input_paths(ingest_run_root)
    input_paths["semantic_analysis"]["sha256"] = "0" * 64

    response = entrypoint.run(
        "ingest",
        input_paths,
        OUTPUT_RELATIVE_PATH,
        _context(ingest_run_root),
    )

    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "INPUT_HASH_MISMATCH"


def test_run_ingest_rejects_failed_input_artifact(
    ingest_run_root: Path,
) -> None:
    input_path = ingest_run_root / INPUT_RELATIVE_PATH
    artifact = json.loads(input_path.read_text(encoding="utf-8"))
    artifact["status"] = "failed"
    artifact["errors"] = [
        {
            "code": "SEMANTIC_FAILED",
            "message": "의미 분석 실패",
            "item_ref": None,
            "retryable": True,
        }
    ]
    artifact["data"] = None
    input_path.write_text(json.dumps(artifact), encoding="utf-8")

    response = entrypoint.run(
        "ingest",
        _input_paths(ingest_run_root),
        OUTPUT_RELATIVE_PATH,
        _context(ingest_run_root),
    )

    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "INPUT_STATUS_FAILED"


def test_run_ingest_rejects_missing_neo4j_configuration(
    ingest_run_root: Path,
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
        "ingest",
        _input_paths(ingest_run_root),
        OUTPUT_RELATIVE_PATH,
        _context(ingest_run_root),
    )

    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "CONFIG_INVALID"


def test_run_ingest_rejects_missing_run_root(tmp_path: Path) -> None:
    missing_root = tmp_path / "run_missing"
    context = {
        "run_id": "run_missing",
        "iteration": 0,
        "mode": "development",
        "run_root": missing_root,
    }

    response = entrypoint.run(
        "ingest",
        {
            "semantic_analysis": {
                "path": INPUT_RELATIVE_PATH,
                "sha256": "a" * 64,
            }
        },
        OUTPUT_RELATIVE_PATH,
        context,
    )

    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "PATH_INVALID"


def test_run_rejects_unsupported_operation() -> None:
    response = entrypoint.run("unknown", {}, ".", {})

    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "OPERATION_UNSUPPORTED"


def test_cli_serializes_control_response(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    expected = {
        "operation": "ingest",
        "status": "completed",
        "graph_id": "graph_cli_001",
        "graph_revision": 1,
        "is_ready": True,
        "errors": [],
    }
    monkeypatch.setattr(entrypoint, "run", lambda **kwargs: expected)

    exit_code = entrypoint.main(
        [
            "ingest",
            "--input-path",
            INPUT_RELATIVE_PATH,
            "--input-sha256",
            "a" * 64,
            "--output-dir",
            OUTPUT_RELATIVE_PATH,
            "--run-root",
            "/tmp/run_demo_001",
            "--run-id",
            "run_demo_001",
            "--iteration",
            "0",
            "--mode",
            "development",
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == expected


def _input_paths(run_root: Path) -> dict[str, dict[str, str]]:
    input_path = run_root / INPUT_RELATIVE_PATH
    return {
        "semantic_analysis": {
            "path": INPUT_RELATIVE_PATH,
            "sha256": calculate_sha256(input_path),
        }
    }


def _context(run_root: Path) -> dict[str, Any]:
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
