import copy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from modules.safety_policy.config_adapter import (
    POLICY_CONFIG_PATH_ENV,
    prepare_policy_configuration,
)
from modules.safety_policy.evaluate_adapter import (
    POLICY_CONFIG_RELATIVE_PATH,
    bind_policy_configuration,
    parse_evaluate_request,
    prepare_evaluate_request,
)
from modules.safety_policy.exceptions import ContractValidationError, SafetyPolicyError
from modules.safety_policy.models import EvaluationArguments, EvaluationRequest
from modules.safety_policy.utils.hashing import calculate_sha256


def test_parse_evaluate_request_resolves_contract_paths(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    evaluate_run_root: Path,
) -> None:
    input_paths, output_dir, context = evaluate_arguments

    request = parse_evaluate_request(input_paths, output_dir, context)

    assert request.input_path == (
        evaluate_run_root
        / "artifacts/iteration-000/scenario_generator/test_scenarios.json"
    )
    assert request.input_relative_path.endswith("test_scenarios.json")
    assert request.run_root == evaluate_run_root
    assert not (evaluate_run_root / POLICY_CONFIG_RELATIVE_PATH).exists()
    assert request.output_path == (
        evaluate_run_root
        / "artifacts/iteration-000/safety_policy/safety_decisions.json"
    )
    assert request.output_relative_path.endswith("safety_decisions.json")
    assert request.run_id == "run_demo_001"
    assert request.iteration == 0
    assert request.mode == "development"
    assert request.approval_record_path is None
    assert request.approval_record_expected_sha256 is None
    assert isinstance(request, EvaluationArguments)
    assert not isinstance(request, EvaluationRequest)


def test_parse_evaluate_request_accepts_approval_descriptor(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    evaluate_run_root: Path,
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    relative_path = (
        "private/safety_policy/approvals/approval_demo_001.json"
    )
    approval_path = evaluate_run_root / relative_path
    context["approval_record"] = {
        "path": relative_path,
        "sha256": calculate_sha256(approval_path),
    }

    request = parse_evaluate_request(input_paths, output_dir, context)

    assert request.approval_record_path == approval_path
    assert request.approval_record_expected_sha256 == calculate_sha256(
        approval_path
    )


def test_parse_evaluate_request_accepts_explicitly_absent_approval(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    context["approval_record"] = None

    request = parse_evaluate_request(input_paths, output_dir, context)

    assert request.approval_record_path is None


def test_parse_evaluate_request_accepts_exact_absolute_output_directory(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    evaluate_run_root: Path,
) -> None:
    input_paths, _, context = evaluate_arguments
    output_dir = evaluate_run_root / "artifacts/iteration-000/safety_policy"

    request = parse_evaluate_request(input_paths, output_dir, context)

    assert request.output_path.parent == output_dir


@pytest.mark.parametrize(
    "context_update",
    [
        {"iteration": -1},
        {"iteration": True},
        {"mode": "unsupported"},
        {"run_id": ""},
    ],
)
def test_parse_evaluate_request_rejects_invalid_context_value(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    context_update: dict[str, Any],
) -> None:
    input_paths, output_dir, context = copy.deepcopy(evaluate_arguments)
    context.update(context_update)

    with pytest.raises(ContractValidationError):
        parse_evaluate_request(input_paths, output_dir, context)


def test_parse_evaluate_request_rejects_context_key_change(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
) -> None:
    input_paths, output_dir, context = copy.deepcopy(evaluate_arguments)
    context["unexpected"] = True

    with pytest.raises(ContractValidationError, match="context 필드 구성"):
        parse_evaluate_request(input_paths, output_dir, context)


def test_parse_evaluate_request_rejects_wrong_input_key(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
) -> None:
    input_paths, output_dir, context = copy.deepcopy(evaluate_arguments)
    input_paths["scenario"] = input_paths.pop("test_scenarios")

    with pytest.raises(ContractValidationError, match="input_paths 필드 구성"):
        parse_evaluate_request(input_paths, output_dir, context)


def test_parse_evaluate_request_rejects_wrong_input_path(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
) -> None:
    input_paths, output_dir, context = copy.deepcopy(evaluate_arguments)
    input_paths["test_scenarios"]["path"] = "test_scenarios.json"

    with pytest.raises(ContractValidationError, match="입력 경로"):
        parse_evaluate_request(input_paths, output_dir, context)


def test_parse_evaluate_request_rejects_descriptor_key_change(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
) -> None:
    input_paths, output_dir, context = copy.deepcopy(evaluate_arguments)
    input_paths["test_scenarios"]["unexpected"] = True

    with pytest.raises(ContractValidationError, match="필드 구성이 올바르지 않음"):
        parse_evaluate_request(input_paths, output_dir, context)


def test_parse_evaluate_request_rejects_invalid_sha256(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
) -> None:
    input_paths, output_dir, context = copy.deepcopy(evaluate_arguments)
    input_paths["test_scenarios"]["sha256"] = "invalid"

    with pytest.raises(ContractValidationError, match="SHA-256 형식"):
        parse_evaluate_request(input_paths, output_dir, context)


@pytest.mark.parametrize(
    "change",
    [
        {"path": "private/other/policy.json"},
        {"path": "../policy.json"},
        {"sha256": "invalid"},
        {"unexpected": True},
    ],
)
def test_parse_evaluate_request_rejects_legacy_policy_descriptor(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    change: dict[str, Any],
) -> None:
    input_paths, output_dir, context = copy.deepcopy(evaluate_arguments)
    context["policy_config"] = change

    with pytest.raises(ContractValidationError, match="context 필드 구성"):
        parse_evaluate_request(input_paths, output_dir, context)


@pytest.mark.parametrize(
    "change",
    [
        {"path": "private/safety_policy/approval_demo_001.json"},
        {"path": "private/safety_policy/approvals/not_approval.json"},
        {"path": "../approval_demo_001.json"},
        {"sha256": "invalid"},
        {"unexpected": True},
    ],
)
def test_parse_evaluate_request_rejects_invalid_approval_descriptor(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    evaluate_run_root: Path,
    change: dict[str, Any],
) -> None:
    input_paths, output_dir, context = copy.deepcopy(evaluate_arguments)
    relative_path = (
        "private/safety_policy/approvals/approval_demo_001.json"
    )
    context["run_root"] = evaluate_run_root
    context["approval_record"] = {
        "path": relative_path,
        "sha256": calculate_sha256(evaluate_run_root / relative_path),
    }
    context["approval_record"].update(change)

    with pytest.raises(SafetyPolicyError):
        parse_evaluate_request(input_paths, output_dir, context)


def test_parse_evaluate_request_rejects_missing_input_file(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    evaluate_run_root: Path,
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    input_path = evaluate_run_root / input_paths["test_scenarios"]["path"]
    input_path.unlink()

    with pytest.raises(SafetyPolicyError, match="입력 파일이 존재하지 않음"):
        parse_evaluate_request(input_paths, output_dir, context)


def test_parse_evaluate_request_accepts_missing_policy_file(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    evaluate_run_root: Path,
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    policy_path = evaluate_run_root / POLICY_CONFIG_RELATIVE_PATH

    request = parse_evaluate_request(input_paths, output_dir, context)

    assert request.run_root == evaluate_run_root
    assert not policy_path.exists()


def test_prepare_evaluate_request_rejects_policy_symlink_escape(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    evaluate_run_root: Path,
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    policy_path = evaluate_run_root / POLICY_CONFIG_RELATIVE_PATH
    outside_path = evaluate_run_root.parent / "outside-policy.json"
    outside_path.write_text("{}", encoding="utf-8")
    policy_path.symlink_to(outside_path)

    with pytest.raises(SafetyPolicyError, match="신뢰 경로를 벗어난"):
        prepare_evaluate_request(input_paths, output_dir, context)


@pytest.mark.parametrize(
    "output_dir",
    ["artifacts/iteration-000/other", "../outside", "/tmp/outside"],
)
def test_parse_evaluate_request_rejects_wrong_output_directory(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    output_dir: str,
) -> None:
    input_paths, _, context = evaluate_arguments

    with pytest.raises(SafetyPolicyError):
        parse_evaluate_request(input_paths, output_dir, context)


def test_parse_evaluate_request_rejects_run_root_name_mismatch(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    tmp_path: Path,
) -> None:
    input_paths, output_dir, context = copy.deepcopy(evaluate_arguments)
    other_root = tmp_path / "other_run"
    other_root.mkdir()
    context["run_root"] = other_root

    with pytest.raises(ContractValidationError, match="run_root와 run_id"):
        parse_evaluate_request(input_paths, output_dir, context)


def test_parse_evaluate_request_does_not_read_or_prepare_policy(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    evaluate_run_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(POLICY_CONFIG_PATH_ENV)

    arguments = parse_evaluate_request(*evaluate_arguments)

    assert arguments.run_root == evaluate_run_root
    assert not (evaluate_run_root / POLICY_CONFIG_RELATIVE_PATH).exists()


def test_prepare_evaluate_request_binds_module_owned_policy_reference(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    evaluate_run_root: Path,
) -> None:
    request = prepare_evaluate_request(*evaluate_arguments)

    assert isinstance(request, EvaluationRequest)
    assert request.policy_config_path == evaluate_run_root / POLICY_CONFIG_RELATIVE_PATH
    assert request.policy_config_expected_sha256 == calculate_sha256(request.policy_config_path)
    assert "policy_config" not in evaluate_arguments[2]


@pytest.mark.parametrize("field", ["relative_path", "path", "sha256"])
def test_bind_policy_configuration_rejects_invalid_internal_reference(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    evaluate_run_root: Path,
    field: str,
) -> None:
    arguments = parse_evaluate_request(*evaluate_arguments)
    prepared = prepare_policy_configuration(evaluate_run_root)
    changes = {
        "relative_path": "private/other/policy.json",
        "path": evaluate_run_root.parent / "other_run" / "policy.json",
        "sha256": "invalid",
    }
    invalid_reference = replace(prepared, **{field: changes[field]})

    with pytest.raises(ContractValidationError):
        bind_policy_configuration(arguments, invalid_reference)


@pytest.mark.parametrize("field", ["run_id", "iteration", "mode", "run_root"])
def test_parse_evaluate_request_rejects_missing_execution_context_key(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    field: str,
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    context.pop(field)

    with pytest.raises(ContractValidationError, match="context 필드 구성"):
        parse_evaluate_request(input_paths, output_dir, context)
