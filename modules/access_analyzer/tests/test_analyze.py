"""analyze end-to-end. 질의 실패·partial·stale을 '후보 0개'와 구분하는 것이 핵심(명세 m4).

입력 fixture(graph_query_result)는 KG #29 이후 의미대로: 계정·역할 ID는 crawl 원본(acc_alice·role_user).
각 테스트는 '이 판정을 빼면 통과해 버리는' 버그 하나를 겨눈다. 커밋 테스트는 우리 폴더 Schema만 읽는다.
"""

import hashlib
import json
import shutil
from pathlib import Path

from modules.access_analyzer import entrypoint as ep
from modules.access_analyzer.tests.helpers import make_context, output_dir_for, schema_errors

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "graph_query_result"
INPUT_REL = "artifacts/iteration-000/knowledge_graph/graph_query_result.json"
CANDIDATES_SCHEMA = "output/vulnerability_candidates.schema.json"


def _place_input(run_root: Path, fixture_name: str) -> Path:
    target = run_root / INPUT_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURE_DIR / f"{fixture_name}.json", target)
    return target


def _analyze(run_root: Path, fixture_name: str, **ctx_overrides):
    _place_input(run_root, fixture_name)
    context = make_context(run_root, **ctx_overrides)
    return ep.run("analyze", [INPUT_REL], output_dir_for(run_root), context)


def _published(run_root: Path) -> dict:
    return json.loads((output_dir_for(run_root) / "vulnerability_candidates.json").read_bytes())


def test_completed_emits_empty_candidates(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    result = _analyze(run_root, "completed")
    doc = _published(run_root)
    assert result["status"] == "completed"
    assert doc["data"]["candidates"] == []
    assert doc["data"]["model_info"] is None
    assert doc["data"]["source_graph_revision"] == 1
    assert doc["errors"] == []
    assert schema_errors(CANDIDATES_SCHEMA, doc) == []
    assert ep._exit_code(result) == ep.EXIT_COMPLETED


def test_completed_links_input_ref(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    input_path = _place_input(run_root, "completed")
    context = make_context(run_root)
    result = ep.run("analyze", [INPUT_REL], output_dir_for(run_root), context)
    doc = _published(run_root)
    [ref] = doc["input_refs"]
    assert ref["artifact_type"] == "graph_query_result"
    assert ref["path"] == INPUT_REL
    assert ref["sha256"] == hashlib.sha256(input_path.read_bytes()).hexdigest()
    assert result["status"] == "completed"


def test_partial_keeps_errors_not_zero_candidates(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    result = _analyze(run_root, "partial")
    doc = _published(run_root)
    assert result["status"] == "partial"
    assert [e["code"] for e in doc["errors"]] == ["QUERY_RESULT_PARTIAL"]
    assert doc["data"]["candidates"] == []
    assert doc["data"]["source_graph_revision"] == 1
    assert schema_errors(CANDIDATES_SCHEMA, doc) == []
    assert ep._exit_code(result) == ep.EXIT_PARTIAL


def test_failed_input_is_failed_not_empty(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    result = _analyze(run_root, "failed")
    doc = _published(run_root)
    assert result["status"] == "failed"
    assert doc["data"] is None
    assert doc["errors"][0]["code"] == "INPUT_RESULT_FAILED"
    assert ep._exit_code(result) == ep.EXIT_FAILED_WITH_FILE


def test_stale_revision_makes_no_candidates(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    # fixture revision은 1인데 계획(context)은 2 → stale.
    result = _analyze(run_root, "completed", expected_graph_revision=2)
    doc = _published(run_root)
    assert result["status"] == "failed"
    assert doc["data"] is None
    assert doc["errors"][0]["code"] == "GRAPH_REVISION_STALE"


def test_null_expected_revision_accepts_actual(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    result = _analyze(run_root, "completed", expected_graph_revision=None)
    doc = _published(run_root)
    assert result["status"] == "completed"
    assert doc["data"]["source_graph_revision"] == 1


def test_graph_id_mismatch_rejected(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    result = _analyze(run_root, "completed", graph_id="graph_other")
    assert result["status"] == "failed"
    assert _published(run_root)["data"] is None
    assert result["errors"][0]["code"] == "GRAPH_ID_MISMATCH"


def test_run_id_mismatch_rejected(tmp_path: Path) -> None:
    # fixture run_id는 run_demo_001. run_root 이름을 다르게 하면 run_id가 달라진다.
    run_root = tmp_path / "run_other"
    result = _analyze(run_root, "completed")
    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "RUN_ID_MISMATCH"


def test_missing_input_writes_failed_file(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    context = make_context(run_root)
    result = ep.run("analyze", [], output_dir_for(run_root), context)
    assert result["status"] == "failed"
    assert result["artifact_path"] is not None
    assert result["errors"][0]["code"] == "INPUT_MISSING"
    assert ep._exit_code(result) == ep.EXIT_FAILED_WITH_FILE


def test_two_inputs_rejected(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    _place_input(run_root, "completed")
    context = make_context(run_root)
    result = ep.run("analyze", [INPUT_REL, INPUT_REL], output_dir_for(run_root), context)
    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "INPUT_UNEXPECTED"


def test_input_outside_run_root_rejected(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    run_root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    context = make_context(run_root)
    result = ep.run("analyze", [str(outside)], output_dir_for(run_root), context)
    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "INPUT_PATH_INVALID"


def test_input_contract_invalid_rejected(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    target = run_root / INPUT_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    document = json.loads((FIXTURE_DIR / "completed.json").read_bytes())
    document["data"]["surprise"] = "x"  # 미정의 키
    target.write_text(json.dumps(document), encoding="utf-8")
    context = make_context(run_root)
    result = ep.run("analyze", [INPUT_REL], output_dir_for(run_root), context)
    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "INPUT_CONTRACT_INVALID"


def test_unknown_operation_writes_no_file(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    context = make_context(run_root)
    result = ep.run("foo", [], output_dir_for(run_root), context)
    assert result["status"] == "failed"
    assert result["artifact_path"] is None
    assert result["errors"][0]["code"] == "OPERATION_UNSUPPORTED"
    assert ep._exit_code(result) == ep.EXIT_NO_FILE


def test_republish_rejected(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    first = _analyze(run_root, "completed")
    assert first["status"] == "completed"
    context = make_context(run_root)
    second = ep.run("analyze", [INPUT_REL], output_dir_for(run_root), context)
    assert second["status"] == "failed"
    assert second["artifact_path"] is None
    assert second["errors"][0]["code"] == "ARTIFACT_EXISTS"
