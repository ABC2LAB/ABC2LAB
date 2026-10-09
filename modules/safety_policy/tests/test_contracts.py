import copy
import json
from pathlib import Path
from typing import Any, Callable

import pytest
from jsonschema import Draft202012Validator

from modules.safety_policy.approval_adapter import APPROVAL_RECORD_SCHEMA
from modules.safety_policy.contracts import (
    SAFETY_DECISIONS_SCHEMA,
    SCHEMA_DIRECTORY,
    TEST_SCENARIOS_SCHEMA,
    load_safety_decisions,
    load_test_scenarios,
)
from modules.safety_policy.config_adapter import POLICY_CONFIG_SCHEMA
from modules.safety_policy.exceptions import ContractValidationError
from modules.safety_policy.utils.validation import (
    load_json,
    validate_safety_decisions_against_scenarios,
    validate_safety_decisions_semantics,
    validate_schema,
    validate_test_scenarios_semantics,
)

ContractLoader = Callable[[Path], tuple[dict[str, Any], object | None]]


def test_all_schemas_are_valid() -> None:
    schema_paths = sorted(SCHEMA_DIRECTORY.rglob("*.schema.json"))

    assert len(schema_paths) == 4
    for schema_path in schema_paths:
        Draft202012Validator.check_schema(load_json(schema_path))

    assert POLICY_CONFIG_SCHEMA in schema_paths
    assert APPROVAL_RECORD_SCHEMA in schema_paths
    for schema_path in (POLICY_CONFIG_SCHEMA, APPROVAL_RECORD_SCHEMA):
        schema = load_json(schema_path)
        assert schema["properties"]["schema_version"]["const"] == "0.1.0"


def test_completed_contract_fixtures_are_valid(
    completed_input_path: Path,
    completed_output_path: Path,
) -> None:
    scenarios_artifact, scenarios = load_test_scenarios(completed_input_path)
    decisions_artifact, decisions = load_safety_decisions(completed_output_path)

    validate_safety_decisions_against_scenarios(
        decisions_artifact,
        scenarios_artifact,
        completed_input_path,
    )

    assert scenarios is not None
    assert decisions is not None
    assert scenarios_artifact["schema_version"] == "0.2.0"
    assert decisions_artifact["schema_version"] == "0.2.0"
    assert len(scenarios.scenarios) == 2
    assert len(decisions.decisions) == 2


@pytest.mark.parametrize(
    ("filename", "loader", "expected_status"),
    [
        ("test_scenarios.partial.json", load_test_scenarios, "partial"),
        ("test_scenarios.failed.json", load_test_scenarios, "failed"),
        ("safety_decisions.partial.json", load_safety_decisions, "partial"),
        ("safety_decisions.failed.json", load_safety_decisions, "failed"),
    ],
)
def test_partial_and_failed_contract_fixtures_are_valid(
    fixture_root: Path,
    filename: str,
    loader: ContractLoader,
    expected_status: str,
) -> None:
    artifact, model = loader(fixture_root / "status" / filename)

    assert artifact["schema_version"] == "0.2.0"
    assert artifact["status"] == expected_status
    assert (model is None) is (expected_status == "failed")


@pytest.mark.parametrize("schema_version", ["0.1.0", "0.3.0"])
@pytest.mark.parametrize("filename", ["test_scenarios.json", "safety_decisions.json"])
def test_contract_loaders_reject_unsupported_versions(
    fixture_root: Path,
    tmp_path: Path,
    schema_version: str,
    filename: str,
) -> None:
    producer = (
        "scenario_generator"
        if filename == "test_scenarios.json"
        else "safety_policy"
    )
    source_path = (
        fixture_root
        / "runs/run_demo_001/artifacts/iteration-000"
        / producer
        / filename
    )
    artifact = load_json(source_path)
    artifact["schema_version"] = schema_version
    input_path = tmp_path / filename
    input_path.write_text(json.dumps(artifact), encoding="utf-8")
    loader = (
        load_test_scenarios
        if filename == "test_scenarios.json"
        else load_safety_decisions
    )

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        loader(input_path)


def test_partial_fixture_preserves_scenario_hash(fixture_root: Path) -> None:
    scenarios_path = fixture_root / "status/test_scenarios.partial.json"
    scenarios_artifact, _ = load_test_scenarios(scenarios_path)
    decisions_artifact, _ = load_safety_decisions(
        fixture_root / "status/safety_decisions.partial.json"
    )

    validate_safety_decisions_against_scenarios(
        decisions_artifact,
        scenarios_artifact,
        scenarios_path,
    )


def test_schema_rejects_undefined_key(completed_input_path: Path) -> None:
    artifact = load_json(completed_input_path)
    artifact["unexpected"] = True

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        validate_schema(artifact, TEST_SCENARIOS_SCHEMA)


@pytest.mark.parametrize(
    "resource_ids",
    [
        ["opaque-node"],
        ["opaque-z", "opaque-a"],
        ["opaque-node", "opaque-node"],
    ],
)
def test_scenarios_accept_opaque_resource_ids_without_rewriting(
    completed_input_path: Path,
    tmp_path: Path,
    resource_ids: list[str],
) -> None:
    artifact = load_json(completed_input_path)
    for scenario in artifact["data"]["scenarios"]:
        scenario["resource_ids"] = resource_ids.copy()
    input_path = tmp_path / "test_scenarios.json"
    input_path.write_text(json.dumps(artifact), encoding="utf-8")

    loaded_artifact, scenarios = load_test_scenarios(input_path)

    assert scenarios is not None
    assert loaded_artifact["schema_version"] == "0.2.0"
    assert all(
        scenario["resource_ids"] == resource_ids
        for scenario in loaded_artifact["data"]["scenarios"]
    )
    assert load_json(input_path) == artifact


def test_scenario_schema_requires_resource_ids(completed_input_path: Path) -> None:
    artifact = load_json(completed_input_path)
    for scenario in artifact["data"]["scenarios"]:
        scenario.pop("resource_ids")

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        validate_schema(artifact, TEST_SCENARIOS_SCHEMA)


@pytest.mark.parametrize(
    "resource_ids",
    [None, [], "opaque-node", [""], [None], [7], [True], ["opaque-node", ""]],
)
def test_scenario_schema_rejects_invalid_resource_ids(
    completed_input_path: Path,
    resource_ids: object,
) -> None:
    artifact = load_json(completed_input_path)
    for scenario in artifact["data"]["scenarios"]:
        scenario["resource_ids"] = resource_ids

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        validate_schema(artifact, TEST_SCENARIOS_SCHEMA)


def test_scenario_schema_still_rejects_undefined_keys(
    completed_input_path: Path,
) -> None:
    artifact = load_json(completed_input_path)
    for scenario in artifact["data"]["scenarios"]:
        scenario["unexpected"] = True

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        validate_schema(artifact, TEST_SCENARIOS_SCHEMA)


def test_schema_enforces_failed_state_contract(completed_output_path: Path) -> None:
    artifact = load_json(completed_output_path)
    artifact["status"] = "failed"

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        validate_schema(artifact, SAFETY_DECISIONS_SCHEMA)


def test_scenarios_reject_duplicate_scenario_id(completed_input_path: Path) -> None:
    artifact = load_json(completed_input_path)
    artifact["data"]["scenarios"].append(
        copy.deepcopy(artifact["data"]["scenarios"][0])
    )

    with pytest.raises(ContractValidationError, match="중복 scenario_id"):
        validate_test_scenarios_semantics(artifact)


def test_scenarios_reject_non_contiguous_step_order(
    completed_input_path: Path,
) -> None:
    artifact = load_json(completed_input_path)
    artifact["data"]["scenarios"][0]["steps"][1]["order"] = 2

    with pytest.raises(ContractValidationError, match="0부터 연속"):
        validate_test_scenarios_semantics(artifact)


def test_scenarios_reject_duplicate_check_id(completed_input_path: Path) -> None:
    artifact = load_json(completed_input_path)
    scenario = artifact["data"]["scenarios"][0]
    scenario["assertions"][0]["check_id"] = scenario["preconditions"][0]["check_id"]

    with pytest.raises(ContractValidationError, match="중복 check_id"):
        validate_test_scenarios_semantics(artifact)


def test_binding_rejects_missing_source_step(completed_input_path: Path) -> None:
    artifact = load_json(completed_input_path)
    binding = artifact["data"]["scenarios"][0]["steps"][1]["bindings"][0]
    binding["source_step_id"] = "missing_step"

    with pytest.raises(ContractValidationError, match="source_step_id"):
        validate_test_scenarios_semantics(artifact)


def test_binding_rejects_current_or_future_step(completed_input_path: Path) -> None:
    artifact = load_json(completed_input_path)
    scenario = artifact["data"]["scenarios"][0]
    scenario["steps"][1]["bindings"][0]["source_step_id"] = "step_read_order_001"

    with pytest.raises(ContractValidationError, match="앞선 단계"):
        validate_test_scenarios_semantics(artifact)


def test_parameter_rejects_unknown_binding(completed_input_path: Path) -> None:
    artifact = load_json(completed_input_path)
    parameter = artifact["data"]["scenarios"][0]["steps"][1]["request"][
        "parameters"
    ][0]
    parameter["binding_ref"] = "missing_binding"

    with pytest.raises(ContractValidationError, match="현재 step bindings"):
        validate_test_scenarios_semantics(artifact)


def test_parameter_rejects_literal_with_binding(completed_input_path: Path) -> None:
    artifact = load_json(completed_input_path)
    parameter = artifact["data"]["scenarios"][0]["steps"][1]["request"][
        "parameters"
    ][0]
    parameter["value"] = "literal"

    with pytest.raises(ContractValidationError, match="value는 null"):
        validate_test_scenarios_semantics(artifact)


def test_url_template_rejects_unknown_binding(completed_input_path: Path) -> None:
    artifact = load_json(completed_input_path)
    request = artifact["data"]["scenarios"][0]["steps"][1]["request"]
    request["url_template"] += "/{missing_binding}"

    with pytest.raises(ContractValidationError, match="없는 binding_id"):
        validate_test_scenarios_semantics(artifact)


def test_body_binding_requires_json_pointer(completed_input_path: Path) -> None:
    artifact = load_json(completed_input_path)
    binding = artifact["data"]["scenarios"][0]["steps"][1]["bindings"][0]
    binding["selector"] = "orders.0.id"

    with pytest.raises(ContractValidationError, match="JSON Pointer"):
        validate_test_scenarios_semantics(artifact)


def test_allow_requires_all_assessments_to_pass(completed_output_path: Path) -> None:
    artifact = load_json(completed_output_path)
    decision = artifact["data"]["decisions"][0]
    decision["assessment"]["data_impact"]["status"] = "unknown"

    with pytest.raises(ContractValidationError, match="모두 pass"):
        validate_safety_decisions_semantics(artifact)


def test_block_assessment_requires_block_decision(completed_output_path: Path) -> None:
    artifact = load_json(completed_output_path)
    decision = artifact["data"]["decisions"][1]
    decision["assessment"]["target_scope"]["status"] = "block"

    with pytest.raises(ContractValidationError, match="최종 판정도 block"):
        validate_safety_decisions_semantics(artifact)


def test_non_allow_decision_rejects_approval_ref(completed_output_path: Path) -> None:
    artifact = load_json(completed_output_path)
    artifact["data"]["decisions"][1]["approval_ref"] = "approval_001"

    with pytest.raises(ContractValidationError, match="approval_ref는 null"):
        validate_safety_decisions_semantics(artifact)


def test_decisions_reject_duplicate_decision_id(completed_output_path: Path) -> None:
    artifact = load_json(completed_output_path)
    decisions = artifact["data"]["decisions"]
    decisions[1]["decision_id"] = decisions[0]["decision_id"]

    with pytest.raises(ContractValidationError, match="중복 decision_id"):
        validate_safety_decisions_semantics(artifact)


def test_decisions_require_exact_scenario_coverage(
    completed_input_path: Path,
    completed_output_path: Path,
) -> None:
    scenarios = load_json(completed_input_path)
    decisions = load_json(completed_output_path)
    decisions["data"]["decisions"].pop()

    with pytest.raises(ContractValidationError, match="모든 scenario_id"):
        validate_safety_decisions_against_scenarios(
            decisions,
            scenarios,
            completed_input_path,
        )


def test_decisions_require_exact_scenario_hash(
    completed_input_path: Path,
    completed_output_path: Path,
) -> None:
    scenarios = load_json(completed_input_path)
    decisions = load_json(completed_output_path)
    decisions["data"]["scenarios_sha256"] = "f" * 64

    with pytest.raises(ContractValidationError, match="실제 해시"):
        validate_safety_decisions_against_scenarios(
            decisions,
            scenarios,
            completed_input_path,
        )
