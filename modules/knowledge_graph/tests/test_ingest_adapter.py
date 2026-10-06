from pathlib import Path

import pytest

from modules.knowledge_graph.exceptions import ContractValidationError
from modules.knowledge_graph.ingest_adapter import parse_ingest_request
from modules.knowledge_graph.utils.hashing import calculate_sha256


INPUT_RELATIVE_PATH = (
    "artifacts/iteration-000/semantic_analyzer/semantic_analysis.json"
)
OUTPUT_RELATIVE_PATH = "artifacts/iteration-000/knowledge_graph"


def test_parse_ingest_request_resolves_trusted_input(
    ingest_run_root: Path,
) -> None:
    input_path = ingest_run_root / INPUT_RELATIVE_PATH

    request = parse_ingest_request(
        _input_paths(calculate_sha256(input_path)),
        OUTPUT_RELATIVE_PATH,
        _context(ingest_run_root),
    )

    assert request.input_path == input_path.resolve()
    assert request.run_id == "run_demo_001"
    assert request.iteration == 0
    assert request.mode == "development"


@pytest.mark.parametrize(
    ("input_paths", "output_dir", "context"),
    [
        ({}, OUTPUT_RELATIVE_PATH, None),
        (
            {
                "semantic_analysis": {
                    "path": INPUT_RELATIVE_PATH,
                    "sha256": "invalid",
                }
            },
            OUTPUT_RELATIVE_PATH,
            None,
        ),
        (
            {
                "semantic_analysis": {
                    "path": "../semantic_analysis.json",
                    "sha256": "a" * 64,
                }
            },
            OUTPUT_RELATIVE_PATH,
            None,
        ),
    ],
)
def test_parse_ingest_request_rejects_invalid_public_arguments(
    ingest_run_root: Path,
    input_paths: dict[str, object],
    output_dir: str,
    context: dict[str, object] | None,
) -> None:
    actual_context = _context(ingest_run_root) if context is None else context

    with pytest.raises(ContractValidationError):
        parse_ingest_request(input_paths, output_dir, actual_context)


def test_parse_ingest_request_rejects_wrong_output_directory(
    ingest_run_root: Path,
) -> None:
    input_path = ingest_run_root / INPUT_RELATIVE_PATH

    with pytest.raises(ContractValidationError, match="output_dir"):
        parse_ingest_request(
            _input_paths(calculate_sha256(input_path)),
            "artifacts/iteration-000/semantic_analyzer",
            _context(ingest_run_root),
        )


def _input_paths(sha256: str) -> dict[str, object]:
    return {
        "semantic_analysis": {
            "path": INPUT_RELATIVE_PATH,
            "sha256": sha256,
        }
    }


def _context(run_root: Path) -> dict[str, object]:
    return {
        "run_id": "run_demo_001",
        "iteration": 0,
        "mode": "development",
        "run_root": run_root,
    }
