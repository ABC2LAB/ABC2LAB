"""scenario_generator 테스트 공용 fixture. 값은 하드코딩하지 않고 fixture 파일에서 읽는다."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from modules.scenario_generator.candidate_matcher import (
    CrawlIndex,
    MatchedCandidate,
    build_crawl_index,
    match_candidates,
)
from modules.scenario_generator.input_adapter import InputSource, LoadedArtifact, load_input_artifact

TESTS_DIR = Path(__file__).parent
FIXTURE_RUN_ROOT = TESTS_DIR / "fixtures" / "runs" / "run_demo_001"
CRAWL_RELATIVE_PATH = "artifacts/iteration-000/collector/crawl_result.json"
CANDIDATES_RELATIVE_PATH = "artifacts/iteration-000/access_analyzer/vulnerability_candidates.json"
OUTPUT_RELATIVE_PATH = "artifacts/iteration-000/scenario_generator/test_scenarios.json"
DRAFTS_PATH = TESTS_DIR / "fixtures" / "drafts" / "run_demo_001.drafts.json"
PROVIDER_NONE_CONFIG_PATH = TESTS_DIR / "fixtures" / "configs" / "provider_none.toml"


@pytest.fixture(autouse=True)
def isolated_drafter_config(monkeypatch: pytest.MonkeyPatch) -> Path:
    """drafter= 없이 부르는 테스트가 커밋된 기본 설정·셸 값을 읽지 않게 한다(provider=none, 네트워크 0)."""
    monkeypatch.setenv("SCENARIO_GENERATOR_CONFIG_PATH", str(PROVIDER_NONE_CONFIG_PATH))
    return PROVIDER_NONE_CONFIG_PATH


@pytest.fixture
def fixture_run_root() -> Path:
    return FIXTURE_RUN_ROOT


@pytest.fixture
def run_id() -> str:
    return json.loads((FIXTURE_RUN_ROOT / CRAWL_RELATIVE_PATH).read_text(encoding="utf-8"))["run_id"]


@pytest.fixture
def drafts_path() -> Path:
    return DRAFTS_PATH


@pytest.fixture
def drafts() -> dict[str, Any]:
    return json.loads(DRAFTS_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def crawl_artifact(run_id: str) -> LoadedArtifact:
    source = InputSource("crawl_result", FIXTURE_RUN_ROOT / CRAWL_RELATIVE_PATH)
    return load_input_artifact(source, FIXTURE_RUN_ROOT, run_id)


@pytest.fixture
def candidates_artifact(run_id: str) -> LoadedArtifact:
    source = InputSource("vulnerability_candidates", FIXTURE_RUN_ROOT / CANDIDATES_RELATIVE_PATH)
    return load_input_artifact(source, FIXTURE_RUN_ROOT, run_id)


@pytest.fixture
def crawl_index(crawl_artifact: LoadedArtifact) -> CrawlIndex:
    return build_crawl_index(copy.deepcopy(crawl_artifact.document["data"]))


@pytest.fixture
def matched_candidates(candidates_artifact: LoadedArtifact, crawl_index: CrawlIndex) -> list[MatchedCandidate]:
    candidates = copy.deepcopy(candidates_artifact.document["data"]["candidates"])
    result = match_candidates(candidates, crawl_index)
    assert result.rejected == []
    return result.matched
