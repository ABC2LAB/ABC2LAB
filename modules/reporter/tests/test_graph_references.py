"""Original account/role IDs are independent of graph node IDs."""

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from modules.reporter import entrypoint
from modules.reporter.contracts import (
    INPUT_SCHEMA_BY_NAME,
    prepare_evaluation_inputs,
)
from modules.reporter.exceptions import ContractValidationError
from modules.reporter.input_adapter import parse_evaluate_request
from modules.reporter.input_validation import (
    extract_graph_snapshot,
    validate_semantic_analysis,
)
from modules.reporter.models import ArtifactSource, InputArtifact
from modules.reporter.utils.hashing import calculate_sha256
from modules.reporter.utils.validation import load_json, validate_schema

Arguments = tuple[dict[str, Any], str, dict[str, Any]]
ARTIFACT_TYPES = ("semantic_analysis", "graph_query_result")
IDENTITY_FIELDS = (("User", "account_id"), ("Role", "role_id"))
ORIGINAL_ID_FIELD_BY_NODE_TYPE = dict(IDENTITY_FIELDS)


def _load_artifact(fixture_root: Path, artifact_type: str) -> dict[str, Any]:
    schema = load_json(INPUT_SCHEMA_BY_NAME[artifact_type])
    producer = schema["properties"]["producer"]["const"]
    artifact = load_json(
        fixture_root / "runs/run_demo_001/artifacts/iteration-000"
        / producer / f"{artifact_type}.json"
    )
    _use_distinct_node_ids(_graph_data(artifact), "producer")
    return artifact


def _graph_data(artifact: dict[str, Any]) -> dict[str, Any]:
    if artifact["artifact_type"] == "semantic_analysis":
        return artifact["data"]
    result = next(
        item for item in artifact["data"]["results"]
        if item["query_key"] == "structure_snapshot"
    )
    return next(iter(result["rows"]))


def _use_distinct_node_ids(graph: dict[str, Any], id_style: str) -> None:
    node_id_by_old_id = {}
    for node in graph["nodes"]:
        field = ORIGINAL_ID_FIELD_BY_NODE_TYPE.get(node["node_type"])
        if field is None:
            continue
        properties = node["properties"]
        original_id = properties[field]
        if id_style == "original":
            new_node_id = original_id
        elif id_style == "producer":
            new_node_id = f"{node['node_type'].lower()}:{original_id}"
        else:
            new_node_id = f"opaque-node/{node['node_type']}/{original_id}"
        node_id_by_old_id[node["node_id"]] = new_node_id
        node["node_id"] = new_node_id
    for relationship in graph["relationships"]:
        for field in ("source_id", "target_id"):
            relationship[field] = node_id_by_old_id.get(
                relationship[field], relationship[field]
            )


def _validate_artifact(artifact: dict[str, Any]) -> None:
    artifact_type = artifact["artifact_type"]
    validate_schema(artifact, INPUT_SCHEMA_BY_NAME[artifact_type])
    source = ArtifactSource(
        artifact_id=artifact["artifact_id"],
        artifact_type=artifact_type,
        producer=artifact["producer"],
        relative_path=(
            f"artifacts/iteration-000/{artifact['producer']}/{artifact_type}.json"
        ),
        sha256="0" * 64,
        iteration=artifact["iteration"],
        status=artifact["status"],
    )
    inputs = InputArtifact(source=source, value=artifact, errors=())
    if artifact_type == "semantic_analysis":
        validate_semantic_analysis(inputs)
    else:
        assert extract_graph_snapshot(inputs) is not None


@pytest.mark.parametrize("artifact_type", ARTIFACT_TYPES)
@pytest.mark.parametrize("id_style", ["producer", "opaque", "original"])
def test_original_ids_are_resolved_from_public_properties(
    fixture_root: Path, artifact_type: str, id_style: str,
) -> None:
    artifact = _load_artifact(fixture_root, artifact_type)
    graph = _graph_data(artifact)
    _use_distinct_node_ids(graph, id_style)
    before_validation = copy.deepcopy(artifact)

    _validate_artifact(artifact)

    assert artifact == before_validation
    for node in graph["nodes"]:
        field = ORIGINAL_ID_FIELD_BY_NODE_TYPE.get(node["node_type"])
        if field is not None:
            if id_style == "original":
                assert node["node_id"] == node["properties"][field]
            else:
                assert node["node_id"] != node["properties"][field]


@pytest.mark.parametrize("artifact_type", ARTIFACT_TYPES)
def test_workflows_require_original_role_ids_not_node_ids(
    fixture_root: Path, artifact_type: str,
) -> None:
    artifact = _load_artifact(fixture_root, artifact_type)
    graph = _graph_data(artifact)
    role = next(node for node in graph["nodes"] if node["node_type"] == "Role")
    workflow = next(iter(graph["workflows"]))
    workflow["role_ids"] = [role["node_id"]]

    with pytest.raises(ContractValidationError, match="workflow.*role_id"):
        _validate_artifact(artifact)


@pytest.mark.parametrize("artifact_type", ARTIFACT_TYPES)
def test_workflows_reject_unknown_original_role_id(
    fixture_root: Path, artifact_type: str,
) -> None:
    artifact = _load_artifact(fixture_root, artifact_type)
    next(iter(_graph_data(artifact)["workflows"]))["role_ids"] = ["missing_role"]

    with pytest.raises(ContractValidationError, match="workflow.*role_id"):
        _validate_artifact(artifact)


@pytest.mark.parametrize("artifact_type", ARTIFACT_TYPES)
@pytest.mark.parametrize("identity", IDENTITY_FIELDS)
@pytest.mark.parametrize("invalid_value", [None, "", 12, False, {}, []])
def test_original_id_properties_reject_invalid_values(
    fixture_root: Path, artifact_type: str, identity: tuple[str, str],
    invalid_value: Any,
) -> None:
    node_type, field = identity
    artifact = _load_artifact(fixture_root, artifact_type)
    node = next(
        item for item in _graph_data(artifact)["nodes"]
        if item["node_type"] == node_type
    )
    node["properties"][field] = invalid_value

    with pytest.raises(ContractValidationError, match=f"{node_type}.*{field}"):
        _validate_artifact(artifact)


@pytest.mark.parametrize("artifact_type", ARTIFACT_TYPES)
@pytest.mark.parametrize("node_type, field", IDENTITY_FIELDS)
def test_original_id_properties_are_not_inferred_from_node_ids(
    fixture_root: Path, artifact_type: str, node_type: str, field: str,
) -> None:
    artifact = _load_artifact(fixture_root, artifact_type)
    node = next(
        item for item in _graph_data(artifact)["nodes"]
        if item["node_type"] == node_type
    )
    node["properties"].pop(field)

    with pytest.raises(ContractValidationError, match=f"{node_type}.*{field}"):
        _validate_artifact(artifact)


@pytest.mark.parametrize("artifact_type", ARTIFACT_TYPES)
@pytest.mark.parametrize("node_type, field", IDENTITY_FIELDS)
def test_duplicate_original_id_mapping_is_rejected(
    fixture_root: Path, artifact_type: str, node_type: str, field: str,
) -> None:
    artifact = _load_artifact(fixture_root, artifact_type)
    graph = _graph_data(artifact)
    duplicate = copy.deepcopy(next(
        item for item in graph["nodes"] if item["node_type"] == node_type
    ))
    duplicate["node_id"] = f"duplicate/{duplicate['node_id']}"
    graph["nodes"].append(duplicate)

    with pytest.raises(ContractValidationError, match=f"{field}.*중복"):
        _validate_artifact(artifact)


@pytest.mark.parametrize("field", ["account_id", "role_id"])
def test_normalized_requests_reject_graph_ids_in_original_id_fields(
    fixture_root: Path, field: str,
) -> None:
    artifact = _load_artifact(fixture_root, "semantic_analysis")
    request = next(iter(artifact["data"]["normalized_requests"]))
    node_type = "User" if field == "account_id" else "Role"
    node = next(
        item for item in artifact["data"]["nodes"]
        if item["node_type"] == node_type
        and item["properties"][field] == request[field]
    )
    request[field] = node["node_id"]

    with pytest.raises(ContractValidationError, match=f"normalized request.*{field}"):
        _validate_artifact(artifact)


@pytest.mark.parametrize(
    "field", ["account_id", "role_id", "endpoint_id", "resource_ids"],
)
def test_normalized_requests_reject_missing_references(
    fixture_root: Path, field: str,
) -> None:
    artifact = _load_artifact(fixture_root, "semantic_analysis")
    request = next(iter(artifact["data"]["normalized_requests"]))
    request[field] = (
        ["missing_resource"] if field == "resource_ids" else "missing_id"
    )

    with pytest.raises(ContractValidationError, match="normalized request"):
        _validate_artifact(artifact)


@pytest.mark.parametrize("node_type", ["User", "Role", "Endpoint", "Resource"])
def test_normalized_requests_reject_wrong_node_types(
    fixture_root: Path, node_type: str,
) -> None:
    artifact = _load_artifact(fixture_root, "semantic_analysis")
    node = next(
        item for item in artifact["data"]["nodes"]
        if item["node_type"] == node_type
    )
    node["node_type"] = "Page"

    with pytest.raises(
        ContractValidationError, match="normalized request|workflow.*role_id",
    ):
        _validate_artifact(artifact)


def test_account_and_role_can_have_the_same_original_id(fixture_root: Path) -> None:
    artifact = _load_artifact(fixture_root, "semantic_analysis")
    data = artifact["data"]
    request = next(iter(data["normalized_requests"]))
    account_id = request["account_id"]
    role_id = request["role_id"]
    user = next(
        item for item in data["nodes"]
        if item["node_type"] == "User"
        and item["properties"]["account_id"] == account_id
    )
    user["properties"]["account_id"] = role_id
    for item in data["normalized_requests"]:
        if item["account_id"] == account_id:
            item["account_id"] = role_id

    _validate_artifact(artifact)


@pytest.mark.parametrize("field", ["account_id", "role_id"])
def test_node_id_equality_does_not_override_original_id_properties(
    fixture_root: Path, field: str,
) -> None:
    artifact = _load_artifact(fixture_root, "semantic_analysis")
    graph = artifact["data"]
    node_type = "User" if field == "account_id" else "Role"
    request = next(iter(graph["normalized_requests"]))
    node = next(
        item for item in graph["nodes"] if item["node_type"] == node_type
        and item["properties"][field] == request[field]
    )
    old_node_id = node["node_id"]
    node["node_id"] = request[field]
    node["properties"][field] = f"different-original/{request[field]}"
    for relationship in graph["relationships"]:
        for reference_field in ("source_id", "target_id"):
            if relationship[reference_field] == old_node_id:
                relationship[reference_field] = node["node_id"]

    with pytest.raises(ContractValidationError, match=f"{field}"):
        _validate_artifact(artifact)


def test_resource_id_cannot_replace_endpoint_reference(fixture_root: Path) -> None:
    artifact = _load_artifact(fixture_root, "semantic_analysis")
    request = next(iter(artifact["data"]["normalized_requests"]))
    request["endpoint_id"] = next(iter(request["resource_ids"]))

    with pytest.raises(ContractValidationError, match="normalized request.*node"):
        _validate_artifact(artifact)


@pytest.mark.parametrize("artifact_type", ARTIFACT_TYPES)
@pytest.mark.parametrize("field", ["step_order", "dependency"])
def test_workflow_step_and_dependency_validation_is_preserved(
    fixture_root: Path, artifact_type: str, field: str,
) -> None:
    artifact = _load_artifact(fixture_root, artifact_type)
    workflow = next(iter(_graph_data(artifact)["workflows"]))
    if field == "step_order":
        next(iter(workflow["steps"]))["order"] = len(workflow["steps"])
    else:
        next(iter(workflow["dependencies"]))["before_step_id"] = "missing_step"

    with pytest.raises(
        ContractValidationError, match="workflow step order|workflow dependency",
    ):
        _validate_artifact(artifact)


@pytest.mark.parametrize("artifact_type", ARTIFACT_TYPES)
@pytest.mark.parametrize("field", ["source_id", "target_id"])
def test_relationships_continue_to_require_existing_node_ids(
    fixture_root: Path, artifact_type: str, field: str,
) -> None:
    artifact = _load_artifact(fixture_root, artifact_type)
    next(iter(_graph_data(artifact)["relationships"]))[field] = "missing_node"

    with pytest.raises(ContractValidationError, match=f"relationship {field}"):
        _validate_artifact(artifact)


def _mutate_public_input(
    arguments: Arguments, artifact_type: str,
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    input_paths, _, context = arguments
    descriptor = input_paths[artifact_type]
    path = context["run_root"] / descriptor["path"]
    artifact = load_json(path)
    mutate(artifact)
    path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
    descriptor["sha256"] = calculate_sha256(path)


def test_public_evaluate_accepts_opaque_graph_ids(
    evaluate_arguments: Arguments,
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    originals_by_type = {}
    for artifact_type in ARTIFACT_TYPES:
        originals_by_type[artifact_type] = load_json(
            context["run_root"] / input_paths[artifact_type]["path"]
        )
        _mutate_public_input(
            evaluate_arguments, artifact_type,
            lambda artifact: _use_distinct_node_ids(_graph_data(artifact), "opaque"),
        )
    output_path = (
        context["run_root"] / output_dir / "evaluation_results.json"
    )
    output_path.unlink()

    response = entrypoint.run("evaluate", input_paths, output_dir, context)

    assert response["status"] == "completed"
    assert response["sha256"] == calculate_sha256(output_path)
    output = load_json(output_path)
    assert output["schema_version"] == "0.2.0"
    references_by_type = {
        item["artifact_type"]: item for item in output["input_refs"]
    }
    for artifact_type in ARTIFACT_TYPES:
        reference = references_by_type[artifact_type]
        source = load_json(context["run_root"] / reference["path"])
        assert reference["sha256"] == input_paths[artifact_type]["sha256"]
        assert _graph_data(source)["workflows"] == _graph_data(
            originals_by_type[artifact_type]
        )["workflows"]
    semantic = load_json(
        context["run_root"] / input_paths["semantic_analysis"]["path"]
    )
    assert semantic["data"]["normalized_requests"] == originals_by_type[
        "semantic_analysis"
    ]["data"]["normalized_requests"]


@pytest.mark.parametrize("artifact_type", ARTIFACT_TYPES)
@pytest.mark.parametrize("identity", IDENTITY_FIELDS)
@pytest.mark.parametrize("mutation", ["missing", "duplicate"])
def test_public_evaluate_rejects_invalid_original_id_mapping_without_output(
    evaluate_arguments: Arguments, artifact_type: str,
    identity: tuple[str, str], mutation: str,
) -> None:
    node_type, field = identity

    def invalidate_mapping(artifact: dict[str, Any]) -> None:
        graph = _graph_data(artifact)
        node = next(
            item for item in graph["nodes"] if item["node_type"] == node_type
        )
        if mutation == "missing":
            node["properties"].pop(field)
        else:
            duplicate = copy.deepcopy(node)
            duplicate["node_id"] = f"duplicate/{node['node_id']}"
            graph["nodes"].append(duplicate)

    _mutate_public_input(evaluate_arguments, artifact_type, invalidate_mapping)
    input_paths, output_dir, context = evaluate_arguments
    output_path = context["run_root"] / output_dir / "evaluation_results.json"
    output_path.unlink()

    response = entrypoint.run("evaluate", input_paths, output_dir, context)

    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "CONTRACT_INVALID"
    assert response["errors"][0]["retryable"] is False
    assert response["output_path"] is None
    assert not output_path.exists()
    assert not (context["run_root"] / "reports").exists()
    descriptor = input_paths[artifact_type]
    assert calculate_sha256(
        context["run_root"] / descriptor["path"]
    ) == descriptor["sha256"]


def test_evaluate_preserves_crawl_account_consistency_check(
    evaluate_arguments: Arguments,
) -> None:
    def switch_to_another_account(artifact: dict[str, Any]) -> None:
        data = artifact["data"]
        _use_distinct_node_ids(data, "opaque")
        request = next(iter(data["normalized_requests"]))
        other_user = next(
            node for node in data["nodes"] if node["node_type"] == "User"
            and node["properties"]["account_id"] != request["account_id"]
        )
        request["account_id"] = other_user["properties"]["account_id"]

    _mutate_public_input(
        evaluate_arguments, "semantic_analysis", switch_to_another_account,
    )
    request = parse_evaluate_request(*evaluate_arguments)

    with pytest.raises(ContractValidationError, match="semantic 요청의 계정·역할 연결"):
        prepare_evaluation_inputs(request)
