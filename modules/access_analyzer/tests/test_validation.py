"""graph_query.json 자기 출력 검증. 각 테스트는 '이 검사를 빼면 통과해 버리는' 버그 하나를 겨눈다."""

import json

from modules.access_analyzer.tests.helpers import GRAPH_QUERY_FIXTURE
from modules.access_analyzer.utils import validation as v


def _fixture_bytes() -> bytes:
    return GRAPH_QUERY_FIXTURE.read_bytes()


def _codes(raw: bytes) -> set[str]:
    return {issue.code.value for issue in v.validate_graph_query_bytes(raw)}


def test_valid_graph_query_passes() -> None:
    assert v.validate_graph_query_bytes(_fixture_bytes()) == []


def test_duplicate_query_id_rejected() -> None:
    document = json.loads(_fixture_bytes())
    document["data"]["queries"][1]["query_id"] = document["data"]["queries"][0]["query_id"]
    issues = v.validate_graph_query_bytes(json.dumps(document).encode())
    assert any(issue.code is v.IssueCode.DUPLICATE_ID for issue in issues)
    assert any(issue.location == "$.data.queries[1].query_id" for issue in issues)


def test_empty_queries_rejected() -> None:
    document = json.loads(_fixture_bytes())
    document["data"]["queries"] = []
    assert "QUERIES_EMPTY" in _codes(json.dumps(document).encode())


def test_undefined_key_rejected() -> None:
    document = json.loads(_fixture_bytes())
    document["data"]["queries"][0]["surprise"] = "x"
    assert "SCHEMA_INVALID" in _codes(json.dumps(document).encode())


def test_wrong_producer_rejected() -> None:
    document = json.loads(_fixture_bytes())
    document["producer"] = "collector"
    assert "SCHEMA_INVALID" in _codes(json.dumps(document).encode())


def test_completed_with_errors_rejected() -> None:
    # status=completed인데 errors가 있으면 공통 상태 규칙 위반이다.
    document = json.loads(_fixture_bytes())
    document["errors"] = [{"code": "X", "message": "x", "item_ref": None, "retryable": False}]
    assert "SCHEMA_INVALID" in _codes(json.dumps(document).encode())


def test_non_utc_created_at_rejected() -> None:
    document = json.loads(_fixture_bytes())
    document["created_at"] = "2026-10-07 00:00:00"
    assert "TIME_INVALID" in _codes(json.dumps(document).encode())


def test_duplicate_json_key_rejected() -> None:
    # 표준 json은 뒤 값으로 덮어쓰지만 계약은 중복 키를 금지한다.
    raw = b'{"schema_version": "0.1.0", "schema_version": "0.1.0"}'
    assert "JSON_INVALID" in _codes(raw)


def test_nan_rejected() -> None:
    raw = b'{"runtime_metrics": {"duration_ms": NaN}}'
    assert "JSON_INVALID" in _codes(raw)
