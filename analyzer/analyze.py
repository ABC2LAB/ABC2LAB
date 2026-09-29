"""오케스트레이터: CrawlResult → AnalysisResult (명세 1.0)."""
from __future__ import annotations

from datetime import UTC, datetime

from analyzer.infer import infer_resources
from analyzer.llm_client import LLMClient
from analyzer.loader import load_crawl_result
from analyzer.observed import build_observed
from analyzer.schemas import AnalysisResult, AnalysisRunInfo, CrawlResult
from analyzer.validate import validate

ANALYZER_VERSION = "0.1.0"
INFERENCE_POLICY_VERSION = "1.0"


def analyze_json(raw_json: str | bytes, client: LLMClient, *,
                 analyzed_at: str | None = None, strict: bool = True
                 ) -> tuple[AnalysisResult, list[str]]:
    """원문 crawl JSON → (AnalysisResult, 입력 경고목록).

    검증 단계(loader) → 추론 단계(analyze) 순서로 배선한다.
    치명적 입력 오류는 loader 가 CrawlValidationError 를 던져 추론에 진입하지
    않는다(fail-closed). 사소한 경고는 warnings 로 함께 돌려준다.
    """
    crawl, warnings = load_crawl_result(raw_json)
    result = analyze(crawl, client, analyzed_at=analyzed_at, strict=strict)
    return result, warnings


def analyze(crawl: CrawlResult, client: LLMClient, *,
            analyzed_at: str | None = None, strict: bool = True) -> AnalysisResult:
    # 1) 관찰(rule): ApiOperation·Parameter + HAS_PARAMETER
    nodes, rels = build_observed(crawl)
    # 2) 추론(llm): Resource + ACCESSES
    resources, r_rels = infer_resources(nodes, client)
    nodes += resources
    rels += r_rels
    # (Feature/Flow 는 다음 단계)

    # 3) 조립 + provenance
    result = AnalysisResult(
        analysis=AnalysisRunInfo(
            crawl_run_id=crawl.run_id,
            target_base_url=crawl.target_base_url,
            analyzed_at=analyzed_at or datetime.now(UTC).isoformat(),
            source=f"crawl_result_{crawl.run_id}.json",
            analyzer_version=ANALYZER_VERSION,
            inference_policy_version=INFERENCE_POLICY_VERSION,
        ),
        nodes=nodes, relationships=rels)

    # 4) 자체 검증
    errors = validate(result)
    if errors and strict:
        raise ValueError("analysis_result 검증 실패:\n- " + "\n- ".join(errors))
    return result
