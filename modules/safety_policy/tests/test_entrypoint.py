import json
from pathlib import Path
from shutil import copyfile
from typing import Any

import pytest

from modules.safety_policy import entrypoint
from modules.safety_policy.contracts import load_safety_decisions
from modules.safety_policy.utils.hashing import calculate_sha256
from modules.safety_policy.utils.validation import load_json


def test_run_evaluate_writes_valid_completed_artifact(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    evaluate_run_root: Path,
) -> None:
    response = entrypoint.run("evaluate", *evaluate_arguments)

    output_path = _output_path(evaluate_run_root)
    artifact, decisions = load_safety_decisions(output_path)
    _assert_output_integrity(evaluate_run_root, artifact, response)
    assert response["status"] == "completed"
    assert response["output_path"].endswith("safety_decisions.json")
    assert len(response["sha256"]) == 64
    assert artifact["data"]["policy_id"] == "abc2lab-fixture-policy"
    assert len(artifact["input_refs"]) == 1
    assert "policy_config_sha256" not in artifact["data"]
    assert decisions is not None
    decision_by_scenario = {
        decision.scenario_id: decision for decision in decisions.decisions
    }
    assert decision_by_scenario["scenario_read_order_001"].decision == "allow"
    assert (
        decision_by_scenario["scenario_update_order_001"].decision
        == "require_approval"
    )


def test_run_evaluate_reissues_allow_after_verified_approval(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    evaluate_run_root: Path,
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    approval_relative = (
        "private/safety_policy/approvals/approval_demo_001.json"
    )
    approval_path = evaluate_run_root / approval_relative
    context["approval_record"] = {
        "path": approval_relative,
        "sha256": calculate_sha256(approval_path),
    }

    response = entrypoint.run("evaluate", input_paths, output_dir, context)

    artifact = load_json(_output_path(evaluate_run_root))
    _assert_output_integrity(evaluate_run_root, artifact, response)
    decisions = {
        item["scenario_id"]: item for item in artifact["data"]["decisions"]
    }
    approved = decisions["scenario_update_order_001"]
    assert response["status"] == "completed"
    assert approved["decision"] == "allow"
    assert approved["approval_ref"] == "approval_demo_001"
    assert approved["limits"]["allow_state_change"] is True
    assert all(
        item["status"] == "pass"
        for item in approved["assessment"].values()
    )


def test_run_evaluate_preserves_partial_source_errors(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    evaluate_run_root: Path,
    fixture_root: Path,
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    input_path = _input_path(evaluate_run_root)
    copyfile(fixture_root / "status/test_scenarios.partial.json", input_path)
    input_paths["test_scenarios"]["sha256"] = calculate_sha256(input_path)

    response = entrypoint.run("evaluate", input_paths, output_dir, context)

    artifact = load_json(_output_path(evaluate_run_root))
    _assert_output_integrity(evaluate_run_root, artifact, response)
    assert response["status"] == "partial"
    assert artifact["status"] == "partial"
    assert artifact["errors"][0]["code"] == "SCENARIO_GENERATION_PARTIAL"
    assert artifact["data"]["decisions"] == []


def test_run_evaluate_rejects_failed_source_without_output(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    evaluate_run_root: Path,
    fixture_root: Path,
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    input_path = _input_path(evaluate_run_root)
    copyfile(fixture_root / "status/test_scenarios.failed.json", input_path)
    input_paths["test_scenarios"]["sha256"] = calculate_sha256(input_path)

    response = entrypoint.run("evaluate", input_paths, output_dir, context)

    assert response["status"] == "failed"
    assert response["output_path"] is None
    assert response["errors"][0]["code"] == "INPUT_STATUS_FAILED"
    assert not _output_path(evaluate_run_root).exists()


@pytest.mark.parametrize("schema_version", ["0.1.0", "0.3.0"])
def test_run_evaluate_rejects_unsupported_version_without_output(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    evaluate_run_root: Path,
    schema_version: str,
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    input_path = _input_path(evaluate_run_root)
    artifact = load_json(input_path)
    artifact["schema_version"] = schema_version
    input_path.write_text(json.dumps(artifact), encoding="utf-8")
    input_paths["test_scenarios"]["sha256"] = calculate_sha256(input_path)

    response = entrypoint.run("evaluate", input_paths, output_dir, context)

    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "CONTRACT_INVALID"
    assert response["output_path"] is None
    assert not _output_path(evaluate_run_root).exists()


def test_run_evaluate_rejects_input_hash_mismatch_without_output(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    evaluate_run_root: Path,
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    input_paths["test_scenarios"]["sha256"] = "0" * 64

    response = entrypoint.run("evaluate", input_paths, output_dir, context)

    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "INPUT_HASH_MISMATCH"
    assert not _output_path(evaluate_run_root).exists()


def test_run_evaluate_publishes_failed_artifact_for_invalid_policy(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    evaluate_run_root: Path,
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    policy_path = _policy_path(evaluate_run_root)
    policy = load_json(policy_path)
    policy["unexpected"] = True
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    context["policy_config"]["sha256"] = calculate_sha256(policy_path)

    response = entrypoint.run("evaluate", input_paths, output_dir, context)

    artifact = load_json(_output_path(evaluate_run_root))
    _assert_output_integrity(evaluate_run_root, artifact, response)
    assert response["status"] == "failed"
    assert response["output_path"] is not None
    assert artifact["data"] is None
    assert artifact["errors"][0]["code"] == "CONFIG_INVALID"


def test_run_evaluate_publishes_failed_artifact_for_invalid_policy_origin(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    evaluate_run_root: Path,
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    policy_path = _policy_path(evaluate_run_root)
    policy = load_json(policy_path)
    policy["allowed_targets"][0]["origin"] = "not-an-origin"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    context["policy_config"]["sha256"] = calculate_sha256(policy_path)

    response = entrypoint.run("evaluate", input_paths, output_dir, context)

    artifact = load_json(_output_path(evaluate_run_root))
    assert response["status"] == "failed"
    assert artifact["errors"][0]["code"] == "CONFIG_INVALID"


def test_run_evaluate_publishes_failed_artifact_for_policy_hash_mismatch(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    evaluate_run_root: Path,
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    context["policy_config"]["sha256"] = "0" * 64

    response = entrypoint.run("evaluate", input_paths, output_dir, context)

    artifact = load_json(_output_path(evaluate_run_root))
    assert response["status"] == "failed"
    assert artifact["errors"][0]["code"] == "CONFIG_HASH_MISMATCH"


def test_run_evaluate_publishes_failed_artifact_for_approval_hash_mismatch(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    evaluate_run_root: Path,
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    context["approval_record"] = {
        "path": "private/safety_policy/approvals/approval_demo_001.json",
        "sha256": "0" * 64,
    }

    response = entrypoint.run("evaluate", input_paths, output_dir, context)

    artifact = load_json(_output_path(evaluate_run_root))
    assert response["status"] == "failed"
    assert artifact["data"] is None
    assert artifact["errors"][0]["code"] == "APPROVAL_HASH_MISMATCH"


def test_run_evaluate_rejects_approval_for_unknown_impact(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    evaluate_run_root: Path,
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    policy_path = _policy_path(evaluate_run_root)
    policy = load_json(policy_path)
    policy["request_rules"] = []
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    context["policy_config"]["sha256"] = calculate_sha256(policy_path)
    approval_path = _approval_path(evaluate_run_root)
    approval = load_json(approval_path)
    approval["approved_scenario_ids"] = ["scenario_read_order_001"]
    approval_path.write_text(json.dumps(approval), encoding="utf-8")
    context["approval_record"] = {
        "path": "private/safety_policy/approvals/approval_demo_001.json",
        "sha256": calculate_sha256(approval_path),
    }

    response = entrypoint.run("evaluate", input_paths, output_dir, context)

    artifact = load_json(_output_path(evaluate_run_root))
    assert response["status"] == "failed"
    assert artifact["errors"][0]["code"] == "APPROVAL_INVALID"


def test_run_evaluate_never_overwrites_completed_artifact(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    evaluate_run_root: Path,
) -> None:
    output_path = _output_path(evaluate_run_root)
    output_path.parent.mkdir(parents=True)
    output_path.write_text("existing", encoding="utf-8")

    response = entrypoint.run("evaluate", *evaluate_arguments)

    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "OUTPUT_EXISTS"
    assert output_path.read_text(encoding="utf-8") == "existing"


def test_run_evaluate_reports_storage_failure_without_artifact(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    evaluate_run_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_publication(*_: object) -> None:
        raise OSError("fixture storage failure")

    monkeypatch.setattr(
        entrypoint,
        "publish_evaluation_artifact",
        fail_publication,
    )

    response = entrypoint.run("evaluate", *evaluate_arguments)

    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "STORAGE_FAILED"
    assert response["errors"][0]["retryable"] is True
    assert not _output_path(evaluate_run_root).exists()


def test_run_rejects_unsupported_operation_without_output(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    evaluate_run_root: Path,
) -> None:
    response = entrypoint.run("unknown", *evaluate_arguments)

    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "OPERATION_UNSUPPORTED"
    assert not _output_path(evaluate_run_root).exists()


def test_cli_runs_evaluate_with_contract_paths(
    evaluate_run_root: Path,
    capsys: Any,
) -> None:
    exit_code = entrypoint.main(
        [
            "evaluate",
            "--run-root",
            str(evaluate_run_root),
            "--run-id",
            "run_demo_001",
            "--iteration",
            "0",
            "--mode",
            "development",
        ]
    )

    response = json.loads(capsys.readouterr().out)
    artifact = load_json(_output_path(evaluate_run_root))
    _assert_output_integrity(evaluate_run_root, artifact, response)
    assert exit_code == 0
    assert response["status"] == "completed"
    assert _output_path(evaluate_run_root).exists()


def test_cli_accepts_private_approval_record(
    evaluate_run_root: Path,
    capsys: Any,
) -> None:
    exit_code = entrypoint.main(
        [
            "evaluate",
            "--run-root",
            str(evaluate_run_root),
            "--run-id",
            "run_demo_001",
            "--iteration",
            "0",
            "--mode",
            "development",
            "--approval-record",
            "private/safety_policy/approvals/approval_demo_001.json",
        ]
    )

    response = json.loads(capsys.readouterr().out)
    artifact = load_json(_output_path(evaluate_run_root))
    _assert_output_integrity(evaluate_run_root, artifact, response)
    approved = next(
        item
        for item in artifact["data"]["decisions"]
        if item["scenario_id"] == "scenario_update_order_001"
    )
    assert exit_code == 0
    assert response["status"] == "completed"
    assert approved["approval_ref"] == "approval_demo_001"


def _assert_output_integrity(
    run_root: Path,
    artifact: dict[str, Any],
    response: dict[str, Any],
) -> None:
    input_path = _input_path(run_root)
    source = load_json(input_path)
    source_sha256 = calculate_sha256(input_path)

    assert artifact["schema_version"] == "0.2.0"
    assert artifact["input_refs"] == [
        {
            "artifact_id": source["artifact_id"],
            "artifact_type": source["artifact_type"],
            "iteration": source["iteration"],
            "path": input_path.relative_to(run_root).as_posix(),
            "sha256": source_sha256,
        }
    ]
    assert response["sha256"] == calculate_sha256(_output_path(run_root))
    if artifact["data"] is not None:
        configuration = load_json(_policy_path(run_root))
        assert artifact["data"]["scenarios_sha256"] == source_sha256
        assert artifact["data"]["policy_id"] == configuration["policy_id"]
        assert artifact["data"]["policy_version"] == configuration["policy_version"]


def _input_path(run_root: Path) -> Path:
    return run_root / (
        "artifacts/iteration-000/scenario_generator/test_scenarios.json"
    )


def _policy_path(run_root: Path) -> Path:
    return run_root / "private/safety_policy/policy.json"


def _approval_path(run_root: Path) -> Path:
    return run_root / (
        "private/safety_policy/approvals/approval_demo_001.json"
    )


def _output_path(run_root: Path) -> Path:
    return run_root / (
        "artifacts/iteration-000/safety_policy/safety_decisions.json"
    )
