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
