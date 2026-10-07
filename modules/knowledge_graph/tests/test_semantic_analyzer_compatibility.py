import copy
import json
from pathlib import Path
from typing import Any

import pytest

from modules.knowledge_graph.exceptions import ContractValidationError
from modules.knowledge_graph.models import SemanticGraph
from modules.knowledge_graph.query_results import graph_to_snapshot
from modules.knowledge_graph.service import SCHEMA_DIRECTORY, prepare_ingest
from modules.knowledge_graph.utils.validation import load_json, validate_schema


CURRENT_SEMANTIC_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "semantic_analyzer_current"
    / "semantic_analysis.json"
)


def test_current_semantic_output_matches_kg_json_schema() -> None:
    artifact = load_json(CURRENT_SEMANTIC_FIXTURE)

    validate_schema(
        artifact,
        SCHEMA_DIRECTORY / "input" / "semantic_analysis.schema.json",
    )

    assert artifact["schema_version"] == "0.1.0"
    assert artifact["producer"] == "semantic_analyzer"


def test_current_semantic_output_preserves_new_identity_and_access_shape() -> None:
    data = load_json(CURRENT_SEMANTIC_FIXTURE)["data"]
    request = data["normalized_requests"][0]
    node_by_id = {item["node_id"]: item for item in data["nodes"]}
    access_edges = [
        item
        for item in data["relationships"]
        if item["relation_type"] == "ACCESS"
    ]

    assert request["account_id"] == "acc_alice"
    assert request["role_id"] == "role_user"
    assert node_by_id["user:acc_alice"]["properties"]["account_id"] == "acc_alice"
    assert node_by_id["role:role_user"]["properties"]["role_id"] == "role_user"
    assert data["workflows"][0]["role_ids"] == ["role_user"]
    assert {item["source_id"] for item in access_edges} == {
        "role:role_user",
        "user:acc_alice",
    }
    assert all(item["properties"] == {} for item in access_edges)
    assert "action" not in node_by_id[request["endpoint_id"]]["properties"]
    assert request["action_meaning"] == "read_order"


def test_prepare_ingest_accepts_current_semantic_output() -> None:
    artifact, graph = prepare_ingest(CURRENT_SEMANTIC_FIXTURE)

    assert artifact["artifact_id"] == "semantic_compatibility_001"
    assert isinstance(graph, SemanticGraph)
    assert len(graph.request_observations) == 1
    assert len(graph.nodes) == 5
    assert len(graph.relationships) == 6
    assert len(graph.workflows) == 1
    observation = graph.request_observations[0]
    assert observation.request_id == "request_order_alice"
    assert observation.account_id == "acc_alice"
    assert observation.role_id == "role_user"
    assert observation.user_node_id == "user:acc_alice"
    assert observation.role_node_id == "role:role_user"
    assert observation.endpoint_id == "endpoint:GET:/orders/{id}"
    assert observation.action == "read_order"
    assert observation.resource_ids == ("resource:order",)

    snapshot = graph_to_snapshot(graph, include_evidence_refs=True)
    assert set(snapshot) == {"nodes", "relationships", "workflows"}


@pytest.mark.parametrize(
    ("node_index", "property_name", "error_pattern"),
    [
        (0, "account_id", "User 노드의 account_id"),
        (1, "role_id", "Role 노드의 role_id"),
    ],
)
def test_ingest_rejects_node_without_original_id(
    tmp_path: Path,
    node_index: int,
    property_name: str,
    error_pattern: str,
) -> None:
    artifact = load_json(CURRENT_SEMANTIC_FIXTURE)
    del artifact["data"]["nodes"][node_index]["properties"][property_name]

    with pytest.raises(ContractValidationError, match=error_pattern):
        prepare_ingest(_write_artifact(tmp_path, artifact))


@pytest.mark.parametrize(
    ("node_index", "duplicate_node_id", "error_pattern"),
    [
        (0, "user:duplicate", "중복 User 원본 account_id"),
        (1, "role:duplicate", "중복 Role 원본 role_id"),
    ],
)
def test_ingest_rejects_duplicate_original_id(
    tmp_path: Path,
    node_index: int,
    duplicate_node_id: str,
    error_pattern: str,
) -> None:
    artifact = load_json(CURRENT_SEMANTIC_FIXTURE)
    duplicate_node = copy.deepcopy(artifact["data"]["nodes"][node_index])
    duplicate_node["node_id"] = duplicate_node_id
    artifact["data"]["nodes"].append(duplicate_node)

    with pytest.raises(ContractValidationError, match=error_pattern):
        prepare_ingest(_write_artifact(tmp_path, artifact))


def test_ingest_rejects_unknown_original_request_account_id(
    tmp_path: Path,
) -> None:
    artifact = load_json(CURRENT_SEMANTIC_FIXTURE)
    artifact["data"]["normalized_requests"][0]["account_id"] = "acc_missing"

    with pytest.raises(ContractValidationError, match="원본 account_id"):
        prepare_ingest(_write_artifact(tmp_path, artifact))


def test_ingest_rejects_request_role_not_assigned_to_account(
    tmp_path: Path,
) -> None:
    artifact = load_json(CURRENT_SEMANTIC_FIXTURE)
    admin_role = copy.deepcopy(artifact["data"]["nodes"][1])
    admin_role["node_id"] = "role:role_admin"
    admin_role["properties"] = {"name": "admin", "role_id": "role_admin"}
    artifact["data"]["nodes"].append(admin_role)
    artifact["data"]["normalized_requests"][0]["role_id"] = "role_admin"

    with pytest.raises(ContractValidationError, match="account_id와 role_id 연결"):
        prepare_ingest(_write_artifact(tmp_path, artifact))


def test_ingest_rejects_user_without_has_role_relationship(
    tmp_path: Path,
) -> None:
    artifact = load_json(CURRENT_SEMANTIC_FIXTURE)
    artifact["data"]["relationships"] = [
        item
        for item in artifact["data"]["relationships"]
        if item["relation_type"] != "HAS_ROLE"
    ]

    with pytest.raises(ContractValidationError, match="HAS_ROLE 관계가 없음"):
        prepare_ingest(_write_artifact(tmp_path, artifact))


def test_ingest_rejects_has_role_not_matching_user_role(tmp_path: Path) -> None:
    artifact = load_json(CURRENT_SEMANTIC_FIXTURE)
    admin_role = copy.deepcopy(artifact["data"]["nodes"][1])
    admin_role["node_id"] = "role:role_admin"
    admin_role["properties"] = {"name": "admin", "role_id": "role_admin"}
    artifact["data"]["nodes"].append(admin_role)
    artifact["data"]["relationships"][0]["target_id"] = "role:role_admin"

    with pytest.raises(ContractValidationError, match="User 원본 role_id"):
        prepare_ingest(_write_artifact(tmp_path, artifact))


def test_ingest_rejects_duplicate_has_role_relationship(tmp_path: Path) -> None:
    artifact = load_json(CURRENT_SEMANTIC_FIXTURE)
    duplicate_relationship = copy.deepcopy(artifact["data"]["relationships"][0])
    duplicate_relationship["relationship_id"] = "rel:duplicate_has_role"
    artifact["data"]["relationships"].append(duplicate_relationship)

    with pytest.raises(ContractValidationError, match="중복 User-Role HAS_ROLE"):
        prepare_ingest(_write_artifact(tmp_path, artifact))


def test_ingest_rejects_unknown_original_workflow_role_id(
    tmp_path: Path,
) -> None:
    artifact = load_json(CURRENT_SEMANTIC_FIXTURE)
    artifact["data"]["workflows"][0]["role_ids"] = ["role_missing"]

    with pytest.raises(ContractValidationError, match="없는 원본 role_id"):
        prepare_ingest(_write_artifact(tmp_path, artifact))


def _write_artifact(tmp_path: Path, artifact: dict[str, Any]) -> Path:
    input_path = tmp_path / "semantic_analysis.json"
    input_path.write_text(
        json.dumps(artifact, ensure_ascii=False),
        encoding="utf-8",
    )
    return input_path
