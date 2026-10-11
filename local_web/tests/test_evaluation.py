"""Reporter 공개 평가 계약을 표시하고 정답·평가 계산을 Web으로 옮기지 않는다."""

import hashlib
import json
import os
from pathlib import Path

import pytest

from local_web.artifacts import PUBLIC_FILES, read_results
from local_web.tests.fixtures.runner.public_artifacts import make_artifacts, public_bytes


@pytest.fixture
def evaluation_root(tmp_path: Path) -> Path:
    root = tmp_path / "run-evaluation"
    for name, payload in make_artifacts(root.name, "http://fixture-web:9345/start").items():
        path = root / "artifacts/iteration-000" / PUBLIC_FILES[name]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(public_bytes(payload))
    return root


def evaluation_file(root: Path) -> Path:
    return root / "artifacts/iteration-000/reporter/evaluation_results.json"


def evaluation_result(root: Path, digest: str | None = None) -> dict:
    expected = {"evaluation_results": digest} if digest is not None else None
    return next(row for row in read_results(root, expected) if row["artifact_type"] == "evaluation_results")


def test_evaluation_preserves_public_measurements(evaluation_root: Path) -> None:
    output = evaluation_result(evaluation_root)
    assert output["state"] == "completed"
    view = output["view"]
    assert view["dataset"] == {"dataset_id": "case_v2", "dataset_version": "fixture-v3", "sha256": "c" * 64}
    assert view["source_graph_revision"] == 2 and view["matching_profile"] == "default-v1"
    assert view["metrics"][0] == {"metric_id": "page_recall", "value": 0.75, "numerator": 3, "denominator": 4}
    assert view["metrics"][1] == {"metric_id": "endpoint_recall", "value": None, "numerator": 0, "denominator": 0}
    assert view["candidate_counts"] == {"tp": 2, "fp": 1, "fn": 1}
    assert view["confirmed_counts"] == {"tp": 1, "fp": 0, "fn": 2}
    assert view["unverified_counts"] == {"policy_blocked": 0, "approval_pending": 1, "indeterminate": 1}
    assert view["models"]["semantic_analyzer"]["seed"] is None
    assert view["run_metrics"]["output_tokens"] is None
    assert "ground_truth.json" not in json.dumps(view)


def test_evaluation_is_not_recomputed_or_limited_to_known_metrics(evaluation_root: Path) -> None:
    path = evaluation_file(evaluation_root)
    payload = json.loads(path.read_bytes())
    payload["data"]["metrics"] = [{"metric_id": "future_metric", "value": 0.23, "numerator": 1, "denominator": 3},
                                  {"metric_id": "unmeasured_metric", "value": None, "numerator": 2, "denominator": 5}]
    payload["data"]["models"]["future_module"] = payload["data"]["models"].pop("semantic_analyzer")
    path.write_bytes(public_bytes(payload))
    result = evaluation_result(evaluation_root)
    assert result["state"] == "completed"
    assert result["view"]["metrics"] == payload["data"]["metrics"]
    assert "future_module" in result["view"]["models"]


@pytest.mark.parametrize("case", ["missing", "partial", "failed", "empty"])
def test_evaluation_states_do_not_become_zero_score(evaluation_root: Path, case: str) -> None:
    path = evaluation_file(evaluation_root)
    payload = json.loads(path.read_bytes())
    if case == "missing":
        path.unlink()
    else:
        if case == "empty":
            payload["data"].update({"metrics": [], "models": {}, "notes": []})
        else:
            payload.update({"status": case, "errors": [{"code": "TEST", "message": "PASSWORD_SENTINEL",
                                                        "item_ref": None, "retryable": False}]})
            if case == "failed":
                payload["data"] = None
        path.write_bytes(public_bytes(payload))
    result = evaluation_result(evaluation_root)
    assert result["state"] == ("completed" if case == "empty" else case)
    assert (result["view"] is None) == (case in {"missing", "failed"})
    if case == "partial":
        assert result["view"]["metrics"][1]["value"] is None and result["error_count"] == 1
    assert "PASSWORD_SENTINEL" not in json.dumps(result)


@pytest.mark.parametrize("value", [-1, 1.01, True, "0.5", [], {}])
def test_invalid_metric_value_is_not_displayed(evaluation_root: Path, value: object) -> None:
    path = evaluation_file(evaluation_root)
    payload = json.loads(path.read_bytes())
    payload["data"]["metrics"][0]["value"] = value
    path.write_bytes(public_bytes(payload))
    assert evaluation_result(evaluation_root)["state"] == "invalid"


@pytest.mark.parametrize("case", ["duplicate_metric", "zero_denominator", "negative_numerator", "bool_denominator",
                                  "missing_value", "extra_data", "missing_models", "bad_tp", "bool_fp",
                                  "extra_counts", "negative_unverified", "missing_unverified", "bad_runtime",
                                  "negative_memory", "bad_temperature", "bool_temperature", "bad_seed",
                                  "extra_model", "bad_model_id", "bad_note", "bad_profile"])
def test_evaluation_projection_rejects_invalid_records(evaluation_root: Path, case: str) -> None:
    path = evaluation_file(evaluation_root)
    payload = json.loads(path.read_bytes())
    data = payload["data"]
    if case == "duplicate_metric":
        data["metrics"].append(data["metrics"][0])
    elif case == "zero_denominator":
        data["metrics"][0]["denominator"] = 0
    elif case == "negative_numerator":
        data["metrics"][0]["numerator"] = -1
    elif case == "bool_denominator":
        data["metrics"][0]["denominator"] = True
    elif case == "missing_value":
        del data["metrics"][0]["value"]
    elif case == "extra_data":
        data["password"] = "PASSWORD_SENTINEL"
    elif case == "missing_models":
        del data["models"]
    elif case == "bad_tp":
        data["candidate_counts"]["tp"] = -1
    elif case == "bool_fp":
        data["confirmed_counts"]["fp"] = True
    elif case == "extra_counts":
        data["candidate_counts"]["total"] = 3
    elif case == "negative_unverified":
        data["unverified_counts"]["indeterminate"] = -1
    elif case == "missing_unverified":
        del data["unverified_counts"]["approval_pending"]
    elif case == "bad_runtime":
        data["run_metrics"]["duration_ms"] = True
    elif case == "negative_memory":
        data["run_metrics"]["peak_memory_mb"] = -0.5
    elif case in {"bad_temperature", "bool_temperature", "bad_seed", "extra_model", "bad_model_id"}:
        model = data["models"]["semantic_analyzer"]
        key, value = {"bad_temperature": ("temperature", "0.2"), "bool_temperature": ("temperature", True),
                      "bad_seed": ("seed", 1.5), "extra_model": ("password", "PASSWORD_SENTINEL"),
                      "bad_model_id": ("model_id", "")}[case]
        model[key] = value
    elif case == "bad_note":
        data["notes"] = [None]
    else:
        data["matching_profile"] = ""
    path.write_bytes(public_bytes(payload))
    result = evaluation_result(evaluation_root)
    assert result["state"] == "invalid" and result["view"] is None
    assert "PASSWORD_SENTINEL" not in json.dumps(result)


@pytest.mark.parametrize("case", ["revision", "reference_hash", "reference_id", "reference_path", "dataset_path",
                                  "traversal", "dataset_id", "truth_hash"])
def test_evaluation_metadata_and_public_refs_checked(evaluation_root: Path, case: str) -> None:
    path = evaluation_file(evaluation_root)
    payload = json.loads(path.read_bytes())
    data = payload["data"]
    if case == "revision":
        data["source_graph_revision"] = 3
    elif case.startswith("reference_"):
        key = case.removeprefix("reference_")
        key = "sha256" if key == "hash" else "artifact_id" if key == "id" else key
        payload["input_refs"][0][key] = "d" * 64 if key == "sha256" else "wrong"
    elif case == "dataset_path":
        data["ground_truth_ref"]["path"] = "datasets/other/ground_truth.json"
    elif case == "traversal":
        data["ground_truth_ref"]["path"] = "datasets/case_v2/../../private/secret"
    elif case == "dataset_id":
        data["ground_truth_ref"]["dataset_id"] = "../secret"
    else:
        data["ground_truth_ref"]["sha256"] = "not-a-hash"
    path.write_bytes(public_bytes(payload))
    assert evaluation_result(evaluation_root)["state"] == "invalid"


@pytest.mark.parametrize("key,value", [("schema_version", "0.1.0"), ("run_id", "other"),
                                      ("producer", "verifier"), ("mode", "diagnosis"), ("iteration", 1)])
def test_evaluation_identity_checked(evaluation_root: Path, key: str, value: object) -> None:
    path = evaluation_file(evaluation_root)
    payload = json.loads(path.read_bytes())
    payload[key] = value
    path.write_bytes(public_bytes(payload))
    assert evaluation_result(evaluation_root)["issue"] == "ARTIFACT_IDENTITY_MISMATCH"


def test_evaluation_uses_exit_hash(evaluation_root: Path) -> None:
    path = evaluation_file(evaluation_root)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    payload = json.loads(path.read_bytes())
    payload["data"]["metrics"][0]["value"] = 0.24
    path.write_bytes(public_bytes(payload))
    assert evaluation_result(evaluation_root, digest)["issue"] == "ARTIFACT_HASH_MISMATCH"


def test_evaluation_reads_no_ground_truth_private_or_logs(evaluation_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = os.open
    opened = []

    def guarded_open(path: str, flags: int, **kwargs: object) -> int:
        opened.append(str(path))
        assert str(path) not in {"datasets", "ground_truth.json", "private", "logs", "evidence"}
        return original(path, flags, **kwargs)

    monkeypatch.setattr("local_web.artifacts.os.open", guarded_open)
    assert evaluation_result(evaluation_root)["state"] == "completed"
    assert "evaluation_results.json" in opened
