import copy
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

from modules.knowledge_graph.exceptions import ContractValidationError
from modules.knowledge_graph.models import GraphEdge, VerificationRelationship
from modules.knowledge_graph.service import SCHEMA_DIRECTORY, prepare_ingest
from modules.knowledge_graph.utils.validation import (
    load_json,
    validate_schema,
    validate_verification_results_semantics,
)


def test_verification_fixture_uses_account_source_without_version_bump(
    fixture_root: Path,
) -> None:
    artifact = _artifact(fixture_root)

    _validate(artifact)

    assert artifact["schema_version"] == "0.2.0"
    assert all(
        "source_account_id" in relationship and "source_id" not in relationship
        for relationship in artifact["data"]["graph_updates"]["relationships"]
    )


@pytest.mark.parametrize("source_fields", ["legacy", "both", "missing"])
def test_verification_rejects_legacy_or_ambiguous_source_fields(
    fixture_root: Path,
    source_fields: str,
) -> None:
    artifact = _artifact(fixture_root)
    relationship = _relationship(artifact)
    account_id = relationship["source_account_id"]
    if source_fields in {"legacy", "missing"}:
        del relationship["source_account_id"]
    if source_fields in {"legacy", "both"}:
        relationship["source_id"] = account_id

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        _validate(artifact)


@pytest.mark.parametrize("account_id", ["", None, 3, False, {}, []])
def test_verification_rejects_invalid_account_source(
    fixture_root: Path,
    account_id: Any,
) -> None:
    artifact = _artifact(fixture_root)
    _relationship(artifact)["source_account_id"] = account_id

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        _validate(artifact)


@pytest.mark.parametrize(
    "account_id", ["Account-B", "  Account B  ", "opaque-account/42"]
)
def test_verification_model_preserves_original_account_id(
    fixture_root: Path,
    account_id: str,
) -> None:
    artifact = _artifact(fixture_root)
    relationship = _relationship(artifact)
    relationship["source_account_id"] = account_id
    original = copy.deepcopy(relationship)

    _validate(artifact)
    parsed = VerificationRelationship.from_mapping(relationship)

    assert parsed.source_account_id == account_id
    assert parsed.target_id == original["target_id"]
    assert parsed.properties == original["properties"]
    assert not isinstance(parsed, GraphEdge)
    assert not hasattr(parsed, "source_id")
    assert relationship == original


def test_account_and_node_id_are_not_compared_as_one_namespace(
    fixture_root: Path,
) -> None:
    artifact = _artifact(fixture_root)
    relationship = _relationship(artifact)
    relationship["source_account_id"] = relationship["target_id"]

    # 실제 동일 노드·참조 검사는 User 노드로 해석한 뒤 수행한다.
    _validate(artifact)


@pytest.mark.parametrize("relation_type", ["VERIFIED_ACCESS", "VERIFIED_DENIAL"])
def test_account_source_supports_both_verification_relations(
    fixture_root: Path,
    relation_type: str,
) -> None:
    artifact = _artifact(fixture_root)
    _relationship(artifact)["relation_type"] = relation_type

    _validate(artifact)


@pytest.mark.parametrize("basis", ["observed", "inferred"])
def test_account_source_still_requires_verified_basis(
    fixture_root: Path,
    basis: str,
) -> None:
    artifact = _artifact(fixture_root)
    _relationship(artifact)["basis"] = basis

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        _validate(artifact)


def test_account_source_still_requires_execution_evidence(
    fixture_root: Path,
) -> None:
    artifact = _artifact(fixture_root)
    _relationship(artifact)["evidence_refs"] = []

    with pytest.raises(ContractValidationError, match="EvidenceRef"):
        _validate(artifact)


@pytest.mark.parametrize("schema_version", ["0.1.0", "0.3.0"])
def test_account_source_does_not_enable_other_contract_versions(
    fixture_root: Path,
    schema_version: str,
) -> None:
    artifact = _artifact(fixture_root)
    artifact["schema_version"] = schema_version

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        _validate(artifact)


def test_verification_model_does_not_fall_back_to_graph_source_id(
    fixture_root: Path,
) -> None:
    relationship = _relationship(_artifact(fixture_root))
    relationship["source_id"] = relationship.pop("source_account_id")

    with pytest.raises(KeyError, match="source_account_id"):
        VerificationRelationship.from_mapping(relationship)


def test_verification_relationship_is_frozen(fixture_root: Path) -> None:
    parsed = VerificationRelationship.from_mapping(
        _relationship(_artifact(fixture_root))
    )

    with pytest.raises(FrozenInstanceError):
        parsed.source_account_id = "another-account"  # type: ignore[misc]


def test_semantic_and_snapshot_relationships_keep_graph_source_ids(
    fixture_root: Path,
) -> None:
    _, graph = prepare_ingest(
        fixture_root / "semantic_analyzer" / "semantic_analysis.json"
    )
    snapshot = load_json(fixture_root / "knowledge_graph" / "graph_query_result.json")
    validate_schema(
        snapshot, SCHEMA_DIRECTORY / "output/graph_query_result.schema.json"
    )

    assert all(
        isinstance(relationship, GraphEdge) for relationship in graph.relationships
    )
    assert all(
        not hasattr(relationship, "source_account_id")
        for relationship in graph.relationships
    )
    for result in snapshot["data"]["results"]:
        if result["query_key"] == "structure_snapshot":
            for row in result["rows"]:
                assert all(
                    "source_id" in relationship
                    and "source_account_id" not in relationship
                    for relationship in row["relationships"]
                )


def _artifact(fixture_root: Path) -> dict[str, Any]:
    return load_json(fixture_root / "verifier" / "verification_results.json")


def _relationship(artifact: dict[str, Any]) -> dict[str, Any]:
    return next(iter(artifact["data"]["graph_updates"]["relationships"]))


def _validate(artifact: dict[str, Any]) -> None:
    validate_schema(
        artifact, SCHEMA_DIRECTORY / "input/verification_results.schema.json"
    )
    validate_verification_results_semantics(artifact)
