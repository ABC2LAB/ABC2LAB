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
