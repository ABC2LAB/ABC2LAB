from pathlib import Path

import pytest


@pytest.fixture
def reporter_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def schema_root(reporter_root: Path) -> Path:
    return reporter_root / "schemas"


@pytest.fixture
def fixture_root(reporter_root: Path) -> Path:
    return reporter_root / "tests" / "fixtures"
