"""Policy configuration loading and boundary validation."""

import os
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from modules.safety_policy.exceptions import (
    ContractValidationError,
    OutputArtifactExistsError,
    PathValidationError,
    PolicyConfigHashMismatchError,
    PolicyConfigurationError,
    StorageError,
)
from modules.safety_policy.models import EvaluationRequest, PolicyConfiguration
from modules.safety_policy.utils.atomic_writer import write_bytes_atomically
from modules.safety_policy.utils.hashing import calculate_sha256
from modules.safety_policy.utils.paths import resolve_trusted_relative_path
from modules.safety_policy.utils.validation import (
    load_json,
    parse_json_bytes,
    validate_schema,
)

POLICY_CONFIG_PATH_ENV = "SAFETY_POLICY_CONFIG_PATH"
POLICY_CONFIG_RELATIVE_PATH = "private/safety_policy/policy.json"
POLICY_CONFIG_SCHEMA = (
    Path(__file__).parent / "schemas" / "input" / "policy_config.schema.json"
)


@dataclass(frozen=True)
class PreparedPolicyConfiguration:
    path: Path
    relative_path: str
    sha256: str


def prepare_policy_configuration(run_root: Path) -> PreparedPolicyConfiguration:
    """Publish or reuse a run-local snapshot matching the explicit source bytes."""
    content = _read_policy_source()
    _validate_policy_source(content)
    policy_path = _resolve_policy_snapshot_path(run_root)
    try:
        try:
            write_bytes_atomically(policy_path, content)
        except OutputArtifactExistsError:
            # Another writer may have published the snapshot after path validation.
            policy_path = _resolve_policy_snapshot_path(run_root)
            if not policy_path.is_file():
                raise PolicyConfigurationError("Policy 설정 사본은 파일이어야 함")
        actual_sha256 = calculate_sha256(policy_path)
    except OSError as error:
        raise StorageError("Policy 설정 사본을 저장하거나 읽을 수 없음") from error
    if actual_sha256 != sha256(content).hexdigest():
        raise PolicyConfigHashMismatchError("Policy 설정 사본이 원본 바이트와 다름")
    return PreparedPolicyConfiguration(
        path=policy_path,
        relative_path=POLICY_CONFIG_RELATIVE_PATH,
        sha256=actual_sha256,
    )


def _read_policy_source() -> bytes:
    configured_path = os.environ.get(POLICY_CONFIG_PATH_ENV, "").strip()
    if not configured_path:
        raise PolicyConfigurationError(f"{POLICY_CONFIG_PATH_ENV} 설정이 필요함")
    try:
        source_path = Path(configured_path).resolve(strict=True)
        if not source_path.is_file():
            raise PolicyConfigurationError("Policy 원본 설정은 파일이어야 함")
        return source_path.read_bytes()
    except (OSError, ValueError) as error:
        raise PolicyConfigurationError("Policy 원본 설정 파일을 읽을 수 없음") from error


def _validate_policy_source(content: bytes) -> None:
    try:
        value = parse_json_bytes(content)
        validate_schema(value, POLICY_CONFIG_SCHEMA)
    except ContractValidationError as error:
        raise PolicyConfigurationError("Policy 원본 설정 계약이 올바르지 않음") from error


def _resolve_policy_snapshot_path(run_root: Path) -> Path:
    try:
        resolved_root = run_root.resolve(strict=True)
        if not resolved_root.is_dir():
            raise PathValidationError("run_root는 디렉터리여야 함")
        policy_path = resolve_trusted_relative_path(
            resolved_root,
            POLICY_CONFIG_RELATIVE_PATH,
        )
    except (OSError, ValueError) as error:
        raise PathValidationError("Policy 설정의 run_root 경로가 올바르지 않음") from error
    if policy_path != resolved_root / POLICY_CONFIG_RELATIVE_PATH:
        raise PathValidationError("Policy 설정 사본 경로에 symlink를 사용할 수 없음")
    return policy_path


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
