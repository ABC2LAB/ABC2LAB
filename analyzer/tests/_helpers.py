import json
import pathlib

from analyzer.schemas import CrawlResult

DATA = pathlib.Path(__file__).parent / "data"
SCHEMAS = pathlib.Path(__file__).parent / "schemas"
def load_crawl() -> CrawlResult:
    return CrawlResult.model_validate(json.loads((DATA / "crawl_sample.json").read_text(encoding="utf-8")))
