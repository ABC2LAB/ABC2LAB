"""Contract loaders for safety policy public artifacts."""

from pathlib import Path
from typing import Any

from modules.safety_policy.models import SafetyDecisionsData, ScenarioData
from modules.safety_policy.utils.validation import (
    load_and_validate_artifact,
    validate_safety_decisions_semantics,
    validate_test_scenarios_semantics,
)

MODULE_DIRECTORY = Path(__file__).parent
SCHEMA_DIRECTORY = MODULE_DIRECTORY / "schemas"
TEST_SCENARIOS_SCHEMA = SCHEMA_DIRECTORY / "input" / "test_scenarios.schema.json"
SAFETY_DECISIONS_SCHEMA = (
    SCHEMA_DIRECTORY / "output" / "safety_decisions.schema.json"
)


def load_test_scenarios(
    path: Path,
) -> tuple[dict[str, Any], ScenarioData | None]:
    artifact = load_and_validate_artifact(path, TEST_SCENARIOS_SCHEMA)
    validate_test_scenarios_semantics(artifact)
    data = artifact["data"]
    model = ScenarioData.from_mapping(data) if data is not None else None
    return artifact, model


def load_safety_decisions(
    path: Path,
) -> tuple[dict[str, Any], SafetyDecisionsData | None]:
    artifact = load_and_validate_artifact(path, SAFETY_DECISIONS_SCHEMA)
    validate_safety_decisions_semantics(artifact)
    data = artifact["data"]
    model = SafetyDecisionsData.from_mapping(data) if data is not None else None
    return artifact, model
