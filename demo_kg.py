"""
KG 파이프라인 실습: 크롤 JSON -> 규칙 그래프 -> LLM 보강 -> 저장.
실행(레포 루트에서): uv run python demo_kg.py
키(OPENAI_API_KEY)가 있으면 진짜 LLM, 없으면 연습용 가짜 LLM으로 돈다.
"""
import json
import pathlib

from kg.builder import build_kg
from kg.enrich import (
    EndpointClass,
    LLMClassification,
    enrich_with_llm,
    make_default_client,
)
from kg.kg_schema import CrawlResult


# 키가 없을 때 쓰는 연습용 가짜 LLM (네트워크·비용 0)
class PracticeLLM:
    def classify_endpoints(self, endpoints):
        out = []
        for e in endpoints:
            t = e["template"] or ""
            scope = ("admin_only" if "admin" in t
                     else "user_owned" if "orders" in t else "public")
            out.append(EndpointClass(endpoint_id=e["id"],
                                     access_scope=scope, rationale="연습용 규칙"))
        return LLMClassification(endpoints=out)


# 1) 크롤 결과 읽기 (나중에 민준 형님 실제 파일 경로로 바꾸면 됨)
sample = pathlib.Path("kg/tests/data/crawl_sample.json")
crawl = CrawlResult.model_validate(json.loads(sample.read_text(encoding="utf-8")))
print(f"[1.입력]  target={crawl.target_base_url}  roles={[r.role for r in crawl.roles]}")

# 2) 규칙 기반으로 그래프 만들기
kg = build_kg(crawl)
print(f"[2.빌드]  노드 {len(kg.nodes)}개, 엣지 {len(kg.edges)}개")

# 3) LLM 보강 (키 있으면 진짜, 없으면 연습용)
client = make_default_client()
if client is None:
    print("[3.LLM]  OPENAI_API_KEY 없음 -> 연습용 가짜 LLM 사용")
    client = PracticeLLM()
else:
    print("[3.LLM]  진짜 OpenAI 클라이언트 사용")
kg = enrich_with_llm(kg, client)

# 4) 결과 저장 + 요약 출력
out = pathlib.Path("kg/tests/data/kg_out.json")
out.write_text(kg.model_dump_json(indent=2), encoding="utf-8")
print(f"[4.저장]  {out}")
print("[결과]  Endpoint 별 access_scope:")
for n in kg.nodes:
    if n.label.value == "Endpoint":
        print(f"         {n.id:32} -> {n.properties.get('access_scope', '(없음)')}")
