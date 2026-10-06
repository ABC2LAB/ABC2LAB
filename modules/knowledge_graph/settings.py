"""Neo4j connection settings loaded from the process environment."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlparse

from modules.knowledge_graph.exceptions import ContractValidationError


ALLOWED_NEO4J_SCHEMES = frozenset(
    {"bolt", "bolt+s", "bolt+ssc", "neo4j", "neo4j+s", "neo4j+ssc"}
)


@dataclass(frozen=True)
class Neo4jSettings:
    uri: str
    username: str
    password: str = field(repr=False)
    database: str

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "Neo4jSettings":
        values = os.environ if environment is None else environment
        required_names = (
            "NEO4J_URI",
            "NEO4J_USERNAME",
            "NEO4J_PASSWORD",
            "NEO4J_DATABASE",
        )
        missing_names = [name for name in required_names if not values.get(name)]
        if missing_names:
            missing_text = ", ".join(missing_names)
            raise ContractValidationError(f"필수 Neo4j 설정 누락: {missing_text}")

        settings = cls(
            uri=values["NEO4J_URI"],
            username=values["NEO4J_USERNAME"],
            password=values["NEO4J_PASSWORD"],
            database=values["NEO4J_DATABASE"],
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        parsed_uri = urlparse(self.uri)
        if parsed_uri.scheme not in ALLOWED_NEO4J_SCHEMES:
            raise ContractValidationError("지원하지 않는 Neo4j URI scheme")
        if not parsed_uri.hostname:
            raise ContractValidationError("Neo4j URI에 hostname이 없음")
        if not self.username or not self.password or not self.database:
            raise ContractValidationError("Neo4j 연결 설정에 빈 값이 있음")
