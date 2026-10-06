import json
from pathlib import Path
from time import perf_counter
from typing import Any

import pytest

from modules.safety_policy.approval_adapter import load_approval_record
from modules.safety_policy.config_adapter import load_policy_configuration
from modules.safety_policy.evaluate_adapter import parse_evaluate_request
from modules.safety_policy.exceptions import ContractValidationError
from modules.safety_policy.output_adapter import (
    build_evaluation_artifact,
    publish_evaluation_artifact,
)
from modules.safety_policy.policy import evaluate_policy
from modules.safety_policy.service import prepare_evaluation
from modules.safety_policy.utils.hashing import calculate_sha256


@pytest.mark.parametrize("changed_source", ["scenarios", "policy"])
def test_publication_rejects_source_changed_after_evaluation(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    evaluate_run_root: Path,
    changed_source: str,
) -> None:
    request = parse_evaluate_request(*evaluate_arguments)
    prepared = prepare_evaluation(request)
    configuration = load_policy_configuration(request)
    data = evaluate_policy(prepared, configuration)
    artifact = build_evaluation_artifact(prepared, data, perf_counter())
    source_path = (
        request.input_path
        if changed_source == "scenarios"
        else request.policy_config_path
    )
    value = json.loads(source_path.read_text(encoding="utf-8"))
    value["changed_after_evaluation"] = True
    source_path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ContractValidationError, match="평가 중"):
        publish_evaluation_artifact(prepared, artifact)

    assert not request.output_path.exists()


def test_publication_rejects_approval_changed_after_evaluation(
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
    request = parse_evaluate_request(input_paths, output_dir, context)
    prepared = prepare_evaluation(request)
    configuration = load_policy_configuration(request)
    approval = load_approval_record(request, prepared, configuration)
    data = evaluate_policy(prepared, configuration, approval)
    artifact = build_evaluation_artifact(prepared, data, perf_counter())
    value = json.loads(approval_path.read_text(encoding="utf-8"))
    value["approved_by"] = "operator_changed"
    approval_path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ContractValidationError, match="승인 기록"):
        publish_evaluation_artifact(prepared, artifact)

    assert not request.output_path.exists()
