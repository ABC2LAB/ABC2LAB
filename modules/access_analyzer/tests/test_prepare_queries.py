"""prepare_queries end-to-end. 특히 실패 파일 공개 vs 경로 불확실(파일 없음) 경계를 본다."""

import hashlib
import json
from pathlib import Path

from modules.access_analyzer import entrypoint as ep
from modules.access_analyzer.tests.helpers import (
    GRAPH_QUERY_FIXTURE,
    make_context,
    output_dir_for,
    schema_errors,
)
from modules.access_analyzer.utils.validation import validate_graph_query_bytes

EXPECTED_QUERY_KEYS = ["resource_ownership", "role_resource_access", "workflow_dependencies", "structure_snapshot"]


def _run(run_root: Path, **ctx_overrides):
    context = make_context(run_root, **ctx_overrides)
    return ep.run("prepare_queries", [], output_dir_for(run_root), context)


def test_prepare_queries_publishes_valid_artifact(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo"
    result = _run(run_root)
    assert result["status"] == "completed"
    published = output_dir_for(run_root) / "graph_query.json"
    raw = published.read_bytes()
    assert result["artifact_path"] == str(published)
    assert result["sha256"] == hashlib.sha256(raw).hexdigest()
    assert validate_graph_query_bytes(raw) == []
    assert ep._exit_code(result) == ep.EXIT_COMPLETED


def test_published_queries_match_fixture_shape(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo"
    _run(run_root)
    published = json.loads((output_dir_for(run_root) / "graph_query.json").read_bytes())
    fixture = json.loads(GRAPH_QUERY_FIXTURE.read_bytes())
    assert [q["query_key"] for q in published["data"]["queries"]] == EXPECTED_QUERY_KEYS
    assert published["data"]["queries"] == fixture["data"]["queries"]
    assert schema_errors("output/graph_query.schema.json", published) == []


def test_expected_revision_null_queries_current(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo"
    result = _run(run_root, expected_graph_revision=None)
    published = json.loads((output_dir_for(run_root) / "graph_query.json").read_bytes())
    assert result["status"] == "completed"
    assert published["data"]["expected_graph_revision"] is None


# --- 경계: 경로를 신뢰할 수 있으면 failed 파일 공개(종료코드 2) ---

def test_unknown_operation_writes_no_file(tmp_path: Path) -> None:
    # 알 수 없는 operation은 어떤 산출물을 쓸지 몰라 파일을 만들지 않는다(analyze는 정상 operation).
    run_root = tmp_path / "run_demo"
    context = make_context(run_root)
    result = ep.run("foo", [], output_dir_for(run_root), context)
    assert result["status"] == "failed"
    assert result["artifact_path"] is None
    assert result["errors"][0]["code"] == "OPERATION_UNSUPPORTED"
    assert ep._exit_code(result) == ep.EXIT_NO_FILE


def test_input_paths_publishes_failed_file(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo"
    context = make_context(run_root)
    result = ep.run("prepare_queries", ["x.json"], output_dir_for(run_root), context)
    assert result["status"] == "failed"
    assert result["artifact_path"] is not None
    assert result["errors"][0]["code"] == "INPUT_UNEXPECTED"
    assert ep._exit_code(result) == ep.EXIT_FAILED_WITH_FILE


# --- 경계: 경로가 불확실하면 파일 없이 종료코드 3 ---

def test_undefined_context_key_writes_no_file(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo"
    result = _run(run_root, surprise="x")
    assert result["status"] == "failed"
    assert result["artifact_path"] is None
    assert result["errors"][0]["code"] == "CONTEXT_INVALID"
    assert ep._exit_code(result) == ep.EXIT_NO_FILE
    assert not output_dir_for(run_root).exists() or not (output_dir_for(run_root) / "graph_query.json").exists()


def test_run_root_name_mismatch_writes_no_file(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo"
    # run_id는 그대로인데 run_root가 다른 폴더를 가리키면 경로를 신뢰할 수 없다.
    context = make_context(run_root)
    context["run_root"] = str(tmp_path / "other")
    result = ep.run("prepare_queries", [], output_dir_for(run_root), context)
    assert result["artifact_path"] is None
    assert result["errors"][0]["code"] == "CONTEXT_INVALID"
    assert ep._exit_code(result) == ep.EXIT_NO_FILE


def test_output_dir_outside_run_root_writes_no_file(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo"
    context = make_context(run_root)
    result = ep.run("prepare_queries", [], tmp_path / "elsewhere", context)
    assert result["artifact_path"] is None
    assert result["errors"][0]["code"] == "OUTPUT_PATH_INVALID"
    assert ep._exit_code(result) == ep.EXIT_NO_FILE


def test_negative_expected_revision_writes_no_file(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo"
    result = _run(run_root, expected_graph_revision=-1)
    assert result["artifact_path"] is None
    assert result["errors"][0]["code"] == "CONTEXT_INVALID"


def test_bool_expected_revision_rejected(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo"
    result = _run(run_root, expected_graph_revision=True)
    assert result["artifact_path"] is None
    assert result["errors"][0]["code"] == "CONTEXT_INVALID"


# --- 완료 파일 불변 ---

def test_republish_rejected(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo"
    first = _run(run_root)
    assert first["status"] == "completed"
    second = _run(run_root)
    assert second["status"] == "failed"
    assert second["artifact_path"] is None
    assert second["errors"][0]["code"] == "ARTIFACT_EXISTS"
    assert ep._exit_code(second) == ep.EXIT_NO_FILE
