"""테스트 공용 도구: 신뢰된 run_root·context 만들기와 fixture 경로.

Schema는 우리 폴더 사본만 읽는다(명세: 다른 모듈 Schema 참조 금지). 다른 모듈 Schema와의 교차
검증은 커밋 테스트가 아니라 수동 확인(PR 본문)으로 남긴다.
"""

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

MODULE_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = MODULE_ROOT / "schemas"
GRAPH_QUERY_FIXTURE = (
    MODULE_ROOT
    / "tests/fixtures/runs/run_demo_001/artifacts/iteration-000/access_analyzer/graph_query.json"
)
CRAWL_FIXTURE_DIR = MODULE_ROOT / "tests/fixtures/crawl_result"
# analyze 입력 둘의 run 안 경로(명세 03 폴더 구조).
RESULT_INPUT_REL = "artifacts/iteration-000/knowledge_graph/graph_query_result.json"
CRAWL_INPUT_REL = "artifacts/iteration-000/collector/crawl_result.json"
DEFAULT_RUN_ID = "run_demo_001"
JSON_CONTENT_TYPE = "application/json"


def make_crawl_artifact(
    accounts: list[tuple[str, str, str | None]],
    requests: list[tuple[str, str, int | None]],
    run_id: str = DEFAULT_RUN_ID,
    status: str = "completed",
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """규칙 테스트용 최소 crawl_result(0.3.0 모양). 값은 리터럴로 받는다.

    accounts: (account_id, role_id, session_ref). session_ref=None이 비인증 계정이다.
    requests: (request_id, account_id, status_code). status_code=None은 응답을 못 받은 요청. 응답은 JSON으로 둔다.
    status가 completed가 아니면 errors를 하나 채운다(공통 상태 규칙). failed면 data=null.
    """
    account_by_id = {account[0]: account for account in accounts}
    role_ids = sorted({account[1] for account in accounts})
    observed_requests = []
    for request_id, account_id, status_code in requests:
        _, role_id, session_ref = account_by_id[account_id]
        observed_requests.append({
            "request_id": request_id, "account_id": account_id, "role_id": role_id, "session_ref": session_ref,
            "page_id": None, "action_id": None, "method": "GET", "url": f"http://target.example.test/{request_id}",
            "observed_at": "2026-10-07T00:00:00Z", "parameters": [], "headers": [], "body_ref": None,
            "response": {
                "status_code": status_code, "content_type": JSON_CONTENT_TYPE if status_code is not None else None,
                "headers": [], "body_ref": None, "identifiers": None, "identifiers_truncated": False,
            },
            "evidence_refs": [],
        })
    data = None if status == "failed" else {
        "target_url": "http://target.example.test/",
        "roles": [{"role_id": role_id, "name": role_id} for role_id in role_ids],
        "accounts": [{"account_id": a, "role_id": r, "alias": a, "session_ref": s} for a, r, s in accounts],
        "pages": [], "actions": [], "requests": observed_requests, "discovered_endpoints": [],
    }
    if errors is None:
        errors = [] if status == "completed" else [{"code": "X", "message": "x", "item_ref": None, "retryable": False}]
    return {
        "schema_version": "0.3.0", "artifact_type": "crawl_result", "artifact_id": "crawl_result-test",
        "run_id": run_id, "iteration": 0, "producer": "collector", "mode": "development",
        "created_at": "2026-10-07T00:02:00Z", "status": status, "input_refs": [], "errors": errors,
        "runtime_metrics": None, "data": data,
    }


def load_our_schema(relative_name: str) -> dict[str, Any]:
    """우리 폴더 schemas/ 안의 Schema만 읽는다."""
    return json.loads((SCHEMA_DIR / relative_name).read_text(encoding="utf-8"))


def schema_errors(relative_name: str, document: Any) -> list[str]:
    validator = Draft202012Validator(load_our_schema(relative_name), format_checker=FormatChecker())
    return [f"{list(error.absolute_path)}: {error.message}" for error in validator.iter_errors(document)]


def make_context(run_root: Path, **overrides: Any) -> dict[str, Any]:
    context = {
        "run_id": run_root.name,
        "iteration": 0,
        "mode": "development",
        "run_root": str(run_root),
        "graph_id": "graph_demo_001",
        "expected_graph_revision": 1,
    }
    context.update(overrides)
    return context


def output_dir_for(run_root: Path, iteration: int = 0) -> Path:
    return run_root / "artifacts" / f"iteration-{iteration:03d}" / "access_analyzer"
