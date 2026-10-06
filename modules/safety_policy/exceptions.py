"""Safety policy exception hierarchy."""


class SafetyPolicyError(Exception):
    """Base exception for safety policy failures."""


class ContractValidationError(SafetyPolicyError):
    """Raised when an artifact violates its public contract."""


class PathValidationError(SafetyPolicyError):
    """Raised when a path escapes a trusted run root."""


class StorageError(SafetyPolicyError):
    """Raised when an artifact cannot be published safely."""


class OutputArtifactExistsError(StorageError):
    """Raised when publication would overwrite an immutable artifact."""


class SourceArtifactFailedError(ContractValidationError):
    """Raised when the scenario generator published a failed artifact."""


class PolicyConfigurationError(SafetyPolicyError):
    """Raised when safety policy settings are invalid or ambiguous."""


class InputHashMismatchError(ContractValidationError):
    """Raised when test_scenarios differs from its supplied digest."""


class PolicyConfigHashMismatchError(PolicyConfigurationError):
    """Raised when the policy configuration differs from its supplied digest."""


class ApprovalRecordError(SafetyPolicyError):
    """Raised when a supplied approval record cannot authorize evaluation."""


class ApprovalRecordHashMismatchError(ApprovalRecordError):
    """Raised when an approval record differs from its supplied digest."""
