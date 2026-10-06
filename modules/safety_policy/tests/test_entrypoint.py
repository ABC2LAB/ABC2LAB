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
    assert exit_code == 0
    assert response["status"] == "completed"
    assert _output_path(evaluate_run_root).exists()


def _input_path(run_root: Path) -> Path:
    return run_root / (
        "artifacts/iteration-000/scenario_generator/test_scenarios.json"
    )


def _policy_path(run_root: Path) -> Path:
    return run_root / "private/safety_policy/policy.json"


def _output_path(run_root: Path) -> Path:
    return run_root / (
        "artifacts/iteration-000/safety_policy/safety_decisions.json"
    )
