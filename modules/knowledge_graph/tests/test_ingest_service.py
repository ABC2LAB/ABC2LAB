import json
from pathlib import Path
from typing import Any

import pytest

from modules.knowledge_graph.exceptions import (
    ContractValidationError,
    InputArtifactFailedError,
    InputHashMismatchError,
)
from modules.knowledge_graph.models import GraphSource, GraphState, IngestRequest
from modules.knowledge_graph.service import (
    execute_ingest,
    prepare_ingest_operation,
)
from modules.knowledge_graph.utils.hashing import calculate_sha256
from modules.knowledge_graph.utils.validation import load_json


INPUT_RELATIVE_PATH = (
    "artifacts/iteration-000/semantic_analyzer/semantic_analysis.json"
)


class FakeRepository:
    def __init__(self, state: GraphState) -> None:
        self.state = state
        self.calls: list[tuple[str, str, object, GraphSource]] = []

    def ingest(
        self,
        graph_id: str,
        run_id: str,
        graph: object,
        source: GraphSource,
    ) -> GraphState:
        self.calls.append((graph_id, run_id, graph, source))
        return self.state


def test_prepare_and_execute_completed_ingest(ingest_run_root: Path) -> None:
    input_path = ingest_run_root / INPUT_RELATIVE_PATH
    prepared = prepare_ingest_operation(_request(input_path))
    repository = FakeRepository(
        GraphState(
            graph_id="graph_demo_001",
            graph_revision=1,
            is_created=True,
        )
    )

    response = execute_ingest(
        prepared,
        repository,  # type: ignore[arg-type]
        graph_id_factory=lambda: "graph_generated_001",
    )

    assert response.to_mapping() == {
        "operation": "ingest",
        "status": "completed",
        "graph_id": "graph_demo_001",
        "graph_revision": 1,
        "is_ready": True,
        "errors": [],
    }
    assert repository.calls[0][0] == "graph_generated_001"
    assert repository.calls[0][1] == "run_demo_001"
    assert repository.calls[0][3].artifact_id == "semantic_demo_001"


def test_partial_input_remains_partial_and_query_ready(
    ingest_run_root: Path,
) -> None:
    input_path = ingest_run_root / INPUT_RELATIVE_PATH
    artifact = load_json(input_path)
    artifact["status"] = "partial"
    artifact["errors"] = [
        {
            "code": "SEMANTIC_ITEM_FAILED",
            "message": "일부 항목 분석 실패",
            "item_ref": "request_002",
            "retryable": False,
        }
    ]
    _write_json(input_path, artifact)
    prepared = prepare_ingest_operation(_request(input_path))
    repository = FakeRepository(
        GraphState("graph_partial", 1, True)
    )

    response = execute_ingest(prepared, repository)  # type: ignore[arg-type]

    assert response.status == "partial"
    assert response.is_ready is True
    assert response.errors[0].code == "SEMANTIC_ITEM_FAILED"


def test_prepare_ingest_rejects_failed_input(ingest_run_root: Path) -> None:
    input_path = ingest_run_root / INPUT_RELATIVE_PATH
    artifact = load_json(input_path)
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
    _write_json(input_path, artifact)

    with pytest.raises(InputArtifactFailedError):
        prepare_ingest_operation(_request(input_path))


def test_prepare_ingest_rejects_hash_mismatch(ingest_run_root: Path) -> None:
    input_path = ingest_run_root / INPUT_RELATIVE_PATH
    request = IngestRequest(
        input_path=input_path,
        expected_sha256="0" * 64,
        run_id="run_demo_001",
        iteration=0,
        mode="development",
    )

    with pytest.raises(InputHashMismatchError):
        prepare_ingest_operation(request)


def test_prepare_ingest_rejects_context_mismatch(ingest_run_root: Path) -> None:
    input_path = ingest_run_root / INPUT_RELATIVE_PATH
    request = IngestRequest(
        input_path=input_path,
        expected_sha256=calculate_sha256(input_path),
        run_id="different_run",
        iteration=0,
        mode="development",
    )

    with pytest.raises(ContractValidationError, match="run_id"):
        prepare_ingest_operation(request)


def _request(input_path: Path) -> IngestRequest:
    return IngestRequest(
        input_path=input_path,
        expected_sha256=calculate_sha256(input_path),
        run_id="run_demo_001",
        iteration=0,
        mode="development",
    )


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
