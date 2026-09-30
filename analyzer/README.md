# analyzer

크롤러(`crawler/`)가 만든 크롤 JSON을 받아 **결정론적으로** 웹 구조를 검증·정규화하고,
Knowledge Graph 씨앗과 "검증해야 할 가설(verification task)"을 만들어내는 모듈입니다.

이 모듈에는 **LLM이 없습니다.** 전부 순수 함수/규칙이며, 같은 입력이면 항상 같은 출력이 나옵니다.
LLM 기반 판단(AI agent)은 이 모듈이 낸 태스크를 입력으로 받아 `analyzer/` 아래에 이후 단계로
추가됩니다.

## 이 모듈이 하는 일 / 하지 않는 일

- **한다**: 크롤 JSON 스키마 검증, URL/파라미터 정규화, 그래프 조립(dict), 룰 기반 검증 태스크 추출
- **하지 않는다**: 실제 공격 요청 전송, 취약 여부 최종 확정, Neo4j 적재(→ `kg/` 담당), LLM 추론

핵심 관점: analyzer의 출력은 "탐지 결과"가 아니라 **검증 지시서**입니다.
크롤은 각 역할이 정상 흐름만 밟기 때문에 "남의 id로 접근하면?"(IDOR) 같은 건 데이터에 없습니다.
analyzer는 관측된 표면에서 "이것을 이렇게 검증하라"는 태스크를 빠짐없이 뽑아, 뒤 단계
(실행 모듈 / AI agent)가 실제로 확인하도록 넘깁니다.

## 파이프라인

```
크롤 JSON ─▶ schema(검증·파싱) ─▶ graph(조립) ─▶ rules(태스크 추출) ─▶ AnalyzeReport(JSON)
```

## 룰

| 룰 | 이름 | 무엇을 뽑나 | 겨냥하는 취약점 |
|---|---|---|---|
| R1 | cross_access | id로 파라미터화됐고 여러 역할이 200 관측한 엔드포인트 → 교차접근(IDOR) 검증 | V1, V2 |
| R2 | hidden_surface | 링크에 없는 GET fetch 엔드포인트 → 직접호출·열거 검증 | V2 |
| R3 | coverage_gap | `/admin` 표면인데 저권한에서 미관측 → 저권한 직접호출 재검증. fetch API는 high, HTML 페이지는 low | V3 (HTML 페이지는 decoy로 low) |
| R4 | state_change | 상태변경(POST 등) → Safety Policy 검토 플래그 + 값 신뢰 가설 | V5 |

R1~R4로 관측된 표면은 커버되지만, **크롤에 관측조차 안 된 것(예: 숨은 엔드포인트 V4, UI에 없는
파라미터)은 원리상 룰로 못 잡습니다.** 그 빈칸이 이후 AI agent가 채워야 할 지점입니다.

## 실행

```bash
# 크롤 JSON → 검증 태스크 리포트
uv run python -m analyzer.pipeline crawl_vulnerable.json -o analyze_out.json

# 그래프만 따로 저장
uv run python -m analyzer.pipeline crawl_vulnerable.json -o out.json --graph graph.json

# Ground Truth 대조 (룰 커버리지 측정)
uv run python -m analyzer.evaluate analyze_out.json vulnerabilities.json
```

## 출력 구조 (AnalyzeReport)

```jsonc
{
  "run_id": "...", "target_base_url": "...",
  "schema_ok": true, "schema_errors": [],
  "roles": ["guest", "user", "admin"],
  "graph": { "nodes": [...], "edges": [...] },   // kg/ 모듈이 Neo4j로 적재
  "tasks": [                                      // AI agent / 실행 모듈의 입력
    {
      "task_id": "T-R1-GET-orders_id",
      "rule_id": "R1", "category": "cross_access",
      "method": "GET", "endpoint": "/orders/{id}",
      "rationale": "...",
      "verify": { "type": "cross_object_access", "steps": [...] },
      "evidence": {...},
      "expected_vuln_hint": "V1",       // 평가 매핑용 힌트(확정 아님)
      "severity_hint": "high"
    }
  ],
  "stats": {...}
}
```

`expected_vuln_hint`는 Ground Truth 대조를 쉽게 하려는 **힌트**일 뿐, 취약 확정이 아닙니다.

## 테스트

```bash
uv run pytest analyzer/
```

`analyzer/tests/fixtures/`의 실제 크롤 데이터(vulnerable)와 Ground Truth로 회귀를 검증합니다.
현재 룰 커버리지: V1·V2·V3·V5 도달, V4는 설계상 미도달(관측 불가).

## 다음 단계 (이 아래로 추가 예정)

- `analyzer/agent/` : 태스크를 받아 작은/큰 모델로 판정하는 AI agent + 벤더 중립 모델 추상화
- 실행 모듈 연동     : cross_access/privilege_probe 태스크를 실제 요청으로 검증 (Safety Policy 경유)
