"""Independent regression checks for the public 0.2.0 contract migration."""

import copy
from pathlib import Path
from typing import Any

import pytest

from modules.reporter.contracts import INPUT_SCHEMA_BY_NAME
from modules.reporter.evaluation_output_adapter import EVALUATION_RESULTS_SCHEMA
from modules.reporter.exceptions import ContractValidationError
from modules.reporter.output_adapter import DIAGNOSIS_REPORT_SCHEMA
from modules.reporter.utils.hashing import calculate_sha256
from modules.reporter.utils.validation import load_json, validate_schema

SCHEMA_BY_ARTIFACT = {
    **INPUT_SCHEMA_BY_NAME,
    "diagnosis_report": DIAGNOSIS_REPORT_SCHEMA,
    "evaluation_results": EVALUATION_RESULTS_SCHEMA,
}
RUNTIME_ARTIFACT_TYPES = tuple(
    name for name in SCHEMA_BY_ARTIFACT if name != "ground_truth"
)


def _fixture_path(fixture_root: Path, artifact_type: str) -> Path:
    schema = load_json(SCHEMA_BY_ARTIFACT[artifact_type])
    producer = schema["properties"]["producer"]["const"]
    return (
        fixture_root
        / "runs/run_demo_001/artifacts/iteration-000"
        / producer
        / f"{artifact_type}.json"
    )


def _semantic_resource(artifact: dict[str, Any]) -> dict[str, Any]:
    return next(
        node for node in artifact["data"]["nodes"]
        if node["node_type"] == "Resource"
    )


def _graph_update_record(
    artifact: dict[str, Any],
    record_type: str,
) -> dict[str, Any]:
    updates = artifact["data"]["graph_updates"]
    relationship = next(
        item for item in updates["relationships"]
        if item["relation_type"] in {"VERIFIED_ACCESS", "VERIFIED_DENIAL"}
    )
    if record_type == "relationships":
        return relationship
    node = {
        "node_id": relationship["source_id"],
        "node_type": "User",
        "properties": {},
        "basis": "verified",
        "evidence_refs": copy.deepcopy(relationship["evidence_refs"]),
    }
    updates["nodes"].append(node)
    return node


@pytest.mark.parametrize("artifact_type", RUNTIME_ARTIFACT_TYPES)
def test_runtime_contracts_and_fixtures_use_0_2(
    fixture_root: Path,
    artifact_type: str,
) -> None:
    schema = load_json(SCHEMA_BY_ARTIFACT[artifact_type])
    artifact = load_json(_fixture_path(fixture_root, artifact_type))

    assert schema["properties"]["schema_version"]["const"] == "0.2.0"
    assert artifact["schema_version"] == "0.2.0"
    validate_schema(artifact, SCHEMA_BY_ARTIFACT[artifact_type])


@pytest.mark.parametrize("artifact_type", RUNTIME_ARTIFACT_TYPES)
@pytest.mark.parametrize("schema_version", ["0.1.0", "0.3.0"])
def test_runtime_contracts_reject_unsupported_versions(
    fixture_root: Path,
    artifact_type: str,
    schema_version: str,
) -> None:
    artifact = load_json(_fixture_path(fixture_root, artifact_type))
    artifact["schema_version"] = schema_version

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        validate_schema(artifact, SCHEMA_BY_ARTIFACT[artifact_type])


def test_ground_truth_retains_its_independent_0_1_contract(
    fixture_root: Path,
) -> None:
    artifact = load_json(fixture_root / "datasets/shop_demo/ground_truth.json")
    schema = load_json(INPUT_SCHEMA_BY_NAME["ground_truth"])

    assert artifact["schema_version"] == "0.1.0"
    assert schema["properties"]["schema_version"]["const"] == "0.1.0"
    validate_schema(artifact, INPUT_SCHEMA_BY_NAME["ground_truth"])


def test_fixture_plan_and_reference_hashes_match_actual_bytes(
    fixture_root: Path,
) -> None:
    scenarios_sha256 = calculate_sha256(
        _fixture_path(fixture_root, "test_scenarios")
    )
    for artifact_type in ("safety_decisions", "verification_results"):
        artifact = load_json(_fixture_path(fixture_root, artifact_type))
        assert artifact["data"]["scenarios_sha256"] == scenarios_sha256
    output_paths = [
        _fixture_path(fixture_root, "diagnosis_report"),
        _fixture_path(fixture_root, "evaluation_results"),
        *sorted((fixture_root / "status").glob("diagnosis_report.*.json")),
        *sorted((fixture_root / "status").glob("evaluation_results.*.json")),
    ]
    for output_path in output_paths:
        artifact = load_json(output_path)
        run_root = fixture_root / "runs" / artifact["run_id"]
        for reference in artifact["input_refs"]:
            source_path = run_root / reference["path"]
            source = load_json(source_path)
            assert reference["sha256"] == calculate_sha256(source_path)
            assert reference["artifact_id"] == source["artifact_id"]
            assert reference["artifact_type"] == source["artifact_type"]
            assert reference["iteration"] == source["iteration"]
        if artifact["data"] is not None and "ground_truth_ref" in artifact["data"]:
            reference = artifact["data"]["ground_truth_ref"]
            assert reference["sha256"] == calculate_sha256(
                fixture_root / reference["path"]
            )


@pytest.mark.parametrize("resource_scope", ["type", "instance"])
def test_semantic_resource_accepts_current_scope_contract(
    fixture_root: Path,
    resource_scope: str,
) -> None:
    artifact = load_json(_fixture_path(fixture_root, "semantic_analysis"))
    properties = _semantic_resource(artifact)["properties"]
    properties["resource_scope"] = resource_scope
    if resource_scope == "type":
        properties["match_key"] = None

    validate_schema(artifact, INPUT_SCHEMA_BY_NAME["semantic_analysis"])


@pytest.mark.parametrize("field", ["resource_key", "resource_scope", "match_key"])
def test_semantic_resource_requires_current_fields(
    fixture_root: Path,
    field: str,
) -> None:
    artifact = load_json(_fixture_path(fixture_root, "semantic_analysis"))
    _semantic_resource(artifact)["properties"].pop(field)

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        validate_schema(artifact, INPUT_SCHEMA_BY_NAME["semantic_analysis"])


@pytest.mark.parametrize("resource_scope", ["type", "instance"])
def test_semantic_resource_rejects_scope_match_key_mismatch(
    fixture_root: Path,
    resource_scope: str,
) -> None:
    artifact = load_json(_fixture_path(fixture_root, "semantic_analysis"))
    properties = _semantic_resource(artifact)["properties"]
    properties["resource_scope"] = resource_scope
    if resource_scope == "instance":
        properties["match_key"] = None

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        validate_schema(artifact, INPUT_SCHEMA_BY_NAME["semantic_analysis"])


def test_semantic_resource_rejects_empty_identifiers(fixture_root: Path) -> None:
    artifact = load_json(_fixture_path(fixture_root, "semantic_analysis"))
    properties = _semantic_resource(artifact)["properties"]
    properties["match_key"]["identifiers"] = []

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        validate_schema(artifact, INPUT_SCHEMA_BY_NAME["semantic_analysis"])


@pytest.mark.parametrize("field", ["resource_ids", "source_request_ids"])
def test_candidate_rejects_empty_reference_array(
    fixture_root: Path,
    field: str,
) -> None:
    artifact = load_json(_fixture_path(fixture_root, "vulnerability_candidates"))
    candidate = next(iter(artifact["data"]["candidates"]))
    candidate[field] = []

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        validate_schema(artifact, INPUT_SCHEMA_BY_NAME["vulnerability_candidates"])


def test_graph_updates_reject_resource_creation(fixture_root: Path) -> None:
    artifact = load_json(_fixture_path(fixture_root, "verification_results"))
    _graph_update_record(artifact, "nodes")["node_type"] = "Resource"

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        validate_schema(artifact, INPUT_SCHEMA_BY_NAME["verification_results"])


@pytest.mark.parametrize("relation_type", ["VERIFIED_ACCESS", "VERIFIED_DENIAL"])
def test_graph_updates_accept_verified_relationships(
    fixture_root: Path,
    relation_type: str,
) -> None:
    artifact = load_json(_fixture_path(fixture_root, "verification_results"))
    _graph_update_record(artifact, "nodes")
    _graph_update_record(artifact, "relationships")["relation_type"] = relation_type

    validate_schema(artifact, INPUT_SCHEMA_BY_NAME["verification_results"])


@pytest.mark.parametrize("relation_type", ["ACCESS", "OWNS"])
def test_graph_updates_reject_unverified_relationship_types(
    fixture_root: Path,
    relation_type: str,
) -> None:
    artifact = load_json(_fixture_path(fixture_root, "verification_results"))
    _graph_update_record(artifact, "relationships")["relation_type"] = relation_type

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        validate_schema(artifact, INPUT_SCHEMA_BY_NAME["verification_results"])


@pytest.mark.parametrize("record_type", ["nodes", "relationships"])
@pytest.mark.parametrize("basis", ["observed", "inferred"])
def test_graph_updates_require_verified_basis(
    fixture_root: Path,
    record_type: str,
    basis: str,
) -> None:
    artifact = load_json(_fixture_path(fixture_root, "verification_results"))
    _graph_update_record(artifact, record_type)["basis"] = basis

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        validate_schema(artifact, INPUT_SCHEMA_BY_NAME["verification_results"])


@pytest.mark.parametrize("record_type", ["nodes", "relationships"])
def test_graph_updates_require_evidence(
    fixture_root: Path,
    record_type: str,
) -> None:
    artifact = load_json(_fixture_path(fixture_root, "verification_results"))
    _graph_update_record(artifact, record_type)["evidence_refs"] = []

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        validate_schema(artifact, INPUT_SCHEMA_BY_NAME["verification_results"])


@pytest.mark.parametrize("field", ["is_sensitive", "redacted"])
def test_crawl_contract_rejects_unmasked_sensitive_values(
    fixture_root: Path,
    field: str,
) -> None:
    artifact = load_json(_fixture_path(fixture_root, "crawl_result"))
    request = next(iter(artifact["data"]["requests"]))
    if field == "is_sensitive":
        request["parameters"].append({
            "name": "private_field",
            "location": "body",
            "value": "synthetic_unmasked_value",
            "is_sensitive": True,
        })
    else:
        request["headers"].append({
            "name": "x-private-field",
            "value": "synthetic_unmasked_value",
            "redacted": True,
        })

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        validate_schema(artifact, INPUT_SCHEMA_BY_NAME["crawl_result"])


def test_crawl_contract_rejects_malformed_evidence_hash(fixture_root: Path) -> None:
    artifact = load_json(_fixture_path(fixture_root, "crawl_result"))
    page = next(iter(artifact["data"]["pages"]))
    page["evidence_refs"].append({
        "evidence_id": "fixture_evidence",
        "kind": "dom",
        "path": "evidence/collector/fixture_evidence.json",
        "sha256": "not-a-sha256",
        "redacted": True,
    })

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        validate_schema(artifact, INPUT_SCHEMA_BY_NAME["crawl_result"])


@pytest.mark.parametrize("status", ["partial", "failed"])
def test_crawl_contract_requires_status_data_and_errors(
    fixture_root: Path,
    status: str,
) -> None:
    artifact = load_json(_fixture_path(fixture_root, "crawl_result"))
    artifact["status"] = status

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        validate_schema(artifact, INPUT_SCHEMA_BY_NAME["crawl_result"])
