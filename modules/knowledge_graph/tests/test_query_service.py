import json
from pathlib import Path
from typing import Any

import pytest

from modules.knowledge_graph.exceptions import (
    ContractValidationError,
    InputHashMismatchError,
    OutputArtifactExistsError,
    RepositoryError,
)
from modules.knowledge_graph.models import GraphSource, GraphState, QueryRequest, SemanticGraph
from modules.knowledge_graph.service import (
    PreparedQuery,
    execute_query,
    prepare_query_operation,
    publish_query_artifact,
)
from modules.knowledge_graph.utils.hashing import calculate_sha256
from modules.knowledge_graph.utils.validation import load_json


INPUT_PATH = "artifacts/iteration-000/access_analyzer/graph_query.json"
OUTPUT_PATH = "artifacts/iteration-000/knowledge_graph/graph_query_result.json"


class FakeQueryRepository:
    def __init__(
        self,
        revisions: list[int | None],
        rows_by_key: dict[str, list[dict[str, Any]]] | None = None,
        failing_keys: set[str] | None = None,
    ) -> None:
        self.revisions = revisions
        self.rows_by_key = rows_by_key or {}
        self.failing_keys = failing_keys or set()
        self.query_keys: list[str] = []

    def get_revision(self, graph_id: str, run_id: str) -> int | None:
        return self.revisions.pop(0)

    def query(
        self,
        graph_id: str,
        run_id: str,
        query_key: str,
        parameters: dict[str, Any],
    ) -> list[dict[str, Any]]:
        self.query_keys.append(query_key)
        if query_key in self.failing_keys:
            raise RepositoryError("query failed")
        return self.rows_by_key.get(query_key, [])

    def ingest(
        self,
        graph_id: str,
        run_id: str,
        graph: SemanticGraph,
        source: GraphSource,
    ) -> GraphState:
        raise NotImplementedError


def test_execute_and_publish_completed_query(query_run_root: Path) -> None:
    prepared = _prepare(query_run_root)
    repository = FakeQueryRepository(
        revisions=[1, 1],
        rows_by_key={
            "resource_ownership": [
                {
                    "resource_id": "resource_order_001",
                    "owner_account_id": "account_user",
                    "basis": "observed",
                    "evidence_refs": [],
                }
            ],
            "role_resource_access": [
                {
                    "account_id": "account_user",
                    "role_id": "role_user",
                    "endpoint_id": "endpoint_orders",
                    "resource_id": "resource_order_001",
                    "action": "read_order",
                    "access_observed": True,
                    "evidence_refs": [],
                }
            ],
            "workflow_dependencies": [],
            "structure_snapshot": [
                {"nodes": [], "relationships": [], "workflows": []}
            ],
        },
    )

    artifact = execute_query(
        prepared,
        repository,  # type: ignore[arg-type]
        artifact_id_factory=lambda: "graph_query_result_test",
        created_at_factory=lambda: "2026-10-06T01:00:00Z",
    )
    response = publish_query_artifact(prepared, artifact)

    assert artifact["status"] == "completed"
    assert artifact["data"]["graph_revision"] == 1
    assert len(artifact["data"]["results"]) == 4
    assert response.output_path == OUTPUT_PATH
    assert response.graph_revision == 1
    assert len(response.sha256 or "") == 64
    assert prepared.request.output_path.is_file()


def test_execute_query_marks_one_failed_query_as_partial(
    query_run_root: Path,
) -> None:
    prepared = _prepare(query_run_root)
    repository = FakeQueryRepository(
        revisions=[1, 1],
        failing_keys={"role_resource_access"},
        rows_by_key={
            "structure_snapshot": [
                {"nodes": [], "relationships": [], "workflows": []}
            ]
        },
    )

    artifact = execute_query(prepared, repository)  # type: ignore[arg-type]

    assert artifact["status"] == "partial"
    assert artifact["errors"][0]["item_ref"] == "query_access"
    failed = next(
        item for item in artifact["data"]["results"] if item["status"] == "failed"
    )
    assert failed["rows"] == []
    assert failed["errors"][0]["code"] == "QUERY_EXECUTION_FAILED"


def test_execute_query_fails_when_every_query_fails(query_run_root: Path) -> None:
    prepared = _prepare(query_run_root)
    repository = FakeQueryRepository(
        revisions=[1, 1],
        failing_keys={item.query_key for item in prepared.queries},
    )

    artifact = execute_query(prepared, repository)  # type: ignore[arg-type]

    assert artifact["status"] == "failed"
    assert artifact["data"] is None
    assert len(artifact["errors"]) == len(prepared.queries)


def test_execute_query_rejects_revision_mismatch(query_run_root: Path) -> None:
    prepared = _prepare(query_run_root)
    repository = FakeQueryRepository(revisions=[2])

    artifact = execute_query(prepared, repository)  # type: ignore[arg-type]

    assert artifact["status"] == "failed"
    assert artifact["errors"][0]["code"] == "GRAPH_REVISION_MISMATCH"
    assert repository.query_keys == []


def test_execute_query_rejects_revision_change(query_run_root: Path) -> None:
    prepared = _prepare(query_run_root)
    repository = FakeQueryRepository(revisions=[1, 2])

    artifact = execute_query(prepared, repository)  # type: ignore[arg-type]

    assert artifact["status"] == "failed"
    assert artifact["errors"][-1]["code"] == "GRAPH_REVISION_CHANGED"


def test_partial_graph_query_status_is_preserved(query_run_root: Path) -> None:
    input_path = query_run_root / INPUT_PATH
    artifact = load_json(input_path)
    artifact["status"] = "partial"
    artifact["errors"] = [
        {
            "code": "QUERY_PLAN_PARTIAL",
            "message": "일부 질의 계획 누락",
            "item_ref": None,
            "retryable": False,
        }
    ]
    input_path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
    prepared = _prepare(query_run_root)
    repository = FakeQueryRepository(revisions=[1, 1])

    output = execute_query(prepared, repository)  # type: ignore[arg-type]

    assert output["status"] == "partial"
    assert output["errors"][0]["code"] == "QUERY_PLAN_PARTIAL"


def test_prepare_query_rejects_hash_mismatch(query_run_root: Path) -> None:
    request = _request(query_run_root)
    invalid_request = QueryRequest(
        input_path=request.input_path,
        input_relative_path=request.input_relative_path,
        expected_sha256="0" * 64,
        output_path=request.output_path,
        output_relative_path=request.output_relative_path,
        run_id=request.run_id,
        iteration=request.iteration,
        mode=request.mode,
    )

    with pytest.raises(InputHashMismatchError):
        prepare_query_operation(invalid_request)


def test_publish_query_never_overwrites_existing_output(
    query_run_root: Path,
) -> None:
    prepared = _prepare(query_run_root)
    repository = FakeQueryRepository(revisions=[1, 1])
    artifact = execute_query(prepared, repository)  # type: ignore[arg-type]
    prepared.request.output_path.parent.mkdir(parents=True)
    prepared.request.output_path.write_text("existing", encoding="utf-8")

    with pytest.raises(OutputArtifactExistsError):
        publish_query_artifact(prepared, artifact)


def test_publish_query_rejects_result_id_mismatch(query_run_root: Path) -> None:
    prepared = _prepare(query_run_root)
    repository = FakeQueryRepository(revisions=[1, 1])
    artifact = execute_query(prepared, repository)  # type: ignore[arg-type]
    artifact["data"]["results"][0]["query_id"] = "different_query"

    with pytest.raises(ContractValidationError, match="query_id"):
        publish_query_artifact(prepared, artifact)


def _prepare(run_root: Path) -> PreparedQuery:
    return prepare_query_operation(_request(run_root))


def _request(run_root: Path) -> QueryRequest:
    input_path = run_root / INPUT_PATH
    return QueryRequest(
        input_path=input_path,
        input_relative_path=INPUT_PATH,
        expected_sha256=calculate_sha256(input_path),
        output_path=run_root / OUTPUT_PATH,
        output_relative_path=OUTPUT_PATH,
        run_id="run_demo_001",
        iteration=0,
        mode="development",
    )
