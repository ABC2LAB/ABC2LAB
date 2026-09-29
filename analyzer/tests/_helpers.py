import json, pathlib
from common.schemas import CrawlResult
SAMPLE = pathlib.Path(__file__).parent / "data" / "crawl_sample.json"
def load_crawl() -> CrawlResult:
    return CrawlResult.model_validate(json.loads(SAMPLE.read_text(encoding="utf-8")))
