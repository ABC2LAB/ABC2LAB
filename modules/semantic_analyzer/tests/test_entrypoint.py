import json
from pathlib import Path

import pytest

from modules.semantic_analyzer.entrypoint import run
from modules.semantic_analyzer.utils.validation import (
    check_referential_integrity,
    validate_semantic_analysis,
)

_FIXTURE = (
    Path(__file__).parent
    / "fixtures/runs/run_demo_001/artifacts/iteration-000/collector/crawl_result.json"
)


def _read_output(output_dir: Path) -> dict:
    return json.loads((output_dir / "semantic_analysis.json").read_text(encoding="utf-8"))


def test_completed_run_writes_valid_output(tmp_path):
    result = run("analyze", {"crawl_result": str(_FIXTURE)}, tmp_path)
    assert result["status"] == "completed"
    assert result["errors"] == 0

    payload = _read_output(tmp_path)
    assert validate_semantic_analysis(payload) == []
    assert check_referential_integrity(payload["data"]) == []
    assert payload["producer"] == "semantic_analyzer"
    assert payload["artifact_type"] == "semantic_analysis"
    assert payload["input_refs"][0]["artifact_type"] == "crawl_result"
    assert result["summary"]["normalized_requests"] == 6


def test_llm_failure_produces_failed(tmp_path):
    # LLM(의미 추론) 실패는 가짜 정상으로 숨기지 않고 failed/data=null로 공개한다.
    from modules.semantic_analyzer.llm.adapter import LlmError

    class _FailingClient:
        def infer_request_meaning(self, features):
            raise LlmError("추론 실패(테스트)")

        def model_info(self):
            return None

    result = run("analyze", {"crawl_result": str(_FIXTURE)}, tmp_path, client=_FailingClient())
    assert result["status"] == "failed"
    payload = _read_output(tmp_path)
    assert payload["data"] is None
    assert any(e["code"] == "LLM_INFERENCE_FAILED" for e in payload["errors"])


def test_unsupported_operation_raises(tmp_path):
    with pytest.raises(ValueError):
        run("verify", {"crawl_result": str(_FIXTURE)}, tmp_path)


def test_unreadable_input_produces_failed(tmp_path):
    missing = tmp_path / "nope.json"
    result = run("analyze", {"crawl_result": str(missing)}, tmp_path)
    assert result["status"] == "failed"
    payload = _read_output(tmp_path)
    assert payload["data"] is None
    assert payload["errors"] and payload["errors"][0]["code"] == "INPUT_UNREADABLE"


def test_schema_invalid_input_produces_failed(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema_version": "0.1.0", "artifact_type": "crawl_result"}), encoding="utf-8")
    result = run("analyze", {"crawl_result": str(bad)}, tmp_path)
    assert result["status"] == "failed"
    payload = _read_output(tmp_path)
    assert payload["data"] is None
    assert any(e["code"] == "CONTRACT_INVALID" for e in payload["errors"])


def test_previous_contract_version_input_produces_failed(tmp_path):
    source = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    source["schema_version"] = "0.1.0"
    old_in = tmp_path / "old_crawl.json"
    old_in.write_text(json.dumps(source), encoding="utf-8")
    result = run("analyze", {"crawl_result": str(old_in)}, tmp_path)
    assert result["status"] == "failed"
    payload = _read_output(tmp_path)
    assert payload["data"] is None
    assert any(e["code"] == "CONTRACT_INVALID" and "schema_version" in e["message"] for e in payload["errors"])


def test_upstream_failed_input_produces_failed(tmp_path):
    source = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    source["status"] = "failed"
    source["data"] = None
    source["errors"] = [{"code": "X", "message": "upstream", "item_ref": None, "retryable": False}]
    failed_in = tmp_path / "failed_crawl.json"
    failed_in.write_text(json.dumps(source), encoding="utf-8")
    result = run("analyze", {"crawl_result": str(failed_in)}, tmp_path)
    assert result["status"] == "failed"
    assert _read_output(tmp_path)["data"] is None


def test_partial_input_is_processed_as_partial(tmp_path):
    source = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    source["status"] = "partial"
    source["errors"] = [{"code": "X", "message": "some pages missed", "item_ref": None, "retryable": True}]
    partial_in = tmp_path / "partial_crawl.json"
    partial_in.write_text(json.dumps(source), encoding="utf-8")
    result = run("analyze", {"crawl_result": str(partial_in)}, tmp_path)
    assert result["status"] == "partial"
    payload = _read_output(tmp_path)
    assert payload["data"] is not None
    assert payload["errors"]


def test_empty_requests_still_completed(tmp_path):
    source = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    source["data"]["requests"] = []
    source["data"]["pages"] = []
    source["data"]["actions"] = []
    empty_in = tmp_path / "empty_crawl.json"
    empty_in.write_text(json.dumps(source), encoding="utf-8")
    result = run("analyze", {"crawl_result": str(empty_in)}, tmp_path)
    assert result["status"] == "completed"
    payload = _read_output(tmp_path)
    assert payload["data"]["normalized_requests"] == []
    # 계정·역할 노드는 남는다(요청이 없어도 선언된 구조).
    assert any(n["node_type"] == "Role" for n in payload["data"]["nodes"])
