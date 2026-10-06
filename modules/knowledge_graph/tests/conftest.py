from pathlib import Path

import pytest


@pytest.fixture
def fixture_root() -> Path:
    return (
        Path(__file__).parent
        / "fixtures"
        / "runs"
        / "run_demo_001"
        / "artifacts"
        / "iteration-000"
    )
