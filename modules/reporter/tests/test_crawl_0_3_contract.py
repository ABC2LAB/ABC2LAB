"""Independent coverage of the Collector 0.3.0 input copy."""

from pathlib import Path
from typing import Any

import pytest

from modules.reporter.contracts import INPUT_SCHEMA_BY_NAME
from modules.reporter.exceptions import ContractValidationError
from modules.reporter.utils.validation import load_json, validate_schema

CRAWL_SCHEMA = INPUT_SCHEMA_BY_NAME["crawl_result"]


@pytest.fixture
def crawl_artifact(fixture_root: Path) -> dict[str, Any]:
    return load_json(
        fixture_root
        / "runs/run_demo_001/artifacts/iteration-000/collector/crawl_result.json"
    )


@pytest.mark.parametrize("source", ["form", "link", "js", "spec"])
def test_crawl_accepts_discovered_endpoint(
    crawl_artifact: dict[str, Any],
    source: str,
) -> None:
    crawl_artifact["data"]["discovered_endpoints"] = [
        {
            "discovered_id": "discovered_test",
            "method": "GET",
            "url_template": "/api/discovered/{id}",
            "parameters": [
                {"name": "id", "location": "path", "is_sensitive": False},
            ],
            "source": source,
            "discovered_from": [
                {"page_id": "page_test", "action_id": "action_test"},
            ],
            "basis": "inferred",
            "evidence_refs": [],
        },
    ]

    validate_schema(crawl_artifact, CRAWL_SCHEMA)


@pytest.mark.parametrize(
    "field",
    ["discovered_endpoints", "identifiers", "identifiers_truncated"],
)
def test_crawl_rejects_missing_0_3_fields(
    crawl_artifact: dict[str, Any],
    field: str,
) -> None:
    value = (
        crawl_artifact["data"]
        if field == "discovered_endpoints"
        else crawl_artifact["data"]["requests"][0]["response"]
    )
    del value[field]

    with pytest.raises(ContractValidationError):
        validate_schema(crawl_artifact, CRAWL_SCHEMA)


@pytest.mark.parametrize(
    "identifier",
    [0, "42", "550e8400-e29b-41d4-a716-446655440000"],
)
def test_crawl_preserves_identifier_json_type(
    crawl_artifact: dict[str, Any],
    identifier: int | str,
) -> None:
    response = crawl_artifact["data"]["requests"][0]["response"]
    response["identifiers"] = [{"pointer": "/items/0/id", "value": identifier}]

    validate_schema(crawl_artifact, CRAWL_SCHEMA)

    assert type(response["identifiers"][0]["value"]) is type(identifier)


@pytest.mark.parametrize("identifier", [-1, 1.5, "name", True])
def test_crawl_rejects_unsupported_identifier_values(
    crawl_artifact: dict[str, Any],
    identifier: Any,
) -> None:
    crawl_artifact["data"]["requests"][0]["response"]["identifiers"] = [
        {"pointer": "/id", "value": identifier},
    ]

    with pytest.raises(ContractValidationError):
        validate_schema(crawl_artifact, CRAWL_SCHEMA)


@pytest.mark.parametrize("identifiers", [None, []])
def test_crawl_accepts_unmeasured_and_empty_identifiers(
    crawl_artifact: dict[str, Any],
    identifiers: list[Any] | None,
) -> None:
    crawl_artifact["data"]["requests"][0]["response"]["identifiers"] = identifiers

    validate_schema(crawl_artifact, CRAWL_SCHEMA)
