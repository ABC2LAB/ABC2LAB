from pathlib import Path
from shutil import copytree
from typing import Any

import pytest

from modules.reporter.utils.hashing import calculate_sha256


@pytest.fixture
def reporter_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def schema_root(reporter_root: Path) -> Path:
    return reporter_root / "schemas"


@pytest.fixture
def fixture_root(reporter_root: Path) -> Path:
    return reporter_root / "tests" / "fixtures"


@pytest.fixture
def reporter_run_root(fixture_root: Path, tmp_path: Path) -> Path:
    run_root = tmp_path / "run_demo_001"
    copytree(fixture_root / "runs/run_demo_001", run_root)
    return run_root


@pytest.fixture
def reporter_project_root(fixture_root: Path, tmp_path: Path) -> Path:
    project_root = tmp_path / "project"
    copytree(fixture_root / "datasets", project_root / "datasets")
    return project_root


def artifact_descriptor(root: Path, relative_path: str) -> dict[str, str]:
    return {
        "path": relative_path,
        "sha256": calculate_sha256(root / relative_path),
    }


@pytest.fixture
def report_arguments(
    reporter_run_root: Path,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    prefix = "artifacts/iteration-000"
    paths = {
        "vulnerability_candidates": (
            f"{prefix}/access_analyzer/vulnerability_candidates.json"
        ),
        "test_scenarios": (
            f"{prefix}/scenario_generator/test_scenarios.json"
        ),
        "safety_decisions": f"{prefix}/safety_policy/safety_decisions.json",
        "verification_results": (
            f"{prefix}/verifier/verification_results.json"
        ),
    }
    input_paths = {
        name: artifact_descriptor(reporter_run_root, path)
        for name, path in paths.items()
    }
    context = {
        "run_id": "run_demo_001",
        "iteration": 0,
        "mode": "development",
        "run_root": reporter_run_root,
        "target_url": "http://127.0.0.1:8001/",
    }
    return input_paths, f"{prefix}/reporter", context


@pytest.fixture
def evaluate_arguments(
    report_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    reporter_project_root: Path,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    input_paths, output_dir, context = report_arguments
    prefix = "artifacts/iteration-000"
    evaluation_paths = {
        "crawl_result": f"{prefix}/collector/crawl_result.json",
        "semantic_analysis": (
            f"{prefix}/semantic_analyzer/semantic_analysis.json"
        ),
        "graph_query_result": (
            f"{prefix}/knowledge_graph/graph_query_result.json"
        ),
    }
    input_paths.update(
        {
            name: artifact_descriptor(context["run_root"], path)
            for name, path in evaluation_paths.items()
        }
    )
    ground_truth_path = "datasets/shop_demo/ground_truth.json"
    input_paths["ground_truth"] = artifact_descriptor(
        reporter_project_root,
        ground_truth_path,
    )
    context.update(
        {
            "project_root": reporter_project_root,
            "matching_profile": "default-v1",
        }
    )
    return input_paths, output_dir, context
