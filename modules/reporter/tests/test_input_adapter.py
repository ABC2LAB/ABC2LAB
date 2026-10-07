import copy
from pathlib import Path
from typing import Any

import pytest

from modules.reporter.exceptions import ReporterError
from modules.reporter.input_adapter import (
    parse_evaluate_request,
    parse_report_request,
)


def test_parse_report_request_resolves_contract_paths(
    report_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    reporter_run_root: Path,
) -> None:
    input_paths, output_dir, context = report_arguments

    request = parse_report_request(input_paths, output_dir, context)

    assert request.operation == "report"
    assert len(request.inputs) == 4
    assert request.input_by_name("test_scenarios").path == (
        reporter_run_root
        / "artifacts/iteration-000/scenario_generator/test_scenarios.json"
    )
    assert request.output_path == (
        reporter_run_root
        / "artifacts/iteration-000/reporter/diagnosis_report.json"
    )
    assert request.target_url == "http://127.0.0.1:8001/"


def test_parse_evaluate_request_resolves_ground_truth(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    reporter_project_root: Path,
) -> None:
    input_paths, output_dir, context = evaluate_arguments

    request = parse_evaluate_request(input_paths, output_dir, context)

    assert request.operation == "evaluate"
    assert len(request.inputs) == 8
    assert request.input_by_name("ground_truth").path == (
        reporter_project_root / "datasets/shop_demo/ground_truth.json"
    )
    assert request.output_path.name == "evaluation_results.json"
    assert request.matching_profile == "default-v1"


def test_parse_request_accepts_exact_absolute_output_directory(
    report_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    reporter_run_root: Path,
) -> None:
    input_paths, _, context = report_arguments
    output_dir = reporter_run_root / "artifacts/iteration-000/reporter"

    request = parse_report_request(input_paths, output_dir, context)

    assert request.output_path.parent == output_dir


@pytest.mark.parametrize(
    "context_update",
    [
        {"iteration": -1},
        {"iteration": True},
        {"mode": "unsupported"},
        {"run_id": ""},
        {"target_url": "file:///tmp/target"},
    ],
)
def test_parse_report_request_rejects_invalid_context(
    report_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    context_update: dict[str, Any],
) -> None:
    input_paths, output_dir, context = copy.deepcopy(report_arguments)
    context.update(context_update)

    with pytest.raises(ReporterError):
        parse_report_request(input_paths, output_dir, context)


def test_parse_evaluate_request_requires_development_mode(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    context["mode"] = "diagnosis"

    with pytest.raises(ReporterError, match="development"):
        parse_evaluate_request(input_paths, output_dir, context)


def test_parse_request_rejects_input_key_change(
    report_arguments: tuple[dict[str, Any], str, dict[str, Any]],
) -> None:
    input_paths, output_dir, context = report_arguments
    input_paths["unexpected"] = input_paths["test_scenarios"]

    with pytest.raises(ReporterError, match="input_paths 필드 구성"):
        parse_report_request(input_paths, output_dir, context)


def test_parse_request_rejects_wrong_input_path(
    report_arguments: tuple[dict[str, Any], str, dict[str, Any]],
) -> None:
    input_paths, output_dir, context = report_arguments
    input_paths["test_scenarios"]["path"] = "test_scenarios.json"

    with pytest.raises(ReporterError, match="입력 경로"):
        parse_report_request(input_paths, output_dir, context)


def test_parse_request_rejects_invalid_descriptor(
    report_arguments: tuple[dict[str, Any], str, dict[str, Any]],
) -> None:
    input_paths, output_dir, context = report_arguments
    input_paths["test_scenarios"]["unexpected"] = True

    with pytest.raises(ReporterError, match="필드 구성"):
        parse_report_request(input_paths, output_dir, context)


def test_parse_request_rejects_invalid_sha256(
    report_arguments: tuple[dict[str, Any], str, dict[str, Any]],
) -> None:
    input_paths, output_dir, context = report_arguments
    input_paths["test_scenarios"]["sha256"] = "invalid"

    with pytest.raises(ReporterError, match="SHA-256 형식"):
        parse_report_request(input_paths, output_dir, context)


@pytest.mark.parametrize(
    "output_dir",
    ["artifacts/iteration-000/other", "../outside", "/tmp/outside"],
)
def test_parse_request_rejects_wrong_output_directory(
    report_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    output_dir: str,
) -> None:
    input_paths, _, context = report_arguments

    with pytest.raises(ReporterError):
        parse_report_request(input_paths, output_dir, context)


def test_parse_evaluate_request_rejects_wrong_ground_truth_path(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    input_paths["ground_truth"]["path"] = "../ground_truth.json"

    with pytest.raises(ReporterError, match="ground_truth 입력 경로"):
        parse_evaluate_request(input_paths, output_dir, context)


def test_parse_evaluate_request_rejects_ground_truth_symlink_escape(
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    reporter_project_root: Path,
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    ground_truth_path = reporter_project_root / "datasets/shop_demo/ground_truth.json"
    outside_path = reporter_project_root.parent / "outside-ground-truth.json"
    outside_path.write_text("{}", encoding="utf-8")
    ground_truth_path.unlink()
    ground_truth_path.symlink_to(outside_path)

    with pytest.raises(ReporterError, match="신뢰 경로를 벗어난"):
        parse_evaluate_request(input_paths, output_dir, context)


def test_parse_request_rejects_run_root_name_mismatch(
    report_arguments: tuple[dict[str, Any], str, dict[str, Any]],
    tmp_path: Path,
) -> None:
    input_paths, output_dir, context = report_arguments
    other_root = tmp_path / "other_run"
    other_root.mkdir()
    context["run_root"] = other_root

    with pytest.raises(ReporterError, match="run_root와 run_id"):
        parse_report_request(input_paths, output_dir, context)
