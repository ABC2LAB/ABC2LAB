# knowledge_graph

`semantic_analysis.json`을 Neo4j에 적재하고, 허용된 정형 질의를 실행하며,
`verification_results.json`의 검증된 변경만 그래프에 반영한다.

## 공개 operation

- `ingest`: 의미 분석 노드·관계·업무 흐름 적재
- `query`: `graph_query.json`을 처리해 `graph_query_result.json` 생성
- `apply_verification`: 검증된 graph update 반영

## 현재 구현 범위

계약 기반과 Neo4j 저장 계층이 구현되어 있다.

- 입력 Schema: semantic analysis, graph query, verification results
- 출력 Schema: graph query result
- JSON·Schema·교차 ID 검증
- 신뢰 경로 검증
- SHA-256 계산과 원자적 JSON 저장
- 환경변수 기반 Neo4j 연결 설정
- 그래프 메타데이터·노드·관계·workflow 트랜잭션 적재
- Neo4j 제약조건과 run_id·graph_id 격리
- 중첩 JSON 직렬화·복원

`entrypoint.run`과 query·verification operation은 후속 단계에서 구현한다.

## Neo4j 설정

- `NEO4J_URI`
- `NEO4J_USERNAME`
- `NEO4J_PASSWORD`
- `NEO4J_DATABASE`

실제 비밀번호는 `.env`에만 보관한다.

## 테스트

저장소 루트에서 실행한다.

```bash
.venv/bin/python -m pytest modules/knowledge_graph/
```

실제 Neo4j 통합 테스트는 Neo4j가 실행 중일 때 명시적으로 활성화한다.

```bash
KG_RUN_NEO4J_INTEGRATION=1 .venv/bin/python -m pytest \
  modules/knowledge_graph/tests/test_neo4j_integration.py
```

실행 결과물과 인증 정보는 커밋하지 않는다.
