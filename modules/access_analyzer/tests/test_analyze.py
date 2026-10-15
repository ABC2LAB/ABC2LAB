"""analyze end-to-end. 질의 실패·partial·stale을 '후보 0개'와 구분하는 것이 핵심(명세 m4).

입력 fixture(graph_query_result)는 KG #29 이후 의미대로: 계정·역할 ID는 crawl 원본(acc_alice·role_user).
각 테스트는 '이 판정을 빼면 통과해 버리는' 버그 하나를 겨눈다. 커밋 테스트는 우리 폴더 Schema만 읽는다.
"""

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from modules.access_analyzer import entrypoint as ep
from modules.access_analyzer.tests.helpers import (
    CRAWL_FIXTURE_DIR,
    CRAWL_INPUT_REL,
    RESULT_INPUT_REL,
    make_context,
    make_crawl_artifact,
    output_dir_for,
    schema_errors,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "graph_query_result"
INPUT_REL = RESULT_INPUT_REL
CANDIDATES_SCHEMA = "output/vulnerability_candidates.schema.json"
QUERY_PLAN_MISMATCH = "QUERY_PLAN_MISMATCH"
# 결과 rows에만 있는 값. 오류 메시지에 rows 내용이 섞이면 안 된다.
ROW_ONLY_VALUES = ("acc_alice", "resource:order_001", "role_user")
OTHER_PRODUCER_GRAPH_QUERY_PATH = "artifacts/iteration-000/knowledge_graph/graph_query.json"
# crawl fixture(acc_alice·acc_bob, 요청 4개 전부 2xx)에 맞는 두 입력.
BOTH_INPUTS = [INPUT_REL, CRAWL_INPUT_REL]


def _place_input(run_root: Path, fixture_name: str) -> Path:
    target = run_root / INPUT_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURE_DIR / f"{fixture_name}.json", target)
    return target


def _place_crawl(run_root: Path, fixture_name: str = "completed") -> Path:
    target = run_root / CRAWL_INPUT_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(CRAWL_FIXTURE_DIR / f"{fixture_name}.json", target)
    return target


def _write_json(run_root: Path, relative: str, document: dict[str, Any]) -> Path:
    target = run_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document), encoding="utf-8")
    return target


def _analyze(run_root: Path, fixture_name: str, **ctx_overrides):
    _place_input(run_root, fixture_name)
    _place_crawl(run_root)
    context = make_context(run_root, **ctx_overrides)
    return ep.run("analyze", BOTH_INPUTS, output_dir_for(run_root), context)


def _published(run_root: Path) -> dict:
    return json.loads((output_dir_for(run_root) / "vulnerability_candidates.json").read_bytes())


def _crawl_fixture_accounts() -> list[tuple[str, str, str | None]]:
    document = json.loads((CRAWL_FIXTURE_DIR / "completed.json").read_bytes())
    return [(a["account_id"], a["role_id"], a["session_ref"]) for a in document["data"]["accounts"]]


def _crawl_fixture_requests() -> list[tuple[str, str, int | None]]:
    document = json.loads((CRAWL_FIXTURE_DIR / "completed.json").read_bytes())
    return [(r["request_id"], r["account_id"], r["response"]["status_code"]) for r in document["data"]["requests"]]


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


def test_completed_links_both_input_refs(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    input_path = _place_input(run_root, "completed")
    crawl_path = _place_crawl(run_root)
    context = make_context(run_root)
    # 경로 순서를 바꿔 넘겨도 input_refs는 종류 순서(graph_query_result, crawl_result)다.
    result = ep.run("analyze", [CRAWL_INPUT_REL, INPUT_REL], output_dir_for(run_root), context)
    doc = _published(run_root)
    assert [(ref["artifact_type"], ref["path"]) for ref in doc["input_refs"]] == [
        ("graph_query_result", INPUT_REL), ("crawl_result", CRAWL_INPUT_REL),
    ]
    assert doc["input_refs"][0]["sha256"] == hashlib.sha256(input_path.read_bytes()).hexdigest()
    assert doc["input_refs"][1]["sha256"] == hashlib.sha256(crawl_path.read_bytes()).hexdigest()
    assert result["status"] == "completed"


# --- crawl_result 입력 (명세 m4: 없으면 crawl 관찰 규칙만 건너뛰고 partial, 다른 실행 것이면 failed) ---


def test_without_crawl_result_rule_a_runs_but_partial(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    _place_input(run_root, "two_owners")
    result = ep.run("analyze", [INPUT_REL], output_dir_for(run_root), make_context(run_root))
    doc = _published(run_root)
    assert result["status"] == "partial"
    assert [e["code"] for e in doc["errors"]] == ["CRAWL_INPUT_UNAVAILABLE"]
    # 소유 관계만 보는 Rule A는 crawl 없이도 돈다. 후보를 비우지 않는다.
    assert len(doc["data"]["candidates"]) == 2
    assert [ref["artifact_type"] for ref in doc["input_refs"]] == ["graph_query_result"]
    assert schema_errors(CANDIDATES_SCHEMA, doc) == []


def test_crawl_result_from_other_run_rejected(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    _place_input(run_root, "two_owners")
    _write_json(run_root, CRAWL_INPUT_REL, make_crawl_artifact(_crawl_fixture_accounts(), _crawl_fixture_requests(), run_id="run_other"))
    result = ep.run("analyze", BOTH_INPUTS, output_dir_for(run_root), make_context(run_root))
    assert result["status"] == "failed"
    assert _published(run_root)["data"] is None
    assert result["errors"][0]["code"] == "RUN_ID_MISMATCH"


def test_failed_crawl_result_skips_crawl_rules_but_keeps_rule_a(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    _place_input(run_root, "two_owners")
    _write_json(run_root, CRAWL_INPUT_REL, make_crawl_artifact([], [], status="failed"))
    result = ep.run("analyze", BOTH_INPUTS, output_dir_for(run_root), make_context(run_root))
    doc = _published(run_root)
    assert result["status"] == "partial"
    assert [e["code"] for e in doc["errors"]] == ["CRAWL_INPUT_UNAVAILABLE"]
    assert len(doc["data"]["candidates"]) == 2
    assert [ref["artifact_type"] for ref in doc["input_refs"]] == ["graph_query_result", "crawl_result"]


def test_partial_crawl_result_is_used_as_normal_input(tmp_path: Path) -> None:
    # partial은 data가 유효하다. partial이라는 이유로 crawl 관찰 규칙을 건너뛰지 않는다.
    run_root = tmp_path / "run_demo_001"
    _place_input(run_root, "two_owners")
    partial = make_crawl_artifact(_crawl_fixture_accounts(), _crawl_fixture_requests(), status="partial")
    _write_json(run_root, CRAWL_INPUT_REL, partial)
    result = ep.run("analyze", BOTH_INPUTS, output_dir_for(run_root), make_context(run_root))
    doc = _published(run_root)
    assert result["status"] == "completed"
    assert doc["errors"] == []
    assert len(doc["data"]["candidates"]) == 2


def test_crawl_result_contract_invalid_rejected(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    _place_input(run_root, "completed")
    document = json.loads((CRAWL_FIXTURE_DIR / "completed.json").read_bytes())
    document["data"]["surprise"] = "x"  # 미정의 키
    _write_json(run_root, CRAWL_INPUT_REL, document)
    result = ep.run("analyze", BOTH_INPUTS, output_dir_for(run_root), make_context(run_root))
    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "INPUT_CONTRACT_INVALID"


def test_input_of_unknown_artifact_type_rejected(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    _place_input(run_root, "completed")
    document = json.loads((CRAWL_FIXTURE_DIR / "completed.json").read_bytes())
    document["artifact_type"] = "semantic_analysis"
    _write_json(run_root, CRAWL_INPUT_REL, document)
    result = ep.run("analyze", BOTH_INPUTS, output_dir_for(run_root), make_context(run_root))
    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "INPUT_CONTRACT_INVALID"


def test_crawl_result_from_earlier_iteration_is_accepted(tmp_path: Path) -> None:
    # 다음 회차 analyze는 회차 0의 crawl_result를 그대로 읽는다. 입력 경로 검사는 run_root 안인지만 보고 iteration 폴더에 묶지 않는다.
    run_root = tmp_path / "run_demo_001"
    later_result_rel = INPUT_REL.replace("iteration-000", "iteration-001")
    _write_json(run_root, later_result_rel, json.loads((FIXTURE_DIR / "two_owners.json").read_bytes()))
    _place_crawl(run_root)  # iteration-000에 그대로
    context = make_context(run_root, iteration=1)
    result = ep.run("analyze", [later_result_rel, CRAWL_INPUT_REL], output_dir_for(run_root, iteration=1), context)
    doc = json.loads((output_dir_for(run_root, iteration=1) / "vulnerability_candidates.json").read_bytes())
    assert result["status"] == "completed"
    assert doc["iteration"] == 1
    assert [ref["path"] for ref in doc["input_refs"]] == [later_result_rel, CRAWL_INPUT_REL]
    assert len(doc["data"]["candidates"]) == 2


def test_crawl_result_alone_is_missing_graph_result(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    _place_crawl(run_root)
    result = ep.run("analyze", [CRAWL_INPUT_REL], output_dir_for(run_root), make_context(run_root))
    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "INPUT_MISSING"


# --- expected_sha256 (런너가 넘긴 파일 바이트가 그대로인지) ---


def _sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_matching_expected_sha256_accepted(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    input_path = _place_input(run_root, "completed")
    crawl_path = _place_crawl(run_root)
    expected = {"graph_query_result": _sha256_of(input_path), "crawl_result": _sha256_of(crawl_path)}
    result = ep.run("analyze", BOTH_INPUTS, output_dir_for(run_root), make_context(run_root, expected_sha256=expected))
    assert result["status"] == "completed"


@pytest.mark.parametrize("artifact_type", ["graph_query_result", "crawl_result"])
def test_mismatching_expected_sha256_rejected(tmp_path: Path, artifact_type: str) -> None:
    run_root = tmp_path / "run_demo_001"
    _place_input(run_root, "completed")
    _place_crawl(run_root)
    expected = {artifact_type: "0" * 64}
    result = ep.run("analyze", BOTH_INPUTS, output_dir_for(run_root), make_context(run_root, expected_sha256=expected))
    assert result["status"] == "failed"
    assert _published(run_root)["data"] is None
    assert result["errors"][0]["code"] == "INPUT_HASH_MISMATCH"
    assert "0" * 64 not in result["errors"][0]["message"]  # 해시값은 메시지에 넣지 않는다


@pytest.mark.parametrize(
    "expected_sha256",
    [
        pytest.param("abc", id="not_a_mapping"),
        pytest.param({"semantic_analysis": "0" * 64}, id="unknown_input_type"),
        pytest.param({"crawl_result": "0" * 63}, id="short_hex"),
        pytest.param({"crawl_result": "A" * 64}, id="uppercase_hex"),
    ],
)
def test_malformed_expected_sha256_writes_no_file(tmp_path: Path, expected_sha256: Any) -> None:
    run_root = tmp_path / "run_demo_001"
    _place_input(run_root, "completed")
    _place_crawl(run_root)
    result = ep.run("analyze", BOTH_INPUTS, output_dir_for(run_root), make_context(run_root, expected_sha256=expected_sha256))
    assert result["status"] == "failed"
    assert result["artifact_path"] is None
    assert result["errors"][0]["code"] == "CONTEXT_INVALID"


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


def test_three_inputs_rejected(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    _place_input(run_root, "completed")
    _place_crawl(run_root)
    context = make_context(run_root)
    result = ep.run("analyze", [*BOTH_INPUTS, INPUT_REL], output_dir_for(run_root), context)
    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "INPUT_UNEXPECTED"


def test_same_input_type_twice_rejected(tmp_path: Path) -> None:
    # 두 파일이 다 graph_query_result면 어느 쪽이 맞는지 알 수 없다. 뒤 것을 조용히 덮어쓰지 않는다.
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
    _place_crawl(run_root)
    context = make_context(run_root)
    result = ep.run("analyze", BOTH_INPUTS, output_dir_for(run_root), context)
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
    second = ep.run("analyze", BOTH_INPUTS, output_dir_for(run_root), context)
    assert second["status"] == "failed"
    assert second["artifact_path"] is None
    assert second["errors"][0]["code"] == "ARTIFACT_EXISTS"


# --- 계획한 질의와 결과의 대응 (명세 m4: query_id·query_key·revision이 대응하지 않으면 후보를 만들지 않는다) ---


def _completed_document() -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / "completed.json").read_bytes())


def _analyze_document(run_root: Path, document: dict[str, Any]) -> dict[str, Any]:
    _write_json(run_root, INPUT_REL, document)
    _place_crawl(run_root)
    return ep.run("analyze", BOTH_INPUTS, output_dir_for(run_root), make_context(run_root))


def _result_index(document: dict[str, Any], query_key: str) -> int:
    return next(index for index, result in enumerate(document["data"]["results"]) if result["query_key"] == query_key)


def _assert_plan_mismatch(run_root: Path, result: dict[str, Any], *fragments: str) -> None:
    """failed·data=null·후보 없음이고, 오류는 전부 QUERY_PLAN_MISMATCH이며 rows 값이 메시지에 없다."""
    doc = _published(run_root)
    assert result["status"] == "failed"
    assert doc["data"] is None
    assert {error["code"] for error in doc["errors"]} == {QUERY_PLAN_MISMATCH}
    messages = " ".join(error["message"] for error in doc["errors"])
    for fragment in fragments:
        assert fragment in messages
    assert [value for value in ROW_ONLY_VALUES if value in messages] == []
    assert schema_errors(CANDIDATES_SCHEMA, doc) == []
    assert ep._exit_code(result) == ep.EXIT_FAILED_WITH_FILE


def test_missing_planned_result_fails(tmp_path: Path) -> None:
    # 소유 관계 결과가 빠지면 지금은 후보 0개 completed로 끝난다. 분석하지 못한 것을 "후보 없음"으로 숨기면 안 된다.
    run_root = tmp_path / "run_demo_001"
    document = _completed_document()
    del document["data"]["results"][_result_index(document, "resource_ownership")]

    result = _analyze_document(run_root, document)

    _assert_plan_mismatch(run_root, result, "계획한 질의 결과가 없음: q_resource_ownership(resource_ownership)")


def test_unplanned_query_id_fails(tmp_path: Path) -> None:
    run_root = tmp_path / "run_demo_001"
    document = _completed_document()
    document["data"]["results"][_result_index(document, "workflow_dependencies")]["query_id"] = "q_unplanned"

    result = _analyze_document(run_root, document)

    _assert_plan_mismatch(
        run_root,
        result,
        "계획에 없는 질의 결과: q_unplanned(workflow_dependencies)",
        "계획한 질의 결과가 없음: q_workflow_dependencies(workflow_dependencies)",
    )


def test_swapped_query_pair_fails(tmp_path: Path) -> None:
    # query_id는 계획에 있지만 짝지은 query_key가 다르다. 규칙이 query_key로만 찾으면 그대로 후보를 만든다.
    run_root = tmp_path / "run_demo_001"
    document = _completed_document()
    results = document["data"]["results"]
    ownership, access = _result_index(document, "resource_ownership"), _result_index(document, "role_resource_access")
    ownership_id, access_id = results[ownership]["query_id"], results[access]["query_id"]
    results[ownership]["query_id"], results[access]["query_id"] = access_id, ownership_id

    result = _analyze_document(run_root, document)

    _assert_plan_mismatch(
        run_root,
        result,
        "질의 키가 계획과 다름: q_resource_ownership는 resource_ownership 계획인데 role_resource_access 결과",
        "질의 키가 계획과 다름: q_role_resource_access는 role_resource_access 계획인데 resource_ownership 결과",
    )


def test_duplicate_query_key_fails(tmp_path: Path) -> None:
    # query_id는 달라 입력 의미 검증(query_id 중복)을 통과하지만 같은 query_key가 두 번 온다.
    run_root = tmp_path / "run_demo_001"
    document = _completed_document()
    extra = dict(document["data"]["results"][_result_index(document, "resource_ownership")])
    extra["query_id"] = "q_resource_ownership_again"
    document["data"]["results"].append(extra)

    result = _analyze_document(run_root, document)

    _assert_plan_mismatch(run_root, result, "같은 query_key 결과가 2개: resource_ownership")


@pytest.mark.parametrize(
    "change_refs",
    [
        pytest.param(lambda refs: [], id="no_graph_query_ref"),
        pytest.param(lambda refs: [dict(refs[0], path=OTHER_PRODUCER_GRAPH_QUERY_PATH)], id="other_producer"),
        pytest.param(lambda refs: [refs[0], dict(refs[0])], id="two_graph_query_refs"),
    ],
)
def test_graph_query_input_ref_required(tmp_path: Path, change_refs: Any) -> None:
    run_root = tmp_path / "run_demo_001"
    document = _completed_document()
    document["input_refs"] = change_refs(document["input_refs"])

    result = _analyze_document(run_root, document)

    _assert_plan_mismatch(run_root, result, "access_analyzer가 쓴 graph_query 참조가 정확히 하나여야 함")

