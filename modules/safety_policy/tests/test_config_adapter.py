import json
from pathlib import Path
from typing import Any

import pytest

from modules.safety_policy.config_adapter import load_policy_configuration
from modules.safety_policy.evaluate_adapter import parse_evaluate_request
from modules.safety_policy.exceptions import (
    PolicyConfigHashMismatchError,
    PolicyConfigurationError,
)
from modules.safety_policy.utils.hashing import calculate_sha256
from modules.safety_policy.utils.validation import load_json


def test_load_policy_configuration_returns_internal_model(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
) -> None:
    request = parse_evaluate_request(*evaluate_arguments)

    configuration = load_policy_configuration(request)

    assert configuration.policy_id == "abc2lab-fixture-policy"
    assert configuration.max_requests == 4
    assert configuration.allowed_targets[0].path_prefixes == ("/api/orders",)
    assert configuration.request_rules[1].state_change == "require_approval"


def test_load_policy_configuration_rejects_hash_mismatch(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    context["policy_config"]["sha256"] = "0" * 64
    request = parse_evaluate_request(input_paths, output_dir, context)

    with pytest.raises(PolicyConfigHashMismatchError, match="SHA-256"):
        load_policy_configuration(request)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "9.9.9"),
        ("unexpected", True),
        ("limits", {"max_requests": 0, "max_duration_ms": 1000}),
    ],
)
def test_load_policy_configuration_rejects_schema_violation(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    field: str,
    value: object,
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    policy_path = Path(context["run_root"]) / context["policy_config"]["path"]
    policy = load_json(policy_path)
    policy[field] = value
    policy_path.write_text(
        json.dumps(policy, ensure_ascii=False),
        encoding="utf-8",
    )
    context["policy_config"]["sha256"] = calculate_sha256(policy_path)
    request = parse_evaluate_request(input_paths, output_dir, context)

    with pytest.raises(PolicyConfigurationError, match="계약"):
        load_policy_configuration(request)
