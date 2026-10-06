"""Safety policy exception hierarchy."""


class SafetyPolicyError(Exception):
    """Base exception for safety policy failures."""


class ContractValidationError(SafetyPolicyError):
    """Raised when an artifact violates its public contract."""


class PathValidationError(SafetyPolicyError):
    """Raised when a path escapes a trusted run root."""


class StorageError(SafetyPolicyError):
    """Raised when an artifact cannot be published safely."""
