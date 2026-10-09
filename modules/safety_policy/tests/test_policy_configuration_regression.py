"""Public run and CLI regression tests for the policy configuration lifecycle."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from modules.safety_policy import config_adapter, entrypoint
from modules.safety_policy.config_adapter import (
    POLICY_CONFIG_PATH_ENV,
    POLICY_CONFIG_RELATIVE_PATH,
    prepare_policy_configuration,
)
from modules.safety_policy.contracts import load_safety_decisions
from modules.safety_policy.models import (
    ApprovalRecord,
    EvaluationInput,
    PolicyConfiguration,
    SafetyDecisionsData,
)
from modules.safety_policy.utils import atomic_writer
from modules.safety_policy.utils.hashing import calculate_sha256
from modules.safety_policy.utils.validation import load_json

SOURCE_FAILURES = (
    "unset",
    "missing",
    "directory",
    "invalid_json",
    "invalid_utf8",
    "wrong_schema_version",
    "unknown_field",
    "read_failure",
)


@dataclass(frozen=True)
class PublicEvaluation:
    input_paths: dict[str, Any]
    output_dir: str
    context: dict[str, Any]
    execution_method: str
    capture: pytest.CaptureFixture[str]

    @property
    def run_root(self) -> Path:
        return Path(self.context["run_root"])

    @property
    def input_path(self) -> Path:
        return self.run_root / self.input_paths["test_scenarios"]["path"]

    @property
    def policy_path(self) -> Path:
        return self.run_root / POLICY_CONFIG_RELATIVE_PATH

    @property
    def output_path(self) -> Path:
        return self.run_root / self.output_dir / "safety_decisions.json"

    def invoke(self) -> dict[str, Any]:
        if self.execution_method == "function":
            return entrypoint.run(
                "evaluate", self.input_paths, self.output_dir, self.context
            )
        arguments = [
            "evaluate",
            "--run-root", str(self.run_root),
            "--run-id", self.context["run_id"],
            "--iteration", str(self.context["iteration"]),
            "--mode", self.context["mode"],
            "--output-dir", self.output_dir,
        ]
        approval = self.context.get("approval_record")
        if approval is not None:
            arguments.extend(["--approval-record", approval["path"]])
        exit_code = entrypoint.main(arguments)
        response = json.loads(self.capture.readouterr().out)
        assert exit_code == (
            0 if response["status"] in {"completed", "partial"} else 1
        )
        return response


@pytest.fixture(params=("function", "cli"))
def public_evaluation(
    request: pytest.FixtureRequest,
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    capsys: pytest.CaptureFixture[str],
) -> PublicEvaluation:
    input_paths, output_dir, context = evaluate_arguments
    return PublicEvaluation(input_paths, output_dir, context, request.param, capsys)


@pytest.mark.parametrize("source_failure", SOURCE_FAILURES)
def test_invalid_policy_source_fails_before_snapshot_and_output(
    public_evaluation: PublicEvaluation,
    policy_source_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_failure: str,
) -> None:
    original_input = public_evaluation.input_path.read_bytes()
    _invalidate_policy_source(policy_source_path, source_failure, monkeypatch)

    response = public_evaluation.invoke()

    _assert_control_failure(response, "CONFIG_INVALID")
    assert not public_evaluation.policy_path.exists()
    assert not public_evaluation.output_path.exists()
    assert public_evaluation.input_path.read_bytes() == original_input


@pytest.mark.parametrize("source_failure", ("unset", "missing", "invalid_json"))
def test_invalid_source_does_not_fallback_to_prepared_policy(
    public_evaluation: PublicEvaluation,
    policy_source_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_failure: str,
) -> None:
    prepared = prepare_policy_configuration(public_evaluation.run_root)
    original_policy = prepared.path.read_bytes()
    original_stat = prepared.path.stat()
    _invalidate_policy_source(policy_source_path, source_failure, monkeypatch)

    response = public_evaluation.invoke()

    _assert_control_failure(response, "CONFIG_INVALID")
    assert not public_evaluation.output_path.exists()
    _assert_snapshot_unchanged(prepared.path, original_policy, original_stat.st_mtime_ns)


def test_policy_snapshot_directory_is_configuration_failure(
    public_evaluation: PublicEvaluation,
) -> None:
    public_evaluation.policy_path.mkdir()

    response = public_evaluation.invoke()

    _assert_control_failure(response, "CONFIG_INVALID")
    assert public_evaluation.policy_path.is_dir()
    assert list(public_evaluation.policy_path.iterdir()) == []
    assert not public_evaluation.output_path.exists()


@pytest.mark.parametrize("target_kind", ("outside_run", "other_module"))
def test_policy_snapshot_symlink_is_rejected_without_changing_target(
    public_evaluation: PublicEvaluation,
    policy_source_path: Path,
    target_kind: str,
) -> None:
    target_path = (
        public_evaluation.run_root.parent / "external_policy.json"
        if target_kind == "outside_run"
        else public_evaluation.run_root / "private" / "collector" / "policy.json"
    )
    original_policy = policy_source_path.read_bytes()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(original_policy)
    original_modified_at = target_path.stat().st_mtime_ns
    public_evaluation.policy_path.symlink_to(target_path)

    response = public_evaluation.invoke()

    _assert_control_failure(response, "PATH_INVALID")
    assert public_evaluation.policy_path.is_symlink()
    _assert_snapshot_unchanged(target_path, original_policy, original_modified_at)
    assert not public_evaluation.output_path.exists()


@pytest.mark.parametrize("snapshot_damage", ("changed_limit", "invalid_json"))
def test_changed_snapshot_is_not_repaired_or_used_for_evaluation(
    public_evaluation: PublicEvaluation,
    snapshot_damage: str,
) -> None:
    prepared = prepare_policy_configuration(public_evaluation.run_root)
    if snapshot_damage == "changed_limit":
        configuration = load_json(prepared.path)
        configuration["limits"]["max_requests"] += 1
        changed_bytes = json.dumps(configuration).encode("utf-8")
    else:
        changed_bytes = b"invalid policy snapshot"
    prepared.path.write_bytes(changed_bytes)
    changed_modified_at = prepared.path.stat().st_mtime_ns

    response = public_evaluation.invoke()

    _assert_control_failure(response, "CONFIG_HASH_MISMATCH")
    _assert_snapshot_unchanged(prepared.path, changed_bytes, changed_modified_at)
    assert not public_evaluation.output_path.exists()


def test_snapshot_storage_failure_is_retryable_and_cleans_temporary_file(
    public_evaluation: PublicEvaluation,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(atomic_writer.os, "fsync", _fail_flush)

    response = public_evaluation.invoke()

    _assert_control_failure(response, "STORAGE_FAILED", is_retryable=True)
    assert not public_evaluation.policy_path.exists()
    assert list(public_evaluation.policy_path.parent.glob(".policy.json.*.tmp")) == []
    assert not public_evaluation.output_path.exists()


def test_existing_snapshot_read_failure_is_retryable_without_output(
    public_evaluation: PublicEvaluation,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = prepare_policy_configuration(public_evaluation.run_root)
    original_policy = prepared.path.read_bytes()
    original_modified_at = prepared.path.stat().st_mtime_ns

    def fail_hash(path: Path) -> str:
        raise PermissionError("fixture policy snapshot read failure")

    monkeypatch.setattr(config_adapter, "calculate_sha256", fail_hash)

    response = public_evaluation.invoke()

    _assert_control_failure(response, "STORAGE_FAILED", is_retryable=True)
    _assert_snapshot_unchanged(prepared.path, original_policy, original_modified_at)
    assert not public_evaluation.output_path.exists()


def test_output_storage_failure_preserves_reused_policy_and_cleans_temp(
    public_evaluation: PublicEvaluation,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = prepare_policy_configuration(public_evaluation.run_root)
    original_policy = prepared.path.read_bytes()
    original_modified_at = prepared.path.stat().st_mtime_ns
    original_input = public_evaluation.input_path.read_bytes()
    monkeypatch.setattr(atomic_writer.os, "fsync", _fail_flush)

    response = public_evaluation.invoke()

    _assert_control_failure(response, "STORAGE_FAILED", is_retryable=True)
    assert not public_evaluation.output_path.exists()
    assert list(public_evaluation.output_path.parent.glob(".safety_decisions.json.*.tmp")) == []
    _assert_snapshot_unchanged(prepared.path, original_policy, original_modified_at)
    assert public_evaluation.input_path.read_bytes() == original_input


@pytest.mark.parametrize("changed_source", ("scenarios", "policy", "approval"))
def test_file_change_after_evaluation_blocks_publication(
    public_evaluation: PublicEvaluation,
    monkeypatch: pytest.MonkeyPatch,
    changed_source: str,
) -> None:
    if changed_source == "approval":
        _attach_fixture_approval(public_evaluation)
    original_evaluate_policy = entrypoint.evaluate_policy

    def change_file_after_evaluation(
        prepared: EvaluationInput,
        configuration: PolicyConfiguration,
        approval_record: ApprovalRecord | None = None,
    ) -> SafetyDecisionsData:
        data = original_evaluate_policy(prepared, configuration, approval_record)
        path_by_source = {
            "scenarios": prepared.request.input_path,
            "policy": prepared.request.policy_config_path,
            "approval": prepared.request.approval_record_path,
        }
        source_path = path_by_source[changed_source]
        assert source_path is not None
        source_path.write_bytes(source_path.read_bytes() + b"\n")
        return data

    monkeypatch.setattr(entrypoint, "evaluate_policy", change_file_after_evaluation)

    response = public_evaluation.invoke()

    _assert_control_failure(response, "OUTPUT_INVALID")
    assert not public_evaluation.output_path.exists()


@pytest.mark.parametrize(
    "source_kind", ("completed", "partial", "completed_empty", "partial_empty")
)
def test_policy_reuse_preserves_valid_status_errors_and_decision_count(
    public_evaluation: PublicEvaluation,
    fixture_root: Path,
    source_kind: str,
) -> None:
    source = load_json(public_evaluation.input_path)
    source["status"] = "partial" if source_kind.startswith("partial") else "completed"
    source["errors"] = (
        load_json(fixture_root / "status/test_scenarios.partial.json")["errors"]
        if source["status"] == "partial"
        else []
    )
    if source_kind.endswith("empty"):
        source["data"]["scenarios"] = []
    _store_scenarios(public_evaluation, source)
    source_bytes = public_evaluation.input_path.read_bytes()
    source_sha256 = calculate_sha256(public_evaluation.input_path)
    prepared = prepare_policy_configuration(public_evaluation.run_root)
    original_policy = prepared.path.read_bytes()
    original_stat = prepared.path.stat()

    response = public_evaluation.invoke()

    artifact, decisions = load_safety_decisions(public_evaluation.output_path)
    assert response["status"] == artifact["status"] == source["status"]
    assert artifact["errors"] == response["errors"] == source["errors"]
    assert artifact["data"]["scenarios_sha256"] == source_sha256
    assert artifact["input_refs"] == [
        {
            "artifact_id": source["artifact_id"],
            "artifact_type": source["artifact_type"],
            "iteration": source["iteration"],
            "path": public_evaluation.input_paths["test_scenarios"]["path"],
            "sha256": source_sha256,
        }
    ]
    assert response["sha256"] == calculate_sha256(public_evaluation.output_path)
    assert decisions is not None
    assert len(decisions.decisions) == len(source["data"]["scenarios"])
    assert {item.scenario_id for item in decisions.decisions} == {
        item["scenario_id"] for item in source["data"]["scenarios"]
    }
    assert public_evaluation.input_path.read_bytes() == source_bytes
    _assert_snapshot_unchanged(prepared.path, original_policy, original_stat.st_mtime_ns)
    assert prepared.path.stat().st_ino == original_stat.st_ino


@pytest.mark.parametrize(
    "scenario_failure",
    (
        "failed_status",
        "wrong_schema_version",
        "run_mismatch",
        "iteration_mismatch",
        "missing_resource_ids",
        "empty_resource_ids",
    ),
)
def test_policy_reuse_does_not_hide_failed_or_invalid_scenarios(
    public_evaluation: PublicEvaluation,
    fixture_root: Path,
    scenario_failure: str,
) -> None:
    prepared = prepare_policy_configuration(public_evaluation.run_root)
    original_policy = prepared.path.read_bytes()
    original_modified_at = prepared.path.stat().st_mtime_ns
    source = load_json(public_evaluation.input_path)
    if scenario_failure == "failed_status":
        source = load_json(fixture_root / "status/test_scenarios.failed.json")
    elif scenario_failure == "wrong_schema_version":
        source["schema_version"] = "0.1.0"
    elif scenario_failure == "run_mismatch":
        source["run_id"] = "other_run"
    elif scenario_failure == "iteration_mismatch":
        source["iteration"] += 1
    else:
        for scenario in source["data"]["scenarios"]:
            if scenario_failure == "missing_resource_ids":
                scenario.pop("resource_ids")
            else:
                scenario["resource_ids"] = []
    _store_scenarios(public_evaluation, source)
    original_input = public_evaluation.input_path.read_bytes()

    response = public_evaluation.invoke()

    code = "INPUT_STATUS_FAILED" if scenario_failure == "failed_status" else "CONTRACT_INVALID"
    _assert_control_failure(response, code)
    assert not public_evaluation.output_path.exists()
    assert public_evaluation.input_path.read_bytes() == original_input
    _assert_snapshot_unchanged(prepared.path, original_policy, original_modified_at)


def _store_scenarios(evaluation: PublicEvaluation, source: dict[str, Any]) -> None:
    evaluation.input_path.write_text(json.dumps(source), encoding="utf-8")
    evaluation.input_paths["test_scenarios"]["sha256"] = calculate_sha256(
        evaluation.input_path
    )


def _invalidate_policy_source(
    source_path: Path,
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if failure == "unset":
        monkeypatch.delenv(POLICY_CONFIG_PATH_ENV)
    elif failure == "missing":
        monkeypatch.setenv(
            POLICY_CONFIG_PATH_ENV,
            str(source_path.with_name("missing.json")),
        )
    elif failure == "directory":
        monkeypatch.setenv(POLICY_CONFIG_PATH_ENV, str(source_path.parent))
    elif failure in {"invalid_json", "invalid_utf8"}:
        source_path.write_bytes(b"{" if failure == "invalid_json" else b"\xff")
    elif failure in {"wrong_schema_version", "unknown_field"}:
        value = load_json(source_path)
        if failure == "wrong_schema_version":
            value["schema_version"] = "0.2.0"
        else:
            value["unknown_field"] = True
        source_path.write_text(json.dumps(value), encoding="utf-8")
    elif failure == "read_failure":
        original_read_bytes = Path.read_bytes

        def fail_source_read(path: Path) -> bytes:
            if path == source_path:
                raise PermissionError("fixture policy source read failure")
            return original_read_bytes(path)

        monkeypatch.setattr(Path, "read_bytes", fail_source_read)
    else:
        raise AssertionError(f"unsupported source failure fixture: {failure}")


def _assert_control_failure(
    response: dict[str, Any],
    code: str,
    is_retryable: bool = False,
) -> None:
    assert response["operation"] == "evaluate"
    assert response["status"] == "failed"
    assert response["artifact_id"] is None
    assert response["output_path"] is None
    assert response["sha256"] is None
    error, = response["errors"]
    assert error["code"] == code
    assert error["retryable"] is is_retryable


def _assert_snapshot_unchanged(
    path: Path,
    content: bytes,
    modified_at: int,
) -> None:
    assert path.read_bytes() == content
    assert path.stat().st_mtime_ns == modified_at


def _attach_fixture_approval(evaluation: PublicEvaluation) -> None:
    relative_path = "private/safety_policy/approvals/approval_demo_001.json"
    evaluation.context["approval_record"] = {
        "path": relative_path,
        "sha256": calculate_sha256(evaluation.run_root / relative_path),
    }


def _fail_flush(descriptor: int) -> None:
    raise OSError("fixture storage failure")
