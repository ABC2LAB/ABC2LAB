"""Policy configuration loading and boundary validation."""

from pathlib import Path

from modules.safety_policy.exceptions import (
    ContractValidationError,
    PolicyConfigHashMismatchError,
    PolicyConfigurationError,
)
from modules.safety_policy.models import EvaluationRequest, PolicyConfiguration
from modules.safety_policy.utils.hashing import calculate_sha256
from modules.safety_policy.utils.validation import load_json, validate_schema

POLICY_CONFIG_SCHEMA = (
    Path(__file__).parent / "schemas" / "input" / "policy_config.schema.json"
)


def load_policy_configuration(
    request: EvaluationRequest,
) -> PolicyConfiguration:
    actual_sha256 = calculate_sha256(request.policy_config_path)
    if actual_sha256 != request.policy_config_expected_sha256:
        raise PolicyConfigHashMismatchError(
            "Policy 설정 SHA-256이 실제 파일과 다름"
        )
    try:
        value = load_json(request.policy_config_path)
        validate_schema(value, POLICY_CONFIG_SCHEMA)
    except ContractValidationError as error:
        raise PolicyConfigurationError("Policy 설정 계약이 올바르지 않음") from error
    return PolicyConfiguration.from_mapping(value)
