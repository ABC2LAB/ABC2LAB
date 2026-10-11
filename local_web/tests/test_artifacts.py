"""독립 공개 fixture로 최소 표시 계약·경로·해시·비밀값 비노출을 검증한다."""

import hashlib
import json
import os
from pathlib import Path

import pytest
from jsonschema.validators import validator_for

from local_web.artifacts import MAX_ARTIFACT_BYTES, PUBLIC_FILES, read_results
from local_web.tests.fixtures.runner.public_artifacts import make_artifacts, public_bytes


@pytest.fixture
def artifact_root(tmp_path: Path) -> Path:
    root = tmp_path / "run-fixture"
    root.mkdir()
    for name, payload in make_artifacts(root.name, "http://fixture-web:9345/start").items():
        path = artifact_file(root, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(public_bytes(payload))
    return root


def artifact_file(root: Path, name: str) -> Path:
    return root / "artifacts" / "iteration-000" / PUBLIC_FILES[name]


def output(root: Path, name: str, hashes: dict[str, str] | None = None) -> dict:
    return next(row for row in read_results(root, hashes) if row["artifact_type"] == name)


def rewrite(root: Path, name: str, payload: dict) -> None:
    artifact_file(root, name).write_bytes(public_bytes(payload))


def test_fixture_public_schema_admission(artifact_root: Path) -> None:
    project = Path(__file__).resolve().parents[2]
    for name, suffix in PUBLIC_FILES.items():
        schema_path = project / "modules" / suffix.split("/")[0] / "schemas/output" / (name + ".schema.json")
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        validator_for(schema)(schema).validate(json.loads(artifact_file(artifact_root, name).read_bytes()))


def test_public_views_are_whitelisted_and_connected_by_ids(artifact_root: Path) -> None:
    values = read_results(artifact_root)
    assert len(values) == 9 and all(row["state"] == "completed" for row in values)
    by_type = {row["artifact_type"]: row["view"] for row in values}
    assert by_type["crawl_result"]["counts"]["pages"] == 1
    assert by_type["semantic_analysis"]["counts"]["nodes"] == 8
    graph = by_type["graph_query_result"]
    assert graph["snapshot_state"] == "completed" and len(graph["nodes"]) == 8
    assert graph["graph_revision"] == 2
    assert [step["step_id"] for step in graph["workflows"][0]["steps"]] == ["earlier", "later"]
    scopes = {node["resource_scope"] for node in graph["nodes"] if node["node_type"] == "Resource"}
    assert scopes == {"type", "instance"}
    report = by_type["diagnosis_report"]
    assert [row["finding_id"] for row in report["confirmed_findings"]] == ["finding-alpha"]
    assert {row["status"] for row in report["other_findings"]} == {"approval_pending", "indeterminate"}
    assert report["summary"]["approval_pending_count"] == 1
    assert by_type["test_scenarios"]["scenarios"][0]["candidate_id"] == "candidate-omega"
    assert next(row for row in by_type["safety_decisions"]["decisions"] if row["decision"] == "require_approval")["scenario_id"] == "scenario-delta"
    serialized = json.dumps(values)
    for forbidden in ("PASSWORD_SENTINEL", "PRIVATE_SESSION_SENTINEL", "url_template", "parameters", "session_ref", "match_key", "properties", "evidence/verifier/", str(artifact_root)):
        assert forbidden not in serialized


@pytest.mark.parametrize("key,value", [("schema_version", "0.1.0"), ("run_id", "other-run"), ("mode", "diagnosis"),
                                      ("iteration", 1), ("iteration", False), ("producer", "verifier"), ("artifact_type", "test_scenarios")])
def test_artifact_identity_mismatch(artifact_root: Path, key: str, value: object) -> None:
    payload = json.loads(artifact_file(artifact_root, "crawl_result").read_bytes())
    payload[key] = value
    rewrite(artifact_root, "crawl_result", payload)
    result = output(artifact_root, "crawl_result")
    assert result["state"] == "invalid" and result["issue"] == "ARTIFACT_IDENTITY_MISMATCH"
    assert result["view"] is None and result["error_count"] is None


@pytest.mark.parametrize("case", ["failed", "partial", "completed_with_errors", "failed_with_data", "unknown_key", "missing_key"])
def test_envelope_states_do_not_become_zero_results(artifact_root: Path, case: str) -> None:
    payload = json.loads(artifact_file(artifact_root, "crawl_result").read_bytes())
    if case in {"failed", "partial", "failed_with_data", "completed_with_errors"}:
        payload["status"] = "completed" if case == "completed_with_errors" else "failed" if case == "failed_with_data" else case
        payload["errors"] = [{"code": "TEST", "message": "PASSWORD_SENTINEL", "item_ref": None, "retryable": False}]
        if case == "failed":
            payload["data"] = None
    elif case == "unknown_key":
        payload["password"] = "PASSWORD_SENTINEL"
    else:
        del payload["data"]
    rewrite(artifact_root, "crawl_result", payload)
    result = output(artifact_root, "crawl_result")
    expected = case if case in {"failed", "partial"} else "invalid"
    assert result["state"] == expected
    assert (result["view"] is not None) == (case == "partial")
    assert "PASSWORD_SENTINEL" not in json.dumps(read_results(artifact_root))


@pytest.mark.parametrize("body", [b"PUBLICATION_METADATA_ONLY", b"[]", b"\xff", b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}'])
def test_invalid_json_not_empty_success(artifact_root: Path, body: bytes) -> None:
    artifact_file(artifact_root, "crawl_result").write_bytes(body)
    result = output(artifact_root, "crawl_result")
    assert result["state"] == "invalid" and result["view"] is None


def test_missing_artifact_is_not_zero(artifact_root: Path) -> None:
    artifact_file(artifact_root, "crawl_result").unlink()
    assert output(artifact_root, "crawl_result") == {
        "artifact_type": "crawl_result", "state": "missing", "view": None, "error_count": None, "issue": None,
    }


def test_hash_mismatch_after_exit(artifact_root: Path) -> None:
    hashes = {name: hashlib.sha256(artifact_file(artifact_root, name).read_bytes()).hexdigest() for name in PUBLIC_FILES}
    artifact_file(artifact_root, "crawl_result").write_bytes(b"PASSWORD_SENTINEL")
    result = output(artifact_root, "crawl_result", hashes)
    assert result["state"] == "invalid" and result["issue"] == "ARTIFACT_HASH_MISMATCH"


@pytest.mark.parametrize("kind", ["file", "producer", "run", "hardlink", "fifo"])
def test_symlinks_hardlinks_and_nonregular_files_rejected(artifact_root: Path, tmp_path: Path, kind: str) -> None:
    secret = tmp_path / "private-secret"
    secret.write_bytes(b"PASSWORD_SENTINEL")
    file = artifact_file(artifact_root, "crawl_result")
    if kind == "file":
        file.unlink()
        file.symlink_to(secret)
    elif kind == "hardlink":
        file.unlink()
        os.link(secret, file)
    elif kind == "fifo":
        file.unlink()
        os.mkfifo(file)
    elif kind == "producer":
        file.parent.rename(file.parent.with_name("original-collector"))
        file.parent.symlink_to(file.parent.with_name("original-collector"), target_is_directory=True)
    else:
        link = tmp_path / "linked-run"
        link.symlink_to(artifact_root, target_is_directory=True)
        artifact_root = link
    result = output(artifact_root, "crawl_result")
    assert result["state"] == "invalid" and result["view"] is None
    assert "PASSWORD_SENTINEL" not in json.dumps(read_results(artifact_root))


def test_reader_never_opens_private_logs_evidence_or_ground_truth(artifact_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = os.open
    opened = []

    def guarded_open(path: str, flags: int, **kwargs: object) -> int:
        opened.append(str(path))
        assert str(path) not in {"private", "logs", "evidence", "ground_truth.json"}
        return original(path, flags, **kwargs)

    monkeypatch.setattr("local_web.artifacts.os.open", guarded_open)
    assert all(row["state"] == "completed" for row in read_results(artifact_root))
    assert {Path(suffix).name for suffix in PUBLIC_FILES.values()} <= set(opened)


def test_size_and_item_limits_are_explicit(artifact_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifact_file(artifact_root, "crawl_result").write_bytes(b"x" * (MAX_ARTIFACT_BYTES + 1))
    assert output(artifact_root, "crawl_result")["issue"] == "ARTIFACT_TOO_LARGE"
    monkeypatch.setattr("local_web.artifacts.MAX_VIEW_ITEMS", 2)
    assert output(artifact_root, "vulnerability_candidates")["issue"] == "ARTIFACT_VIEW_LIMIT"


@pytest.mark.parametrize("case", ["duplicate_id", "missing_endpoint", "multiple_snapshots", "empty_snapshot", "failed_snapshot", "missing_snapshot"])
def test_graph_snapshot_selection_and_references(artifact_root: Path, case: str) -> None:
    payload = json.loads(artifact_file(artifact_root, "graph_query_result").read_bytes())
    snapshot = payload["data"]["results"][1]
    row = snapshot["rows"][0]
    if case == "duplicate_id":
        row["nodes"].append(row["nodes"][0])
    elif case == "missing_endpoint":
        row["relationships"][0]["target_id"] = "nonexistent"
    elif case == "multiple_snapshots":
        copy = json.loads(json.dumps(snapshot))
        copy["query_id"] = "second-snapshot"
        payload["data"]["results"].append(copy)
    elif case == "empty_snapshot":
        row.update({"nodes": [], "relationships": [], "workflows": []})
    elif case == "failed_snapshot":
        snapshot.update({"status": "failed", "rows": [], "errors": [{"code": "TEST", "message": "MASKED", "item_ref": None, "retryable": False}]})
    else:
        payload["data"]["results"] = payload["data"]["results"][:1]
    rewrite(artifact_root, "graph_query_result", payload)
    result = output(artifact_root, "graph_query_result")
    if case in {"duplicate_id", "missing_endpoint", "multiple_snapshots"}:
        assert result["state"] == "invalid" and result["view"] is None
    else:
        assert result["state"] == "completed"
        assert result["view"]["snapshot_state"] == {"empty_snapshot": "completed", "failed_snapshot": "failed", "missing_snapshot": "missing"}[case]
        assert result["view"]["nodes"] == ([] if case == "empty_snapshot" else None)


@pytest.mark.parametrize("case", ["input_hash", "input_id", "scenario_hash", "unsafe_evidence", "evidence_path", "duplicate_finding", "bad_count", "summary_mismatch", "unknown_data_key"])
def test_display_records_and_refs_fail_closed(artifact_root: Path, case: str) -> None:
    name = "safety_decisions" if case == "scenario_hash" else "diagnosis_report"
    payload = json.loads(artifact_file(artifact_root, name).read_bytes())
    if case == "input_hash":
        payload["input_refs"][0]["sha256"] = "f" * 64
    elif case == "input_id":
        payload["input_refs"][0]["artifact_id"] = "stale-artifact"
    elif case == "scenario_hash":
        payload["data"]["scenarios_sha256"] = "f" * 64
    elif case == "unsafe_evidence":
        payload["data"]["findings"][0]["evidence_refs"][0]["redacted"] = False
    elif case == "evidence_path":
        payload["data"]["findings"][0]["evidence_refs"][0]["path"] = "evidence/../../private/secret"
    elif case == "duplicate_finding":
        payload["data"]["findings"].append(payload["data"]["findings"][0])
    elif case == "bad_count":
        payload["data"]["summary"]["confirmed_count"] = True
    elif case == "summary_mismatch":
        payload["data"]["summary"]["confirmed_count"] = 0
    else:
        payload["data"]["extra_password"] = "PASSWORD_SENTINEL"
    rewrite(artifact_root, name, payload)
    result = output(artifact_root, name)
    assert result["state"] == "invalid" and result["view"] is None
