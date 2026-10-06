from pathlib import Path
from typing import Any

import pytest

from modules.knowledge_graph import entrypoint
from modules.knowledge_graph.exceptions import (
    GraphNotFoundError,
    GraphRevisionMismatchError,
    GraphUpdateConflictError,
    GraphUpdateReferenceError,
    RepositoryError,
    VerificationConflictError,
)
from modules.knowledge_graph.models import VerificationState, VerificationUpdate
from modules.knowledge_graph.utils.hashing import calculate_sha256


INPUT_PATH = "artifacts/iteration-000/verifier/verification_results.json"
OUTPUT_DIR = "artifacts/iteration-000/knowledge_graph"


class FakeVerificationRepository:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def __enter__(self) -> "FakeVerificationRepository":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def apply_verification(
        self,
        graph_id: str,
        run_id: str,
        update: VerificationUpdate,
    ) -> VerificationState:
        if self.error is not None:
            raise self.error
        return VerificationState(
            graph_id=graph_id,
            previous_graph_revision=1,
            graph_revision=2,
            applied_verification_ids=update.verification_ids,
            is_applied=True,
        )


def test_run_apply_verification_returns_control_response(
    verification_run_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_environment(monkeypatch)
    monkeypatch.setattr(
        entrypoint,
        "Neo4jGraphRepository",
        lambda settings: FakeVerificationRepository(),
    )

    response = entrypoint.run(
        "apply_verification",
        _input_paths(verification_run_root),
        OUTPUT_DIR,
        _context(verification_run_root),
    )

    assert response == {
        "operation": "apply_verification",
        "status": "completed",
        "graph_id": "graph_demo_001",
        "previous_graph_revision": 1,
        "graph_revision": 2,
        "applied_verification_ids": ["verification_001"],
        "is_applied": True,
        "errors": [],
    }


@pytest.mark.parametrize(
    ("error", "expected_code", "is_retryable"),
    [
        (GraphNotFoundError("missing"), "GRAPH_NOT_FOUND", False),
        (
            GraphRevisionMismatchError("stale"),
            "GRAPH_REVISION_MISMATCH",
            False,
        ),
        (VerificationConflictError("duplicate"), "VERIFICATION_CONFLICT", False),
        (GraphUpdateConflictError("conflict"), "GRAPH_UPDATE_CONFLICT", False),
        (
            GraphUpdateReferenceError("missing node"),
            "GRAPH_UPDATE_REFERENCE_INVALID",
            False,
        ),
        (RepositoryError("offline"), "NEO4J_UNAVAILABLE", True),
    ],
)
def test_run_apply_verification_maps_repository_errors(
    verification_run_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_code: str,
    is_retryable: bool,
) -> None:
    _configure_environment(monkeypatch)
    monkeypatch.setattr(
        entrypoint,
        "Neo4jGraphRepository",
        lambda settings: FakeVerificationRepository(error),
    )

    response = entrypoint.run(
        "apply_verification",
        _input_paths(verification_run_root),
        OUTPUT_DIR,
        _context(verification_run_root),
    )

    assert response["status"] == "failed"
    assert response["is_applied"] is False
    assert response["errors"][0]["code"] == expected_code
    assert response["errors"][0]["retryable"] is is_retryable


def test_run_apply_verification_rejects_hash_before_connection(
    verification_run_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_created(settings: object) -> None:
        raise AssertionError("repository must not be created")

    monkeypatch.setattr(entrypoint, "Neo4jGraphRepository", fail_if_created)
    input_paths = _input_paths(verification_run_root)
    input_paths["verification_results"]["sha256"] = "0" * 64

    response = entrypoint.run(
        "apply_verification",
        input_paths,
        OUTPUT_DIR,
        _context(verification_run_root),
    )

    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "INPUT_HASH_MISMATCH"


def test_run_apply_verification_rejects_missing_configuration(
    verification_run_root: Path,
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
        "apply_verification",
        _input_paths(verification_run_root),
        OUTPUT_DIR,
        _context(verification_run_root),
    )

    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "CONFIG_INVALID"


def test_apply_verification_cli_passes_graph_id(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "operation": "apply_verification",
            "status": "completed",
            "graph_id": "graph_demo_001",
            "previous_graph_revision": 1,
            "graph_revision": 2,
            "applied_verification_ids": ["verification_001"],
            "is_applied": True,
            "errors": [],
        }

    monkeypatch.setattr(entrypoint, "run", fake_run)

    exit_code = entrypoint.main(
        [
            "apply_verification",
            "--input-path",
            INPUT_PATH,
            "--input-sha256",
            "a" * 64,
            "--output-dir",
            OUTPUT_DIR,
            "--run-root",
            "/tmp/run_demo_001",
            "--run-id",
            "run_demo_001",
            "--iteration",
            "0",
            "--mode",
            "development",
            "--graph-id",
            "graph_demo_001",
        ]
    )

    assert exit_code == 0
    assert captured["context"]["graph_id"] == "graph_demo_001"
    assert "verification_results" in captured["input_paths"]
    assert capsys.readouterr().out


def _input_paths(run_root: Path) -> dict[str, dict[str, str]]:
    input_path = run_root / INPUT_PATH
    return {
        "verification_results": {
            "path": INPUT_PATH,
            "sha256": calculate_sha256(input_path),
        }
    }


def _context(run_root: Path) -> dict[str, Any]:
    return {
        "run_id": "run_demo_001",
        "iteration": 0,
        "mode": "development",
        "run_root": run_root,
        "graph_id": "graph_demo_001",
    }


def _configure_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "test-password")
    monkeypatch.setenv("NEO4J_DATABASE", "neo4j")
