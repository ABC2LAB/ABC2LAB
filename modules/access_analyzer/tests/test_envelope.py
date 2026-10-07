"""공통 envelope 생성. 값 고정보다 계약 모양·시각 규칙을 본다."""

from datetime import datetime
from pathlib import Path

import pytest

from modules.access_analyzer.utils import envelope as env

ENVELOPE_KEYS = {
    "schema_version", "artifact_type", "artifact_id", "run_id", "iteration", "producer",
    "mode", "created_at", "status", "input_refs", "errors", "runtime_metrics", "data",
}


def _context() -> env.RunContext:
    return env.RunContext("run_demo_001", 0, env.Mode.DEVELOPMENT, Path("runs/run_demo_001"), "graph_demo_001", 1)


def test_build_envelope_shape() -> None:
    result = env.WorkResult(env.Status.COMPLETED, [], {"graph_id": "g"}, 5)
    document = env.build_envelope(env.ARTIFACT_TYPE_GRAPH_QUERY, _context(), result)
    assert set(document) == ENVELOPE_KEYS
    assert document["producer"] == "access_analyzer"
    assert document["artifact_type"] == "graph_query"
    assert document["input_refs"] == []
    assert document["runtime_metrics"] == {
        "duration_ms": 5, "llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "peak_memory_mb": None,
    }


def test_build_envelope_passes_input_refs() -> None:
    result = env.WorkResult(env.Status.COMPLETED, [], {"x": 1}, 5)
    refs = [{"artifact_id": "a", "artifact_type": "graph_query_result", "iteration": 0, "path": "p", "sha256": "0" * 64}]
    document = env.build_envelope(env.ARTIFACT_TYPE_CANDIDATES, _context(), result, input_refs=refs)
    assert document["input_refs"] == refs
    assert document["artifact_type"] == "vulnerability_candidates"


def test_artifact_id_names_type_and_run() -> None:
    artifact_id = env.make_artifact_id(env.ARTIFACT_TYPE_GRAPH_QUERY, _context())
    assert artifact_id.startswith("graph_query-run_demo_001-000-")


def test_format_utc_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError):
        env.format_utc(datetime(2026, 10, 7, 0, 0, 0))
