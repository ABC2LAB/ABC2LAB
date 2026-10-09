"""Resource identity matching preserves raw strings independently of UI labels."""

import copy
import json
from dataclasses import replace
from typing import Any

import pytest

from modules.reporter import entrypoint
from modules.reporter.contracts import prepare_evaluation_inputs
from modules.reporter.evaluation_output_adapter import EVALUATION_RESULTS_SCHEMA
from modules.reporter.evaluation_service import build_evaluation_results
from modules.reporter.exceptions import ContractValidationError
from modules.reporter.input_adapter import parse_evaluate_request
from modules.reporter.matching import case_signature, mapping_signature, matches_key
from modules.reporter.models import EvaluationInputs
from modules.reporter.utils.hashing import calculate_sha256
from modules.reporter.utils.validation import load_json, validate_schema

Arguments = tuple[dict[str, Any], str, dict[str, Any]]
DISTINCT_IDENTIFIERS = (
    ("Ab", "ab"),
    ("item-1", " item-1"),
    ("item-1", "item-1 "),
    ("a  b", "a b"),
    ("a\tb", "a b"),
    ("a\nb", "a b"),
    ("straße", "strasse"),
)
DISTINCT_FIXTURE_IDENTIFIERS = ("ORDER-B", " order-b", "order-b ")


def _resource_instance(
    identifiers: tuple[tuple[str, str], ...],
    resource_key: str = "record",
) -> dict[str, Any]:
    return {
        "resource_key": resource_key,
        "resource_scope": "instance",
        "match_key": {
            "resource_key": resource_key,
            "identifiers": [
                {"key": key, "value": value} for key, value in identifiers
            ],
        },
    }


@pytest.fixture
def resource_evaluation(evaluate_arguments: Arguments) -> EvaluationInputs:
    return prepare_evaluation_inputs(parse_evaluate_request(*evaluate_arguments))


@pytest.mark.parametrize("expected_value, observed_value", DISTINCT_IDENTIFIERS)
def test_resource_identifier_values_do_not_normalize(
    expected_value: str, observed_value: str,
) -> None:
    observed = _resource_instance((("external_id", observed_value),))

    assert not matches_key({"external_id": expected_value}, observed)


@pytest.mark.parametrize("value", ["Ab", " item-1 ", "a  b", "a\tb", " "])
def test_identical_raw_resource_identifiers_match(value: str) -> None:
    observed = _resource_instance((("external_id", value),))

    assert matches_key({"resource_type": "record", "external_id": value}, observed)


@pytest.mark.parametrize(
    "expected_key, observed_key",
    [("external_id", "EXTERNAL_ID"), ("external_id", " external_id ")],
)
def test_resource_identifier_names_do_not_normalize(
    expected_key: str, observed_key: str,
) -> None:
    observed = _resource_instance(((observed_key, "Ab"),))

    assert not matches_key({expected_key: "Ab"}, observed)


def test_nonempty_identifier_name_is_not_stripped() -> None:
    observed = _resource_instance(((" ", "Ab"),))

    assert matches_key({" ": "Ab"}, observed)


@pytest.mark.parametrize("resource_key", ["Record", " record", "record "])
def test_resource_kind_aliases_preserve_case_and_whitespace(
    resource_key: str,
) -> None:
    observed = _resource_instance((("external_id", "Ab"),), resource_key)

    assert not matches_key({"resource_type": "record"}, observed)
    assert not matches_key({"name": "record"}, observed)
    assert matches_key({"name": resource_key}, observed)


@pytest.mark.parametrize(
    "identifier",
    [
        ("method", "GET", "get"),
        ("path", "/items/", "/items"),
        ("url", "http://example.invalid/items", "https://example.invalid/items"),
    ],
)
def test_resource_identifier_names_do_not_trigger_generic_normalization(
    identifier: tuple[str, str, str],
) -> None:
    key, expected_value, observed_value = identifier
    observed = _resource_instance(((key, observed_value),))

    assert not matches_key({key: expected_value}, observed)


def test_composite_resource_key_matches_independently_of_identifier_order() -> None:
    observed = _resource_instance((("tenant_id", " T-1 "), ("external_id", "Ab")))
    expected = _resource_instance((("external_id", "Ab"), ("tenant_id", " T-1 ")))
    before = copy.deepcopy((expected, observed))

    assert matches_key(expected, observed)
    assert matches_key({"external_id": "Ab", "tenant_id": " T-1 "}, observed)
    assert (expected, observed) == before


def test_composite_resource_key_requires_every_raw_identifier() -> None:
    observed = _resource_instance((("tenant_id", " T-1 "), ("external_id", "Ab")))

    assert not matches_key({"external_id": "Ab", "tenant_id": "T-1"}, observed)


def test_canonical_resource_identifiers_override_legacy_properties() -> None:
    observed = _resource_instance((("external_id", "Ab"),))
    observed.update({"external_id": "ab", "resource_type": "stale", "name": "stale"})

    assert matches_key({"resource_type": "record", "external_id": "Ab"}, observed)
    assert not matches_key({"external_id": "ab"}, observed)
    assert not matches_key({"name": "stale"}, observed)


@pytest.mark.parametrize("expected_value, observed_value", DISTINCT_IDENTIFIERS)
def test_resource_duplicate_signatures_preserve_raw_identity(
    expected_value: str, observed_value: str,
) -> None:
    expected = {"resource_type": "record", "external_id": expected_value}
    observed = {"resource_type": "record", "external_id": observed_value}

    assert mapping_signature(expected, entity_type="Resource") != mapping_signature(
        observed, entity_type="Resource",
    )
    case = {
        "category": "authorization",
        "vulnerability_type": "horizontal_access",
        "actor_alias": "User_A",
        "resource_match_key": expected,
    }
    assert case_signature(case) != case_signature({
        **case, "resource_match_key": observed,
    })


def test_composite_resource_duplicate_signatures_ignore_identifier_order() -> None:
    first = _resource_instance((("tenant_id", "T-1"), ("external_id", "Ab")))
    second = _resource_instance((("external_id", "Ab"), ("tenant_id", "T-1")))

    assert mapping_signature(first, entity_type="Resource") == mapping_signature(
        second, entity_type="Resource",
    )


@pytest.mark.parametrize("key", ["EXTERNAL_ID", " external_id "])
def test_resource_duplicate_signatures_preserve_identifier_names(key: str) -> None:
    assert mapping_signature({"external_id": "Ab"}, entity_type="Resource") != (
        mapping_signature({key: "Ab"}, entity_type="Resource")
    )


def test_case_labels_keep_normalization_without_changing_resource_identity() -> None:
    case = {
        "category": "authorization",
        "vulnerability_type": "horizontal_access",
        "actor_alias": "User_A",
        "resource_match_key": {"external_id": " Ab "},
    }

    assert case_signature(case) == case_signature({
        **case, "category": " AUTHORIZATION ",
        "vulnerability_type": "HORIZONTAL_ACCESS", "actor_alias": " user_a ",
    })


@pytest.mark.parametrize(
    "expected_value, observed_value",
    [
        (None, False),
        (False, 0),
        (5, "5"),
        ({"value": "Ab"}, {"value": "ab"}),
        (["Ab"], ["ab"]),
    ],
)
def test_resource_match_key_preserves_types_and_nested_raw_values(
    expected_value: Any, observed_value: Any,
) -> None:
    assert not matches_key(
        {"external_id": expected_value}, {"external_id": observed_value},
        entity_type="Resource",
    )
    assert matches_key(
        {"external_id": expected_value}, {"external_id": expected_value},
        entity_type="Resource",
    )


def test_legacy_resource_matching_uses_explicit_entity_type() -> None:
    expected = {"resource_type": "record", "external_id": "Ab"}
    observed = {"resource_type": "record", "external_id": "ab"}

    assert not matches_key(expected, observed, entity_type="Resource")


@pytest.mark.parametrize(
    "entity_type, expected, observed",
    [
        ("Role", {"name": " User "}, {"NAME": "user"}),
        ("Endpoint", {"method": "get", "path_template": "/items/"},
         {"method": "GET", "path_template": "/items"}),
        ("Page", {"path": "/items/"}, {"path": "/items"}),
        ("Parameter", {"name": "ITEM_ID", "location": "PATH"},
         {"name": "item_id", "location": "path"}),
    ],
)
def test_non_resource_entities_keep_existing_normalization(
    entity_type: str, expected: dict[str, Any], observed: dict[str, Any],
) -> None:
    assert matches_key(expected, observed, entity_type=entity_type)
    assert mapping_signature(expected, entity_type=entity_type) == mapping_signature(
        observed, entity_type=entity_type,
    )


@pytest.mark.parametrize("identifier", ["order-b", *DISTINCT_FIXTURE_IDENTIFIERS])
def test_resource_structure_and_case_evaluation_use_raw_identity(
    resource_evaluation: EvaluationInputs, identifier: str,
) -> None:
    resource = next(
        item for item in resource_evaluation.ground_truth.entities
        if item["entity_type"] == "Resource"
    )
    match_key = {**resource["match_key"], "external_id": identifier}
    ground_truth = replace(
        resource_evaluation.ground_truth,
        entities=tuple(
            {**item, "match_key": match_key} if item is resource else item
            for item in resource_evaluation.ground_truth.entities
        ),
        cases=tuple(
            {**item, "resource_match_key": match_key}
            for item in resource_evaluation.ground_truth.cases
        ),
    )

    result = build_evaluation_results(replace(
        resource_evaluation, ground_truth=ground_truth,
    ))

    assert result.status == "completed"
    assert result.data is not None
    matched_count = int(identifier == "order-b")
    metrics = {item.metric_id: item for item in result.data.metrics}
    assert metrics["resource_recall"].numerator == matched_count
    assert metrics["resource_recall"].denominator == 1
    assert result.data.candidate_counts.tp == matched_count
    assert result.data.candidate_counts.fn == 1 - matched_count
    assert result.data.confirmed_counts.tp == matched_count
    assert result.data.confirmed_counts.fn == 1 - matched_count
    assert result.data.unverified_counts.to_mapping() == {
        "policy_blocked": 0, "approval_pending": 1, "indeterminate": 1,
    }


@pytest.mark.parametrize("identifier", ["order-b", *DISTINCT_FIXTURE_IDENTIFIERS])
def test_resource_relationship_evaluation_uses_raw_identity(
    resource_evaluation: EvaluationInputs, identifier: str,
) -> None:
    entities = []
    for item in resource_evaluation.ground_truth.entities:
        if item["entity_type"] == "Role":
            item = {**item, "entity_type": "User", "match_key": {"alias": "USER_B"}}
        elif item["entity_type"] == "Resource":
            item = {**item, "match_key": {
                "resource_type": "order", "external_id": identifier,
            }}
        entities.append(item)
    ground_truth = replace(
        resource_evaluation.ground_truth,
        entities=tuple(entities),
    )

    result = build_evaluation_results(replace(
        resource_evaluation, ground_truth=ground_truth,
    ))

    assert result.data is not None
    metric = next(
        item for item in result.data.metrics if item.metric_id == "relationship_recall"
    )
    assert metric.numerator == int(identifier == "order-b")
    assert metric.denominator == 1


@pytest.mark.parametrize("identifier", DISTINCT_FIXTURE_IDENTIFIERS)
def test_distinct_raw_resources_are_not_duplicate_ground_truth_entities(
    resource_evaluation: EvaluationInputs, identifier: str,
) -> None:
    resource = next(
        item for item in resource_evaluation.ground_truth.entities
        if item["entity_type"] == "Resource"
    )
    ground_truth = replace(
        resource_evaluation.ground_truth,
        entities=(*resource_evaluation.ground_truth.entities, {
            **resource, "gt_id": "gt_distinct_resource",
            "match_key": {**resource["match_key"], "external_id": identifier},
        }),
    )

    result = build_evaluation_results(replace(
        resource_evaluation, ground_truth=ground_truth,
    ))

    assert result.status == "completed"
    assert result.data is not None
    metric = next(
        item for item in result.data.metrics if item.metric_id == "resource_recall"
    )
    assert metric.numerator == 1
    assert metric.denominator == 2


@pytest.mark.parametrize("identifier", DISTINCT_FIXTURE_IDENTIFIERS)
def test_distinct_raw_resources_are_not_duplicate_ground_truth_cases(
    resource_evaluation: EvaluationInputs, identifier: str,
) -> None:
    case = next(
        item for item in resource_evaluation.ground_truth.cases if item["is_vulnerable"]
    )
    ground_truth = replace(
        resource_evaluation.ground_truth,
        cases=(*resource_evaluation.ground_truth.cases, {
            **case, "case_id": "case_distinct_resource",
            "resource_match_key": {**case["resource_match_key"], "external_id": identifier},
        }),
    )

    result = build_evaluation_results(replace(
        resource_evaluation, ground_truth=ground_truth,
    ))

    assert result.status == "completed"
    assert result.data is not None
    assert result.data.candidate_counts.tp == 1
    assert result.data.candidate_counts.fn == 1
    assert result.data.confirmed_counts.tp == 1
    assert result.data.confirmed_counts.fn == 1


@pytest.mark.parametrize("kind", ["entity", "case"])
def test_identical_raw_resource_ground_truth_duplicates_are_rejected(
    resource_evaluation: EvaluationInputs, kind: str,
) -> None:
    ground_truth = resource_evaluation.ground_truth
    if kind == "entity":
        resource = next(
            item for item in ground_truth.entities if item["entity_type"] == "Resource"
        )
        ground_truth = replace(ground_truth, entities=(
            *ground_truth.entities, {**resource, "gt_id": "gt_duplicate_resource"},
        ))
    else:
        ground_truth = replace(ground_truth, cases=(
            *ground_truth.cases, {**ground_truth.cases[0], "case_id": "case_duplicate_resource"},
        ))

    with pytest.raises(ContractValidationError, match=f"ground truth {kind}.*중복"):
        build_evaluation_results(replace(
            resource_evaluation, ground_truth=ground_truth,
        ))


@pytest.mark.parametrize("kind", ["entity", "case"])
def test_composite_resource_ground_truth_duplicates_ignore_identifier_order(
    resource_evaluation: EvaluationInputs, kind: str,
) -> None:
    first = _resource_instance((("tenant_id", "T-1"), ("external_id", "Ab")))
    second = _resource_instance((("external_id", "Ab"), ("tenant_id", "T-1")))
    ground_truth = resource_evaluation.ground_truth
    if kind == "entity":
        entities = tuple(
            item for item in ground_truth.entities if item["entity_type"] != "Resource"
        )
        resource = next(
            item for item in ground_truth.entities if item["entity_type"] == "Resource"
        )
        ground_truth = replace(ground_truth, entities=(
            *entities, {**resource, "match_key": first},
            {**resource, "gt_id": "gt_duplicate_resource", "match_key": second},
        ))
    else:
        case = ground_truth.cases[0]
        ground_truth = replace(ground_truth, cases=(
            {**case, "resource_match_key": first},
            {**case, "case_id": "case_duplicate_resource", "resource_match_key": second},
        ))

    with pytest.raises(ContractValidationError, match=f"ground truth {kind}.*중복"):
        build_evaluation_results(replace(
            resource_evaluation, ground_truth=ground_truth,
        ))


@pytest.mark.parametrize("identifier", DISTINCT_FIXTURE_IDENTIFIERS)
def test_public_evaluate_preserves_inputs_and_does_not_merge_raw_resources(
    evaluate_arguments: Arguments, identifier: str,
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    descriptor = input_paths["ground_truth"]
    ground_truth_path = context["project_root"] / descriptor["path"]
    ground_truth = load_json(ground_truth_path)
    resource = next(
        item for item in ground_truth["data"]["entities"]
        if item["entity_type"] == "Resource"
    )
    ground_truth["data"]["entities"].append({
        **resource, "gt_id": "gt_distinct_resource",
        "match_key": {**resource["match_key"], "external_id": identifier},
    })
    ground_truth_path.write_text(
        json.dumps(ground_truth, ensure_ascii=False), encoding="utf-8",
    )
    descriptor["sha256"] = calculate_sha256(ground_truth_path)
    output_path = context["run_root"] / output_dir / "evaluation_results.json"
    output_path.unlink()

    response = entrypoint.run("evaluate", input_paths, output_dir, context)

    assert response["status"] == "completed"
    output = load_json(output_path)
    validate_schema(output, EVALUATION_RESULTS_SCHEMA)
    assert response["sha256"] == calculate_sha256(output_path)
    assert output["data"]["ground_truth_ref"]["sha256"] == descriptor["sha256"]
    metric = next(
        item for item in output["data"]["metrics"]
        if item["metric_id"] == "resource_recall"
    )
    assert metric["numerator"] == 1
    assert metric["denominator"] == 2
    assert output["data"]["confirmed_counts"] == {"tp": 1, "fp": 0, "fn": 0}
    assert load_json(ground_truth_path) == ground_truth
    for name, source in input_paths.items():
        root = context["project_root"] if name == "ground_truth" else context["run_root"]
        assert calculate_sha256(root / source["path"]) == source["sha256"]
