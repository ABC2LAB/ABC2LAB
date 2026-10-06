import pytest

from modules.knowledge_graph.exceptions import ContractValidationError
from modules.knowledge_graph.settings import Neo4jSettings


def test_settings_load_required_environment() -> None:
    settings = Neo4jSettings.from_environment(
        {
            "NEO4J_URI": "bolt://localhost:7687",
            "NEO4J_USERNAME": "neo4j",
            "NEO4J_PASSWORD": "test-password",
            "NEO4J_DATABASE": "neo4j",
        }
    )

    assert settings.uri == "bolt://localhost:7687"
    assert settings.database == "neo4j"
    assert "test-password" not in repr(settings)


def test_settings_reject_missing_password() -> None:
    with pytest.raises(ContractValidationError, match="NEO4J_PASSWORD"):
        Neo4jSettings.from_environment(
            {
                "NEO4J_URI": "bolt://localhost:7687",
                "NEO4J_USERNAME": "neo4j",
                "NEO4J_DATABASE": "neo4j",
            }
        )


def test_settings_reject_unsupported_uri_scheme() -> None:
    with pytest.raises(ContractValidationError, match="URI scheme"):
        Neo4jSettings(
            uri="http://localhost:7474",
            username="neo4j",
            password="test-password",
            database="neo4j",
        ).validate()
