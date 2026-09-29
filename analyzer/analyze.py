"""오케스트레이터: CrawlResult → AnalysisResult. 각 단계를 순서대로 조립한다."""
from __future__ import annotations

from common.schemas import AnalysisMeta, AnalysisResult, CrawlResult
from analyzer.llm_client import LLMClient
from analyzer.observed import build_observed
from analyzer.infer import infer_resources
from analyzer.validate import validate


def analyze(crawl: CrawlResult, client: LLMClient, *, analysis_id: str = "analysis-001",
            strict: bool = True) -> AnalysisResult:
    # 1) 관찰 그래프 (LLM 없음)
    nodes, rels, evid = build_observed(crawl)
    # 2) LLM 추론 얹기
    resources, r_rels = infer_resources(nodes.api_operations, client)
    nodes.resources = resources
    rels += r_rels
    # (features/flows는 다음 단계)
    # 3) 조립
    result = AnalysisResult(
        analysis=AnalysisMeta(analysis_id=analysis_id, crawl_run_id=crawl.run_id,
                              target_base_url=crawl.target_base_url),
        nodes=nodes, relationships=rels, evidence=evid)
    # 4) 자체 검증
    errors = validate(result)
    if errors and strict:
        raise ValueError("analysis_result 검증 실패:\n- " + "\n- ".join(errors))
    return result
