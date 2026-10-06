"""Safety policy input preparation service."""

from modules.safety_policy.contracts import load_test_scenarios
from modules.safety_policy.exceptions import (
    ContractValidationError,
    SourceArtifactFailedError,
)
from modules.safety_policy.models import (
    ErrorItem,
    EvaluationInput,
    EvaluationRequest,
    SourceArtifact,
)
from modules.safety_policy.utils.hashing import calculate_sha256


def prepare_evaluation(request: EvaluationRequest) -> EvaluationInput:
    actual_sha256 = calculate_sha256(request.input_path)
    if actual_sha256 != request.expected_sha256:
        raise ContractValidationError("test_scenarios SHA-256이 실제 파일과 다름")

    artifact, scenarios = load_test_scenarios(request.input_path)
    _validate_execution_context(artifact, request)
    if artifact["status"] == "failed":
        raise SourceArtifactFailedError("failed test_scenarios는 평가할 수 없음")
    if scenarios is None:
        raise ContractValidationError("test_scenarios data가 없음")

    source = SourceArtifact(
        artifact_id=artifact["artifact_id"],
        artifact_type=artifact["artifact_type"],
        relative_path=request.input_relative_path,
        sha256=actual_sha256,
        iteration=artifact["iteration"],
        status=artifact["status"],
    )
    source_errors = tuple(ErrorItem.from_mapping(item) for item in artifact["errors"])
    return EvaluationInput(
        request=request,
        source=source,
        scenarios=scenarios,
        source_errors=source_errors,
    )


def _validate_execution_context(
    artifact: dict[str, object],
    request: EvaluationRequest,
) -> None:
    if artifact["run_id"] != request.run_id:
        raise ContractValidationError("test_scenarios run_id가 context와 다름")
    if artifact["iteration"] != request.iteration:
        raise ContractValidationError("test_scenarios iteration이 context와 다름")
    if artifact["mode"] != request.mode:
        raise ContractValidationError("test_scenarios mode가 context와 다름")
