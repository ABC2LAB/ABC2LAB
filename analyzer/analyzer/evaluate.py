"""
룰 커버리지 평가
==============

analyze가 낸 검증 태스크가 Ground Truth의 V1~V5를 얼마나 '겨냥'했는지 측정한다.

주의: analyze는 '탐지'가 아니라 '검증 태스크 생성' 단계다. 따라서 여기서 재는 것은
최종 Recall/Precision이 아니라 '룰 커버리지(rule reach)' = 각 취약점을 검증할 태스크가
후보로 올라왔는가다. 최종 판정은 뒤 단계(실행/agent) 이후 다시 평가한다.

- reached   : 해당 V의 엔드포인트를 겨냥한 태스크가 하나라도 있는가
- by_llm    : 룰로는 원리상 도달 불가, LLM/소스분석에 넘겨야 하는 항목(V4/V5 일부)

사용:
    python -m analyzer.evaluate /tmp/analyze_out.json vulnerabilities.json
"""

from __future__ import annotations

import argparse
import json
import re

# GT endpoint 문자열에서 (method, path) 추출. path의 {xxx}는 {id}로 통일.
_GT_EP = re.compile(r"(GET|POST|PUT|PATCH|DELETE)\s+(\S+)")


def _norm_path(p: str) -> str:
    return re.sub(r"\{[^}]+\}", "{id}", p)


def _gt_endpoints(vuln: dict) -> list[tuple[str, str]]:
    """하나의 V 항목에서 (method, normalized_path) 목록 추출."""
    out = []
    ep = vuln.get("endpoint", "")
    for m in _GT_EP.finditer(ep):
        out.append((m.group(1), _norm_path(m.group(2))))
    if not out and ep:
        # method 없이 경로만 있는 경우
        for part in ep.split(","):
            part = part.strip()
            if part:
                out.append(("GET", _norm_path(part.split()[0])))
    return out


def evaluate(report: dict, gt: dict) -> dict:
    tasks = report.get("tasks", [])
    task_eps = {(t["method"], _norm_path(t["endpoint"])) for t in tasks}
    # hint -> 태스크 존재 여부도 같이 본다
    hinted = {}
    for t in tasks:
        h = t.get("expected_vuln_hint")
        if h and h.rstrip("?") .startswith("V"):
            hinted.setdefault(h.rstrip("?"), []).append(t["task_id"])

    results = []
    reached = 0
    total = 0
    for v in gt.get("vulnerabilities", []):
        vid = v["id"]
        eps = _gt_endpoints(v)
        hit_eps = [ep for ep in eps if ep in task_eps]
        by_endpoint = len(hit_eps) > 0
        by_hint = vid in hinted
        is_reached = by_endpoint or by_hint
        total += 1
        reached += 1 if is_reached else 0
        results.append({
            "id": vid,
            "name": v.get("name", ""),
            "gt_endpoints": [f"{m} {p}" for m, p in eps],
            "reached": is_reached,
            "matched_by_endpoint": [f"{m} {p}" for m, p in hit_eps],
            "matched_by_hint": hinted.get(vid, []),
        })

    return {
        "reached": reached,
        "total": total,
        "reach_rate": round(reached / total, 3) if total else 0.0,
        "per_vuln": results,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("analyze_report")
    ap.add_argument("ground_truth")
    args = ap.parse_args()

    report = json.load(open(args.analyze_report, encoding="utf-8"))
    gt = json.load(open(args.ground_truth, encoding="utf-8"))
    res = evaluate(report, gt)

    print(f"룰 커버리지: {res['reached']}/{res['total']}  (reach_rate={res['reach_rate']})")
    print("-" * 68)
    for r in res["per_vuln"]:
        mark = "O" if r["reached"] else "X"
        how = []
        if r["matched_by_endpoint"]:
            how.append("endpoint")
        if r["matched_by_hint"]:
            how.append(f"hint({','.join(r['matched_by_hint'])})")
        how_s = ",".join(how) if how else "-"
        print(f"  [{mark}] {r['id']} {r['name'][:22]:24} via {how_s}")
        print(f"       GT: {r['gt_endpoints']}")


if __name__ == "__main__":
    main()
