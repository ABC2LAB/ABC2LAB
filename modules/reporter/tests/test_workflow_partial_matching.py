from collections.abc import Sequence
from copy import deepcopy
from dataclasses import FrozenInstanceError
from itertools import combinations, product
from typing import Any

import pytest

from modules.reporter.matching import (
    WorkflowMatch,
    count_ordered_workflow_step_matches,
    count_workflow_step_matches,
    select_workflow_match,
)


def _ground_truth(actions: Sequence[str]) -> dict[str, Any]:
    return {
        "gt_workflow_id": "logical-flow",
        "name": "logical flow name",
        "ordered_actions": list(actions),
        "constraints": [],
    }


def _observed(workflow_id: str, actions: Sequence[str]) -> dict[str, Any]:
    return {
        "workflow_id": workflow_id,
        "name": "observed flow name",
        "role_ids": [],
        "steps": [
            {
                "step_id": f"step-{index}", "order": index,
                "action": action, "request_ids": [],
            }
            for index, action in enumerate(actions)
        ],
        "dependencies": [],
        "basis": "inferred",
        "evidence_refs": [],
    }


@pytest.mark.parametrize(
    ("expected", "observed", "match_count"),
    [
        (("open", "read"), ("open", "read"), 2),
        (("open", "read"), ("login", "open", "read", "logout"), 2),
        (("open", "read", "submit"), ("open", "submit"), 2),
        (("open", "read", "submit"), ("submit", "read", "open"), 3),
        (("read", "read"), ("read",), 1),
        (("read",), ("read", "read"), 1),
        (("read", "read", "open"), ("read", "open", "read"), 3),
        (("read_order",), ("read_orders",), 0),
        (("open",), (), 0),
        ((), ("open",), 0),
        ((), (), 0),
    ],
)
def test_counts_workflow_steps_with_occurrence_limits(
    expected: tuple[str, ...], observed: tuple[str, ...], match_count: int,
) -> None:
    assert count_workflow_step_matches(expected, observed) == match_count


@pytest.mark.parametrize(
    ("expected", "observed", "match_count"),
    [
        (("open", "read"), ("open", "read"), 2),
        (("open", "read"), ("login", "open", "inspect", "read", "logout"), 2),
        (("open", "read", "submit"), ("open", "submit"), 2),
        (("open", "read", "submit"), ("submit", "read", "open"), 1),
        (("open", "read", "edit", "submit"), ("open", "edit", "read", "submit"), 3),
        (("read", "read"), ("read",), 1),
        (("read",), ("read", "read"), 1),
        (("read", "read", "open"), ("read", "open", "read"), 2),
        (("read_order",), ("read_orders",), 0),
        (("open",), (), 0),
        ((), ("open",), 0),
        ((), (), 0),
    ],
)
def test_counts_longest_common_ordered_steps(
    expected: tuple[str, ...], observed: tuple[str, ...], match_count: int,
) -> None:
    assert count_ordered_workflow_step_matches(expected, observed) == match_count


def _subsequences(actions: tuple[str, ...]) -> set[tuple[str, ...]]:
    return {
        tuple(actions[index] for index in indexes)
        for length in range(len(actions) + 1)
        for indexes in combinations(range(len(actions)), length)
    }


def test_counts_agree_with_independent_exhaustive_references() -> None:
    action_sequences = [
        actions
        for length in range(5)
        for actions in product(("open", "read"), repeat=length)
    ]
    for expected, observed in product(action_sequences, repeat=2):
        remaining_actions = list(observed)
        unordered_count = 0
        for action in expected:
            if action in remaining_actions:
                remaining_actions.remove(action)
                unordered_count += 1
        common_sequences = _subsequences(expected) & _subsequences(observed)
        ordered_count = max(map(len, common_sequences))

        assert count_workflow_step_matches(expected, observed) == unordered_count
        assert count_ordered_workflow_step_matches(expected, observed) == ordered_count
        assert 0 <= ordered_count <= unordered_count <= len(expected)


@pytest.mark.parametrize(
    ("expected", "observed", "expected_match"),
    [
        ("ABCD", (("f1", "ABCD"),), WorkflowMatch("f1", 4, 4, 4)),
        ("ABCD", (("f1", "ABC"),), WorkflowMatch("f1", 3, 3, 4)),
        ("ABCD", (("f1", "ACBD"),), WorkflowMatch("f1", 4, 3, 4)),
        ("ABCD", (("f1", "AXBCD"),), WorkflowMatch("f1", 4, 4, 4)),
        ("ABBC", (("f1", "ABC"),), WorkflowMatch("f1", 3, 3, 4)),
        ("ABCD", (("f1", "DCBA"), ("f2", "ABC")), WorkflowMatch("f2", 3, 3, 4)),
        ("ABCD", (("f1", "ABC"), ("f2", "ACBD")), WorkflowMatch("f2", 4, 3, 4)),
        ("ABCD", (("f_b", "ABC"), ("f_a", "ABC")), WorkflowMatch("f_a", 3, 3, 4)),
        ("ABCD", (("f1", "AB"), ("f2", "CD")), WorkflowMatch("f1", 2, 2, 4)),
        ("ABCD", (), WorkflowMatch(None, 0, 0, 4)),
        ("ABCD", (("f1", "XYZ"),), WorkflowMatch(None, 0, 0, 4)),
    ],
)
def test_selects_one_workflow_for_both_counts(
    expected: str,
    observed: tuple[tuple[str, str], ...],
    expected_match: WorkflowMatch,
) -> None:
    expected_workflow = _ground_truth(tuple(expected))
    observed_workflows = [
        _observed(workflow_id, tuple(actions)) for workflow_id, actions in observed
    ]
    assert select_workflow_match(expected_workflow, observed_workflows) == expected_match
    assert select_workflow_match(
        expected_workflow, observed_workflows[::-1],
    ) == expected_match


def test_selection_uses_normalized_actions_and_sorted_orders_without_mutation() -> None:
    expected = _ground_truth((" Open ", "Read_Order", "submit"))
    observed = _observed("flow-z", ("open", "inspect", " read_order "))
    observed["steps"].reverse()
    observed_workflows = [observed, _observed("flow-a", ("irrelevant",))]
    originals = deepcopy((expected, observed_workflows))

    assert select_workflow_match(expected, observed_workflows) == WorkflowMatch(
        "flow-z", 2, 2, 3,
    )
    assert (expected, observed_workflows) == originals


def test_selection_breaks_ties_using_original_opaque_workflow_ids() -> None:
    expected = _ground_truth(("open", "read"))
    observed_workflows = [
        _observed("flow-a", ("open", "read")),
        _observed("flow-Z", ("open", "read")),
    ]

    assert select_workflow_match(expected, observed_workflows) == WorkflowMatch(
        "flow-Z", 2, 2, 2,
    )


def test_selection_does_not_infer_synonyms_or_regenerate_tokens() -> None:
    expected = _ground_truth(("read_order", "create_order"))
    observed_workflows = [
        _observed("flow", ("read_orders", "submit_order")),
    ]

    assert select_workflow_match(expected, observed_workflows) == WorkflowMatch(
        None, 0, 0, 2,
    )


def test_selection_preserves_denominator_with_empty_observed_steps() -> None:
    assert select_workflow_match(
        _ground_truth(("open", "read")), [_observed("empty-flow", ())],
    ) == WorkflowMatch(None, 0, 0, 2)


def test_empty_expected_actions_have_no_selected_workflow() -> None:
    assert select_workflow_match(
        _ground_truth(()), [_observed("flow", ("open", "read"))],
    ) == WorkflowMatch(None, 0, 0, 0)


def test_selection_is_independent_for_each_ground_truth() -> None:
    observed_workflows = [_observed("session", ("open", "read", "submit"))]

    assert select_workflow_match(
        _ground_truth(("open", "read")), observed_workflows,
    ) == WorkflowMatch("session", 2, 2, 2)
    assert select_workflow_match(
        _ground_truth(("read", "submit")), observed_workflows,
    ) == WorkflowMatch("session", 2, 2, 2)


def test_match_result_is_immutable() -> None:
    result = WorkflowMatch("flow", 2, 1, 2)

    with pytest.raises(FrozenInstanceError):
        result.step_match_count = 0


def test_lcs_handles_longer_input_in_either_position() -> None:
    expected = ("open", "read", "submit")
    observed = ("noise",) * 1000 + ("open", "read")

    assert count_ordered_workflow_step_matches(expected, observed) == 2
    assert count_ordered_workflow_step_matches(observed, expected) == 2
