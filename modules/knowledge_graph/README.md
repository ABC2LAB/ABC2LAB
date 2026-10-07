# knowledge_graph

`semantic_analysis.json`을 Neo4j에 적재하고, 허용된 정형 질의를 실행하며,
`verification_results.json`의 검증된 변경만 그래프에 반영한다.

## 공개 operation

- `ingest`: 의미 분석 요청 관찰·노드·관계·업무 흐름 적재
- `query`: `graph_query.json`을 처리해 `graph_query_result.json` 생성
- `apply_verification`: 검증된 graph update 반영

## 현재 구현 범위

계약 기반, Neo4j 저장 계층과 공개 연산 3개가 구현되어 있다.

- 입력 Schema: semantic analysis, graph query, verification results
- 출력 Schema: graph query result
- JSON·Schema·교차 ID 검증
- 신뢰 경로 검증
- SHA-256 계산과 원자적 JSON 저장
- 환경변수 기반 Neo4j 연결 설정
- 그래프 메타데이터·노드·관계·workflow 트랜잭션 적재
- normalized request 내부 관찰 레코드 적재
- Neo4j 제약조건과 run_id·graph_id 격리
- 중첩 JSON 직렬화·복원
- semantic artifact ID·SHA-256 기반 멱등 적재
- 적재 트랜잭션 내부 건수 검증
- 허용된 `query_key`별 파라미터 검증과 읽기 전용 Cypher 템플릿
- 질의 전후 revision 검증과 질의별 completed/failed 처리
- 실제 Neo4j 적재 구조 기반 snapshot 복원
- `graph_query_result.json` 출력 검증·원자적 저장·불변 경로 보호
- 검증 실행 근거와 graph update의 EvidenceRef 대응 검증
- verification ID별 중복·충돌 방지와 stale revision 차단
- 검증 노드·관계 upsert와 revision 증가를 묶은 단일 트랜잭션
- `entrypoint.run("ingest" | "query" | "apply_verification", ...)`과 CLI

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

`role_resource_access`는 normalized request 내부 관찰 레코드를 기준으로
User→Role과 User→Endpoint 접근 관계가 모두 존재할 때만 row를 반환한다.
계정·역할은 원본 ID, action·Resource는 해당 요청의 의미를 사용한다. 같은
계정·역할·Endpoint·Resource·action의 반복 관찰은 한 row로 합치고 근거만
중복 없이 누적하며, action이나 Resource가 다르면 별도 row로 유지한다.
`resource_ownership`의 계정 필터와 `owner_account_id`도 같은 관찰 레코드를 통해
prefixed User node ID가 아닌 원본 계정 ID를 사용한다.

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

## apply_verification 공개 호출

```python
from modules.knowledge_graph.entrypoint import run

response = run(
    operation="apply_verification",
    input_paths={
        "verification_results": {
            "path": (
                "artifacts/iteration-000/verifier/"
                "verification_results.json"
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
        "graph_id": "graph_example",
    },
)
```

반영 조건은 다음과 같다.

- graph update의 source verification이 `allow`, `completed`, `success|failure`
- 갱신 노드·관계가 `basis=verified`이고 실제 실행 EvidenceRef를 사용
- 입력 `source_graph_revision`과 현재 revision 일치
- 노드·관계 ID가 기존 그래프 구조와 충돌하지 않음
- 관계의 source·target 노드가 기존 그래프 또는 같은 갱신에 존재

새 verification ID를 반영하면 revision을 1 증가시킨다. 동일 산출물의 동일
verification ID를 다시 호출하면 `is_applied=false`로 현재 revision을 반환한다.
같은 ID를 다른 산출물이 재사용하거나 일부 ID만 이미 반영된 입력은 거절한다.
graph update가 비어 있으면 revision을 변경하지 않는다. 이 operation은 별도 JSON
파일을 만들지 않는다.

제어 응답 필드는 다음과 같다.

- `operation`: `apply_verification`
- `status`: `completed`, `partial`, `failed`
- `graph_id`
- `previous_graph_revision`, `graph_revision`
- `applied_verification_ids`
- `is_applied`
- `errors`

CLI:

```bash
.venv/bin/python -m modules.knowledge_graph.entrypoint apply_verification \
  --input-path artifacts/iteration-000/verifier/verification_results.json \
  --input-sha256 '<64자리 SHA-256>' \
  --output-dir artifacts/iteration-000/knowledge_graph \
  --run-root /trusted/runs/run_example \
  --run-id run_example \
  --iteration 0 \
  --mode diagnosis \
  --graph-id graph_example
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

## semantic_analyzer 변경 영향 확인 (2026-10-07)

`semantic_analysis.json` 생산자인 `semantic_analyzer`의 1차 구현과 후속 변경을
현재 KG 입력 검증·적재·질의 동작에 대조했다. 확인 범위는 다음 커밋이다.

| 커밋 | 변경 | KG 영향 |
| --- | --- | --- |
| `66557db` | semantic_analyzer 1차 구현 | 실제 생산자 산출물로 KG 호환성을 검증할 수 있게 됨 |
| `953e6c2` | collector Page 입력에 `account_id`를 필수로 추가 | 현재 semantic 출력의 Page 속성에는 전달하지 않아 KG 직접 입력은 바뀌지 않음 |
| `721edf9` | Schema 오류 메시지에서 입력값 노출 차단 | 오류 메시지 처리만 바뀌며 KG 데이터 계약 영향 없음 |
| `d145195` | 기존 Role→Endpoint에 User→Endpoint `ACCESS` 추가 | `role_resource_access`가 사용하는 계정 접근 엣지가 실제 출력에 생김 |

최신 semantic fixture로 `analyze`를 실행하면 normalized request 6건, 노드 26건,
관계 32건, workflow 3건이 생성된다. 이 산출물은 KG의 JSON Schema 검증은
통과하지만 2단계 수정 전 KG 구현과 다음 차이가 있었다.

- 명세에서 `NormalizedRequest.account_id`와 `role_id`는 원본 ID다. 실제 노드
  ID는 각각 `user:<account_id>`, `role:<role_id>` 형식인데, KG가 원본 ID를
  곧바로 노드 ID로 간주해 6개 요청을 모두 교차 참조 오류로 거절한다.
- workflow의 `role_ids`도 원본 역할 ID 3건을 담지만, KG가 Role 노드 ID와
  직접 비교해 거절한다.
- 최신 출력의 `ACCESS` 관계 11건은 `properties={}`이고 Endpoint에도 action
  속성이 없다. 현재 `role_resource_access`는 관계 또는 Endpoint 속성에서
  action을 찾으므로, 입력 참조 검증을 통과시킨 뒤에도 해당 질의가 실패한다.
- 현재 질의는 `AccessRow.account_id`와 `role_id`에 User·Role의 노드 ID를
  반환한다. 명세가 요구하는 원본 실행 계정·역할 ID를 반환하려면 노드
  `properties.account_id`와 `properties.role_id`를 사용해야 한다.
- `Page.account_id`는 semantic 입력 검증에는 추가됐지만 Page 노드 속성에는
  전달되지 않는다. 현재 KG 질의에는 필요하지 않으므로 이번 호환 작업 범위에는
  넣지 않고, Page별 계정 연결이 필요해질 때 생산자와 별도로 합의한다.
- 생산자 출력 Schema와 KG 입력 Schema는 모두 `0.1.0`이고 필드 구조는 같지만,
  KG 사본이 빈 문자열·SHA-256 형식·상태별 data/errors 조건 등을 더 엄격하게
  검사한다. 이번 실제 completed 산출물은 통과했지만 계약 사본의 장기 드리프트
  가능성은 남아 있다.

수정 전 기본 KG fixture에는 User→Endpoint `ACCESS`가 없어 접근 질의가 빈 결과로
끝난다. query 단위 테스트와 실제 Neo4j query 테스트도 action을 넣은 record나
관계를 실행 직전에 수동 추가하므로 이 차이를 잡지 못했다. 따라서 현재 상태는
1단계 조사 당시 **Schema 형식 호환, 실제 ingest·접근 질의는 비호환**으로
판단했다. 아래 진행 이력의 2단계에서 ingest 호환은 해결했고 접근 질의 호환은
3·4단계에 남아 있다.

### 변경 계획

계약 필드나 `schema_version`은 바꾸지 않고 KG 내부만 수정하는 것을 기본으로 한다.

1. **실제 생산자 fixture 고정**
   - 최신 semantic_analyzer 실제 출력의 핵심 구조를 축약한 completed fixture를
     KG 테스트에 추가한다.
   - KG 테스트는 semantic_analyzer 코드를 import하지 않고 고정 산출물만 소비해
     모듈 독립성을 유지한다.
   - producer Schema 통과, KG Schema 통과, KG 의미 검증 결과를 각각 검사해
     계약 형식 문제와 소비자 해석 문제를 분리한다.
2. **원본 ID와 그래프 노드 ID 검증 분리**
   - Endpoint·Resource 참조는 지금처럼 `node_id`로 검증한다.
   - request의 원본 `account_id`·`role_id`는 User·Role 노드의
     `properties.account_id`·`properties.role_id` 인덱스로 검증한다.
   - workflow의 `role_ids`도 같은 Role 원본 ID 인덱스로 검증한다.
   - 원본 ID 중복, 누락, User↔Role의 `HAS_ROLE` 연결 불일치는 명시적으로
     거절한다.
3. **normalized request를 KG 내부 관찰 레코드로 보존**
   - 현재 적재에서 버리는 `normalized_requests`의 request ID, 원본 계정·역할 ID,
     Endpoint ID, `action_meaning`, Resource ID를 내부 저장 모델에 추가한다.
   - 이 레코드는 KG의 공개 노드·관계가 아니므로 `structure_snapshot`에는 넣지
     않고, run_id·graph_id 범위와 적재 트랜잭션·멱등성 검증에는 포함한다.
   - action을 `ACCESS.properties`에 새로 요구하지 않아 semantic_analyzer와 다른
     소비자의 계약 변경을 피한다.
4. **`role_resource_access` 질의 수정**
   - 관찰 레코드와 User→Role·User→Endpoint 관계를 함께 확인해 접근 row를 만든다.
   - `action`은 `normalized_requests.action_meaning`, 계정·역할은 원본 ID를
     반환한다.
   - Role→Endpoint `ACCESS`는 역할 단위 집계 근거로 보존하되 계정별 row를
     중복 생성하는 기준으로 사용하지 않는다.
   - Resource가 여러 개이거나 없는 요청, 같은 계정·Endpoint의 반복 요청,
     `role_ids` 필터를 각각 테스트한다.
5. **회귀·통합 확인**
   - 실제 semantic 산출물의 ingest가 completed이고 snapshot의 노드·관계·workflow
     건수가 원본과 일치하는지 확인한다.
   - `role_resource_access`가 failed나 빈 결과가 아니라 원본 계정·역할·action을
     가진 row를 반환하는지 실제 Neo4j 통합 테스트로 확인한다.
   - KG 전체 테스트와 전체 레포 테스트를 실행하고, 실제 Neo4j 테스트는
     `KG_RUN_NEO4J_INTEGRATION=1`로 별도 확인한다.

위 계획은 공개 JSON 계약 변경을 포함하지 않는다. 진행 중 semantic 출력 필드나
의미 변경이 필요해지면 생산자 담당자와 직접 소비자인 reporter 담당자까지 먼저
합의하고, 별도의 스키마 변경 PR로 분리한다.

### 진행 이력

#### 2026-10-07 — 1단계: 호환 fixture와 실패 기준선

- `tests/fixtures/semantic_analyzer_current/semantic_analysis.json`에 원본
  account·role ID, prefixed User·Role 노드 ID, Role/User 양쪽 `ACCESS`, 빈
  ACCESS properties와 `normalized_requests.action_meaning`을 함께 고정했다.
- KG 입력 JSON Schema 통과와 최신 ID·ACCESS 형태를 확인하는 테스트 2건을
  추가했다.
- fixture 작성 시 semantic_analyzer 출력 검증기로도 Schema 오류 0건을 확인했다.
  KG 테스트 실행 경로에서는 다른 모듈을 import하지 않는다.
- 실제 ingest 수용 테스트는 2단계 전까지 `ContractValidationError`가 발생해야
  하는 strict xfail로 두었다. 다른 예외나 조기 성공은 테스트 실패로 처리된다.
- 운영 코드와 공개 출력 계약은 변경하지 않았다.
- KG 전체 결과: `105 passed, 4 skipped, 1 xfailed`. skip 4건은 실제 Neo4j
  활성화가 필요한 기존 통합 테스트다.

#### 2026-10-07 — 2단계: 원본 ID 의미 검증

- User의 `properties.account_id`와 Role의 `properties.role_id`로 원본 ID
  인덱스를 만들고, 누락되거나 중복된 원본 ID를 ingest 전에 거절한다.
- normalized request의 account·role은 원본 ID로, Endpoint·Resource는 그래프
  `node_id`로 나누어 검증한다. 요청의 계정과 역할이 User 속성에서 선언한
  연결과 다른 경우도 거절한다.
- workflow의 `role_ids`를 Role 노드 ID가 아닌 원본 역할 ID로 해석하도록
  수정했다.
- User의 원본 `role_id`, Role 노드, `HAS_ROLE` 관계가 서로 일치하는지 확인하고
  누락·잘못된 방향·중복 연결을 거절한다.
- 기존 KG demo fixture에도 원본 ID 속성과 `HAS_ROLE`를 반영해 이전 fixture만
  통과하는 별도 해석 경로를 두지 않았다.
- 1단계의 strict xfail을 정상 ingest 테스트로 전환하고 원본 ID 누락·중복,
  없는 요청 계정, 계정-역할 불일치, `HAS_ROLE` 누락, 없는 workflow 역할에 대한
  거절 테스트 10건을 추가했다.
- 공개 JSON Schema·출력·Neo4j 저장 구조는 바꾸지 않았다. normalized request
  저장과 접근 질의 수정은 각각 3·4단계에 남아 있다.
- KG 전체 결과: `116 passed, 4 skipped`. skip 4건은 실제 Neo4j 활성화가 필요한
  기존 통합 테스트다.

#### 2026-10-07 — 3단계: normalized request 내부 저장

- `normalized_requests`를 공개 GraphNode가 아닌 내부 `RequestObservation` 모델로
  변환한다. request ID, 원본 account·role ID, Endpoint, action, Resource 목록,
  basis와 EvidenceRef를 보존한다.
- 2단계에서 검증한 원본 ID 인덱스를 사용해 User·Role node ID도 함께 보존한다.
  따라서 다음 접근 질의는 `user:`·`role:` 같은 문자열 규칙에 의존하지 않는다.
- Neo4j에는 `ABC2RequestObservation` 전용 label과
  `(run_id, graph_id, request_id)` 유일성 제약으로 저장한다. 기존
  노드·관계·workflow와 같은 ingest 트랜잭션과 graph·run 범위에 묶는다.
- 내부 관찰 레코드 건수를 적재 검증과 동일 semantic artifact 멱등 재호출 검증에
  포함했다. 저장·복원 시 Resource 배열과 EvidenceRef도 손실 없이 유지한다.
- `structure_snapshot`은 계속 공개 nodes·relationships·workflows만 반환하며 내부
  관찰 레코드를 노출하지 않는다. 공개 JSON Schema와 KG 출력 계약은 바뀌지 않았다.
- 실제 접근 row 생성은 4단계에 남아 있다.
- KG 전체 결과: `117 passed, 4 skipped`. skip 4건은 실제 Neo4j 활성화가 필요한
  기존 통합 테스트다.

#### 2026-10-07 — 4단계: `role_resource_access` 호환 수정

- 접근 row의 기준을 Endpoint에 연결된 전체 Resource가 아니라 개별
  `ABC2RequestObservation`으로 변경했다. 이로써 요청별 action·Resource 연결을
  유지하고 같은 Endpoint의 다른 요청 의미가 섞이지 않는다.
- 내부에 저장한 resolved User·Role node ID로 공개 Entity를 찾고, 실제
  User→Role `HAS_ROLE`과 User→Endpoint `ACCESS|VERIFIED_ACCESS`가 모두 존재할
  때만 row를 만든다.
- Role→Endpoint `ACCESS`는 semantic 그래프에 그대로 보존하지만 계정별 접근
  row를 만들거나 중복시키는 기준으로 사용하지 않는다.
- `account_id`와 `role_id`는 prefixed node ID가 아닌 normalized request의 원본
  ID를 반환하며 `role_ids` 필터도 원본 역할 ID에 적용한다.
- `resource_ownership`도 User node ID 대신 원본 account ID로 필터링하고
  `owner_account_id`를 반환하도록 함께 맞췄다.
- action은 빈 `ACCESS.properties`나 Endpoint 속성이 아니라
  `normalized_requests.action_meaning`에서 가져온 내부 관찰값을 사용한다.
- Resource가 여러 개면 Resource별 row를 만들고 없으면 `resource_id=null` 한
  건을 반환한다. 같은 account·role·Endpoint·Resource·action 반복 관찰은 한
  row로 합치고 서로 다른 EvidenceRef를 누적한다. action이 다르면 별도 row다.
- 잘못 저장된 Resource 배열은 정상 빈 결과로 숨기지 않고 질의 실패로 처리한다.
- 공개 JSON Schema와 AccessRow 필드·의미는 변경하지 않았다.
- KG 전체 결과: `120 passed, 4 skipped`. skip 4건은 실제 Neo4j 활성화가 필요한
  기존 통합 테스트다.

#### 2026-10-07 — 5단계: 통합 회귀와 실제 Neo4j 검증

- 실제 Neo4j 공개 질의 통합 테스트가 테스트용 관계를 실행 직전에 덧붙이지 않고
  `semantic_analyzer_current/semantic_analysis.json` fixture를 그대로 적재하도록
  변경했다. envelope의 실행 식별자만 통합 테스트 run에 맞춘다.
- 실제 DB ingest 후 snapshot이 원본의 노드 5건·관계 6건·workflow 1건과
  일치하고, 내부 RequestObservation도 손실 없이 왕복되는지 확인했다.
- `resource_ownership`은 원본 `acc_alice`와 `resource:order`를,
  `role_resource_access`는 원본 `acc_alice`·`role_user`·`read_order`와
  `resource:order`를 반환해야 통과하도록 고정했다. 빈 결과나 prefixed node ID
  반환은 테스트 실패가 된다.
- 실제 Neo4j 통합 테스트는 ingest round-trip, 공개 ingest 멱등성, 최신 semantic
  fixture의 공개 query, verification revision 반영 4건이 모두 통과했다.
- 실제 Neo4j를 활성화한 KG 전체 결과는 `124 passed`로 skip 없이 통과했다.
  전체 레포 기본 실행도 `980 passed, 4 skipped`였으며, 이때 skip된 4건은 이후
  실제 DB에서 별도로 모두 통과했다.
- 전체 레포 회귀는 Playwright와 로컬 테스트 서버 실행을 위해 샌드박스 밖에서
  재확인했다. 실제 Neo4j 테스트는 다음 환경 변수와 실행 플래그를 사용한다.

```bash
KG_RUN_NEO4J_INTEGRATION=1 \
NEO4J_URI=bolt://localhost:7687 \
NEO4J_USERNAME=neo4j \
NEO4J_PASSWORD='<configured-password>' \
.venv/bin/python -m pytest modules/knowledge_graph/tests/test_neo4j_integration.py
```

공개 JSON 계약 변경과 새 의존성은 없다. 코드·고정 fixture·전체 회귀·실제
Neo4j 통합 검증까지 완료했다.
