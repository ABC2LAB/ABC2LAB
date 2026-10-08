import json
from pathlib import Path

import pytest

from modules.knowledge_graph.exceptions import (
    ContractValidationError,
    InputArtifactFailedError,
    InputHashMismatchError,
)
from modules.knowledge_graph.models import (
    VerificationRequest,
    VerificationState,
    VerificationUpdate,
)
from modules.knowledge_graph.service import (
    execute_verification,
    prepare_verification_operation,
)
from modules.knowledge_graph.utils.hashing import calculate_sha256


INPUT_PATH = "artifacts/iteration-000/verifier/verification_results.json"


class FakeVerificationRepository:
    def __init__(self, state: VerificationState) -> None:
        self.state = state
        self.update: VerificationUpdate | None = None

    def apply_verification(
        self,
        graph_id: str,
        run_id: str,
        update: VerificationUpdate,
    ) -> VerificationState:
        self.update = update
        return self.state


def test_prepare_and_execute_verification(
    verification_run_root: Path,
) -> None:
    prepared = prepare_verification_operation(_request(verification_run_root))
    repository = FakeVerificationRepository(
        VerificationState(
            graph_id="graph_demo_001",
            previous_graph_revision=1,
            graph_revision=2,
            applied_verification_ids=("verification_001",),
            is_applied=True,
        )
    )

    response = execute_verification(
        prepared,
        repository,  # type: ignore[arg-type]
    )

    assert response.status == "completed"
    assert response.previous_graph_revision == 1
    assert response.graph_revision == 2
    assert response.is_applied is True
    assert repository.update is not None
    assert repository.update.source_graph_revision == 1
    assert repository.update.verification_ids == ("verification_001",)
    assert repository.update.relationships[0].basis == "verified"
    assert repository.update.relationships[0].target_id == "resource_order_001"


def test_prepare_verification_rejects_legacy_version(
    verification_run_root: Path,
) -> None:
    input_path = verification_run_root / INPUT_PATH
    artifact = json.loads(input_path.read_text(encoding="utf-8"))
    artifact["schema_version"] = "0.1.0"
    input_path.write_text(json.dumps(artifact), encoding="utf-8")

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        prepare_verification_operation(_request(verification_run_root))


def test_prepare_verification_rejects_new_resource_node(
    verification_run_root: Path,
) -> None:
    input_path = verification_run_root / INPUT_PATH
    artifact = json.loads(input_path.read_text(encoding="utf-8"))
    evidence_refs = artifact["data"]["results"][0]["evidence_refs"]
    artifact["data"]["graph_updates"]["nodes"] = [
        {
            "node_id": "resource_new_001",
            "node_type": "Resource",
            "properties": {
                "resource_key": "order",
                "resource_scope": "instance",
                "match_key": {
                    "resource_key": "order",
                    "identifiers": [
                        {"key": "order_id", "value": "new-001"},
                    ],
                },
            },
            "basis": "verified",
            "evidence_refs": evidence_refs,
        }
    ]
    input_path.write_text(json.dumps(artifact), encoding="utf-8")

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        prepare_verification_operation(_request(verification_run_root))


def test_prepare_verification_rejects_non_verification_relationship(
    verification_run_root: Path,
) -> None:
    input_path = verification_run_root / INPUT_PATH
    artifact = json.loads(input_path.read_text(encoding="utf-8"))
    artifact["data"]["graph_updates"]["relationships"][0][
        "relation_type"
    ] = "ACCESS"
    input_path.write_text(json.dumps(artifact), encoding="utf-8")

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        prepare_verification_operation(_request(verification_run_root))


def test_partial_input_errors_are_preserved(
    verification_run_root: Path,
) -> None:
    input_path = verification_run_root / INPUT_PATH
    artifact = json.loads(input_path.read_text(encoding="utf-8"))
    artifact["status"] = "partial"
    artifact["errors"] = [
        {
            "code": "VERIFY_PARTIAL",
            "message": "일부 시나리오 검증 실패",
            "item_ref": "scenario_002",
            "retryable": True,
        }
    ]
    input_path.write_text(json.dumps(artifact), encoding="utf-8")
    prepared = prepare_verification_operation(_request(verification_run_root))
    repository = FakeVerificationRepository(
        VerificationState(
            graph_id="graph_demo_001",
            previous_graph_revision=1,
            graph_revision=2,
            applied_verification_ids=("verification_001",),
            is_applied=True,
        )
    )

    response = execute_verification(
        prepared,
        repository,  # type: ignore[arg-type]
    )

    assert response.status == "partial"
    assert response.errors[0].code == "VERIFY_PARTIAL"


def test_prepare_verification_rejects_hash_mismatch(
    verification_run_root: Path,
) -> None:
    request = _request(verification_run_root)
    invalid_request = VerificationRequest(
        input_path=request.input_path,
        expected_sha256="0" * 64,
        graph_id=request.graph_id,
        run_id=request.run_id,
        iteration=request.iteration,
        mode=request.mode,
    )

    with pytest.raises(InputHashMismatchError):
        prepare_verification_operation(invalid_request)


def test_prepare_verification_rejects_failed_artifact(
    verification_run_root: Path,
) -> None:
    input_path = verification_run_root / INPUT_PATH
    artifact = json.loads(input_path.read_text(encoding="utf-8"))
    artifact["status"] = "failed"
    artifact["errors"] = [
        {
            "code": "VERIFY_FAILED",
            "message": "검증 실패",
            "item_ref": None,
            "retryable": True,
        }
    ]
    artifact["data"] = None
    input_path.write_text(json.dumps(artifact), encoding="utf-8")

    with pytest.raises(InputArtifactFailedError):
        prepare_verification_operation(_request(verification_run_root))


def test_prepare_verification_rejects_unrelated_update_evidence(
    verification_run_root: Path,
) -> None:
    input_path = verification_run_root / INPUT_PATH
    artifact = json.loads(input_path.read_text(encoding="utf-8"))
    artifact["data"]["graph_updates"]["relationships"][0]["evidence_refs"][0][
        "evidence_id"
    ] = "unrelated_evidence"
    input_path.write_text(json.dumps(artifact), encoding="utf-8")

    with pytest.raises(ContractValidationError, match="실행 근거"):
        prepare_verification_operation(_request(verification_run_root))


def test_prepare_verification_allows_empty_graph_update(
    verification_run_root: Path,
) -> None:
    input_path = verification_run_root / INPUT_PATH
    artifact = json.loads(input_path.read_text(encoding="utf-8"))
    graph_updates = artifact["data"]["graph_updates"]
    graph_updates["source_verification_ids"] = []
    graph_updates["nodes"] = []
    graph_updates["relationships"] = []
    input_path.write_text(json.dumps(artifact), encoding="utf-8")

    prepared = prepare_verification_operation(_request(verification_run_root))

    assert prepared.update.verification_ids == ()
    assert prepared.update.nodes == ()
    assert prepared.update.relationships == ()


def _request(run_root: Path) -> VerificationRequest:
    input_path = run_root / INPUT_PATH
    return VerificationRequest(
        input_path=input_path,
        expected_sha256=calculate_sha256(input_path),
        graph_id="graph_demo_001",
        run_id="run_demo_001",
        iteration=0,
        mode="development",
    )
