"""Knowledge graph contract exceptions."""


class KnowledgeGraphError(Exception):
    """Base exception for knowledge graph failures."""


class ContractValidationError(KnowledgeGraphError):
    """Raised when an artifact violates its public contract."""


class PathValidationError(KnowledgeGraphError):
    """Raised when a supplied path escapes its trusted root."""
