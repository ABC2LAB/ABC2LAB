import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from time import perf_counter
from typing import Any

import pytest

from modules.reporter.contracts import prepare_evaluation_inputs
from modules.reporter.entrypoint import run
from modules.reporter.evaluation_output_adapter import (
    EVALUATION_RESULTS_SCHEMA,
    build_evaluation_artifact,
    publish_evaluation_artifact,
    validate_evaluation_artifact_against_inputs,
)
from modules.reporter.evaluation_service import build_evaluation_results
from modules.reporter.exceptions import (
    ContractValidationError,
    OutputArtifactExistsError,
)
from modules.reporter.input_adapter import parse_evaluate_request
from modules.reporter.input_validation import validate_semantic_analysis
from modules.reporter.matching import matches_key
from modules.reporter.models import (
    ErrorItem,
    EvaluationInputs,
    InputArtifact,
    Metric,
)
from modules.reporter.utils.hashing import calculate_sha256
from modules.reporter.utils.validation import load_json, validate_schema

Arguments = tuple[dict[str, Any], str, dict[str, Any]]
FIXED_TIMESTAMP = "2026-10-07T04:00:00Z"
DISCOVERED_ENDPOINT_ID = "opaque-discovered-endpoint"
DISCOVERED_PARAMETER_ID = "opaque-discovered-parameter"
DISCOVERED_ENDPOINT_KEY = {
    "method": "POST",
    "path_template": "/api/discovered/{item_id}",
}


@pytest.fixture
def prepared_evaluation(evaluate_arguments: Arguments) -> EvaluationInputs:
    input_paths, output_dir, context = evaluate_arguments
    request = parse_evaluate_request(input_paths, output_dir, context)
    return prepare_evaluation_inputs(request)


@pytest.fixture
def parameter_node_evaluation(
    prepared_evaluation: EvaluationInputs,
) -> EvaluationInputs:
    value = deepcopy(prepared_evaluation.semantic_analysis.value)
    _add_parameter_discovery(value)
    semantic = replace(prepared_evaluation.semantic_analysis, value=value)
    validate_semantic_analysis(semantic)
    ground_truth = replace(
        prepared_evaluation.ground_truth,
        entities=(
            *prepared_evaluation.ground_truth.entities,
            {
                "gt_id": "gt_discovered_parameter",
                "entity_type": "Parameter",
                "match_key": {
                    "endpoint": dict(DISCOVERED_ENDPOINT_KEY),
                    "name": "item_id",
                    "location": "path",
                },
            },
        ),
    )
    return replace(
        prepared_evaluation,
        semantic_analysis=semantic,
        ground_truth=ground_truth,
    )


def _add_parameter_discovery(value: dict[str, Any]) -> None:
    value["data"]["nodes"].extend([
        _inferred_node(DISCOVERED_ENDPOINT_ID, "Endpoint", DISCOVERED_ENDPOINT_KEY),
        _inferred_node(
            DISCOVERED_PARAMETER_ID,
            "Parameter",
            {
                "name": "item_id",
                "location": "path",
                "value_type": "unknown",
                "is_sensitive": False,
            },
        ),
    ])
    value["data"]["relationships"].append(
        {
            "relationship_id": "opaque-discovered-use",
            "source_id": DISCOVERED_ENDPOINT_ID,
            "target_id": DISCOVERED_PARAMETER_ID,
            "relation_type": "USE",
            "properties": {},
            "basis": "inferred",
            "evidence_refs": [],
        }
    )


def _inferred_node(
    node_id: str,
    node_type: str,
    properties: dict[str, Any],
) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "node_type": node_type,
        "properties": deepcopy(properties),
        "basis": "inferred",
        "evidence_refs": [],
    }


def _parameter_metric(inputs: EvaluationInputs) -> Metric:
    result = build_evaluation_results(inputs)
    assert result.data is not None
    return next(
        item for item in result.data.metrics if item.metric_id == "parameter_recall"
    )


def test_build_evaluation_results_matches_structure_and_cases(
    prepared_evaluation: EvaluationInputs,
) -> None:
    result = build_evaluation_results(prepared_evaluation)

    assert result.status == "completed"
    assert result.errors == ()
    assert result.data is not None
    metrics = {item.metric_id: item for item in result.data.metrics}
    assert metrics["role_recall"].value == 1
    assert metrics["endpoint_recall"].value == 1
    assert metrics["resource_recall"].value == 1
    assert metrics["relationship_recall"].value == 0
    assert metrics["workflow_recall"].value == 1
    assert metrics["user_recall"].value is None
    assert metrics["user_recall"].denominator == 0
    assert result.data.candidate_counts.to_mapping() == {
        "tp": 1,
        "fp": 2,
        "fn": 0,
    }
    assert metrics["candidate_precision"].value == 1 / 3
    assert metrics["candidate_precision"].denominator == 3
    assert result.data.confirmed_counts.to_mapping() == {
        "tp": 1,
        "fp": 0,
        "fn": 0,
    }
    assert result.data.unverified_counts.to_mapping() == {
        "policy_blocked": 0,
        "approval_pending": 1,
        "indeterminate": 1,
    }
    assert set(result.data.models) == {
        "semantic_analyzer",
        "scenario_generator",
    }
    assert result.data.run_metrics == {
        "duration_ms": 70,
        "llm_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "peak_memory_mb": 32,
    }
    assert any(
        "일대일 매칭되지 않은 후보: 1개" in note
        for note in result.data.notes
    )


def test_kg_0_2_resource_instance_matches_legacy_ground_truth_key() -> None:
    expected = {
        "resource_type": "order",
        "external_id": "order-b",
    }
    observed = {
        "resource_key": "order",
        "resource_scope": "instance",
        "match_key": {
            "resource_key": "order",
            "identifiers": [
                {
                    "key": "external_id",
                    "value": "order-b",
                }
            ],
        },
    }

    assert matches_key(expected, observed)


def test_kg_0_2_resource_type_matches_ground_truth_name() -> None:
    expected = {
        "name": "order",
    }
    observed = {
        "resource_key": "order",
        "resource_scope": "type",
        "match_key": None,
    }

    assert matches_key(expected, observed)


def test_kg_0_2_resource_instance_rejects_different_identifier() -> None:
    expected = {
        "resource_type": "order",
        "external_id": "order-c",
    }
    observed = {
        "resource_key": "order",
        "resource_scope": "instance",
        "match_key": {
            "resource_key": "order",
            "identifiers": [
                {
                    "key": "external_id",
                    "value": "order-b",
                }
            ],
        },
    }

    assert not matches_key(expected, observed)


def test_entity_matching_uses_normalized_keys_not_ground_truth_ids(
    prepared_evaluation: EvaluationInputs,
) -> None:
    endpoint = prepared_evaluation.ground_truth.entities[1]
    changed_endpoint = {
        **endpoint,
        "gt_id": "different_external_identifier",
        "match_key": {
            "method": "get",
            "path_template": "/api/orders/{order_id}/",
        },
    }
    ground_truth = replace(
        prepared_evaluation.ground_truth,
        entities=(
            prepared_evaluation.ground_truth.entities[0],
            changed_endpoint,
            prepared_evaluation.ground_truth.entities[2],
        ),
    )
    changed = replace(
        prepared_evaluation,
        ground_truth=ground_truth,
    )

    result = build_evaluation_results(changed)

    assert result.data is not None
    endpoint_metric = next(
        item
        for item in result.data.metrics
        if item.metric_id == "endpoint_recall"
    )
    assert endpoint_metric.value == 1


def test_parameter_matching_includes_normalized_endpoint_key(
    prepared_evaluation: EvaluationInputs,
) -> None:
    parameter = {
        "gt_id": "gt_parameter_order_id",
        "entity_type": "Parameter",
        "match_key": {
            "endpoint": {
                "method": "get",
                "path_template": "/api/orders/{order_id}/",
            },
            "name": "ORDER_ID",
            "location": "PATH",
        },
    }
    ground_truth = replace(
        prepared_evaluation.ground_truth,
        entities=(
            *prepared_evaluation.ground_truth.entities,
            parameter,
        ),
    )
    changed = replace(
        prepared_evaluation,
        ground_truth=ground_truth,
    )

    result = build_evaluation_results(changed)

    assert result.data is not None
    metric = next(
        item
        for item in result.data.metrics
        if item.metric_id == "parameter_recall"
    )
    assert metric.value == 1


@pytest.mark.parametrize("basis", ["observed", "inferred"])
def test_parameter_nodes_count_discovery_without_requests(
    parameter_node_evaluation: EvaluationInputs,
    basis: str,
) -> None:
    semantic_data = parameter_node_evaluation.semantic_analysis.data
    parameter = next(
        node for node in semantic_data["nodes"]
        if node["node_id"] == DISCOVERED_PARAMETER_ID
    )
    parameter["basis"] = basis
    assert all(
        request["endpoint_id"] != DISCOVERED_ENDPOINT_ID
        for request in semantic_data["normalized_requests"]
    )

    metric = _parameter_metric(parameter_node_evaluation)

    assert (metric.numerator, metric.denominator, metric.value) == (1, 1, 1)


@pytest.mark.parametrize(
    "node_type,field,value",
    [
        ("Endpoint", "method", "GET"),
        ("Endpoint", "path_template", "/api/unrelated/{item_id}"),
        ("Parameter", "name", "other_id"),
        ("Parameter", "location", "query"),
        ("Endpoint", "method", ["POST"]),
        ("Endpoint", "path_template", None),
        ("Parameter", "name", 42),
        ("Parameter", "location", None),
    ],
)
def test_parameter_nodes_require_matching_endpoint_name_and_location(
    parameter_node_evaluation: EvaluationInputs,
    node_type: str,
    field: str,
    value: Any,
) -> None:
    node_id = (
        DISCOVERED_ENDPOINT_ID if node_type == "Endpoint" else DISCOVERED_PARAMETER_ID
    )
    node = next(
        node for node in parameter_node_evaluation.semantic_analysis.data["nodes"]
        if node["node_id"] == node_id
    )
    node["properties"][field] = value

    metric = _parameter_metric(parameter_node_evaluation)

    assert (metric.numerator, metric.denominator, metric.value) == (0, 1, 0)


@pytest.mark.parametrize(
    "node_type,field",
    [
        ("Endpoint", "method"), ("Endpoint", "path_template"),
        ("Parameter", "name"), ("Parameter", "location"),
    ],
)
def test_parameter_nodes_do_not_invent_missing_match_fields(
    parameter_node_evaluation: EvaluationInputs,
    node_type: str,
    field: str,
) -> None:
    node_id = (
        DISCOVERED_ENDPOINT_ID if node_type == "Endpoint" else DISCOVERED_PARAMETER_ID
    )
    node = next(
        node for node in parameter_node_evaluation.semantic_analysis.data["nodes"]
        if node["node_id"] == node_id
    )
    del node["properties"][field]

    assert _parameter_metric(parameter_node_evaluation).numerator == 0


@pytest.mark.parametrize(
    "edge_case",
    ["missing", "reversed", "wrong_source_type", "wrong_target_type", "not_use"],
)
def test_parameter_nodes_require_endpoint_to_parameter_use(
    parameter_node_evaluation: EvaluationInputs,
    edge_case: str,
) -> None:
    data = parameter_node_evaluation.semantic_analysis.data
    edge = next(
        item for item in data["relationships"]
        if item["target_id"] == DISCOVERED_PARAMETER_ID
    )
    if edge_case == "missing":
        data["relationships"].remove(edge)
    elif edge_case == "reversed":
        edge["source_id"], edge["target_id"] = edge["target_id"], edge["source_id"]
    elif edge_case == "wrong_source_type":
        edge["source_id"] = next(
            node["node_id"] for node in data["nodes"] if node["node_type"] == "User"
        )
    elif edge_case == "wrong_target_type":
        edge["target_id"] = next(
            node["node_id"] for node in data["nodes"]
            if node["node_type"] == "Resource"
        )
    else:
        edge["relation_type"] = "CALL"

    assert _parameter_metric(parameter_node_evaluation).numerator == 0


def test_parameter_node_and_request_duplicates_do_not_inflate_recall(
    parameter_node_evaluation: EvaluationInputs,
) -> None:
    data = parameter_node_evaluation.semantic_analysis.data
    parameter = {
        "parameter_id": DISCOVERED_PARAMETER_ID,
        "name": "item_id",
        "location": "path",
        "value_type": "unknown",
        "is_sensitive": False,
    }
    request = {
        **deepcopy(data["normalized_requests"][0]),
        "request_id": "synthetic-discovery-request",
        "endpoint_id": DISCOVERED_ENDPOINT_ID,
        **DISCOVERED_ENDPOINT_KEY,
        "parameters": [
            parameter,
            {**parameter, "parameter_id": "opaque-duplicate-parameter"},
        ],
    }
    data["normalized_requests"].extend([
        request, {**request, "request_id": "synthetic-discovery-request-copy"},
    ])

    metric = _parameter_metric(parameter_node_evaluation)

    assert (metric.numerator, metric.denominator, metric.value) == (1, 1, 1)


def test_parameter_discovery_preserves_inputs_and_diagnosis_counts(
    prepared_evaluation: EvaluationInputs,
    parameter_node_evaluation: EvaluationInputs,
) -> None:
    baseline = build_evaluation_results(prepared_evaluation)
    original = deepcopy(parameter_node_evaluation.semantic_analysis.value)

    result = build_evaluation_results(parameter_node_evaluation)

    assert result.data is not None and baseline.data is not None
    assert result.data.candidate_counts == baseline.data.candidate_counts
    assert result.data.confirmed_counts == baseline.data.confirmed_counts
    assert result.data.unverified_counts == baseline.data.unverified_counts
    assert {
        metric.metric_id: metric for metric in result.data.metrics
        if metric.metric_id != "parameter_recall"
    } == {
        metric.metric_id: metric for metric in baseline.data.metrics
        if metric.metric_id != "parameter_recall"
    }
    assert parameter_node_evaluation.graph_snapshot == prepared_evaluation.graph_snapshot
    assert parameter_node_evaluation.semantic_analysis.value == original
    assert any("inferred Endpoint·Parameter" in note for note in result.data.notes)


def test_sensitive_parameter_discovery_uses_only_name_and_location(
    parameter_node_evaluation: EvaluationInputs,
) -> None:
    parameter = next(
        node for node in parameter_node_evaluation.semantic_analysis.data["nodes"]
        if node["node_id"] == DISCOVERED_PARAMETER_ID
    )
    parameter["properties"]["is_sensitive"] = True

    assert _parameter_metric(parameter_node_evaluation).value == 1


def test_failed_semantic_data_keeps_parameter_discovery_unmeasured(
    parameter_node_evaluation: EvaluationInputs,
) -> None:
    semantic = parameter_node_evaluation.semantic_analysis
    error = ErrorItem("SEMANTIC_FAILED", "합성 입력 실패", None, False)
    failed = replace(
        semantic,
        source=replace(semantic.source, status="failed"),
        errors=(error,),
        value={
            **semantic.value,
            "status": "failed",
            "data": None,
            "errors": [error.to_mapping()],
        },
    )

    metric = _parameter_metric(
        replace(parameter_node_evaluation, semantic_analysis=failed)
    )

    assert metric.numerator == 0
    assert metric.denominator == 1
    assert metric.value is None


def test_public_evaluate_publishes_discovered_parameter_recall(
    evaluate_arguments: Arguments,
    parameter_node_evaluation: EvaluationInputs,
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    semantic_path = (
        Path(context["run_root"]) / input_paths["semantic_analysis"]["path"]
    )
    semantic_path.write_text(
        json.dumps(parameter_node_evaluation.semantic_analysis.value), encoding="utf-8",
    )
    input_paths["semantic_analysis"]["sha256"] = calculate_sha256(semantic_path)
    ground_truth_path = (
        Path(context["project_root"]) / input_paths["ground_truth"]["path"]
    )
    ground_truth = load_json(ground_truth_path)
    ground_truth["data"]["entities"] = list(parameter_node_evaluation.ground_truth.entities)
    ground_truth_path.write_text(json.dumps(ground_truth), encoding="utf-8")
    input_paths["ground_truth"]["sha256"] = calculate_sha256(ground_truth_path)
    output_path = Path(context["run_root"]) / output_dir / "evaluation_results.json"
    output_path.unlink()

    response = run("evaluate", input_paths, output_dir, context)

    assert response["status"] == "completed"
    artifact = load_json(output_path)
    assert artifact["schema_version"] == "0.3.0"
    validate_schema(artifact, EVALUATION_RESULTS_SCHEMA)
    metric = next(
        item for item in artifact["data"]["metrics"]
        if item["metric_id"] == "parameter_recall"
    )
    assert (metric["numerator"], metric["denominator"], metric["value"]) == (1, 1, 1)


@pytest.mark.parametrize("basis", ["observed", "inferred"])
@pytest.mark.parametrize("has_requests", [True, False])
def test_endpoint_nodes_count_discovery_without_duplicate_matches(
    prepared_evaluation: EvaluationInputs,
    basis: str,
    has_requests: bool,
) -> None:
    value = deepcopy(prepared_evaluation.semantic_analysis.value)
    nodes = value["data"]["nodes"]
    endpoint = next(node for node in nodes if node["node_type"] == "Endpoint")
    duplicate = {
        **deepcopy(endpoint),
        "node_id": "endpoint:duplicate",
        "basis": basis,
    }
    nodes.append(duplicate)
    if not has_requests:
        value["data"]["normalized_requests"] = []
    changed = replace(
        prepared_evaluation,
        semantic_analysis=replace(prepared_evaluation.semantic_analysis, value=value),
    )

    result = build_evaluation_results(changed)

    assert result.data is not None
    metrics = {item.metric_id: item for item in result.data.metrics}
    assert metrics["endpoint_recall"].numerator == 1
    assert metrics["endpoint_recall"].denominator == 1
    assert metrics["endpoint_recall"].value == 1
    assert any("inferred Endpoint" in note for note in result.data.notes)


def test_inferred_endpoint_without_request_changes_only_discovery(
    prepared_evaluation: EvaluationInputs,
) -> None:
    baseline = build_evaluation_results(prepared_evaluation)
    value = deepcopy(prepared_evaluation.semantic_analysis.value)
    properties = {"method": "GET", "path_template": "/api/discovered/{id}"}
    value["data"]["nodes"].append(
        {
            "node_id": "endpoint:discovered",
            "node_type": "Endpoint",
            "properties": properties,
            "basis": "inferred",
            "evidence_refs": [],
        }
    )
    ground_truth = replace(
        prepared_evaluation.ground_truth,
        entities=(
            *prepared_evaluation.ground_truth.entities,
            {
                "gt_id": "gt_discovered",
                "entity_type": "Endpoint",
                "match_key": properties,
            },
        ),
    )
    changed = replace(
        prepared_evaluation,
        ground_truth=ground_truth,
        semantic_analysis=replace(prepared_evaluation.semantic_analysis, value=value),
    )

    result = build_evaluation_results(changed)

    assert result.data is not None and baseline.data is not None
    metrics = {item.metric_id: item for item in result.data.metrics}
    assert metrics["endpoint_recall"].numerator == 2
    assert metrics["endpoint_recall"].denominator == 2
    assert result.data.candidate_counts == baseline.data.candidate_counts
    assert result.data.confirmed_counts == baseline.data.confirmed_counts
    assert changed.graph_snapshot == prepared_evaluation.graph_snapshot
    assert len(value["data"]["normalized_requests"]) == len(
        prepared_evaluation.semantic_analysis.data["normalized_requests"]
    )


@pytest.mark.parametrize(
    "properties",
    [
        {},
        {"method": "GET"},
        {"path_template": "/api/orders/{order_id}"},
        {"method": "DELETE", "path_template": "/api/orders/{order_id}"},
        {"method": "GET", "path_template": "/api/unrelated/{id}"},
    ],
)
def test_endpoint_nodes_need_matching_method_and_path(
    prepared_evaluation: EvaluationInputs,
    properties: dict[str, Any],
) -> None:
    value = deepcopy(prepared_evaluation.semantic_analysis.value)
    value["data"]["normalized_requests"] = []
    value["data"]["nodes"] = [
        {
            "node_id": "endpoint:unmatched",
            "node_type": "Endpoint",
            "properties": properties,
            "basis": "inferred",
            "evidence_refs": [],
        },
    ]
    changed = replace(
        prepared_evaluation,
        semantic_analysis=replace(prepared_evaluation.semantic_analysis, value=value),
    )

    result = build_evaluation_results(changed)

    assert result.data is not None
    metric = next(
        item for item in result.data.metrics if item.metric_id == "endpoint_recall"
    )
    assert metric.numerator == 0
    assert metric.denominator == 1
    assert metric.value == 0


def test_relationship_matching_uses_matched_nodes_not_ground_truth_ids(
    prepared_evaluation: EvaluationInputs,
) -> None:
    owner = {
        **prepared_evaluation.ground_truth.entities[0],
        "entity_type": "User",
        "match_key": {
            "alias": "USER_B",
        },
    }
    ground_truth = replace(
        prepared_evaluation.ground_truth,
        entities=(
            owner,
            *prepared_evaluation.ground_truth.entities[1:],
        ),
    )
    changed = replace(
        prepared_evaluation,
        ground_truth=ground_truth,
    )

    result = build_evaluation_results(changed)

    assert result.data is not None
    metric = next(
        item
        for item in result.data.metrics
        if item.metric_id == "relationship_recall"
    )
    assert metric.value == 1


def test_confirmed_recall_keeps_unverified_positive_in_denominator(
    prepared_evaluation: EvaluationInputs,
) -> None:
    report_inputs = replace(
        prepared_evaluation.report_inputs,
        verification_results=(
            prepared_evaluation.report_inputs.verification_results[1],
        ),
    )
    changed = replace(
        prepared_evaluation,
        report_inputs=report_inputs,
    )

    result = build_evaluation_results(changed)

    assert result.data is not None
    assert result.data.confirmed_counts.to_mapping() == {
        "tp": 0,
        "fp": 0,
        "fn": 1,
    }
    metric = next(
        item
        for item in result.data.metrics
        if item.metric_id == "confirmed_recall"
    )
    assert metric.numerator == 0
    assert metric.denominator == 1
    assert metric.value == 0
    assert result.data.unverified_counts.indeterminate == 2


def test_confirmed_normal_case_is_counted_as_false_positive(
    prepared_evaluation: EvaluationInputs,
) -> None:
    report_inputs = prepared_evaluation.report_inputs

    candidate = replace(
        report_inputs.candidates[1],
        expected_basis="rule",
    )
    scenario = replace(
        report_inputs.scenarios[1],
        expected_basis="rule",
    )
    decision = replace(
        report_inputs.decisions[1],
        decision="allow",
        reason="평가 테스트 실행 허용",
    )
    verification = replace(
        report_inputs.verification_results[1],
        policy_decision="allow",
        execution_status="completed",
        result="success",
        reason="정상 대조 사례에서 위반 정황을 잘못 재현함",
        evidence_refs=(
            report_inputs.verification_results[0].evidence_refs
        ),
    )

    changed_report_inputs = replace(
        report_inputs,
        candidates=(
            report_inputs.candidates[0],
            candidate,
            report_inputs.candidates[2],
        ),
        scenarios=(
            report_inputs.scenarios[0],
            scenario,
        ),
        decisions=(
            report_inputs.decisions[0],
            decision,
        ),
        verification_results=(
            report_inputs.verification_results[0],
            verification,
        ),
    )
    changed = replace(
        prepared_evaluation,
        report_inputs=changed_report_inputs,
    )

    result = build_evaluation_results(changed)

    assert result.data is not None
    assert result.data.confirmed_counts.to_mapping() == {
        "tp": 1,
        "fp": 1,
        "fn": 0,
    }
    metric = next(
        item
        for item in result.data.metrics
        if item.metric_id == "confirmed_precision"
    )
    assert metric.value == 0.5


def test_failed_semantic_input_produces_partial_unmeasured_metrics(
    prepared_evaluation: EvaluationInputs,
) -> None:
    source = prepared_evaluation.semantic_analysis
    error = ErrorItem(
        code="SEMANTIC_FAILED",
        message="의미 분석 실패",
        item_ref=None,
        retryable=False,
    )
    failed_semantic = InputArtifact(
        source=replace(
            source.source,
            status="failed",
        ),
        value={
            **source.value,
            "status": "failed",
            "errors": [
                error.to_mapping(),
            ],
            "data": None,
        },
        errors=(
            error,
        ),
    )
    changed = replace(
        prepared_evaluation,
        semantic_analysis=failed_semantic,
    )

    result = build_evaluation_results(changed)

    assert result.status == "partial"
    assert result.errors[0].code.endswith(
        "SEMANTIC_FAILED"
    )
    assert result.data is not None

    endpoint_metric = next(
        item
        for item in result.data.metrics
        if item.metric_id == "endpoint_recall"
    )

    assert endpoint_metric.numerator == 0
    assert endpoint_metric.denominator == 1
    assert endpoint_metric.value is None
    assert "semantic_analyzer" not in result.data.models


def test_missing_graph_snapshot_fails_without_zero_metrics(
    prepared_evaluation: EvaluationInputs,
) -> None:
    changed = replace(
        prepared_evaluation,
        graph_snapshot=None,
    )

    result = build_evaluation_results(changed)

    assert result.status == "failed"
    assert result.data is None
    assert any(
        error.code == "EVALUATION_INPUT_UNAVAILABLE"
        for error in result.errors
    )


def test_evaluation_rejects_unsupported_matching_profile(
    prepared_evaluation: EvaluationInputs,
) -> None:
    request = replace(
        prepared_evaluation.request,
        matching_profile="unknown-v1",
    )
    changed = replace(
        prepared_evaluation,
        request=request,
    )

    with pytest.raises(
        ContractValidationError,
        match="matching_profile",
    ):
        build_evaluation_results(changed)


def test_evaluation_rejects_duplicate_normalized_ground_truth_key(
    prepared_evaluation: EvaluationInputs,
) -> None:
    original = prepared_evaluation.ground_truth.entities[0]
    duplicate = {
        **original,
        "gt_id": "gt_role_user_duplicate",
    }

    ground_truth = replace(
        prepared_evaluation.ground_truth,
        entities=(
            *prepared_evaluation.ground_truth.entities,
            duplicate,
        ),
    )
    changed = replace(
        prepared_evaluation,
        ground_truth=ground_truth,
    )

    with pytest.raises(
        ContractValidationError,
        match="정규화 키가 중복",
    ):
        build_evaluation_results(changed)


def test_run_metrics_preserve_unmeasured_values_as_null(
    prepared_evaluation: EvaluationInputs,
) -> None:
    source = prepared_evaluation.semantic_analysis
    runtime_metrics = {
        **source.value["runtime_metrics"],
        "input_tokens": None,
    }

    changed_semantic = replace(
        source,
        value={
            **source.value,
            "runtime_metrics": runtime_metrics,
        },
    )
    changed = replace(
        prepared_evaluation,
        semantic_analysis=changed_semantic,
    )

    result = build_evaluation_results(changed)

    assert result.data is not None
    assert result.data.run_metrics["input_tokens"] is None
    assert result.data.run_metrics["duration_ms"] == 70


def test_build_evaluation_artifact_uses_all_runtime_inputs(
    prepared_evaluation: EvaluationInputs,
) -> None:
    artifact = build_evaluation_artifact(
        prepared_evaluation,
        perf_counter(),
        timestamp_factory=lambda: FIXED_TIMESTAMP,
    )

    assert artifact["schema_version"] == "0.3.0"
    assert artifact["created_at"] == FIXED_TIMESTAMP
    assert (
        artifact["artifact_id"]
        == "evaluation_results_run_demo_001_000"
    )
    assert len(artifact["input_refs"]) == 7

    assert [
        item["artifact_type"]
        for item in artifact["input_refs"]
    ] == [
        "crawl_result",
        "semantic_analysis",
        "graph_query_result",
        "vulnerability_candidates",
        "test_scenarios",
        "safety_decisions",
        "verification_results",
    ]

    assert artifact["data"]["ground_truth_ref"] == {
        "dataset_id": "shop_demo",
        "dataset_version": "1",
        "path": "datasets/shop_demo/ground_truth.json",
        "sha256": prepared_evaluation.ground_truth.sha256,
    }

    validate_schema(
        artifact,
        EVALUATION_RESULTS_SCHEMA,
    )


def test_failed_evaluation_artifact_satisfies_output_contract(
    prepared_evaluation: EvaluationInputs,
) -> None:
    changed = replace(
        prepared_evaluation,
        graph_snapshot=None,
    )

    artifact = build_evaluation_artifact(
        changed,
        perf_counter(),
        timestamp_factory=lambda: FIXED_TIMESTAMP,
    )

    assert artifact["schema_version"] == "0.3.0"
    assert artifact["status"] == "failed"
    assert artifact["data"] is None

    validate_schema(
        artifact,
        EVALUATION_RESULTS_SCHEMA,
    )


def test_publish_evaluation_artifact_validates_and_writes_atomically(
    prepared_evaluation: EvaluationInputs,
) -> None:
    prepared_evaluation.request.output_path.unlink()

    artifact = build_evaluation_artifact(
        prepared_evaluation,
        perf_counter(),
        timestamp_factory=lambda: FIXED_TIMESTAMP,
    )

    response = publish_evaluation_artifact(
        prepared_evaluation,
        artifact,
    )

    assert response.status == "completed"
    assert response.output_path.endswith(
        "/reporter/evaluation_results.json"
    )
    assert response.sha256 == calculate_sha256(
        prepared_evaluation.request.output_path
    )
    assert (
        load_json(prepared_evaluation.request.output_path)
        == artifact
    )

    with pytest.raises(
        OutputArtifactExistsError,
    ):
        publish_evaluation_artifact(
            prepared_evaluation,
            artifact,
        )


@pytest.mark.parametrize("schema_version", ["0.1.0", "0.2.0", "0.4.0"])
def test_publish_evaluation_rejects_unsupported_output_version(
    prepared_evaluation: EvaluationInputs,
    schema_version: str,
) -> None:
    prepared_evaluation.request.output_path.unlink()
    artifact = build_evaluation_artifact(prepared_evaluation, perf_counter())
    artifact["schema_version"] = schema_version

    with pytest.raises(ContractValidationError, match="Schema 위반"):
        publish_evaluation_artifact(prepared_evaluation, artifact)

    assert not prepared_evaluation.request.output_path.exists()


def test_output_validation_rejects_tampered_metric(
    prepared_evaluation: EvaluationInputs,
) -> None:
    artifact = build_evaluation_artifact(
        prepared_evaluation,
        perf_counter(),
        timestamp_factory=lambda: FIXED_TIMESTAMP,
    )
    artifact["data"]["metrics"][0]["numerator"] += 1

    with pytest.raises(
        ContractValidationError,
        match="지표",
    ):
        validate_evaluation_artifact_against_inputs(
            artifact,
            prepared_evaluation,
        )


def test_publish_rejects_ground_truth_changed_after_preparation(
    prepared_evaluation: EvaluationInputs,
) -> None:
    prepared_evaluation.request.output_path.unlink()

    artifact = build_evaluation_artifact(
        prepared_evaluation,
        perf_counter(),
        timestamp_factory=lambda: FIXED_TIMESTAMP,
    )

    project_root = prepared_evaluation.request.project_root
    assert project_root is not None

    ground_truth_path = (
        project_root
        / prepared_evaluation.ground_truth.relative_path
    )
    ground_truth_path.write_text(
        ground_truth_path.read_text(
            encoding="utf-8",
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ContractValidationError,
        match="ground_truth 파일이 변경됨",
    ):
        publish_evaluation_artifact(
            prepared_evaluation,
            artifact,
        )
