from pathlib import Path

import pytest

from modules.knowledge_graph.exceptions import ContractValidationError
from modules.knowledge_graph.utils.hashing import calculate_sha256
from modules.knowledge_graph.verification_adapter import parse_verification_request


INPUT_PATH = "artifacts/iteration-000/verifier/verification_results.json"
OUTPUT_DIR = "artifacts/iteration-000/knowledge_graph"


def test_parse_verification_request_resolves_contract_values(
    verification_run_root: Path,
) -> None:
    request = parse_verification_request(
        _input_paths(verification_run_root),
        OUTPUT_DIR,
        _context(verification_run_root),
    )

    assert request.input_path == (verification_run_root / INPUT_PATH).resolve()
    assert request.graph_id == "graph_demo_001"
    assert request.run_id == "run_demo_001"


@pytest.mark.parametrize(
    "context_change",
    [
        {"graph_id": ""},
        {"extra": True},
        {"iteration": -1},
    ],
)
def test_parse_verification_request_rejects_invalid_context(
    verification_run_root: Path,
    context_change: dict[str, object],
) -> None:
    context = _context(verification_run_root)
    context.update(context_change)

    with pytest.raises(ContractValidationError):
        parse_verification_request(
            _input_paths(verification_run_root),
            OUTPUT_DIR,
            context,
        )


def test_parse_verification_request_rejects_wrong_input_path(
    verification_run_root: Path,
) -> None:
    input_paths = _input_paths(verification_run_root)
    input_paths["verification_results"]["path"] = "../verification_results.json"

    with pytest.raises(ContractValidationError):
        parse_verification_request(
            input_paths,
            OUTPUT_DIR,
            _context(verification_run_root),
        )


def test_parse_verification_request_rejects_wrong_output_directory(
    verification_run_root: Path,
) -> None:
    with pytest.raises(ContractValidationError, match="output_dir"):
        parse_verification_request(
            _input_paths(verification_run_root),
            "artifacts/iteration-000/verifier",
            _context(verification_run_root),
        )


def _input_paths(run_root: Path) -> dict[str, dict[str, str]]:
    input_path = run_root / INPUT_PATH
    return {
        "verification_results": {
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
        "graph_id": "graph_demo_001",
    }
