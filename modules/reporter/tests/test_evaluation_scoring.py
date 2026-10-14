"""Benchmark scoring regressions independent of upstream implementations."""

from dataclasses import replace
from typing import Any

import pytest

from modules.reporter.contracts import prepare_evaluation_inputs
from modules.reporter.evaluation_service import build_evaluation_results
from modules.reporter.input_adapter import parse_evaluate_request
from modules.reporter.models import EvaluationInputs

Arguments = tuple[dict[str, Any], str, dict[str, Any]]


@pytest.fixture
def evaluation_inputs(evaluate_arguments: Arguments) -> EvaluationInputs:
    return prepare_evaluation_inputs(parse_evaluate_request(*evaluate_arguments))


def _evaluate(inputs: EvaluationInputs) -> dict[str, Any]:
    result = build_evaluation_results(inputs)
    assert result.status == "completed"
    assert result.data is not None
    return result.data.to_mapping()


def _precision(data: dict[str, Any], metric_id: str) -> float | None:
    return next(item["value"] for item in data["metrics"] if item["metric_id"] == metric_id)


def test_unmatched_predictions_are_false_positives(evaluation_inputs: EvaluationInputs) -> None:
    original = evaluation_inputs.report_inputs.candidates[0]
    candidates = tuple(
        replace(original, candidate_id=f"unmatched-{index}", vulnerability_type="unlisted")
        for index in range(5)
    )
    changed = replace(
        evaluation_inputs,
        report_inputs=replace(evaluation_inputs.report_inputs, candidates=candidates),
    )

    data = _evaluate(changed)

    assert data["candidate_counts"] == {"tp": 0, "fp": 5, "fn": 1}
    assert _precision(data, "candidate_precision") == 0.0
    assert any("일대일 매칭되지 않은 후보: 5개" in note for note in data["notes"])


def test_zero_predictions_have_null_precision(evaluation_inputs: EvaluationInputs) -> None:
    changed = replace(
        evaluation_inputs,
        report_inputs=replace(evaluation_inputs.report_inputs, candidates=()),
    )

    data = _evaluate(changed)

    assert data["candidate_counts"] == {"tp": 0, "fp": 0, "fn": 1}
    assert data["confirmed_counts"] == {"tp": 0, "fp": 0, "fn": 1}
    assert _precision(data, "candidate_precision") is None
    assert _precision(data, "confirmed_precision") is None


def test_duplicate_positive_prediction_is_false_positive(evaluation_inputs: EvaluationInputs) -> None:
    candidate = evaluation_inputs.report_inputs.candidates[0]
    changed = replace(
        evaluation_inputs,
        report_inputs=replace(
            evaluation_inputs.report_inputs,
            candidates=(candidate, replace(candidate, candidate_id="duplicate-positive")),
        ),
    )

    data = _evaluate(changed)

    assert data["candidate_counts"] == {"tp": 1, "fp": 1, "fn": 0}
    assert _precision(data, "candidate_precision") == 0.5
    assert any("일대일 매칭되지 않은 후보: 1개" in note for note in data["notes"])


def test_explicit_negative_prediction_is_false_positive(evaluation_inputs: EvaluationInputs) -> None:
    changed = replace(
        evaluation_inputs,
        report_inputs=replace(
            evaluation_inputs.report_inputs,
            candidates=(evaluation_inputs.report_inputs.candidates[1],),
        ),
    )

    data = _evaluate(changed)

    assert data["candidate_counts"] == {"tp": 0, "fp": 1, "fn": 1}
    assert _precision(data, "candidate_precision") == 0.0
    assert not any("일대일 매칭되지 않은 후보:" in note for note in data["notes"])


def test_unmatched_confirmed_prediction_is_false_positive(evaluation_inputs: EvaluationInputs) -> None:
    candidate = replace(
        evaluation_inputs.report_inputs.candidates[0], vulnerability_type="unlisted",
    )
    changed = replace(
        evaluation_inputs,
        report_inputs=replace(evaluation_inputs.report_inputs, candidates=(candidate,)),
    )

    data = _evaluate(changed)

    assert data["confirmed_counts"] == {"tp": 0, "fp": 1, "fn": 1}
    assert _precision(data, "confirmed_precision") == 0.0
    assert any("일대일 매칭되지 않은 확정 후보: 1개" in note for note in data["notes"])


def test_duplicate_confirmed_predictions_are_matched_one_to_one(
    evaluation_inputs: EvaluationInputs,
) -> None:
    report = evaluation_inputs.report_inputs
    candidate = replace(report.candidates[0], candidate_id="confirmed-duplicate")
    scenario = replace(report.scenarios[0], scenario_id="duplicate-scenario",
                       candidate_id=candidate.candidate_id)
    decision = replace(report.decisions[0], decision_id="duplicate-decision",
                       scenario_id=scenario.scenario_id)
    result = replace(report.verification_results[0], verification_id="duplicate-verification",
                     scenario_id=scenario.scenario_id, candidate_id=candidate.candidate_id,
                     decision_id=decision.decision_id)
    changed = replace(evaluation_inputs, report_inputs=replace(
        report, candidates=(report.candidates[0], candidate),
        scenarios=(report.scenarios[0], scenario), decisions=(report.decisions[0], decision),
        verification_results=(report.verification_results[0], result),
    ))

    data = _evaluate(changed)

    assert data["candidate_counts"] == {"tp": 1, "fp": 1, "fn": 0}
    assert data["confirmed_counts"] == {"tp": 1, "fp": 1, "fn": 0}
    assert _precision(data, "confirmed_precision") == 0.5


def test_multiple_confirmed_scenarios_count_as_one_prediction(
    evaluation_inputs: EvaluationInputs,
) -> None:
    report = evaluation_inputs.report_inputs
    scenario = replace(report.scenarios[0], scenario_id="additional-scenario")
    decision = replace(report.decisions[0], decision_id="additional-decision",
                       scenario_id=scenario.scenario_id)
    result = replace(report.verification_results[0], verification_id="additional-verification",
                     scenario_id=scenario.scenario_id, decision_id=decision.decision_id)
    changed = replace(evaluation_inputs, report_inputs=replace(
        report, candidates=(report.candidates[0],), scenarios=(report.scenarios[0], scenario),
        decisions=(report.decisions[0], decision), verification_results=(report.verification_results[0], result),
    ))

    data = _evaluate(changed)

    assert data["confirmed_counts"] == {"tp": 1, "fp": 0, "fn": 0}
    assert _precision(data, "confirmed_precision") == 1.0


def test_predictions_with_empty_gt_are_false_positives(evaluation_inputs: EvaluationInputs) -> None:
    data = _evaluate(replace(
        evaluation_inputs, ground_truth=replace(evaluation_inputs.ground_truth, cases=()),
    ))
    assert data["candidate_counts"] == {"tp": 0, "fp": 3, "fn": 0}
    assert data["confirmed_counts"] == {"tp": 0, "fp": 1, "fn": 0}
    assert _precision(data, "candidate_precision") == 0.0
    assert _precision(data, "confirmed_precision") == 0.0


def test_matching_notes_keep_positive_negative_and_unmatched_counts(
    evaluation_inputs: EvaluationInputs,
) -> None:
    data = _evaluate(evaluation_inputs)
    assert "후보 매칭: 양성 1개, 명시적 음성 1개, 일대일 미매칭 1개" in data["notes"]
    assert any("완전한 정답 집합 benchmark 가정" in note for note in data["notes"])


def test_positive_matching_uses_augmenting_path_before_negative(
    evaluation_inputs: EvaluationInputs,
) -> None:
    original = evaluation_inputs.ground_truth.cases[0]
    broad = {**original, "resource_match_key": {"resource_type": "order"}}
    narrow = {**original, "case_id": "second-positive"}
    negative = {**original, "case_id": "negative", "is_vulnerable": False,
                "resource_match_key": {"resource_type": "order", "external_id": "other"}}
    snapshot = evaluation_inputs.graph_snapshot
    assert snapshot is not None
    resource = next(node for node in snapshot.nodes if node["node_type"] == "Resource")
    other = {**resource, "node_id": "other-resource", "properties": {
        "resource_key": "order", "resource_scope": "instance", "match_key": {
            "resource_key": "order", "identifiers": [{"key": "external_id", "value": "other"}],
        },
    }}
    candidate = evaluation_inputs.report_inputs.candidates[0]
    changed = replace(
        evaluation_inputs,
        ground_truth=replace(evaluation_inputs.ground_truth, cases=(broad, narrow, negative)),
        graph_snapshot=replace(snapshot, nodes=(*snapshot.nodes, other)),
        report_inputs=replace(evaluation_inputs.report_inputs, candidates=(
            candidate,
            replace(candidate, candidate_id="second-prediction", resource_ids=("other-resource",)),
        )),
    )

    data = _evaluate(changed)

    assert data["candidate_counts"] == {"tp": 2, "fp": 0, "fn": 0}
    assert _precision(data, "candidate_precision") == 1.0
