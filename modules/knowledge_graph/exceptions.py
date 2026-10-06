"""Knowledge graph contract exceptions."""


class KnowledgeGraphError(Exception):
    """Base exception for knowledge graph failures."""


class ContractValidationError(KnowledgeGraphError):
    """Raised when an artifact violates its public contract."""


class PathValidationError(KnowledgeGraphError):
    """Raised when a supplied path escapes its trusted root."""


class RepositoryError(KnowledgeGraphError):
    """Raised when Neo4j cannot complete a repository operation."""


class GraphAlreadyExistsError(RepositoryError):
    """Raised when an ingest attempts to replace an existing graph."""


class GraphNotFoundError(RepositoryError):
    """Raised when a requested graph does not exist in the run scope."""
