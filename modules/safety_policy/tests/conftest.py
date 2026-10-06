from pathlib import Path
from shutil import copyfile
from typing import Any

import pytest


@pytest.fixture
def fixture_root() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def completed_input_path(fixture_root: Path) -> Path:
    return fixture_root / (
        "runs/run_demo_001/artifacts/iteration-000/"
        "scenario_generator/test_scenarios.json"
    )


@pytest.fixture
def completed_output_path(fixture_root: Path) -> Path:
    return fixture_root / (
        "runs/run_demo_001/artifacts/iteration-000/"
        "safety_policy/safety_decisions.json"
    )


@pytest.fixture
def evaluate_run_root(tmp_path: Path, completed_input_path: Path) -> Path:
    run_root = tmp_path / "run_demo_001"
    input_path = run_root / (
        "artifacts/iteration-000/scenario_generator/test_scenarios.json"
    )
    input_path.parent.mkdir(parents=True)
    copyfile(completed_input_path, input_path)
    return run_root


@pytest.fixture
def evaluate_arguments(
    evaluate_run_root: Path,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    from modules.safety_policy.utils.hashing import calculate_sha256

    relative_path = (
        "artifacts/iteration-000/scenario_generator/test_scenarios.json"
    )
    input_path = evaluate_run_root / relative_path
    return (
        {
            "test_scenarios": {
                "path": relative_path,
                "sha256": calculate_sha256(input_path),
            }
        },
        "artifacts/iteration-000/safety_policy",
        {
            "run_id": "run_demo_001",
            "iteration": 0,
            "mode": "development",
            "run_root": evaluate_run_root,
        },
    )
