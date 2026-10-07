import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas"
SCHEMA_PATHS = sorted(SCHEMA_DIR.glob("*/*.schema.json"))


def test_expected_schema_files_exist() -> None:
    assert [(path.parent.name, path.name) for path in SCHEMA_PATHS] == [
        ("input", "crawl_result.schema.json"),
        ("input", "vulnerability_candidates.schema.json"),
        ("output", "test_scenarios.schema.json"),
    ]


@pytest.mark.parametrize("schema_path", SCHEMA_PATHS, ids=lambda path: path.name)
def test_schema_is_a_valid_json_schema(schema_path: Path) -> None:
    Draft202012Validator.check_schema(json.loads(schema_path.read_text(encoding="utf-8")))
