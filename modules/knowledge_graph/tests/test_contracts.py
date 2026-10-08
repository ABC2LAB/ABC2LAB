import copy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from modules.knowledge_graph.exceptions import ContractValidationError
from modules.knowledge_graph.models import SemanticGraph
from modules.knowledge_graph.service import (
    SCHEMA_DIRECTORY,
    prepare_ingest,
    prepare_query,
    prepare_verification,
)
from modules.knowledge_graph.utils.validation import (
    load_json,
    validate_graph_query_result_semantics,
    validate_schema,
)


def test_contract_fixtures_are_valid(fixture_root: Path) -> None:
    semantic_path = fixture_root / "semantic_analyzer" / "semantic_analysis.json"
    query_path = fixture_root / "access_analyzer" / "graph_query.json"
    verification_path = fixture_root / "verifier" / "verification_results.json"
    output_path = fixture_root / "knowledge_graph" / "graph_query_result.json"

    semantic_artifact, semantic_graph = prepare_ingest(semantic_path)
    query_artifact = prepare_query(query_path)
    verification_artifact = prepare_verification(verification_path)
    output_artifact = load_json(output_path)
    validate_schema(
        output_artifact,
        SCHEMA_DIRECTORY / "output" / "graph_query_result.schema.json",
    )
    validate_graph_query_result_semantics(output_artifact)

    assert semantic_artifact["artifact_id"] == "semantic_demo_001"
    assert isinstance(semantic_graph, SemanticGraph)
    assert len(semantic_graph.request_observations) == 1
    assert len(semantic_graph.nodes) == 4
    assert len(semantic_graph.relationships) == 2
    assert len(semantic_graph.workflows) == 1
    assert len(query_artifact["data"]["queries"]) == 4
    assert len(verification_artifact["data"]["graph_updates"]["relationships"]) == 1


def test_all_schemas_are_valid() -> None:
    schema_paths = sorted(SCHEMA_DIRECTORY.rglob("*.schema.json"))

    assert len(schema_paths) == 4
    for schema_path in schema_paths:
        Draft202012Validator.check_schema(load_json(schema_path))


def test_schema_rejects_undefined_key(fixture_root: Path) -> None:
    artifact = load_json(fixture_root / "access_analyzer" / "graph_query.json")
    artifact["unexpected"] = True

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        validate_schema(
            artifact,
            SCHEMA_DIRECTORY / "input" / "graph_query.schema.json",
        )


def test_schema_enforces_failed_state_contract(fixture_root: Path) -> None:
    artifact = load_json(fixture_root / "access_analyzer" / "graph_query.json")
    artifact["status"] = "failed"

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        validate_schema(
            artifact,
            SCHEMA_DIRECTORY / "input" / "graph_query.schema.json",
        )


def test_ingest_rejects_duplicate_node_id(fixture_root: Path, tmp_path: Path) -> None:
    source = load_json(fixture_root / "semantic_analyzer" / "semantic_analysis.json")
    source["data"]["nodes"].append(copy.deepcopy(source["data"]["nodes"][0]))
    input_path = _write_test_json(tmp_path / "semantic.json", source)

    with pytest.raises(ContractValidationError, match="중복 node_id"):
        prepare_ingest(input_path)


def test_ingest_accepts_type_resource(fixture_root: Path, tmp_path: Path) -> None:
    source = load_json(fixture_root / "semantic_analyzer" / "semantic_analysis.json")
    resource = _resource_node(source)
    resource["properties"] = {
        "resource_key": "order",
        "resource_scope": "type",
        "match_key": None,
    }
    relationship = _relationship(source, "OWNS")
    relationship.update(
        {
            "relationship_id": "relationship_reference_type_001",
            "source_id": "endpoint_orders",
            "relation_type": "REFERENCE",
        }
    )

    artifact, _ = prepare_ingest(_write_test_json(tmp_path / "semantic.json", source))

    assert artifact["schema_version"] == "0.2.0"


def test_ingest_accepts_composite_resource_match_key(
    fixture_root: Path,
    tmp_path: Path,
) -> None:
    source = load_json(fixture_root / "semantic_analyzer" / "semantic_analysis.json")
    _resource_node(source)["properties"]["match_key"]["identifiers"] = [
        {"key": "tenant_id", "value": "company-a"},
        {"key": "order_id", "value": "001"},
    ]

    artifact, _ = prepare_ingest(_write_test_json(tmp_path / "semantic.json", source))

    assert artifact["schema_version"] == "0.2.0"


@pytest.mark.parametrize("property_name", ["resource_key", "resource_scope", "match_key"])
def test_ingest_rejects_resource_without_required_property(
    fixture_root: Path,
    tmp_path: Path,
    property_name: str,
) -> None:
    source = load_json(fixture_root / "semantic_analyzer" / "semantic_analysis.json")
    del _resource_node(source)["properties"][property_name]

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        prepare_ingest(_write_test_json(tmp_path / "semantic.json", source))


@pytest.mark.parametrize(
    ("resource_scope", "match_key"),
    [
        (
            "type",
            {
                "resource_key": "order",
                "identifiers": [{"key": "order_id", "value": "001"}],
            },
        ),
        ("instance", None),
    ],
)
def test_ingest_rejects_resource_scope_match_key_mismatch(
    fixture_root: Path,
    tmp_path: Path,
    resource_scope: str,
    match_key: dict[str, object] | None,
) -> None:
    source = load_json(fixture_root / "semantic_analyzer" / "semantic_analysis.json")
    properties = _resource_node(source)["properties"]
    properties["resource_scope"] = resource_scope
    properties["match_key"] = match_key

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        prepare_ingest(_write_test_json(tmp_path / "semantic.json", source))


def test_ingest_rejects_empty_resource_identifiers(
    fixture_root: Path,
    tmp_path: Path,
) -> None:
    source = load_json(fixture_root / "semantic_analyzer" / "semantic_analysis.json")
    _resource_node(source)["properties"]["match_key"]["identifiers"] = []

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        prepare_ingest(_write_test_json(tmp_path / "semantic.json", source))


def test_ingest_rejects_unknown_resource_scope(
    fixture_root: Path,
    tmp_path: Path,
) -> None:
    source = load_json(fixture_root / "semantic_analyzer" / "semantic_analysis.json")
    _resource_node(source)["properties"]["resource_scope"] = "collection"

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        prepare_ingest(_write_test_json(tmp_path / "semantic.json", source))


@pytest.mark.parametrize("field_name", ["key", "value"])
def test_ingest_rejects_empty_resource_identifier_field(
    fixture_root: Path,
    tmp_path: Path,
    field_name: str,
) -> None:
    source = load_json(fixture_root / "semantic_analyzer" / "semantic_analysis.json")
    identifier = _resource_node(source)["properties"]["match_key"]["identifiers"][0]
    identifier[field_name] = ""

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        prepare_ingest(_write_test_json(tmp_path / "semantic.json", source))


def test_ingest_rejects_duplicate_resource_identifier_key(
    fixture_root: Path,
    tmp_path: Path,
) -> None:
    source = load_json(fixture_root / "semantic_analyzer" / "semantic_analysis.json")
    _resource_node(source)["properties"]["match_key"]["identifiers"] = [
        {"key": "order_id", "value": "001"},
        {"key": "order_id", "value": "002"},
    ]

    with pytest.raises(ContractValidationError, match="중복 Resource identifier key"):
        prepare_ingest(_write_test_json(tmp_path / "semantic.json", source))


def test_ingest_rejects_resource_key_mismatch(
    fixture_root: Path,
    tmp_path: Path,
) -> None:
    source = load_json(fixture_root / "semantic_analyzer" / "semantic_analysis.json")
    _resource_node(source)["properties"]["match_key"]["resource_key"] = "invoice"

    with pytest.raises(ContractValidationError, match="resource_key가 노드 속성과"):
        prepare_ingest(_write_test_json(tmp_path / "semantic.json", source))


def test_ingest_rejects_duplicate_resource_match_key_regardless_of_order(
    fixture_root: Path,
    tmp_path: Path,
) -> None:
    source = load_json(fixture_root / "semantic_analyzer" / "semantic_analysis.json")
    resource = _resource_node(source)
    resource["properties"]["match_key"]["identifiers"] = [
        {"key": "tenant_id", "value": "company-a"},
        {"key": "order_id", "value": "001"},
    ]
    duplicate = copy.deepcopy(resource)
    duplicate["node_id"] = "resource_order_duplicate"
    duplicate["properties"]["match_key"]["identifiers"].reverse()
    source["data"]["nodes"].append(duplicate)

    with pytest.raises(ContractValidationError, match="동일 Resource match_key"):
        prepare_ingest(_write_test_json(tmp_path / "semantic.json", source))


def test_ingest_rejects_duplicate_request_resource_id(
    fixture_root: Path,
    tmp_path: Path,
) -> None:
    source = load_json(fixture_root / "semantic_analyzer" / "semantic_analysis.json")
    resource_id = _resource_node(source)["node_id"]
    source["data"]["normalized_requests"][0]["resource_ids"] = [
        resource_id,
        resource_id,
    ]

    with pytest.raises(ContractValidationError, match="중복 resource_id"):
        prepare_ingest(_write_test_json(tmp_path / "semantic.json", source))


@pytest.mark.parametrize(
    ("resource_id", "error_pattern"),
    [
        ("missing_resource", "존재하지 않는 Resource node_id"),
        ("endpoint_orders", "Resource 노드가 아님"),
    ],
)
def test_ingest_rejects_invalid_request_resource_reference(
    fixture_root: Path,
    tmp_path: Path,
    resource_id: str,
    error_pattern: str,
) -> None:
    source = load_json(fixture_root / "semantic_analyzer" / "semantic_analysis.json")
    source["data"]["normalized_requests"][0]["resource_ids"] = [resource_id]

    with pytest.raises(ContractValidationError, match=error_pattern):
        prepare_ingest(_write_test_json(tmp_path / "semantic.json", source))


@pytest.mark.parametrize(
    ("source_id", "target_id"),
    [
        ("account_user", "resource_order_001"),
        ("endpoint_orders", "role_user"),
    ],
)
def test_ingest_rejects_invalid_reference_relationship_direction(
    fixture_root: Path,
    tmp_path: Path,
    source_id: str,
    target_id: str,
) -> None:
    source = load_json(fixture_root / "semantic_analyzer" / "semantic_analysis.json")
    relationship = _relationship(source, "OWNS")
    relationship.update(
        {
            "relationship_id": "relationship_invalid_reference",
            "source_id": source_id,
            "target_id": target_id,
            "relation_type": "REFERENCE",
        }
    )

    with pytest.raises(ContractValidationError, match="Endpoint에서 Resource"):
        prepare_ingest(_write_test_json(tmp_path / "semantic.json", source))


def test_ingest_rejects_owns_from_non_user(
    fixture_root: Path,
    tmp_path: Path,
) -> None:
    source = load_json(fixture_root / "semantic_analyzer" / "semantic_analysis.json")
    _relationship(source, "OWNS")["source_id"] = "role_user"

    with pytest.raises(ContractValidationError, match="User에서 Resource"):
        prepare_ingest(_write_test_json(tmp_path / "semantic.json", source))


def test_ingest_rejects_owns_to_resource_type(
    fixture_root: Path,
    tmp_path: Path,
) -> None:
    source = load_json(fixture_root / "semantic_analyzer" / "semantic_analysis.json")
    _resource_node(source)["properties"] = {
        "resource_key": "order",
        "resource_scope": "type",
        "match_key": None,
    }

    with pytest.raises(ContractValidationError, match="Resource instance"):
        prepare_ingest(_write_test_json(tmp_path / "semantic.json", source))


def test_ingest_rejects_legacy_semantic_version(
    fixture_root: Path,
    tmp_path: Path,
) -> None:
    source = load_json(fixture_root / "semantic_analyzer" / "semantic_analysis.json")
    source["schema_version"] = "0.1.0"

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        prepare_ingest(_write_test_json(tmp_path / "semantic.json", source))


def test_ingest_rejects_unknown_relationship_target(
    fixture_root: Path,
    tmp_path: Path,
) -> None:
    source = load_json(fixture_root / "semantic_analyzer" / "semantic_analysis.json")
    source["data"]["relationships"][0]["target_id"] = "missing_node"
    input_path = _write_test_json(tmp_path / "semantic.json", source)

    with pytest.raises(ContractValidationError, match="target_id"):
        prepare_ingest(input_path)


def test_ingest_rejects_non_contiguous_workflow_order(
    fixture_root: Path,
    tmp_path: Path,
) -> None:
    source = load_json(fixture_root / "semantic_analyzer" / "semantic_analysis.json")
    source["data"]["workflows"][0]["steps"][0]["order"] = 1
    input_path = _write_test_json(tmp_path / "semantic.json", source)

    with pytest.raises(ContractValidationError, match="0부터 연속"):
        prepare_ingest(input_path)


def test_query_rejects_duplicate_query_id(fixture_root: Path, tmp_path: Path) -> None:
    source = load_json(fixture_root / "access_analyzer" / "graph_query.json")
    source["data"]["queries"][1]["query_id"] = source["data"]["queries"][0]["query_id"]
    input_path = _write_test_json(tmp_path / "query.json", source)

    with pytest.raises(ContractValidationError, match="중복 query_id"):
        prepare_query(input_path)


def test_verification_rejects_non_allow_update_source(
    fixture_root: Path,
    tmp_path: Path,
) -> None:
    source = load_json(fixture_root / "verifier" / "verification_results.json")
    result = source["data"]["results"][0]
    result["policy_decision"] = "block"
    result["execution_status"] = "not_executed"
    result["result"] = "blocked"
    input_path = _write_test_json(tmp_path / "verification.json", source)

    with pytest.raises(ContractValidationError, match="차단 또는 승인 대기"):
        prepare_verification(input_path)


def test_verification_requires_verified_basis(
    fixture_root: Path,
    tmp_path: Path,
) -> None:
    source = load_json(fixture_root / "verifier" / "verification_results.json")
    source["data"]["graph_updates"]["relationships"][0]["basis"] = "inferred"
    input_path = _write_test_json(tmp_path / "verification.json", source)

    with pytest.raises(ContractValidationError, match="basis는 verified"):
        prepare_verification(input_path)


def _write_test_json(path: Path, value: dict[str, object]) -> Path:
    import json

    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def _resource_node(artifact: dict[str, object]) -> dict[str, object]:
    data = artifact["data"]
    assert isinstance(data, dict)
    nodes = data["nodes"]
    assert isinstance(nodes, list)
    return next(item for item in nodes if item["node_type"] == "Resource")


def _relationship(
    artifact: dict[str, object],
    relation_type: str,
) -> dict[str, object]:
    data = artifact["data"]
    assert isinstance(data, dict)
    relationships = data["relationships"]
    assert isinstance(relationships, list)
    return next(
        item for item in relationships if item["relation_type"] == relation_type
    )
