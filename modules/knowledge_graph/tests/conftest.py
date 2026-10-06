from pathlib import Path
from shutil import copyfile

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


@pytest.fixture
def ingest_run_root(fixture_root: Path, tmp_path: Path) -> Path:
    run_root = tmp_path / "run_demo_001"
    input_path = (
        run_root
        / "artifacts"
        / "iteration-000"
        / "semantic_analyzer"
        / "semantic_analysis.json"
    )
    input_path.parent.mkdir(parents=True)
    copyfile(
        fixture_root / "semantic_analyzer" / "semantic_analysis.json",
        input_path,
    )
    return run_root


@pytest.fixture
def query_run_root(fixture_root: Path, tmp_path: Path) -> Path:
    run_root = tmp_path / "run_demo_001"
    input_path = (
        run_root
        / "artifacts"
        / "iteration-000"
        / "access_analyzer"
        / "graph_query.json"
    )
    input_path.parent.mkdir(parents=True)
    copyfile(
        fixture_root / "access_analyzer" / "graph_query.json",
        input_path,
    )
    return run_root


@pytest.fixture
def verification_run_root(fixture_root: Path, tmp_path: Path) -> Path:
    run_root = tmp_path / "run_demo_001"
    input_path = (
        run_root
        / "artifacts"
        / "iteration-000"
        / "verifier"
        / "verification_results.json"
    )
    input_path.parent.mkdir(parents=True, exist_ok=True)
    copyfile(
        fixture_root / "verifier" / "verification_results.json",
        input_path,
    )
    return run_root
