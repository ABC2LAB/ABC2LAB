from pathlib import Path

from modules.safety_policy.contracts import (
    load_safety_decisions,
    load_test_scenarios,
)
from modules.safety_policy.models import SafetyDecisionsData, ScenarioData


def test_test_scenarios_convert_to_immutable_models(
    completed_input_path: Path,
) -> None:
    _, data = load_test_scenarios(completed_input_path)

    assert isinstance(data, ScenarioData)
    assert data.model_info is not None
    assert data.scenarios[0].steps[1].bindings[0].source_step_id == (
        "step_list_orders_001"
    )
    assert data.scenarios[0].steps[1].request.parameters[0].binding_ref == "order_id"


def test_safety_decisions_convert_to_immutable_models(
    completed_output_path: Path,
) -> None:
    _, data = load_safety_decisions(completed_output_path)

    assert isinstance(data, SafetyDecisionsData)
    assert data.decisions[0].assessment.statuses() == ("pass",) * 6
    assert data.decisions[1].decision == "require_approval"
    assert data.decisions[1].limits.max_requests == 0
