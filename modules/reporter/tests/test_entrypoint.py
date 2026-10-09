import json
from pathlib import Path
from typing import Any

import pytest

from modules.reporter import entrypoint
from modules.reporter.contracts import INPUT_SCHEMA_BY_NAME
from modules.reporter.utils.hashing import calculate_sha256
from modules.reporter.utils.validation import load_json

Arguments = tuple[dict[str, Any], str, dict[str, Any]]


def test_run_report_publishes_json_and_local_html(
    report_arguments: Arguments,
) -> None:
    input_paths, output_dir, context = report_arguments
    _remove_contract_output(context["run_root"], "diagnosis_report.json")

    response = entrypoint.run("report", input_paths, output_dir, context)

    output_path = _contract_output(context["run_root"], "diagnosis_report.json")
    report_path = context["run_root"] / response["report_path"]
    assert response["status"] == "completed"
    assert response["output_path"].endswith("diagnosis_report.json")
    assert response["sha256"] == calculate_sha256(output_path)
    assert output_path.exists()
    assert report_path.is_file()
    assert "ABC2LAB" in report_path.read_text(encoding="utf-8")
    _assert_output_integrity(input_paths, context, response)


def test_run_evaluate_publishes_evaluation_only(
    evaluate_arguments: Arguments,
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    _remove_contract_output(context["run_root"], "evaluation_results.json")

    response = entrypoint.run("evaluate", input_paths, output_dir, context)

    artifact = load_json(
        _contract_output(context["run_root"], "evaluation_results.json")
    )
    assert response["status"] == "completed"
    assert artifact["artifact_type"] == "evaluation_results"
    assert "report_path" not in response
    assert not (context["run_root"] / "reports").exists()
    _assert_output_integrity(input_paths, context, response)


@pytest.mark.parametrize(
    "artifact_type",
    tuple(name for name in INPUT_SCHEMA_BY_NAME if name != "ground_truth"),
)
@pytest.mark.parametrize("schema_version", ["0.1.0", "0.3.0"])
def test_run_evaluate_rejects_unsupported_runtime_contract_versions(
    evaluate_arguments: Arguments,
    artifact_type: str,
    schema_version: str,
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    _remove_contract_output(context["run_root"], "evaluation_results.json")
    descriptor = input_paths[artifact_type]
    input_path = context["run_root"] / descriptor["path"]
    artifact = load_json(input_path)
    artifact["schema_version"] = schema_version
    input_path.write_text(json.dumps(artifact), encoding="utf-8")
    descriptor["sha256"] = calculate_sha256(input_path)

    response = entrypoint.run("evaluate", input_paths, output_dir, context)

    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "CONTRACT_INVALID"
    assert response["output_path"] is None
    assert not _contract_output(
        context["run_root"], "evaluation_results.json"
    ).exists()
    assert not (context["run_root"] / "reports").exists()


def test_run_report_rejects_hash_mismatch_without_outputs(
    report_arguments: Arguments,
) -> None:
    input_paths, output_dir, context = report_arguments
    _remove_contract_output(context["run_root"], "diagnosis_report.json")
    input_paths["test_scenarios"]["sha256"] = "0" * 64

    response = entrypoint.run("report", input_paths, output_dir, context)

    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "INPUT_HASH_MISMATCH"
    assert not _contract_output(
        context["run_root"],
        "diagnosis_report.json",
    ).exists()
    assert not (context["run_root"] / "reports").exists()


def test_run_evaluate_rejects_non_development_mode(
    evaluate_arguments: Arguments,
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    _remove_contract_output(context["run_root"], "evaluation_results.json")
    context["mode"] = "diagnosis"

    response = entrypoint.run("evaluate", input_paths, output_dir, context)

    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "CONTRACT_INVALID"


def test_run_rejects_unsupported_operation(
    report_arguments: Arguments,
) -> None:
    response = entrypoint.run("unknown", *report_arguments)

    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "OPERATION_UNSUPPORTED"


def test_run_report_never_overwrites_existing_html(
    report_arguments: Arguments,
) -> None:
    input_paths, output_dir, context = report_arguments
    _remove_contract_output(context["run_root"], "diagnosis_report.json")
    report_path = (
        context["run_root"] / "reports/diagnosis_report-iteration-000.html"
    )
    report_path.parent.mkdir(parents=True)
    report_path.write_text("existing", encoding="utf-8")

    response = entrypoint.run("report", input_paths, output_dir, context)

    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "OUTPUT_EXISTS"
    assert report_path.read_text(encoding="utf-8") == "existing"
    assert not _contract_output(
        context["run_root"],
        "diagnosis_report.json",
    ).exists()


def test_run_report_rolls_back_html_when_json_publication_fails(
    report_arguments: Arguments,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_paths, output_dir, context = report_arguments
    _remove_contract_output(context["run_root"], "diagnosis_report.json")

    def fail_publication(*_: object) -> None:
        raise OSError("fixture storage failure")

    monkeypatch.setattr(
        entrypoint,
        "publish_diagnosis_artifact",
        fail_publication,
    )

    response = entrypoint.run("report", input_paths, output_dir, context)

    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "STORAGE_FAILED"
    assert not (
        context["run_root"] / "reports/diagnosis_report-iteration-000.html"
    ).exists()


def test_run_evaluate_reports_storage_failure(
    evaluate_arguments: Arguments,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    _remove_contract_output(context["run_root"], "evaluation_results.json")

    def fail_publication(*_: object) -> None:
        raise OSError("fixture storage failure")

    monkeypatch.setattr(
        entrypoint,
        "publish_evaluation_artifact",
        fail_publication,
    )

    response = entrypoint.run("evaluate", input_paths, output_dir, context)

    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "STORAGE_FAILED"


def test_cli_runs_report_with_contract_paths(
    report_arguments: Arguments,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_paths, _, context = report_arguments
    _remove_contract_output(context["run_root"], "diagnosis_report.json")

    exit_code = entrypoint.main(
        [
            "report",
            "--run-root",
            str(context["run_root"]),
            "--run-id",
            context["run_id"],
            "--iteration",
            "0",
            "--mode",
            "development",
            "--target-url",
            context["target_url"],
        ]
    )

    response = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert response["status"] == "completed"
    assert (context["run_root"] / response["report_path"]).exists()
    _assert_output_integrity(input_paths, context, response)


def test_cli_runs_evaluate_with_dataset(
    evaluate_arguments: Arguments,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_paths, _, context = evaluate_arguments
    _remove_contract_output(context["run_root"], "evaluation_results.json")

    exit_code = entrypoint.main(
        [
            "evaluate",
            "--run-root",
            str(context["run_root"]),
            "--run-id",
            context["run_id"],
            "--iteration",
            "0",
            "--mode",
            "development",
            "--target-url",
            context["target_url"],
            "--project-root",
            str(context["project_root"]),
            "--dataset-id",
            "shop_demo",
        ]
    )

    response = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert response["status"] == "completed"
    assert response["output_path"].endswith("evaluation_results.json")
    _assert_output_integrity(input_paths, context, response)


def test_cli_returns_failure_when_input_is_missing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_root = tmp_path / "run_missing"
    run_root.mkdir()

    exit_code = entrypoint.main(
        [
            "report",
            "--run-root",
            str(run_root),
            "--run-id",
            "run_missing",
            "--iteration",
            "0",
            "--mode",
            "diagnosis",
            "--target-url",
            "http://127.0.0.1:9000/",
        ]
    )

    response = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "PATH_INVALID"


def _assert_output_integrity(
    input_paths: dict[str, Any],
    context: dict[str, Any],
    response: dict[str, Any],
) -> None:
    output_path = context["run_root"] / response["output_path"]
    artifact = load_json(output_path)
    assert artifact["schema_version"] == "0.2.0"
    assert response["sha256"] == calculate_sha256(output_path)
    references_by_type = {
        item["artifact_type"]: item for item in artifact["input_refs"]
    }
    assert len(references_by_type) == len(artifact["input_refs"])
    assert set(references_by_type) == set(input_paths) - {"ground_truth"}
    for artifact_type, reference in references_by_type.items():
        descriptor = input_paths[artifact_type]
        source_path = context["run_root"] / descriptor["path"]
        source = load_json(source_path)
        assert reference == {
            "artifact_id": source["artifact_id"],
            "artifact_type": source["artifact_type"],
            "iteration": source["iteration"],
            "path": descriptor["path"],
            "sha256": calculate_sha256(source_path),
        }
    if "ground_truth" in input_paths:
        reference = artifact["data"]["ground_truth_ref"]
        ground_truth_path = context["project_root"] / reference["path"]
        assert reference["sha256"] == calculate_sha256(ground_truth_path)
        assert load_json(ground_truth_path)["schema_version"] == "0.1.0"


def _contract_output(run_root: Path, filename: str) -> Path:
    return run_root / f"artifacts/iteration-000/reporter/{filename}"


def _remove_contract_output(run_root: Path, filename: str) -> None:
    _contract_output(run_root, filename).unlink(missing_ok=True)
