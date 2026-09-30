"""
analyze 파이프라인
=================

크롤 JSON -> (검증) -> (정규화/그래프) -> (룰) -> AnalyzeReport(JSON)

LLM은 여기 없다. 전부 결정론적. secure/vulnerable 어느 모드 크롤이든 동일 처리.

사용:
    python -m analyzer.pipeline crawl_vulnerable.json -o analyze_out.json
    python -m analyzer.pipeline crawl_vulnerable.json --graph graph.json
"""

from __future__ import annotations

import argparse
import json
import sys

from .schema import parse_crawl
from .graph import build_graph
from .rules import run_rules
from .models import AnalyzeReport


def analyze(data: dict) -> AnalyzeReport:
    run, errors = parse_crawl(data)
    graph = build_graph(run)
    tasks = run_rules(run)

    # 통계
    by_rule = {}
    by_category = {}
    for t in tasks:
        by_rule[t.rule_id] = by_rule.get(t.rule_id, 0) + 1
        by_category[t.category] = by_category.get(t.category, 0) + 1

    report = AnalyzeReport(
        run_id=run.run_id,
        target_base_url=run.target_base_url,
        schema_ok=(len(errors) == 0),
        schema_errors=errors,
        roles=run.role_names(),
        graph=graph,
        tasks=tasks,
        stats={
            "pages": len(run.all_pages()),
            "requests": len(run.all_requests()),
            "endpoints": len({(r.method, r.endpoint) for r in run.all_requests()}),
            "graph_nodes": len(graph.nodes),
            "graph_edges": len(graph.edges),
            "tasks_total": len(tasks),
            "tasks_by_rule": by_rule,
            "tasks_by_category": by_category,
        },
    )
    return report


def main():
    ap = argparse.ArgumentParser(description="analyze: 크롤 JSON -> 검증 태스크")
    ap.add_argument("crawl_json", help="크롤러 출력 JSON 경로")
    ap.add_argument("-o", "--out", default="-", help="AnalyzeReport 출력 경로 (기본: stdout)")
    ap.add_argument("--graph", default=None, help="그래프만 따로 저장할 경로(선택)")
    ap.add_argument("--quiet", action="store_true", help="요약 로그 끄기")
    args = ap.parse_args()

    with open(args.crawl_json, encoding="utf-8") as f:
        data = json.load(f)

    report = analyze(data)

    out_str = json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
    if args.out == "-":
        print(out_str)
    else:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out_str)

    if args.graph and report.graph:
        with open(args.graph, "w", encoding="utf-8") as f:
            json.dump(report.graph.to_dict(), f, ensure_ascii=False, indent=2)

    if not args.quiet:
        s = report.stats
        print(f"[analyze] run_id={report.run_id} schema_ok={report.schema_ok} "
              f"errors={len(report.schema_errors)}", file=sys.stderr)
        print(f"[analyze] roles={report.roles} pages={s['pages']} "
              f"requests={s['requests']} endpoints={s['endpoints']}", file=sys.stderr)
        print(f"[analyze] graph: {s['graph_nodes']} nodes / {s['graph_edges']} edges",
              file=sys.stderr)
        print(f"[analyze] tasks={s['tasks_total']} by_rule={s['tasks_by_rule']}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
