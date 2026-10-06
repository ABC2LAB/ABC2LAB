from pathlib import Path

import pytest

from modules.knowledge_graph.exceptions import ContractValidationError
from modules.knowledge_graph.query_adapter import parse_query_request
from modules.knowledge_graph.utils.hashing import calculate_sha256


INPUT_PATH = "artifacts/iteration-000/access_analyzer/graph_query.json"
OUTPUT_DIR = "artifacts/iteration-000/knowledge_graph"


def test_parse_query_request_resolves_contract_paths(query_run_root: Path) -> None:
    source_path = query_run_root / INPUT_PATH

    request = parse_query_request(
        _input_paths(calculate_sha256(source_path)),
        OUTPUT_DIR,
        _context(query_run_root),
    )

    assert request.input_path == source_path.resolve()
    assert request.output_path == (
        query_run_root / OUTPUT_DIR / "graph_query_result.json"
    ).resolve()
    assert request.output_relative_path.endswith("graph_query_result.json")


@pytest.mark.parametrize(
    "input_paths",
    [
        {},
        {"graph_query": {"path": INPUT_PATH, "sha256": "invalid"}},
        {
            "graph_query": {
                "path": "../graph_query.json",
                "sha256": "a" * 64,
            }
        },
        {
            "graph_query": {
                "path": INPUT_PATH,
                "sha256": "a" * 64,
                "extra": True,
            }
        },
    ],
)
def test_parse_query_request_rejects_invalid_inputs(
    query_run_root: Path,
    input_paths: dict[str, object],
) -> None:
    with pytest.raises(ContractValidationError):
        parse_query_request(input_paths, OUTPUT_DIR, _context(query_run_root))


def test_parse_query_request_rejects_wrong_output_dir(
    query_run_root: Path,
) -> None:
    source_path = query_run_root / INPUT_PATH

    with pytest.raises(ContractValidationError, match="output_dir"):
        parse_query_request(
            _input_paths(calculate_sha256(source_path)),
            "artifacts/iteration-000/access_analyzer",
            _context(query_run_root),
        )


def _input_paths(sha256: str) -> dict[str, object]:
    return {"graph_query": {"path": INPUT_PATH, "sha256": sha256}}


def _context(run_root: Path) -> dict[str, object]:
    return {
        "run_id": "run_demo_001",
        "iteration": 0,
        "mode": "development",
        "run_root": run_root,
    }
