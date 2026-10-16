from collections.abc import Sequence
from copy import deepcopy
from dataclasses import replace
from itertools import combinations, product
from pathlib import Path
from typing import Any

import pytest

from modules.reporter.contracts import prepare_evaluation_inputs
from modules.reporter.entrypoint import run
from modules.reporter.evaluation_output_adapter import EVALUATION_RESULTS_SCHEMA
from modules.reporter.evaluation_service import build_evaluation_results
from modules.reporter.exceptions import ContractValidationError
from modules.reporter.input_adapter import parse_evaluate_request
from modules.reporter.matching import (
    DEFAULT_MATCHING_PROFILE,
    WORKFLOW_SUBSEQUENCE_PROFILE,
    matches_workflow,
    require_supported_profile,
    workflow_actions,
    workflow_signature,
)
from modules.reporter.models import EvaluationData, EvaluationInputs, Metric
from modules.reporter.utils.hashing import calculate_sha256
from modules.reporter.utils.validation import load_json, validate_schema

Arguments = tuple[dict[str, Any], str, dict[str, Any]]


def _ground_truth(
    actions: Sequence[str], name: str = "logical-flow",
) -> dict[str, Any]:
    return {
        "gt_workflow_id": f"gt-{name}",
        "name": name,
        "ordered_actions": list(actions),
        "constraints": [],
    }


def _observed(
    actions: Sequence[str], name: str = "observed-flow",
) -> dict[str, Any]:
    return {
        "workflow_id": name,
        "name": name,
        "role_ids": [],
        "steps": [
            {
                "step_id": f"step-{order}", "order": order,
                "action": action, "request_ids": [],
            }
            for order, action in enumerate(actions)
        ],
        "dependencies": [],
        "basis": "inferred",
        "evidence_refs": [],
    }


@pytest.fixture
def workflow_evaluation(evaluate_arguments: Arguments) -> EvaluationInputs:
    input_paths, output_dir, context = evaluate_arguments
    return prepare_evaluation_inputs(
        parse_evaluate_request(input_paths, output_dir, context)
    )


def _with_workflows(
    inputs: EvaluationInputs,
    expected: tuple[dict[str, Any], ...],
    observed: tuple[dict[str, Any], ...],
) -> EvaluationInputs:
    assert inputs.graph_snapshot is not None
    return replace(
        inputs,
        ground_truth=replace(inputs.ground_truth, workflows=expected),
        graph_snapshot=replace(inputs.graph_snapshot, workflows=observed),
    )


def _evaluate(inputs: EvaluationInputs, profile: str) -> EvaluationData:
    result = build_evaluation_results(
        replace(inputs, request=replace(inputs.request, matching_profile=profile))
    )
    assert result.status == "completed"
    assert result.data is not None
    return result.data


def _recall(inputs: EvaluationInputs, profile: str) -> Metric:
    return next(
        item for item in _evaluate(inputs, profile).metrics
        if item.metric_id == "workflow_recall"
    )


@pytest.mark.parametrize(
    ("expected", "observed", "is_match"),
    [
        (("open", "read"), ("open", "read"), True),
        (("open", "read"), ("login", "open", "read"), True),
        (("open", "read"), ("open", "inspect", "read"), True),
        (("open", "read"), ("open", "read", "logout"), True),
        (("open", "read"), ("login", "open", "inspect", "read", "logout"), True),
        (("open", "read"), ("read", "open"), False),
        (("open", "read", "submit"), ("open", "read"), False),
        (("open", "read", "submit"), ("open", "submit"), False),
        (("read", "read"), ("read",), False),
        (("read", "read"), ("read", "inspect", "read"), True),
        (("read_order",), ("read_orders",), False),
        (("read_order",), ("view_order",), False),
        ((" Read_Order ",), ("read_order",), True),
        (("open",), ("open",), True),
        (("open",), (), False),
        ((), ("open",), False),
        ((), (), False),
    ],
)
def test_workflow_matches_ordered_subsequence_without_name(
    expected: tuple[str, ...], observed: tuple[str, ...], is_match: bool,
) -> None:
    assert matches_workflow(_ground_truth(expected), _observed(observed)) is is_match


def test_workflow_uses_order_not_array_position_and_preserves_inputs() -> None:
    expected = _ground_truth(("open", "read"))
    observed = _observed(("open", "inspect", "read"))
    observed["steps"].reverse()
    original = deepcopy((expected, observed))

    assert workflow_actions(observed, is_ground_truth=False) == (
        "open", "inspect", "read",
    )
    assert matches_workflow(expected, observed)
    assert (expected, observed) == original


def test_legacy_signature_still_requires_name_and_exact_actions() -> None:
    expected = _ground_truth(("open", "read"), "same-name")
    signature = workflow_signature(expected, is_ground_truth=True)

    assert signature == workflow_signature(
        _observed(("open", "read"), "same-name"), False,
    )
    assert signature != workflow_signature(
        _observed(("open", "read"), "other-name"), False,
    )
    assert signature != workflow_signature(
        _observed(("open", "inspect", "read"), "same-name"), False,
    )


def test_subsequence_agrees_with_exhaustive_small_sequences() -> None:
    sequences = [
        tuple(sequence)
        for length in range(5)
        for sequence in product(("open", "read"), repeat=length)
    ]
    for expected in sequences:
        for observed in sequences:
            is_match = bool(expected) and any(
                tuple(observed[index] for index in indexes) == expected
                for indexes in combinations(range(len(observed)), len(expected))
            )
            assert matches_workflow(_ground_truth(expected), _observed(observed)) is is_match


@pytest.mark.parametrize(
    "profile", [DEFAULT_MATCHING_PROFILE, WORKFLOW_SUBSEQUENCE_PROFILE],
)
def test_both_evaluation_profiles_are_supported(profile: str) -> None:
    assert require_supported_profile(profile) == profile


@pytest.mark.parametrize("profile", [None, "", "unknown-v1"])
def test_unknown_evaluation_profiles_are_rejected(profile: str | None) -> None:
    with pytest.raises(ContractValidationError, match="matching_profile"):
        require_supported_profile(profile)


@pytest.mark.parametrize(
    ("observed_actions", "observed_name"),
    [
        (("open", "read", "submit"), "different-display-name"),
        (("open", "inspect", "read", "submit"), "logical-flow"),
    ],
)
def test_profiles_compare_same_inputs_and_only_change_workflow_metric(
    workflow_evaluation: EvaluationInputs,
    observed_actions: tuple[str, ...],
    observed_name: str,
) -> None:
    inputs = _with_workflows(
        workflow_evaluation,
        (_ground_truth(("open", "read", "submit")),),
        (_observed(observed_actions, observed_name),),
    )
    original = deepcopy(inputs)
    legacy = _evaluate(inputs, DEFAULT_MATCHING_PROFILE).to_mapping()
    updated = _evaluate(inputs, WORKFLOW_SUBSEQUENCE_PROFILE).to_mapping()
    legacy_metrics = {metric["metric_id"]: metric for metric in legacy["metrics"]}
    updated_metrics = {metric["metric_id"]: metric for metric in updated["metrics"]}

    assert legacy_metrics.pop("workflow_recall")["value"] == 0
    assert updated_metrics.pop("workflow_recall")["value"] == 1
    for metric_id in ("workflow_step_recall", "workflow_ordered_step_recall"):
        assert updated_metrics.pop(metric_id) == {
            "metric_id": metric_id, "value": 1, "numerator": 3, "denominator": 3,
        }
    assert legacy_metrics == updated_metrics
    for field in (
        "candidate_counts", "confirmed_counts", "unverified_counts",
        "models", "run_metrics",
    ):
        assert legacy[field] == updated[field]
    assert any("정규화한 이름" in note for note in legacy["notes"])
    assert any("순서 부분수열" in note for note in updated["notes"])
    assert inputs == original


@pytest.mark.parametrize(
    "observed_actions",
    [("read", "open", "submit"), ("open", "read"), ("open", "submit"), ()],
)
def test_metric_rejects_reordered_or_missing_actions(
    workflow_evaluation: EvaluationInputs, observed_actions: tuple[str, ...],
) -> None:
    inputs = _with_workflows(
        workflow_evaluation,
        (_ground_truth(("open", "read", "submit")),),
        (_observed(observed_actions),),
    )
    metric = _recall(inputs, WORKFLOW_SUBSEQUENCE_PROFILE)
    assert (metric.numerator, metric.denominator, metric.value) == (0, 1, 0)


def test_metric_does_not_join_separate_observed_workflows(
    workflow_evaluation: EvaluationInputs,
) -> None:
    inputs = _with_workflows(
        workflow_evaluation,
        (_ground_truth(("open", "read", "submit")),),
        (
            _observed(("open", "read"), "account-one"),
            _observed(("submit",), "account-two"),
        ),
    )
    metric = _recall(inputs, WORKFLOW_SUBSEQUENCE_PROFILE)
    assert metric.value == 0


def test_multiple_observations_count_one_ground_truth_once(
    workflow_evaluation: EvaluationInputs,
) -> None:
    inputs = _with_workflows(
        workflow_evaluation, (_ground_truth(("open", "read")),),
        (
            _observed(("open", "read"), "first"),
            _observed(("open", "read"), "second"),
        ),
    )
    metric = _recall(inputs, WORKFLOW_SUBSEQUENCE_PROFILE)
    assert (metric.numerator, metric.denominator, metric.value) == (1, 1, 1)


def test_one_observation_can_contain_distinct_ground_truth_flows(
    workflow_evaluation: EvaluationInputs,
) -> None:
    inputs = _with_workflows(
        workflow_evaluation,
        (
            _ground_truth(("open", "read"), "first"),
            _ground_truth(("read", "submit"), "second"),
        ),
        (_observed(("open", "read", "submit")),),
    )
    metric = _recall(inputs, WORKFLOW_SUBSEQUENCE_PROFILE)
    assert (metric.numerator, metric.denominator, metric.value) == (2, 2, 1)


def test_v2_rejects_duplicate_action_sequences_even_with_different_names(
    workflow_evaluation: EvaluationInputs,
) -> None:
    inputs = _with_workflows(
        workflow_evaluation,
        (
            _ground_truth(("open", "read"), "first"),
            _ground_truth(("open", "read"), "second"),
        ),
        (_observed(("open", "read"), "first"),),
    )
    assert _evaluate(inputs, DEFAULT_MATCHING_PROFILE).metrics
    with pytest.raises(ContractValidationError, match="workflow 정규화 키가 중복"):
        _evaluate(inputs, WORKFLOW_SUBSEQUENCE_PROFILE)


@pytest.mark.parametrize(
    "profile", [DEFAULT_MATCHING_PROFILE, WORKFLOW_SUBSEQUENCE_PROFILE],
)
def test_empty_ground_truth_actions_still_fail(
    workflow_evaluation: EvaluationInputs, profile: str,
) -> None:
    inputs = _with_workflows(
        workflow_evaluation, (_ground_truth(()),), (_observed(("open",)),),
    )
    with pytest.raises(ContractValidationError, match="ordered_actions가 비어"):
        _evaluate(inputs, profile)


@pytest.mark.parametrize(
    "profile", [DEFAULT_MATCHING_PROFILE, WORKFLOW_SUBSEQUENCE_PROFILE],
)
def test_no_ground_truth_flows_keeps_null_denominator(
    workflow_evaluation: EvaluationInputs, profile: str,
) -> None:
    inputs = _with_workflows(workflow_evaluation, (), (_observed(("open",)),))
    metric = _recall(inputs, profile)
    assert (metric.numerator, metric.denominator, metric.value) == (0, 0, None)


@pytest.mark.parametrize(
    ("observed_actions", "counts"),
    [
        (("open", "read", "edit", "submit"), (4, 4, 1)),
        (("open", "read", "edit"), (3, 3, 0)),
        (("open", "edit", "read", "submit"), (4, 3, 0)),
        (("submit", "edit", "read", "open"), (4, 1, 0)),
        (("login", "open", "read", "inspect", "edit", "submit"), (4, 4, 1)),
        (("other",), (0, 0, 0)),
        ((), (0, 0, 0)),
    ],
)
def test_v2_evaluation_distinguishes_step_order_and_complete_coverage(
    workflow_evaluation: EvaluationInputs,
    observed_actions: tuple[str, ...],
    counts: tuple[int, int, int],
) -> None:
    inputs = _with_workflows(
        workflow_evaluation,
        (_ground_truth(("open", "read", "edit", "submit")),),
        (_observed(observed_actions),),
    )
    metrics = {
        item.metric_id: item
        for item in _evaluate(inputs, WORKFLOW_SUBSEQUENCE_PROFILE).metrics
    }
    step_count, ordered_count, complete_count = counts

    assert metrics["workflow_step_recall"] == Metric(
        "workflow_step_recall", step_count / 4, step_count, 4,
    )
    assert metrics["workflow_ordered_step_recall"] == Metric(
        "workflow_ordered_step_recall", ordered_count / 4, ordered_count, 4,
    )
    assert metrics["workflow_recall"] == Metric(
        "workflow_recall", complete_count, complete_count, 1,
    )


@pytest.mark.parametrize(
    ("observed_actions", "counts"),
    [
        ((("submit", "edit", "read", "open"), ("open", "read", "edit")), (3, 3)),
        ((("open", "read", "edit"), ("open", "edit", "read", "submit")), (4, 3)),
        ((("open", "read"), ("edit", "submit")), (2, 2)),
    ],
)
def test_partial_metrics_use_both_counts_from_one_selected_workflow(
    workflow_evaluation: EvaluationInputs,
    observed_actions: tuple[tuple[str, ...], ...],
    counts: tuple[int, int],
) -> None:
    inputs = _with_workflows(
        workflow_evaluation,
        (_ground_truth(("open", "read", "edit", "submit")),),
        tuple(
            _observed(actions, f"flow-{index}")
            for index, actions in enumerate(observed_actions)
        ),
    )
    metrics = {
        item.metric_id: item
        for item in _evaluate(inputs, WORKFLOW_SUBSEQUENCE_PROFILE).metrics
    }

    assert metrics["workflow_step_recall"].numerator == counts[0]
    assert metrics["workflow_ordered_step_recall"].numerator == counts[1]
    assert metrics["workflow_step_recall"].denominator == 4
    assert metrics["workflow_ordered_step_recall"].denominator == 4
    assert metrics["workflow_recall"].value == 0


def test_partial_metrics_limit_repeated_action_consumption(
    workflow_evaluation: EvaluationInputs,
) -> None:
    inputs = _with_workflows(
        workflow_evaluation, (_ground_truth(("read", "read", "submit")),),
        (_observed(("read", "submit")),),
    )
    metrics = {
        item.metric_id: item
        for item in _evaluate(inputs, WORKFLOW_SUBSEQUENCE_PROFILE).metrics
    }

    for metric_id in ("workflow_step_recall", "workflow_ordered_step_recall"):
        assert metrics[metric_id] == Metric(metric_id, 2 / 3, 2, 3)
    assert metrics["workflow_recall"].value == 0


def test_partial_metrics_micro_average_ground_truth_steps(
    workflow_evaluation: EvaluationInputs,
) -> None:
    inputs = _with_workflows(
        workflow_evaluation,
        (
            _ground_truth(("open", "read", "edit", "submit"), "long-flow"),
            _ground_truth(("logout",), "short-flow"),
        ),
        (_observed(("open", "read", "edit", "logout")),),
    )
    metrics = {
        item.metric_id: item
        for item in _evaluate(inputs, WORKFLOW_SUBSEQUENCE_PROFILE).metrics
    }

    for metric_id in ("workflow_step_recall", "workflow_ordered_step_recall"):
        assert metrics[metric_id] == Metric(metric_id, 4 / 5, 4, 5)
    assert metrics["workflow_recall"].value == 1 / 2


@pytest.mark.parametrize("has_expected", [True, False])
def test_partial_metrics_distinguish_zero_coverage_from_zero_denominator(
    workflow_evaluation: EvaluationInputs, has_expected: bool,
) -> None:
    inputs = _with_workflows(
        workflow_evaluation,
        (_ground_truth(("open", "read")),) if has_expected else (),
        (),
    )
    metrics = {
        item.metric_id: item
        for item in _evaluate(inputs, WORKFLOW_SUBSEQUENCE_PROFILE).metrics
    }
    for metric_id in ("workflow_step_recall", "workflow_ordered_step_recall"):
        assert metrics[metric_id] == Metric(
            metric_id, 0 if has_expected else None, 0, 2 if has_expected else 0,
        )


def test_legacy_profile_does_not_add_partial_metrics_or_notes(
    workflow_evaluation: EvaluationInputs,
) -> None:
    result = _evaluate(workflow_evaluation, DEFAULT_MATCHING_PROFILE)
    metric_ids = {item.metric_id for item in result.metrics}

    assert "workflow_step_recall" not in metric_ids
    assert "workflow_ordered_step_recall" not in metric_ids
    assert not any("LCS" in note for note in result.notes)


def test_v2_notes_explain_partial_metric_selection_and_limits(
    workflow_evaluation: EvaluationInputs,
) -> None:
    result = _evaluate(workflow_evaluation, WORKFLOW_SUBSEQUENCE_PROFILE)
    notes = "\n".join(result.notes)

    assert "workflow_step_recall" in notes
    assert "workflow_ordered_step_recall" in notes
    assert "전체 GT 단계 수 합계" in notes
    assert "LCS 길이→단계 일치 수→원문 workflow_id 오름차순" in notes
    assert "같은 흐름에서 두 분자를 계산" in notes
    assert "다른 workflow/계정의 단계를 합치지 않음" in notes
    assert "LLM 생성 품질을 평가하지 않음" in notes


def test_public_evaluate_v2_publishes_valid_output_without_changing_inputs(
    evaluate_arguments: Arguments,
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    context = {**context, "matching_profile": WORKFLOW_SUBSEQUENCE_PROFILE}
    output_path = Path(context["run_root"]) / output_dir / "evaluation_results.json"
    output_path.unlink()
    original_hashes = {
        name: calculate_sha256(Path(context["project_root"] if name == "ground_truth"
                                   else context["run_root"]) / descriptor["path"])
        for name, descriptor in input_paths.items()
    }
    inputs = prepare_evaluation_inputs(
        parse_evaluate_request(input_paths, output_dir, context)
    )
    expected_step_count = sum(
        len(workflow["ordered_actions"]) for workflow in inputs.ground_truth.workflows
    )

    response = run("evaluate", input_paths, output_dir, context)

    assert response["status"] == "completed"
    assert response["sha256"] == calculate_sha256(output_path)
    artifact = load_json(output_path)
    validate_schema(artifact, EVALUATION_RESULTS_SCHEMA)
    assert artifact["schema_version"] == "0.3.0"
    assert artifact["data"]["matching_profile"] == WORKFLOW_SUBSEQUENCE_PROFILE
    metrics = {item["metric_id"]: item for item in artifact["data"]["metrics"]}
    assert expected_step_count > 0
    for metric_id in ("workflow_step_recall", "workflow_ordered_step_recall"):
        assert metrics[metric_id] == {
            "metric_id": metric_id, "value": 1,
            "numerator": expected_step_count, "denominator": expected_step_count,
        }
    assert {reference["sha256"] for reference in artifact["input_refs"]} == {
        digest for name, digest in original_hashes.items() if name != "ground_truth"
    }
    assert artifact["data"]["ground_truth_ref"]["sha256"] == original_hashes["ground_truth"]
    for name, descriptor in input_paths.items():
        root = context["project_root"] if name == "ground_truth" else context["run_root"]
        assert calculate_sha256(Path(root) / descriptor["path"]) == original_hashes[name]
