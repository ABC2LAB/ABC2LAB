# knowledge_graph

`semantic_analysis.json`을 Neo4j에 적재하고, 허용된 정형 질의를 실행하며,
`verification_results.json`의 검증된 변경만 그래프에 반영한다.

## 공개 operation

- `ingest`: 의미 분석 노드·관계·업무 흐름 적재
- `query`: `graph_query.json`을 처리해 `graph_query_result.json` 생성
- `apply_verification`: 검증된 graph update 반영

## 현재 구현 범위

계약 기반, Neo4j 저장 계층, 공개 ingest·query 연산이 구현되어 있다.

- 입력 Schema: semantic analysis, graph query, verification results
- 출력 Schema: graph query result
- JSON·Schema·교차 ID 검증
- 신뢰 경로 검증
- SHA-256 계산과 원자적 JSON 저장
- 환경변수 기반 Neo4j 연결 설정
- 그래프 메타데이터·노드·관계·workflow 트랜잭션 적재
- Neo4j 제약조건과 run_id·graph_id 격리
- 중첩 JSON 직렬화·복원
- semantic artifact ID·SHA-256 기반 멱등 적재
- 적재 트랜잭션 내부 건수 검증
- 허용된 `query_key`별 파라미터 검증과 읽기 전용 Cypher 템플릿
- 질의 전후 revision 검증과 질의별 completed/failed 처리
- 실제 Neo4j 적재 구조 기반 snapshot 복원
- `graph_query_result.json` 출력 검증·원자적 저장·불변 경로 보호
- `entrypoint.run("ingest" | "query", ...)`과 CLI

apply_verification operation은 후속 단계에서 구현한다.

## ingest 공개 호출

```python
from modules.knowledge_graph.entrypoint import run

response = run(
    operation="ingest",
    input_paths={
        "semantic_analysis": {
            "path": (
                "artifacts/iteration-000/semantic_analyzer/"
                "semantic_analysis.json"
            ),
            "sha256": "<64자리 SHA-256>",
        }
    },
    output_dir="artifacts/iteration-000/knowledge_graph",
    context={
        "run_id": "run_example",
        "iteration": 0,
        "mode": "diagnosis",
        "run_root": "/trusted/runs/run_example",
    },
)
```

제어 응답 필드는 다음과 같다.

- `operation`: `ingest`
- `status`: `completed`, `partial`, `failed`
- `graph_id`: 실패 시 null
- `graph_revision`: 최초 적재는 1, 실패 시 null
- `is_ready`: query 가능 여부
- `errors`: 공통 ErrorItem 형식

동일한 `run_id`, semantic artifact ID, SHA-256 재호출은 기존 graph 상태를
반환한다. 동일 artifact ID를 다른 SHA-256으로 다시 사용하면 실패한다.

CLI:

```bash
.venv/bin/python -m modules.knowledge_graph.entrypoint ingest \
  --input-path artifacts/iteration-000/semantic_analyzer/semantic_analysis.json \
  --input-sha256 '<64자리 SHA-256>' \
  --output-dir artifacts/iteration-000/knowledge_graph \
  --run-root /trusted/runs/run_example \
  --run-id run_example \
  --iteration 0 \
  --mode diagnosis
```

## query 공개 호출

```python
from modules.knowledge_graph.entrypoint import run

response = run(
    operation="query",
    input_paths={
        "graph_query": {
            "path": (
                "artifacts/iteration-000/access_analyzer/"
                "graph_query.json"
            ),
            "sha256": "<64자리 SHA-256>",
        }
    },
    output_dir="artifacts/iteration-000/knowledge_graph",
    context={
        "run_id": "run_example",
        "iteration": 0,
        "mode": "diagnosis",
        "run_root": "/trusted/runs/run_example",
    },
)
```

지원하는 고정 질의 키는 다음과 같다.

- `resource_ownership`: 계정·자원 필터로 소유 관계 조회
- `role_resource_access`: 역할 필터로 계정·역할·Endpoint·자원 접근 관계 조회
- `workflow_dependencies`: workflow 필터로 단계 의존 관계 조회
- `structure_snapshot`: 실제 적재된 노드·관계·workflow 반환

`expected_graph_revision`이 현재 revision과 다르거나 질의 실행 중 revision이
바뀌면 failed 산출물을 기록한다. 일부 질의만 실패하면 정상 질의 결과와
오류를 함께 담은 partial 산출물을 기록한다. 기존 완료 파일은 덮어쓰지 않는다.

query 제어 응답은 `artifact_id`, `output_path`, `sha256`, `graph_id`,
`graph_revision`, `errors`를 반환한다.

CLI:

```bash
.venv/bin/python -m modules.knowledge_graph.entrypoint query \
  --input-path artifacts/iteration-000/access_analyzer/graph_query.json \
  --input-sha256 '<64자리 SHA-256>' \
  --output-dir artifacts/iteration-000/knowledge_graph \
  --run-root /trusted/runs/run_example \
  --run-id run_example \
  --iteration 0 \
  --mode diagnosis
```

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
