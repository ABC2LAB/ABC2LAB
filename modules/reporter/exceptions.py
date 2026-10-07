"""Reporter-specific exception hierarchy."""


class ReporterError(Exception):
    """Base error raised by the reporter module."""


class ContractValidationError(ReporterError):
    """Raised when a public contract is invalid or inconsistent."""


class PathValidationError(ContractValidationError):
    """Raised when a path leaves its trusted root or is unexpected."""


class HashMismatchError(ContractValidationError):
    """Raised when declared and actual content hashes differ."""


class OutputArtifactExistsError(ReporterError):
    """Raised when an immutable output already exists."""
