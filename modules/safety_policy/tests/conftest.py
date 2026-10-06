from pathlib import Path

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
