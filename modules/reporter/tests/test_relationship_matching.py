"""Snapshot relationships use comparison views, never reconstructed graph edges."""

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from modules.reporter.contracts import prepare_evaluation_inputs
from modules.reporter.entrypoint import run
from modules.reporter.evaluation_output_adapter import EVALUATION_RESULTS_SCHEMA
from modules.reporter.evaluation_service import _relationship_metric
from modules.reporter.input_adapter import parse_evaluate_request
from modules.reporter.matching import ActorMatcher
from modules.reporter.models import EvaluationInputs
from modules.reporter.relationship_matching import RelationshipScore, score_relationships
from modules.reporter.service import build_diagnosis_report
from modules.reporter.utils.hashing import calculate_sha256
from modules.reporter.utils.validation import load_json, validate_schema


def node(node_id: str, node_type: str, **properties: Any) -> dict[str, Any]:
    return {"node_id": node_id, "node_type": node_type, "properties": properties}


def edge(source: str, target: str, relation_type: str) -> dict[str, str]:
    return {"source_id": source, "target_id": target, "relation_type": relation_type}


@pytest.fixture
def relationship_inputs(evaluate_arguments: tuple) -> EvaluationInputs:
    inputs = prepare_evaluation_inputs(parse_evaluate_request(*evaluate_arguments))
    assert inputs.graph_snapshot is not None
    return replace(
        inputs,
        graph_snapshot=replace(
            inputs.graph_snapshot,
            nodes=(
                node("opaque-page", "Page", url="https://example.test/orders/?sort=new"),
                node("opaque-endpoint", "Endpoint", method="GET", path_template="/api/orders/{id}"),
                node("opaque-parameter", "Parameter", name="id", location="path", is_sensitive=False),
            ),
            relationships=(
                edge("opaque-page", "opaque-endpoint", "CALL"),
                edge("opaque-endpoint", "opaque-parameter", "USE"),
            ),
        ),
        ground_truth=replace(
            inputs.ground_truth,
            entities=(
                {"gt_id": "page", "entity_type": "Page", "match_key": {"path": "/orders"}},
                {"gt_id": "endpoint", "entity_type": "Endpoint", "match_key": {
                    "method": "get", "path_template": "/api/orders/{id}/",
                }},
                {"gt_id": "parameter", "entity_type": "Parameter", "match_key": {
                    "endpoint_method": "get", "endpoint_path_template": "/api/orders/{id}",
                    "name": "id", "location": "path", "is_sensitive": False,
                }},
            ),
            relationships=(
                {"source_gt_id": "page", "target_gt_id": "endpoint", "relation_type": "CALL"},
                {"source_gt_id": "endpoint", "target_gt_id": "parameter", "relation_type": "USE"},
            ),
        ),
    )


def test_page_path_and_parameter_context_match_without_mutating_snapshot(
    relationship_inputs: EvaluationInputs,
) -> None:
    before = deepcopy(relationship_inputs)
    metric = _relationship_metric(relationship_inputs)
    assert (metric.numerator, metric.denominator, metric.value) == (2, 2, 1.0)
    assert relationship_inputs == before


@pytest.mark.parametrize("key", ["endpoint", "flat"])
def test_parameter_comparison_supports_existing_endpoint_key_forms(
    relationship_inputs: EvaluationInputs, key: str,
) -> None:
    entities = deepcopy(relationship_inputs.ground_truth.entities)
    parameter_key = entities[2]["match_key"]
    parameter_key.pop("endpoint_method")
    parameter_key.pop("endpoint_path_template")
    if key == "endpoint":
        parameter_key["endpoint"] = {"method": "GET", "path_template": "/api/orders/{id}"}
    else:
        parameter_key.update(method="GET", path_template="/api/orders/{id}")
    changed = replace(relationship_inputs, ground_truth=replace(
        relationship_inputs.ground_truth, entities=entities,
    ))
    assert _relationship_metric(changed).numerator == 2


@pytest.mark.parametrize("field,value", [
    ("endpoint_method", "POST"), ("endpoint_path_template", "/api/products/{id}"),
    ("name", "item_id"), ("location", "query"), ("is_sensitive", True),
])
def test_parameter_context_does_not_hide_mismatch(
    relationship_inputs: EvaluationInputs, field: str, value: object,
) -> None:
    entities = deepcopy(relationship_inputs.ground_truth.entities)
    entities[2]["match_key"][field] = value
    changed = replace(relationship_inputs, ground_truth=replace(
        relationship_inputs.ground_truth, entities=entities,
    ))
    assert _relationship_metric(changed).numerator == 1


def test_shared_parameter_node_does_not_mix_endpoint_contexts(
    relationship_inputs: EvaluationInputs,
) -> None:
    snapshot = relationship_inputs.graph_snapshot
    assert snapshot is not None
    entities = deepcopy(relationship_inputs.ground_truth.entities)
    # The GT source is products, but its Parameter is explicitly scoped to orders.
    entities[1]["match_key"]["path_template"] = "/api/products/{id}"
    changed = replace(
        relationship_inputs,
        ground_truth=replace(relationship_inputs.ground_truth, entities=entities,
                             relationships=relationship_inputs.ground_truth.relationships[1:]),
        graph_snapshot=replace(snapshot, nodes=(*snapshot.nodes,
            node("other-endpoint", "Endpoint", method="GET", path_template="/api/products/{id}")),
            relationships=(*snapshot.relationships, edge("other-endpoint", "opaque-parameter", "USE"))),
    )
    assert _relationship_metric(changed).numerator == 0


@pytest.mark.parametrize("relationship", [
    edge("opaque-parameter", "opaque-endpoint", "USE"),
    edge("opaque-endpoint", "opaque-parameter", "REFERENCE"),
    edge("opaque-page", "opaque-parameter", "USE"),
])
def test_parameter_context_requires_real_endpoint_to_parameter_use(
    relationship_inputs: EvaluationInputs, relationship: dict[str, str],
) -> None:
    snapshot = relationship_inputs.graph_snapshot
    assert snapshot is not None
    changed = replace(relationship_inputs, graph_snapshot=replace(
        snapshot, relationships=(snapshot.relationships[0], relationship),
    ))
    assert _relationship_metric(changed).numerator == 1


def test_duplicate_edges_and_node_order_do_not_inflate_recall(
    relationship_inputs: EvaluationInputs,
) -> None:
    snapshot = relationship_inputs.graph_snapshot
    assert snapshot is not None
    changed = replace(relationship_inputs, graph_snapshot=replace(
        snapshot, nodes=tuple(reversed(snapshot.nodes)), relationships=snapshot.relationships * 2,
    ))
    assert _relationship_metric(changed).numerator == 2


@pytest.mark.parametrize("properties", [
    {"url": "https://example.test/orders/?q=1"}, {"path": "/orders/"},
])
def test_legacy_page_match_keys_are_preserved(
    relationship_inputs: EvaluationInputs, properties: dict[str, str],
) -> None:
    entities = deepcopy(relationship_inputs.ground_truth.entities)
    entities[0]["match_key"] = properties
    snapshot = relationship_inputs.graph_snapshot
    assert snapshot is not None
    nodes = (node("opaque-page", "Page", **properties), *snapshot.nodes[1:])
    changed = replace(relationship_inputs, graph_snapshot=replace(snapshot, nodes=nodes),
                      ground_truth=replace(relationship_inputs.ground_truth, entities=entities))
    assert _relationship_metric(changed).numerator == 2


@pytest.mark.parametrize("relation", [None,
    edge("opaque-endpoint", "opaque-page", "CALL"),
    edge("opaque-page", "opaque-endpoint", "REFERENCE"),
])
def test_page_endpoint_match_requires_actual_call_with_same_direction(
    relationship_inputs: EvaluationInputs, relation: dict[str, str] | None,
) -> None:
    snapshot = relationship_inputs.graph_snapshot
    assert snapshot is not None
    relations = snapshot.relationships[1:] + (() if relation is None else (relation,))
    changed = replace(relationship_inputs, graph_snapshot=replace(snapshot, relationships=relations))
    assert _relationship_metric(changed).numerator == 1


@pytest.mark.parametrize("index,field", [(1, "method"), (1, "path_template"), (2, "name"), (2, "location")])
def test_incomplete_parameter_context_is_not_inferred(
    relationship_inputs: EvaluationInputs, index: int, field: str,
) -> None:
    snapshot = relationship_inputs.graph_snapshot
    assert snapshot is not None
    nodes = deepcopy(snapshot.nodes)
    nodes[index]["properties"].pop(field)
    changed = replace(relationship_inputs, graph_snapshot=replace(snapshot, nodes=nodes))
    metric = _relationship_metric(changed)
    assert metric.numerator == (0 if index == 1 else 1)


def test_relationship_evaluation_does_not_use_semantic_or_crawl_nodes(
    relationship_inputs: EvaluationInputs,
) -> None:
    snapshot = relationship_inputs.graph_snapshot
    assert snapshot is not None
    semantic = deepcopy(relationship_inputs.semantic_analysis.value)
    semantic["data"]["nodes"] = list(snapshot.nodes)
    semantic["data"]["relationships"] = list(snapshot.relationships)
    changed = replace(relationship_inputs, semantic_analysis=replace(
        relationship_inputs.semantic_analysis, value=semantic,
    ), graph_snapshot=replace(snapshot, nodes=(), relationships=()))
    assert _relationship_metric(changed).numerator == 0


def test_relationship_diagnostics_partition_every_ground_truth_relation(
    relationship_inputs: EvaluationInputs,
) -> None:
    snapshot = relationship_inputs.graph_snapshot
    assert snapshot is not None
    entities = (*relationship_inputs.ground_truth.entities,
                {"gt_id": "absent", "entity_type": "Page", "match_key": {"path": "/absent"}})
    relationships = tuple(
        {"source_gt_id": source, "target_gt_id": target, "relation_type": "CALL"}
        for source, target in (
            ("page", "endpoint"), ("absent", "endpoint"), ("page", "absent"),
            ("absent", "absent"), ("endpoint", "page"),
        )
    )
    score = score_relationships(snapshot, entities, relationships, ActorMatcher({}, {}))
    assert score == RelationshipScore(1, 1, 1, 1, 1)
    assert score.to_note() == (
        "관계 매칭: 일치 1개, 출발 노드 미매칭 1개, 도착 노드 미매칭 1개, "
        "양쪽 노드 미매칭 1개, 노드 일치·관계 미매칭 1개"
    )


def test_public_evaluate_validates_and_publishes_relationship_views(
    evaluate_arguments: tuple, relationship_inputs: EvaluationInputs,
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    baseline = prepare_evaluation_inputs(parse_evaluate_request(*evaluate_arguments))
    expected_report = build_diagnosis_report(baseline.report_inputs)
    graph_path = Path(context["run_root"]) / input_paths["graph_query_result"]["path"]
    graph = load_json(graph_path)
    row = next(item for item in graph["data"]["results"]
               if item["query_key"] == "structure_snapshot")["rows"][0]
    snapshot = relationship_inputs.graph_snapshot
    assert snapshot is not None
    row["nodes"].extend({**node, "basis": "observed", "evidence_refs": []} for node in snapshot.nodes)
    row["relationships"].extend(
        {**edge, "relationship_id": f"new-edge-{index}", "basis": "observed",
         "properties": {}, "evidence_refs": []}
        for index, edge in enumerate(snapshot.relationships)
    )
    graph_path.write_text(json.dumps(graph), encoding="utf-8")
    input_paths["graph_query_result"]["sha256"] = calculate_sha256(graph_path)
    gt_path = Path(context["project_root"]) / input_paths["ground_truth"]["path"]
    gt = load_json(gt_path)
    gt["data"]["entities"].extend(relationship_inputs.ground_truth.entities)
    gt["data"]["relationships"].extend(
        {**edge, "gt_relation_id": f"new-gt-edge-{index}"}
        for index, edge in enumerate(relationship_inputs.ground_truth.relationships)
    )
    gt_path.write_text(json.dumps(gt), encoding="utf-8")
    input_paths["ground_truth"]["sha256"] = calculate_sha256(gt_path)
    output_path = Path(context["run_root"]) / output_dir / "evaluation_results.json"
    output_path.unlink()

    response = run("evaluate", input_paths, output_dir, context)

    assert response["status"] == "completed"
    artifact = load_json(output_path)
    validate_schema(artifact, EVALUATION_RESULTS_SCHEMA)
    metric = next(item for item in artifact["data"]["metrics"] if item["metric_id"] == "relationship_recall")
    assert (metric["numerator"], metric["denominator"]) == (2, 3)
    assert any(note.startswith("관계 매칭: 일치 2개") for note in artifact["data"]["notes"])
    assert artifact["schema_version"] == "0.3.0"
    assert len(artifact["input_refs"]) == 7
    assert calculate_sha256(graph_path) == input_paths["graph_query_result"]["sha256"]
    assert calculate_sha256(gt_path) == input_paths["ground_truth"]["sha256"]
    prepared = prepare_evaluation_inputs(parse_evaluate_request(*evaluate_arguments))
    assert build_diagnosis_report(prepared.report_inputs) == expected_report
