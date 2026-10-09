import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from modules.reporter.contracts import (
    prepare_evaluation_inputs,
    prepare_report_inputs,
)
from modules.reporter.exceptions import ReporterError
from modules.reporter.input_adapter import (
    parse_evaluate_request,
    parse_report_request,
)
from modules.reporter.utils.hashing import calculate_sha256

Arguments = tuple[dict[str, Any], str, dict[str, Any]]


def test_prepare_report_inputs_builds_internal_models(
    report_arguments: Arguments,
) -> None:
    input_paths, output_dir, context = report_arguments
    request = parse_report_request(input_paths, output_dir, context)

    inputs = prepare_report_inputs(request)

    assert len(inputs.candidates) == 3
    assert len(inputs.scenarios) == 2
    assert len(inputs.decisions) == 2
    assert len(inputs.verification_results) == 2
    assert inputs.candidates[0].candidate_id == "candidate_idor_001"
    assert inputs.verification_results[0].result == "success"


def test_prepare_evaluation_inputs_builds_snapshot_and_ground_truth(
    evaluate_arguments: Arguments,
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    request = parse_evaluate_request(input_paths, output_dir, context)

    inputs = prepare_evaluation_inputs(request)

    assert inputs.graph_snapshot is not None
    assert inputs.graph_snapshot.graph_revision == 1
    assert len(inputs.graph_snapshot.nodes) == 6
    assert inputs.ground_truth.dataset_id == "shop_demo"
    assert len(inputs.ground_truth.cases) == 2


def test_prepare_report_inputs_rejects_hash_mismatch(
    report_arguments: Arguments,
) -> None:
    input_paths, output_dir, context = report_arguments
    path = context["run_root"] / input_paths["test_scenarios"]["path"]
    path.write_text("{}", encoding="utf-8")
    request = parse_report_request(input_paths, output_dir, context)

    with pytest.raises(ReporterError, match="SHA-256 불일치"):
        prepare_report_inputs(request)


def test_prepare_report_inputs_rejects_artifact_context_mismatch(
    report_arguments: Arguments,
) -> None:
    input_paths, output_dir, context = report_arguments
    _mutate_artifact(
        input_paths,
        context,
        "vulnerability_candidates",
        lambda value: value.update({"run_id": "other_run"}),
    )
    request = parse_report_request(input_paths, output_dir, context)

    with pytest.raises(ReporterError, match="run_id"):
        prepare_report_inputs(request)


def test_prepare_report_inputs_accepts_partial_source(
    report_arguments: Arguments,
) -> None:
    input_paths, output_dir, context = report_arguments

    def make_partial(value: dict[str, Any]) -> None:
        value["status"] = "partial"
        value["errors"] = [
            {
                "code": "CANDIDATE_SKIPPED",
                "message": "일부 후보를 만들지 못함",
                "item_ref": "request_missing",
                "retryable": False,
            }
        ]

    _mutate_artifact(
        input_paths,
        context,
        "vulnerability_candidates",
        make_partial,
    )
    request = parse_report_request(input_paths, output_dir, context)

    inputs = prepare_report_inputs(request)

    source = inputs.artifact_by_type("vulnerability_candidates")
    assert source.source.status == "partial"
    assert len(source.errors) == 1
    assert len(inputs.candidates) == 3


def test_prepare_report_inputs_preserves_failed_source(
    report_arguments: Arguments,
) -> None:
    input_paths, output_dir, context = report_arguments

    def make_failed(value: dict[str, Any]) -> None:
        value["status"] = "failed"
        value["data"] = None
        value["errors"] = [
            {
                "code": "ANALYSIS_FAILED",
                "message": "후보 분석 실패",
                "item_ref": None,
                "retryable": False,
            }
        ]

    _mutate_artifact(
        input_paths,
        context,
        "vulnerability_candidates",
        make_failed,
    )
    request = parse_report_request(input_paths, output_dir, context)

    inputs = prepare_report_inputs(request)

    source = inputs.artifact_by_type("vulnerability_candidates")
    assert source.source.status == "failed"
    assert inputs.candidates == ()


def test_prepare_report_inputs_rejects_duplicate_candidate_id(
    report_arguments: Arguments,
) -> None:
    input_paths, output_dir, context = report_arguments

    def duplicate_candidate(value: dict[str, Any]) -> None:
        candidates = value["data"]["candidates"]
        candidates.append(candidates[0])

    _mutate_artifact(
        input_paths,
        context,
        "vulnerability_candidates",
        duplicate_candidate,
    )
    request = parse_report_request(input_paths, output_dir, context)

    with pytest.raises(ReporterError, match="중복 candidate_id"):
        prepare_report_inputs(request)


def test_prepare_report_inputs_rejects_scenarios_when_candidates_empty(
    report_arguments: Arguments,
) -> None:
    input_paths, output_dir, context = report_arguments
    _mutate_artifact(
        input_paths,
        context,
        "vulnerability_candidates",
        lambda value: value["data"].update({"candidates": []}),
    )
    request = parse_report_request(input_paths, output_dir, context)

    with pytest.raises(ReporterError, match="candidate_id"):
        prepare_report_inputs(request)


def test_prepare_report_inputs_rejects_unknown_candidate_reference(
    report_arguments: Arguments,
) -> None:
    input_paths, output_dir, context = report_arguments

    def change_candidate(value: dict[str, Any]) -> None:
        value["data"]["scenarios"][0]["candidate_id"] = "missing_candidate"

    _mutate_artifact(
        input_paths,
        context,
        "test_scenarios",
        change_candidate,
    )
    request = parse_report_request(input_paths, output_dir, context)

    with pytest.raises(ReporterError, match="candidate_id"):
        prepare_report_inputs(request)


def test_prepare_report_inputs_rejects_missing_decision(
    report_arguments: Arguments,
) -> None:
    input_paths, output_dir, context = report_arguments
    _mutate_artifact(
        input_paths,
        context,
        "safety_decisions",
        lambda value: value["data"]["decisions"].pop(),
    )
    request = parse_report_request(input_paths, output_dir, context)

    with pytest.raises(ReporterError, match="정확히 하나의 판정"):
        prepare_report_inputs(request)


def test_prepare_report_inputs_rejects_invalid_allow_assessment(
    report_arguments: Arguments,
) -> None:
    input_paths, output_dir, context = report_arguments

    def change_assessment(value: dict[str, Any]) -> None:
        decision = value["data"]["decisions"][0]
        decision["assessment"]["data_impact"]["status"] = "unknown"

    _mutate_artifact(
        input_paths,
        context,
        "safety_decisions",
        change_assessment,
    )
    request = parse_report_request(input_paths, output_dir, context)

    with pytest.raises(ReporterError, match="assessment"):
        prepare_report_inputs(request)


def test_prepare_report_inputs_rejects_wrong_verification_link(
    report_arguments: Arguments,
) -> None:
    input_paths, output_dir, context = report_arguments

    def change_decision(value: dict[str, Any]) -> None:
        value["data"]["results"][0]["decision_id"] = "missing_decision"

    _mutate_artifact(
        input_paths,
        context,
        "verification_results",
        change_decision,
    )
    request = parse_report_request(input_paths, output_dir, context)

    with pytest.raises(ReporterError, match="decision 연결"):
        prepare_report_inputs(request)


def test_prepare_report_inputs_rejects_duplicate_verification_scenario(
    report_arguments: Arguments,
) -> None:
    input_paths, output_dir, context = report_arguments

    def duplicate_scenario_result(value: dict[str, Any]) -> None:
        original = value["data"]["results"][0]
        duplicate = json.loads(json.dumps(original))
        duplicate["verification_id"] = "verification_idor_duplicate"
        value["data"]["results"].append(duplicate)

    _mutate_artifact(
        input_paths,
        context,
        "verification_results",
        duplicate_scenario_result,
    )
    request = parse_report_request(input_paths, output_dir, context)

    with pytest.raises(ReporterError, match="verification scenario_id"):
        prepare_report_inputs(request)


def test_prepare_report_inputs_rejects_scenario_hash_mismatch(
    report_arguments: Arguments,
) -> None:
    input_paths, output_dir, context = report_arguments

    def change_hash(value: dict[str, Any]) -> None:
        value["data"]["scenarios_sha256"] = "0" * 64

    _mutate_artifact(
        input_paths,
        context,
        "safety_decisions",
        change_hash,
    )
    request = parse_report_request(input_paths, output_dir, context)

    with pytest.raises(ReporterError, match="scenarios_sha256"):
        prepare_report_inputs(request)


def test_prepare_report_inputs_rejects_graph_revision_mismatch(
    report_arguments: Arguments,
) -> None:
    input_paths, output_dir, context = report_arguments

    def change_revision(value: dict[str, Any]) -> None:
        value["data"]["source_graph_revision"] = 2

    _mutate_artifact(
        input_paths,
        context,
        "verification_results",
        change_revision,
    )
    request = parse_report_request(input_paths, output_dir, context)

    with pytest.raises(ReporterError, match="graph revision"):
        prepare_report_inputs(request)


def test_prepare_report_inputs_rejects_unrelated_graph_update_evidence(
    report_arguments: Arguments,
) -> None:
    input_paths, output_dir, context = report_arguments

    def change_evidence(value: dict[str, Any]) -> None:
        relationship = value["data"]["graph_updates"]["relationships"][0]
        relationship["evidence_refs"][0]["evidence_id"] = "other_evidence"

    _mutate_artifact(
        input_paths,
        context,
        "verification_results",
        change_evidence,
    )
    request = parse_report_request(input_paths, output_dir, context)

    with pytest.raises(ReporterError, match="verification과 다름"):
        prepare_report_inputs(request)


def test_prepare_report_inputs_rejects_evidence_path_escape(
    report_arguments: Arguments,
) -> None:
    input_paths, output_dir, context = report_arguments

    def change_path(value: dict[str, Any]) -> None:
        value["data"]["candidates"][0]["evidence_refs"][0]["path"] = (
            "../outside.json"
        )

    _mutate_artifact(
        input_paths,
        context,
        "vulnerability_candidates",
        change_path,
    )
    request = parse_report_request(input_paths, output_dir, context)

    with pytest.raises(ReporterError, match="evidence 아래"):
        prepare_report_inputs(request)


def test_prepare_evaluation_inputs_rejects_target_url_mismatch(
    evaluate_arguments: Arguments,
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    context["target_url"] = "http://127.0.0.1:8002/"
    request = parse_evaluate_request(input_paths, output_dir, context)

    with pytest.raises(ReporterError, match="target_url"):
        prepare_evaluation_inputs(request)


def test_prepare_evaluation_inputs_rejects_snapshot_revision_mismatch(
    evaluate_arguments: Arguments,
) -> None:
    input_paths, output_dir, context = evaluate_arguments

    def change_revision(value: dict[str, Any]) -> None:
        value["data"]["graph_revision"] = 2

    _mutate_artifact(
        input_paths,
        context,
        "graph_query_result",
        change_revision,
    )
    request = parse_evaluate_request(input_paths, output_dir, context)

    with pytest.raises(ReporterError, match="snapshot과 다름"):
        prepare_evaluation_inputs(request)


def test_prepare_evaluation_inputs_rejects_missing_semantic_request(
    evaluate_arguments: Arguments,
) -> None:
    input_paths, output_dir, context = evaluate_arguments

    def remove_request(value: dict[str, Any]) -> None:
        value["data"]["normalized_requests"].pop()

    _mutate_artifact(
        input_paths,
        context,
        "semantic_analysis",
        remove_request,
    )
    request = parse_evaluate_request(input_paths, output_dir, context)

    with pytest.raises(ReporterError, match="request_id"):
        prepare_evaluation_inputs(request)


def test_prepare_evaluation_inputs_rejects_unknown_candidate_request(
    evaluate_arguments: Arguments,
) -> None:
    input_paths, output_dir, context = evaluate_arguments

    def change_request(value: dict[str, Any]) -> None:
        value["data"]["candidates"][0]["source_request_ids"] = [
            "missing_request"
        ]

    _mutate_artifact(
        input_paths,
        context,
        "vulnerability_candidates",
        change_request,
    )
    request = parse_evaluate_request(input_paths, output_dir, context)

    with pytest.raises(ReporterError, match="source_request_id"):
        prepare_evaluation_inputs(request)


def test_prepare_evaluation_inputs_accepts_source_request_from_reference_account(
    evaluate_arguments: Arguments,
) -> None:
    input_paths, output_dir, context = evaluate_arguments

    def move_crawl_request_to_reference_account(
        value: dict[str, Any],
    ) -> None:
        request = next(
            item
            for item in value["data"]["requests"]
            if item["request_id"] == "request_read_order"
        )
        request["account_id"] = "account_user_b"
        request["role_id"] = "role_user"
        request["session_ref"] = "session_account_user_b"

    def move_semantic_request_to_reference_account(
        value: dict[str, Any],
    ) -> None:
        request = next(
            item
            for item in value["data"]["normalized_requests"]
            if item["request_id"] == "request_read_order"
        )
        request["account_id"] = "account_user_b"
        request["role_id"] = "role_user"

    _mutate_artifact(
        input_paths,
        context,
        "crawl_result",
        move_crawl_request_to_reference_account,
    )
    _mutate_artifact(
        input_paths,
        context,
        "semantic_analysis",
        move_semantic_request_to_reference_account,
    )

    request = parse_evaluate_request(
        input_paths,
        output_dir,
        context,
    )
    inputs = prepare_evaluation_inputs(request)

    scenario_artifact = inputs.report_inputs.artifact_by_type(
        "test_scenarios"
    )
    assert scenario_artifact.data is not None
    assert inputs.crawl_result.data is not None

    step = scenario_artifact.data["scenarios"][0]["steps"][0]
    source_request = next(
        item
        for item in inputs.crawl_result.data["requests"]
        if item["request_id"] == step["source_request_id"]
    )

    assert source_request["account_id"] == "account_user_b"
    assert step["account_id"] == "account_user_a"
    assert source_request["account_id"] != step["account_id"]


def test_prepare_evaluation_inputs_rejects_actor_session_mismatch(
    evaluate_arguments: Arguments,
) -> None:
    input_paths, output_dir, context = evaluate_arguments

    def rotate_actor_session(
        value: dict[str, Any],
    ) -> None:
        account = next(
            item
            for item in value["data"]["accounts"]
            if item["account_id"] == "account_user_a"
        )
        account["session_ref"] = "session_account_user_a_rotated"

        for request in value["data"]["requests"]:
            if request["account_id"] == "account_user_a":
                request["session_ref"] = "session_account_user_a_rotated"

    _mutate_artifact(
        input_paths,
        context,
        "crawl_result",
        rotate_actor_session,
    )

    request = parse_evaluate_request(
        input_paths,
        output_dir,
        context,
    )

    with pytest.raises(
        ReporterError,
        match="scenario session_ref 연결이 다름",
    ):
        prepare_evaluation_inputs(request)


def test_prepare_evaluation_inputs_rejects_ground_truth_reference(
    evaluate_arguments: Arguments,
) -> None:
    input_paths, output_dir, context = evaluate_arguments

    def change_reference(value: dict[str, Any]) -> None:
        value["data"]["relationships"][0]["target_gt_id"] = "missing_gt"

    _mutate_ground_truth(input_paths, context, change_reference)
    request = parse_evaluate_request(input_paths, output_dir, context)

    with pytest.raises(ReporterError, match="relation target"):
        prepare_evaluation_inputs(request)


def _mutate_artifact(
    input_paths: dict[str, Any],
    context: dict[str, Any],
    artifact_type: str,
    mutate: Callable[[dict[str, Any]], object],
) -> None:
    descriptor = input_paths[artifact_type]
    path = context["run_root"] / descriptor["path"]
    value = json.loads(path.read_text(encoding="utf-8"))
    mutate(value)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    descriptor["sha256"] = calculate_sha256(path)


def _mutate_ground_truth(
    input_paths: dict[str, Any],
    context: dict[str, Any],
    mutate: Callable[[dict[str, Any]], object],
) -> None:
    descriptor = input_paths["ground_truth"]
    path = context["project_root"] / descriptor["path"]
    value = json.loads(path.read_text(encoding="utf-8"))
    mutate(value)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    descriptor["sha256"] = calculate_sha256(path)