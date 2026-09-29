import json
from common.schemas import AnalysisResult
from analyzer.analyze import analyze
from analyzer.llm_client import FakeClient
from analyzer.infer import demo_fake_responder
from analyzer.tests._helpers import load_crawl


def test_full_pipeline_produces_valid_result():
    result = analyze(load_crawl(), FakeClient(demo_fake_responder))
    assert result.schema_version == "1.0"
    assert result.analysis.crawl_run_id == "20261001-051203-ab12"
    assert result.nodes.roles and result.nodes.api_operations and result.nodes.resources
    # 계약대로 직렬화/역직렬화 왕복
    again = AnalysisResult.model_validate(json.loads(result.model_dump_json()))
    assert again.analysis.analysis_id == "analysis-001"
