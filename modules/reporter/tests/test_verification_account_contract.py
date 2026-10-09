"""Account-source input migration without changing graph snapshot contracts."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from modules.reporter import entrypoint
from modules.reporter.contracts import INPUT_SCHEMA_BY_NAME
from modules.reporter.exceptions import ContractValidationError
from modules.reporter.utils.hashing import calculate_sha256
from modules.reporter.utils.validation import load_json, validate_schema


Arguments = tuple[dict[str, Any], str, dict[str, Any]]
VERIFICATION_PATH = "artifacts/iteration-000/verifier/verification_results.json"


def test_fixture_uses_original_account_source_with_version_0_2(fixture_root: Path) -> None:
    artifact = _verification_fixture(fixture_root)
    relationship = _relationship(artifact)
    semantic = load_json(
        fixture_root / "runs/run_demo_001/artifacts/iteration-000"
        / "semantic_analyzer/semantic_analysis.json"
    )
    user = next(
        node for node in semantic["data"]["nodes"]
        if node["node_type"] == "User"
        and node["properties"]["account_id"] == relationship["source_account_id"]
    )

    assert artifact["schema_version"] == "0.2.0"
    assert relationship["source_account_id"] != user["node_id"]
    assert "source_id" not in relationship
    validate_schema(artifact, INPUT_SCHEMA_BY_NAME["verification_results"])


@pytest.mark.parametrize("field_case", ["legacy", "both", "missing"])
def test_schema_rejects_old_or_missing_source_field(
    fixture_root: Path,
    field_case: str,
) -> None:
    artifact = _verification_fixture(fixture_root)
    _change_source_field(artifact, field_case)

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        validate_schema(artifact, INPUT_SCHEMA_BY_NAME["verification_results"])


@pytest.mark.parametrize("account_id", ["", None, 12, False, {}, []])
def test_schema_rejects_invalid_account_id(fixture_root: Path, account_id: Any) -> None:
    artifact = _verification_fixture(fixture_root)
    _relationship(artifact)["source_account_id"] = account_id

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        validate_schema(artifact, INPUT_SCHEMA_BY_NAME["verification_results"])


@pytest.mark.parametrize("account_id", ["Case.Account", " B ", "user:opaque", "resource_order_b"])
def test_schema_preserves_opaque_account_id(fixture_root: Path, account_id: str) -> None:
    artifact = _verification_fixture(fixture_root)
    _relationship(artifact)["source_account_id"] = account_id
    original = copy.deepcopy(artifact)

    validate_schema(artifact, INPUT_SCHEMA_BY_NAME["verification_results"])

    assert artifact == original


@pytest.mark.parametrize("target_id", ["", None])
def test_target_still_requires_nonempty_node_id(fixture_root: Path, target_id: Any) -> None:
    artifact = _verification_fixture(fixture_root)
    _relationship(artifact)["target_id"] = target_id

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        validate_schema(artifact, INPUT_SCHEMA_BY_NAME["verification_results"])


@pytest.mark.parametrize("artifact_type", ["semantic_analysis", "graph_query_result"])
def test_ordinary_graph_relationships_retain_node_source_ids(
    fixture_root: Path,
    artifact_type: str,
) -> None:
    producer = "semantic_analyzer" if artifact_type == "semantic_analysis" else "knowledge_graph"
    path = (
        fixture_root / "runs/run_demo_001/artifacts/iteration-000"
        / producer / f"{artifact_type}.json"
    )
    artifact = load_json(path)
    if artifact_type == "semantic_analysis":
        graph = artifact["data"]
    else:
        snapshot = next(
            result for result in artifact["data"]["results"]
            if result["query_key"] == "structure_snapshot"
        )
        graph = next(iter(snapshot["rows"]))

    assert graph["relationships"]
    assert all(
        "source_id" in relationship and "source_account_id" not in relationship
        for relationship in graph["relationships"]
    )
    validate_schema(artifact, INPUT_SCHEMA_BY_NAME[artifact_type])


@pytest.mark.parametrize("status", ["partial", "failed"])
def test_verification_status_envelopes_still_validate(fixture_root: Path, status: str) -> None:
    artifact = _verification_fixture(fixture_root)
    artifact["status"] = status
    artifact["errors"] = [{
        "code": "VERIFICATION_ERROR",
        "message": "fixture verification failure",
        "item_ref": None,
        "retryable": False,
    }]
    if status == "failed":
        artifact["data"] = None

    validate_schema(artifact, INPUT_SCHEMA_BY_NAME["verification_results"])


@pytest.mark.parametrize("operation", ["report", "evaluate"])
@pytest.mark.parametrize("field_case", ["legacy", "both", "missing"])
def test_public_operations_reject_old_source_contract_without_outputs(
    request: pytest.FixtureRequest,
    operation: str,
    field_case: str,
) -> None:
    arguments = request.getfixturevalue(f"{operation}_arguments")
    artifact = load_json(arguments[2]["run_root"] / VERIFICATION_PATH)
    _change_source_field(artifact, field_case)
    input_path = _replace_verification(arguments, artifact)
    input_sha256 = calculate_sha256(input_path)
    output_path = _discard_copied_output(arguments, operation)

    response = entrypoint.run(operation, *arguments)

    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "CONTRACT_INVALID"
    assert response["output_path"] is None
    assert not output_path.exists()
    assert not (arguments[2]["run_root"] / "reports").exists()
    assert calculate_sha256(input_path) == input_sha256


@pytest.mark.parametrize("operation", ["report", "evaluate"])
@pytest.mark.parametrize("account_id", ["Case.Account", " B ", "user:opaque"])
def test_public_operations_preserve_account_source_without_node_resolution(
    request: pytest.FixtureRequest,
    operation: str,
    account_id: str,
) -> None:
    arguments = request.getfixturevalue(f"{operation}_arguments")
    artifact = load_json(arguments[2]["run_root"] / VERIFICATION_PATH)
    _relationship(artifact)["source_account_id"] = account_id
    input_path = _replace_verification(arguments, artifact)
    input_sha256 = calculate_sha256(input_path)
    output_path = _discard_copied_output(arguments, operation)

    response = entrypoint.run(operation, *arguments)

    assert response["status"] == "completed"
    output = load_json(output_path)
    reference = next(
        reference for reference in output["input_refs"]
        if reference["artifact_type"] == "verification_results"
    )
    assert reference["sha256"] == input_sha256
    assert output["schema_version"] == "0.2.0"
    assert calculate_sha256(input_path) == input_sha256
    assert load_json(input_path) == artifact


@pytest.mark.parametrize("operation", ["report", "evaluate"])
def test_public_operations_keep_verification_byte_hash_check(
    request: pytest.FixtureRequest,
    operation: str,
) -> None:
    arguments = request.getfixturevalue(f"{operation}_arguments")
    original_sha256 = arguments[0]["verification_results"]["sha256"]
    artifact = load_json(arguments[2]["run_root"] / VERIFICATION_PATH)
    _relationship(artifact)["source_account_id"] = "different-account"
    _replace_verification(arguments, artifact)
    arguments[0]["verification_results"]["sha256"] = original_sha256
    output_path = _discard_copied_output(arguments, operation)

    response = entrypoint.run(operation, *arguments)

    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "INPUT_HASH_MISMATCH"
    assert not output_path.exists()
    assert not (arguments[2]["run_root"] / "reports").exists()


def _verification_fixture(fixture_root: Path) -> dict[str, Any]:
    return load_json(fixture_root / "runs/run_demo_001" / VERIFICATION_PATH)


def _relationship(artifact: dict[str, Any]) -> dict[str, Any]:
    return next(iter(artifact["data"]["graph_updates"]["relationships"]))


def _change_source_field(artifact: dict[str, Any], field_case: str) -> None:
    relationship = _relationship(artifact)
    if field_case == "both":
        relationship["source_id"] = "opaque-node-id"
    else:
        relationship.pop("source_account_id")
        if field_case == "legacy":
            relationship["source_id"] = "opaque-node-id"


def _replace_verification(arguments: Arguments, artifact: dict[str, Any]) -> Path:
    input_paths, _, context = arguments
    descriptor = input_paths["verification_results"]
    input_path = context["run_root"] / descriptor["path"]
    input_path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
    descriptor["sha256"] = calculate_sha256(input_path)
    return input_path


def _discard_copied_output(arguments: Arguments, operation: str) -> Path:
    _, output_dir, context = arguments
    filename = "diagnosis_report.json" if operation == "report" else "evaluation_results.json"
    output_path = context["run_root"] / output_dir / filename
    # 테스트 임시 디렉터리에 복사된 출력 fixture만 제거한다.
    output_path.unlink()
    return output_path
