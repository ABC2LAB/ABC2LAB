import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError


SCHEMA_PATH_BY_ARTIFACT = {
    "crawl_result": "input/crawl_result.schema.json",
    "semantic_analysis": "input/semantic_analysis.schema.json",
    "graph_query_result": "input/graph_query_result.schema.json",
    "vulnerability_candidates": "input/vulnerability_candidates.schema.json",
    "test_scenarios": "input/test_scenarios.schema.json",
    "safety_decisions": "input/safety_decisions.schema.json",
    "verification_results": "input/verification_results.schema.json",
    "ground_truth": "input/ground_truth.schema.json",
    "diagnosis_report": "output/diagnosis_report.schema.json",
    "evaluation_results": "output/evaluation_results.schema.json",
}


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        value = json.load(file)

    assert isinstance(value, dict)
    return value


def validate_fixture(
    artifact: dict[str, Any],
    schema_root: Path,
) -> None:
    schema_path = schema_root / SCHEMA_PATH_BY_ARTIFACT[artifact["artifact_type"]]
    validator = Draft202012Validator(
        load_json(schema_path),
        format_checker=FormatChecker(),
    )
    validator.validate(artifact)


def test_all_contract_schemas_are_valid(schema_root: Path) -> None:
    schema_paths = sorted(schema_root.rglob("*.schema.json"))

    assert len(schema_paths) == 10
    for schema_path in schema_paths:
        Draft202012Validator.check_schema(load_json(schema_path))


def test_all_contract_fixtures_are_valid(
    fixture_root: Path,
    schema_root: Path,
) -> None:
    fixture_paths = sorted(fixture_root.rglob("*.json"))

    assert len(fixture_paths) == 15
    for fixture_path in fixture_paths:
        validate_fixture(load_json(fixture_path), schema_root)


@pytest.mark.parametrize("artifact_type", sorted(SCHEMA_PATH_BY_ARTIFACT))
def test_contracts_reject_undefined_top_level_key(
    artifact_type: str,
    fixture_root: Path,
    schema_root: Path,
) -> None:
    fixture_path_by_artifact = {
        "crawl_result": fixture_root / "runs/run_demo_001/artifacts/iteration-000/collector/crawl_result.json",
        "semantic_analysis": fixture_root / "runs/run_demo_001/artifacts/iteration-000/semantic_analyzer/semantic_analysis.json",
        "graph_query_result": fixture_root / "runs/run_demo_001/artifacts/iteration-000/knowledge_graph/graph_query_result.json",
        "vulnerability_candidates": fixture_root / "runs/run_demo_001/artifacts/iteration-000/access_analyzer/vulnerability_candidates.json",
        "test_scenarios": fixture_root / "runs/run_demo_001/artifacts/iteration-000/scenario_generator/test_scenarios.json",
        "safety_decisions": fixture_root / "runs/run_demo_001/artifacts/iteration-000/safety_policy/safety_decisions.json",
        "verification_results": fixture_root / "runs/run_demo_001/artifacts/iteration-000/verifier/verification_results.json",
        "ground_truth": fixture_root / "datasets/shop_demo/ground_truth.json",
        "diagnosis_report": fixture_root / "runs/run_demo_001/artifacts/iteration-000/reporter/diagnosis_report.json",
        "evaluation_results": fixture_root / "runs/run_demo_001/artifacts/iteration-000/reporter/evaluation_results.json",
    }
    artifact = load_json(fixture_path_by_artifact[artifact_type])
    artifact["unexpected"] = True

    with pytest.raises(ValidationError):
        validate_fixture(artifact, schema_root)


def test_output_contract_enforces_failed_state(
    fixture_root: Path,
    schema_root: Path,
) -> None:
    path = fixture_root / "runs/run_demo_001/artifacts/iteration-000/reporter/diagnosis_report.json"
    artifact = load_json(path)
    artifact["status"] = "failed"

    with pytest.raises(ValidationError):
        validate_fixture(artifact, schema_root)


def test_output_contract_enforces_partial_errors(
    fixture_root: Path,
    schema_root: Path,
) -> None:
    path = fixture_root / "status/evaluation_results.partial.json"
    artifact = load_json(path)
    artifact["errors"] = []

    with pytest.raises(ValidationError):
        validate_fixture(artifact, schema_root)


def test_fixture_preserves_candidate_without_scenario(fixture_root: Path) -> None:
    candidates_path = fixture_root / "runs/run_demo_001/artifacts/iteration-000/access_analyzer/vulnerability_candidates.json"
    scenarios_path = fixture_root / "runs/run_demo_001/artifacts/iteration-000/scenario_generator/test_scenarios.json"
    candidates = load_json(candidates_path)["data"]["candidates"]
    scenarios = load_json(scenarios_path)["data"]["scenarios"]
    candidate_ids = {candidate["candidate_id"] for candidate in candidates}
    planned_candidate_ids = {scenario["candidate_id"] for scenario in scenarios}

    assert candidate_ids - planned_candidate_ids == {"candidate_unplanned_001"}


def test_empty_candidates_fixture_is_valid(
    fixture_root: Path,
    schema_root: Path,
) -> None:
    artifact = load_json(fixture_root / "status/vulnerability_candidates.empty.json")

    validate_fixture(artifact, schema_root)
    assert artifact["data"]["candidates"] == []


def test_zero_denominator_metric_uses_null(fixture_root: Path) -> None:
    path = fixture_root / "runs/run_demo_001/artifacts/iteration-000/reporter/evaluation_results.json"
    metrics = load_json(path)["data"]["metrics"]
    zero_denominator_metrics = [
        metric for metric in metrics if metric["denominator"] == 0
    ]

    assert zero_denominator_metrics
    assert all(metric["value"] is None for metric in zero_denominator_metrics)


def test_metric_value_must_be_ratio(
    fixture_root: Path,
    schema_root: Path,
) -> None:
    path = fixture_root / "runs/run_demo_001/artifacts/iteration-000/reporter/evaluation_results.json"
    artifact = copy.deepcopy(load_json(path))
    artifact["data"]["metrics"][0]["value"] = 1.1

    with pytest.raises(ValidationError):
        validate_fixture(artifact, schema_root)


def test_finding_rejects_unknown_status(
    fixture_root: Path,
    schema_root: Path,
) -> None:
    path = fixture_root / "runs/run_demo_001/artifacts/iteration-000/reporter/diagnosis_report.json"
    artifact = copy.deepcopy(load_json(path))
    artifact["data"]["findings"][0]["status"] = "safe"

    with pytest.raises(ValidationError):
        validate_fixture(artifact, schema_root)
        
def test_graph_query_access_row_requires_request_ids(
    fixture_root: Path,
    schema_root: Path,
) -> None:
    path = (
        fixture_root
        / "runs/run_demo_001/artifacts/iteration-000/"
        "knowledge_graph/graph_query_result.json"
    )
    artifact = copy.deepcopy(load_json(path))
    access_result = next(
        item
        for item in artifact["data"]["results"]
        if item["query_key"] == "role_resource_access"
    )
    access_result["rows"][0].pop("request_ids")

    with pytest.raises(ValidationError):
        validate_fixture(artifact, schema_root)


@pytest.mark.parametrize(
    "request_ids",
    [
        [],
        [""],
        ["request_read_order", "request_read_order"],
    ],
)
def test_graph_query_access_row_rejects_invalid_request_ids(
    request_ids: list[str],
    fixture_root: Path,
    schema_root: Path,
) -> None:
    path = (
        fixture_root
        / "runs/run_demo_001/artifacts/iteration-000/"
        "knowledge_graph/graph_query_result.json"
    )
    artifact = copy.deepcopy(load_json(path))
    access_result = next(
        item
        for item in artifact["data"]["results"]
        if item["query_key"] == "role_resource_access"
    )
    access_result["rows"][0]["request_ids"] = request_ids

    with pytest.raises(ValidationError):
        validate_fixture(artifact, schema_root)
        
def test_crawl_page_requires_account_id(
    fixture_root: Path,
    schema_root: Path,
) -> None:
    path = (
        fixture_root
        / "runs/run_demo_001/artifacts/iteration-000/"
        "collector/crawl_result.json"
    )
    artifact = copy.deepcopy(load_json(path))
    artifact["data"]["pages"][0].pop("account_id")

    with pytest.raises(ValidationError):
        validate_fixture(artifact, schema_root)
