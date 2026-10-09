# knowledge_graph

`semantic_analysis.json`을 Neo4j 기반 Knowledge Graph로 저장하고, 허용된 정형 질의를 실행하며, 검증 결과를 그래프에 반영하는 모듈이다.

## 역할

knowledge_graph는 다음 세 가지 공개 operation을 제공한다.

| operation | 역할 |
|---|---|
| `ingest` | semantic analyzer 결과를 검증하고 Neo4j에 적재 |
| `query` | `graph_query.json`의 허용된 질의를 실행하고 `graph_query_result.json` 생성 |
| `apply_verification` | verifier가 확인한 graph update를 검증 후 반영 |

Neo4j 직접 접근과 Cypher 실행은 knowledge_graph 내부에서만 수행한다. 외부 모듈은 정해진 JSON 계약과 `query_key`만 사용한다.

---

## 검증 관계 계정 source 전환 상태

`verification_results.data.graph_updates.relationships`의 source를 KG node_id가
아닌 원본 `source_account_id`로 받는 소비자 측 변경을 완료했다.
KG 입력·User 조회·트랜잭션 반영, Reporter 입력 사본, 실제 Neo4j 검증까지 완료했다.

확인 기준 커밋 `b2aec96`에서 verifier 출력 Schema는 아직 `source_id`를
요구한다. 생산자 전환과 동일 run의 실제 산출물 연결 검증은 후속 작업이며,
소비자 구현 완료를 전체 pipeline 연결 완료로 해석하지 않는다.
버전은 `0.2.0`을 유지하므로 생산자와 모든 직접 소비자의 필드 전환을 함께 적용해야 한다.

최종 계약·단계별 커밋·담당자 전달 체크리스트는 아래 계정 source 전환 5단계에
정리했다. 이전 단계의 테스트 건수와 다음 작업은 각 기록 당시 기준이다.

---

## 주요 구현

- JSON Schema 및 교차 ID 검증
- `run_id`, `graph_id` 단위 그래프 격리
- semantic artifact ID + SHA-256 기반 멱등 적재
- Neo4j 트랜잭션 기반 노드·관계·workflow 저장
- normalized request를 내부 `RequestObservation`으로 보존
- 허용된 `query_key` 기반 읽기 전용 질의
- graph revision 검증
- 실제 저장 상태 기반 `structure_snapshot`
- verification 결과 기반 노드·관계 갱신
- 검증 결과 반영 시 graph revision 증가
- 원자적 JSON 산출물 저장 및 기존 완료 파일 덮어쓰기 방지

`RequestObservation`은 KG 내부 조회를 위한 저장 구조이며 공개 `GraphNode`가 아니다. 따라서 `structure_snapshot`에는 포함하지 않는다.

---

## 입력·출력

### ingest

입력:

```text
semantic_analyzer/semantic_analysis.json
```

주요 저장 대상:

```text
normalized_requests
        ↓
RequestObservation

nodes
relationships
workflows
        ↓
Neo4j
```

동일한 `run_id`, artifact ID, SHA-256으로 다시 호출하면 기존 graph 상태를 반환한다.

동일 artifact ID를 다른 SHA-256으로 다시 사용하면 충돌로 처리한다.

### query

입력:

```text
access_analyzer/graph_query.json
```

입력 `graph_query.json`은 `schema_version=0.2.0`만 지원한다. `0.1.0`과
미지원 버전은 거절하며 입력을 묵시적으로 변환하지 않는다.

출력:

```text
knowledge_graph/graph_query_result.json
```

지원하는 `query_key`는 다음 네 개다.

| query_key | 역할 |
|---|---|
| `resource_ownership` | 계정과 Resource 소유 관계 조회 |
| `role_resource_access` | 계정·역할·Endpoint·Resource 접근 관찰 조회 |
| `workflow_dependencies` | workflow 단계 의존 관계 조회 |
| `structure_snapshot` | 실제 저장된 노드·관계·workflow 조회 |

임의 Cypher는 외부에서 전달받지 않는다.

### apply_verification

입력:

```text
verifier/verification_results.json
```

검증된 graph update만 기존 그래프에 반영한다.

검증 관계 입력의 source는 원본 계정 ID인 `source_account_id`다.
`target_id`는 기존 Resource instance의 KG node_id를 사용한다.
`schema_version=0.2.0`을 유지하며 기존 검증 관계의 `source_id`는 거절한다.
일반 그래프 관계·snapshot의 `source_id` 계약은 변경하지 않는다.

계정 source 전환의 User 조회·관계 저장과 실제 Neo4j 통합 검증을 완료했다.
같은 `run_id`·`graph_id`의 기존 User에서 `properties.account_id`를 정확히
대응시켜 저장용 `source_id`로 변환한다. revision 확인·계정 조회·참조 검증·
관계 저장은 같은 쓰기 트랜잭션에서 처리하며, account_id를 node_id로
간주하거나 접두사로 추측하지 않는다. 빈 graph update의 no-op은 유지한다.
저장소 대역과 실제 Neo4j에서 계정 변환·범위 격리·revision·snapshot·rollback을
검증했다. 상세 결과는 아래 계정 source 전환 4단계 이력에 기록한다.

주요 조건:

- source verification이 실행 가능한 결정이어야 함
- verification 실행이 완료되어야 함
- update의 `basis`는 `verified`
- 실제 실행 EvidenceRef와 연결되어야 함
- `source_graph_revision`이 현재 revision과 일치해야 함
- 관계 source는 기존 User, target은 기존 Resource instance여야 함
- 기존 User의 account_id·node_id 대응은 유효하고 중복이 없어야 함
- verifier의 User 갱신은 account_id를 바꾸거나 중복 계정을 만들 수 없음
- verifier가 신규 Resource 노드를 생성하지 않아야 함

새로운 verification을 반영하면 graph revision이 증가한다.

---

## `role_resource_access`

접근 row는 개별 `RequestObservation`을 기준으로 생성한다.

다음 조건을 모두 만족해야 한다.

```text
RequestObservation
      +
User ──HAS_ROLE──> Role
      +
User ──ACCESS──> Endpoint
```

반환되는 주요 값은 다음과 같다.

```json
{
  "account_id": "acc_alice",
  "role_id": "role_user",
  "endpoint_id": "endpoint:GET:/orders/{id}",
  "resource_id": "resource:order",
  "resource_key": "order",
  "resource_scope": "instance",
  "match_key": {
    "resource_key": "order",
    "identifiers": [
      {"key": "order_id", "value": "example-order"}
    ]
  },
  "action": "read_order",
  "request_ids": [
    "request_order_alice"
  ],
  "access_observed": true,
  "evidence_refs": []
}
```

`account_id`, `role_id`는 prefixed Graph Node ID가 아니라 semantic analyzer에서 전달된 **원본 ID**를 사용한다.

`action`은 `normalized_requests.action_meaning`을 사용한다.

Resource가 여러 개이면 Resource별 AccessRow를 생성한다. Resource가 없으면 `resource_id`, `resource_key`, `resource_scope`, `match_key`가 모두 `null`인 row를 생성한다.

동일한

```text
account
role
endpoint
resource
action
```

관찰이 반복되면 하나의 AccessRow로 병합한다.

이때 다음 provenance 정보는 중복 없이 누적한다.

```text
request_ids
evidence_refs
```

따라서 access_analyzer는 `request_ids`를 이용해 취약점 후보의 `source_request_ids`를 구성할 수 있다.

---

## 원본 ID 처리

semantic analyzer의 다음 값은 Graph Node ID가 아니라 원본 ID다.

```text
NormalizedRequest.account_id
NormalizedRequest.role_id
Workflow.role_ids
```

KG는 User와 Role 노드의 속성을 이용해 원본 ID와 Graph Node ID를 연결한다.

예:

```text
account_id = acc_alice
        ↓
User node
user:acc_alice
```

```text
role_id = role_user
        ↓
Role node
role:role_user
```

Endpoint와 Resource는 기존 Graph Node ID를 사용한다.

---

## graph revision

모든 query는 graph revision을 기준으로 수행한다.

`expected_graph_revision`이 현재 graph revision과 다르면 질의를 거절한다.

질의 실행 중 revision이 변경된 경우에도 결과를 정상 완료로 처리하지 않는다.

일부 query만 실패하면 `partial`, 전체가 실패하면 `failed` 상태를 반환한다.

---

## Neo4j 설정

다음 환경변수를 사용한다.

```text
NEO4J_URI
NEO4J_USERNAME
NEO4J_PASSWORD
NEO4J_DATABASE
```

실제 인증 정보는 `.env`에만 저장하고 Git에 커밋하지 않는다.

---

## 테스트

저장소 루트에서 실행한다.

```bash
.venv/bin/python -m pytest modules/knowledge_graph/ -q
```

현재 기본 테스트 결과:

```text
243 passed, 12 skipped
```

skip 12건은 실제 Neo4j가 필요한 opt-in 통합 테스트다.

실제 Neo4j 통합 테스트는 Neo4j 실행 후 명시적으로 활성화한다.

```bash
KG_RUN_NEO4J_INTEGRATION=1 \
NEO4J_URI=bolt://localhost:7687 \
NEO4J_USERNAME=neo4j \
NEO4J_PASSWORD='<configured-password>' \
NEO4J_DATABASE=neo4j \
.venv/bin/python -m pytest \
modules/knowledge_graph/tests/test_neo4j_integration.py -q
```

---

# 변경 이력

## 2026-10-07 — semantic_analyzer 호환성 확인

semantic analyzer 실제 출력과 KG 입력·질의 동작을 비교했다.

| 커밋 | 변경 | KG 영향 |
|---|---|---|
| `66557db` | semantic_analyzer 1차 구현 | 실제 생산자 산출물 기반 호환 테스트 가능 |
| `953e6c2` | collector Page 입력에 `account_id` 추가 | 현재 KG 직접 영향 없음 |
| `721edf9` | Schema 오류 메시지 입력값 노출 차단 | 데이터 계약 영향 없음 |
| `d145195` | User→Endpoint `ACCESS` 추가 | 계정별 접근 질의 가능 |

초기 확인 당시 다음 차이가 발견됐다.

- normalized request의 account·role 원본 ID와 Graph Node ID 해석 불일치
- workflow `role_ids`의 원본 ID 해석 불일치
- `ACCESS.properties`에 action이 없어 기존 접근 질의 사용 불가
- AccessRow가 원본 account·role ID가 아닌 Graph Node ID를 반환
- normalized request의 request provenance가 접근 결과까지 전달되지 않음

이를 아래 단계에서 순차적으로 해결했다.

---

## 1단계 — 실제 semantic fixture 고정

- 실제 semantic analyzer 출력 형태를 축약한 fixture 추가
- producer/KG Schema 호환성 검증
- 기존 KG가 실제 입력을 거절하는 상태를 테스트로 고정

당시 결과:

```text
105 passed, 4 skipped, 1 xfailed
```

---

## 2단계 — 원본 ID 검증 분리

- User `properties.account_id` 기반 account 원본 ID 인덱스 구성
- Role `properties.role_id` 기반 role 원본 ID 인덱스 구성
- request의 account·role과 Endpoint·Resource 검증 방식 분리
- workflow `role_ids`를 원본 Role ID로 해석
- User↔Role `HAS_ROLE` 일관성 검증 추가

당시 결과:

```text
116 passed, 4 skipped
```

---

## 3단계 — RequestObservation 저장

`normalized_requests`를 KG 내부 `RequestObservation`으로 보존하도록 변경했다.

저장 정보:

```text
request_id
account_id
role_id
user_node_id
role_node_id
endpoint_id
action
resource_ids
basis
evidence_refs
```

Neo4j에는 `ABC2RequestObservation`으로 저장한다.

유일성 기준:

```text
(run_id, graph_id, request_id)
```

`structure_snapshot`에는 노출하지 않는다.

당시 결과:

```text
117 passed, 4 skipped
```

---

## 4단계 — `role_resource_access` 수정

접근 질의 기준을 Graph Entity 조합이 아닌 개별 `RequestObservation`으로 변경했다.

주요 변경:

- 원본 `account_id`, `role_id` 반환
- `action_meaning` 기반 action 반환
- 요청별 Resource 연결 유지
- User→Role `HAS_ROLE` 검증
- User→Endpoint `ACCESS` 검증
- Resource가 여러 개인 요청 지원
- Resource가 없는 요청 지원
- 동일 접근 관찰 병합
- evidence 중복 제거
- `resource_ownership.owner_account_id`도 원본 account ID로 통일

당시 결과:

```text
120 passed, 4 skipped
```

---

## 5단계 — 실제 Neo4j 통합 검증

실제 semantic fixture를 Neo4j에 적재하고 다음 경로를 검증했다.

```text
semantic_analysis
      ↓
ingest
      ↓
Neo4j
      ↓
query
      ↓
graph_query_result
```

확인 항목:

- RequestObservation 저장·복원
- snapshot 노드·관계·workflow 일치
- `resource_ownership` 원본 account ID 반환
- `role_resource_access` 원본 account·role·action·Resource 반환
- verification 반영 및 revision 증가

당시 실제 Neo4j 테스트 결과:

```text
124 passed
```

전체 저장소 기본 회귀 결과:

```text
980 passed, 4 skipped
```

---

## 6단계 — A3: AccessRow request provenance 보존

access_analyzer가 취약점 후보의 `source_request_ids`를 구성할 수 있도록 `role_resource_access` 결과에 원본 request ID를 추가했다.

변경 전:

```text
RequestObservation.request_id
        ↓
AccessRow 생성 과정에서 소실
```

변경 후:

```text
RequestObservation.request_id
        ↓
AccessRow.request_ids[]
        ↓
취약점 후보 원본 요청 추적 가능
```

동일한 접근 의미가 여러 요청에서 관찰되면:

```text
request_001
request_002
      ↓
하나의 AccessRow
      ↓
request_ids = [
  "request_001",
  "request_002"
]
```

형태로 provenance를 보존한다.

KG 출력 `graph_query_result`의 `AccessRow.request_ids` 계약은 다음 조건을 사용한다.

```text
type: array
minItems: 1
uniqueItems: true
item: non-empty string
```

검증 결과:

```text
test_query_repository.py
11 passed

test_query_service.py
9 passed

knowledge_graph 전체
121 passed, 4 skipped
```

이후 실제 Neo4j에서 통합 테스트 4건을 다시 실행해 `request_ids`를 포함한 조회 결과를 검증했다.

---

## 현재 상태

knowledge_graph 기준으로 semantic analyzer 호환 문제와 A2/A3 관련 KG 책임은 해결된 상태다.

```text
원본 account/role ID 처리        완료
RequestObservation 내부 저장     완료
role_resource_access 호환        완료
AccessRow request_ids 제공        완료
KG 기본 회귀 테스트               완료
A3 실제 Neo4j 재검증              완료
```

외부 소비자 모듈의 입력 Schema 및 `Candidate.source_request_ids` 연결은 각 소비자 모듈의 계약 반영 범위에서 처리한다.

---

## Resource Type/Instance 계약 전환 이력

Resource 종류와 실제 자원 인스턴스를 구분하고, 검증 결과가 정확한 KG 노드를 참조할 수 있도록 Resource 계약을 `0.2.0`으로 전환했다.

### 모델과 입력

Resource 노드는 다음 필드를 필수로 가진다.

```text
resource_key
resource_scope: type | instance
match_key
```

`type`은 `match_key=null`, `instance`는 하나 이상의 문자열 식별값으로 구성된 `match_key`를 사용한다. 복합 식별값은 key 순서와 무관하게 같은 인스턴스로 판단하며, 동일 `match_key`가 다른 `node_id`로 중복되면 ingest를 거절한다. `node_id`는 생산자가 생성하고 knowledge_graph는 불투명 식별자로 검증·저장한다.

### Neo4j 저장과 조회

원본 Resource 속성은 `properties_json`에 보존한다. Cypher 조회용으로 다음 속성을 함께 저장한다.

```text
resource_key
resource_scope
resource_match_key_json
```

`resource_ownership`은 Resource 인스턴스만 반환한다. `role_resource_access`와 `resource_ownership`의 Resource 결과에는 기존 `resource_id`와 함께 `resource_key`, `resource_scope`, `match_key`를 제공한다. 여기서 `resource_id`는 실제 KG `node_id`다. 연결된 Resource가 없는 AccessRow는 네 필드를 모두 `null`로 반환한다.

`graph_query_result.json`은 `schema_version=0.2.0`을 사용한다.

### 검증 결과 반영

`verification_results.json` 입력은 `schema_version=0.2.0`을 사용한다. 검증 관계는 다음 구조만 허용한다.

```text
User ── VERIFIED_ACCESS | VERIFIED_DENIAL ──> Resource Instance
```

관계의 source와 target은 현재 `run_id`와 `graph_id`에 이미 존재해야 한다. target이 Endpoint, Resource Type 또는 존재하지 않는 노드이면 반영하지 않는다. verifier는 `graph_updates.nodes`에서 신규 Resource 노드를 생성할 수 없으며, 앞 단계에서 전달받은 Resource `node_id`를 `target_id`로 사용해야 한다.

기존 verification 중복 방지와 revision 규칙은 유지한다. 같은 `verification_id`와 원본 산출물을 다시 적용하면 revision을 올리지 않고, 새로운 유효 검증 관계를 반영한 경우에만 revision을 증가시킨다.

### 적용과 호환성

`0.1.0` Resource 관련 산출물을 묵시적으로 변환하지 않는다.

```text
semantic_analysis 0.2 재생성
        ↓
신규 KG ingest
        ↓
graph_query_result 0.2 소비
        ↓
verification_results 0.2 반영
```

외부 생산자와 소비자는 각 담당 모듈에서 `0.2.0` 계약과 Resource `node_id` 전달을 반영해야 한다. knowledge_graph는 다른 모듈 코드를 직접 수정하거나 import하지 않는다.

검증 결과:

```text
knowledge_graph 전체
160 passed
```

Neo4j `5.26.31-community` 일회성 컨테이너에서 실제 통합 테스트 4건과 knowledge_graph 전체 테스트를 실행했다. ingest·query·apply_verification·revision·snapshot 경로가 모두 통과했다.

---

## 2026-10-09 — graph_query 입력 계약 0.2.0 동기화

팀의 공통 산출물 `0.2.0` 전환 합의와 access_analyzer의 질의 출력 계약에 맞춰
KG의 `graph_query.json` 입력 Schema와 기본 질의 fixture를 `0.2.0`으로 변경했다.
버전 이외의 질의 필드·타입·허용값은 그대로 유지하며, 입력 Schema 정의는
생산자 출력 Schema와 일치한다(제목·설명 제외).

`0.1.0`과 미지원 버전은 입력 검증에서 거절한다. 기존 질의 파일을 묵시적으로
변환하거나 지원 버전을 넓혀 수용하지 않는다.

### 독립 fixture와 회귀 검증

- 기준 커밋 `82d3864`의 access_analyzer 공개 fixture를 바이트 그대로
  `tests/fixtures/access_analyzer_current/graph_query.json`에 고정했다.
- 원본 위치는
  `modules/access_analyzer/tests/fixtures/runs/run_demo_001/artifacts/iteration-000/access_analyzer/graph_query.json`이다.
- KG 테스트는 자기 fixture와 저장소 대역만 사용한다. 다른 모듈 코드나 Schema를
  import하거나 `$ref`하지 않는다.
- 최신 fixture 수신, `0.1.0`·미지원 버전 거절, 공개 `query` 실행의
  `query_id`·`query_key` 대응을 검증했다.
- 결과의 `input_refs`가 원본 artifact ID·경로·정확한 파일 SHA-256을 보존하고,
  완료 응답의 SHA-256이 실제 출력 파일과 일치함을 확인했다.

Resource 모델, Neo4j 저장·질의 템플릿, `graph_query_result` 출력 계약,
`ingest`·`apply_verification` 로직과 revision 규칙은 변경하지 않았다.

검증 명령과 결과:

```bash
.venv/bin/python -m pytest modules/knowledge_graph -q
```

```text
160 passed, 4 skipped
```

이번 단계에서 실제 Neo4j 통합 테스트는 활성화하지 않았다. skip 4건은 해당
통합 테스트이며, 위 Resource 전환 단계의 실제 Neo4j 검증 기록과 구분한다.

연관 모듈 회귀 검증:

```bash
.venv/bin/python -m pytest modules/knowledge_graph modules/access_analyzer modules/safety_policy modules/reporter -q
```

```text
518 passed, 4 skipped
```

---

## 2026-10-09 — 6단계: 담당 모듈 최종 회귀·계약 호환성 검증

기준 커밋 `1b123bd`에서 knowledge_graph·safety_policy·reporter의 1~5단계 수정 결과를 최종 검증했다. 이번 단계는 README 기록만 변경하며 구현 코드·Schema·fixture·의존성은 변경하지 않았다.

### KG 입력·출력 확인

| 경계 | 확인 결과 |
|---|---|
| semantic_analyzer → KG | 생산자 공개 CLI로 생성한 `semantic_analysis 0.2.0`을 KG 입력 Schema와 ingest adapter가 수용 |
| access_analyzer → KG | 공개 `graph_query 0.2.0` fixture 수신 검증 통과. 생산자 Schema와 입력 사본은 최상위 제목·설명·`$id`를 제외하면 일치 |
| verifier → KG | 공개 `verification_results 0.2.0` fixture의 입력 Schema 검증 통과 |
| KG → reporter·access_analyzer | 공개 `graph_query_result 0.2.0` fixture가 양쪽 입력 Schema를 통과 |

semantic CLI 검증은 생산자 소유 수집 fixture와 기본 `fake` LLM을 사용했다. 네트워크 호출 없이 생성한 요청 6개·노드 27개·관계 33개·Workflow 3개를 KG 내부 모델로 변환하고, 수집 fixture의 파일 해시가 변경되지 않았음을 확인했다. LLM 분석 품질이나 전체 파이프라인 실행을 검증한 것은 아니다.

semantic·verification 입력 Schema 및 access_analyzer의 KG 입력 Schema에는 정의 이름·`$ref` 구성 등 문서 구조 차이가 있다. 샘플 수신 통과와 Schema 파일 전체 일치는 구분하며, 모든 가능한 입력의 계약 동등성을 증명했다고 주장하지 않는다.

### 회귀 검증

```text
knowledge_graph 기본 테스트
160 passed, 4 skipped

knowledge_graph + safety_policy + reporter 기본 테스트
661 passed, 4 skipped

같은 담당 3개 모듈, 실제 Neo4j 통합 테스트 활성화
665 passed

modules/ 전체 테스트
1456 passed, 4 skipped
```

기본 테스트 명령은 `.venv/bin/python -m pytest modules/knowledge_graph modules/safety_policy modules/reporter -q -rs`, 전체 모듈 명령은 `.venv/bin/python -m pytest modules -q -rs --tb=short`다. 전체 모듈은 Chromium·로컬 테스트 서버의 실행 권한을 확보한 환경에서 재실행했다.

실제 DB 검증은 기존 `neo4j:5.26.31-community` 이미지의 일회성 컨테이너에서 수행했다. loopback 임시 포트와 테스트 전용 인증을 사용하고 기존 DB·볼륨은 연결하지 않았다. ingest 멱등성, 4종 typed query와 snapshot, verified 관계 반영·revision·중복 반영 방지의 기존 통합 테스트 4건이 통과했다. 종료 후 해당 컨테이너와 임시 볼륨을 정리했다.

AST로 Python 파일 38개를 검사해 다른 모듈 import 0건을 확인했고, Schema 4개의 외부 `$ref`도 0건이다. 실제 진단 `runs/`·`data/`는 읽거나 수정하지 않았다.

### 후속 연결 작업과 구분

이번 6단계로 담당 모듈 내부 수정 목록과 최종 회귀 검증을 마쳤다. scenario_generator의 0.1 출력, verifier의 Safety 입력 0.1 사본, Ground Truth 버전 명세 불일치는 각 담당자·관리자의 확인 항목으로 남긴다. 타 모듈을 대신 수정하거나 구버전 입력을 묵시적으로 변환하지 않는다. 실제 전체 파이프라인 연결은 이 항목의 정리와 동일 실행 산출물 검증 이후 확인한다.

---

## 2026-10-09 — 계정 source 전환 1단계: 입력 계약·모델·파싱

기준 커밋 `27b43ac`에서 팀이 제안한 검증 관계의 원본 계정 ID 전달 방식을
KG 입력에 반영했다. 사용자의 적용 결정에 따라 `verification_results` 버전은
`0.2.0`을 유지하고 필드를 동시 전환한다. 같은 버전이더라도 이전 검증 관계의
`source_id`와 새 `source_account_id`는 호환되지 않으며, 자동 변환하지 않는다.

### 변경 내용

- 변경 대상은 `data.graph_updates.relationships`뿐이다. `source_account_id`는
  실제 접근한 계정의 원본 ID, `target_id`는 대상 Resource instance node_id다.
- 입력 Schema의 검증 관계 정의를 `verificationRelationship`로 분리하고
  `source_account_id`를 필수로 지정했다. `source_id` 또는 두 필드의 동시 입력은
  미정의 키·필수 필드 검사에서 거절한다.
- `VerificationRelationship`·`VerificationInputUpdate`는 해석 전 계정 source를
  보존한다. 저장용 `VerificationUpdate`·일반 `GraphEdge`는 계속 node_id를 사용한다.
- 입력 준비 service와 repository Protocol을 새 입력 모델에 맞췄다. 계정 ID는
  대소문자·공백을 변경하지 않고 보존하며 node_id 접두사를 계산하지 않는다.
- account_id와 target node_id는 다른 식별 공간이므로 입력 단계에서 두 문자열을
  비교해 자기 관계라고 판정하지 않는다. 실제 User·Resource 참조는 해석 후 검사한다.
- 기존 검증 상태·allow·실행 근거·EvidenceRef 대응·해시 검증은 유지했다.
- 소유 verification fixture와 denial 통합 테스트 입력 생성기를 변경했다.
  저장 계층의 기존 회귀 테스트는 해석 완료 node_id 입력을 사용하며 실제 DB의
  계정 조회 구현을 대신했다고 주장하지 않는다.

### 중간 단계의 실행 경계

2단계 User 조회가 아직 없으므로 새 관계를 가진 `VerificationInputUpdate`는
Neo4j 제약 생성·트랜잭션 실행 전에 `GraphUpdateReferenceError`로 차단한다.
공개 entrypoint는 `failed`·`is_applied=false`와
`GRAPH_UPDATE_REFERENCE_INVALID`를 반환한다. 입력 수신 완료와 DB 반영 완료는
구분한다. 관계가 없는 입력은 저장 모델로 변환해 기존 no-op·노드 처리 경로를 유지한다.

### 검증과 다음 작업

```text
신규 계정 source 계약 테스트
24 passed

knowledge_graph 기본 회귀
189 passed, 4 skipped

knowledge_graph + safety_policy + reporter 기본 회귀
690 passed, 4 skipped
```

신규/기존/동시/누락 필드, 빈 값·잘못된 타입, 원본 계정 ID 보존,
VERIFIED_ACCESS·VERIFIED_DENIAL, verified 근거, 버전 유지, 일반 관계·snapshot
비영향을 확인했다. unresolved 관계의 DB 미접근과 공개 실패 제어 응답,
기존 저장 계층의 멱등성·충돌·Resource instance 제한도 회귀 검증했다.
실제 Neo4j 통합 4건은 기존 opt-in 조건으로 skip했으며, 이번 단계에서 DB 반영
성공을 검증하지 않았다.

다음 2단계는 같은 run_id·graph_id의 기존 User에서 `properties.account_id`로
정확히 하나의 node_id를 찾아 저장용 관계로 변환하는 작업이다. 없는 계정·중복
대응은 거절하고 기존 revision·충돌·중복 반영 검사를 유지한다.
Reporter 입력 사본은 3단계에서 변경한다. Verifier 출력의 필드 동시 전환은
Verifier 담당 작업으로 남긴다. 다른 모듈·공용 명세·의존성은 수정하지 않았다.
노션 원본은 현재 연결에서 404여서 저장소 명세와 사용자 전달 팀 합의로 작업했다.

---

## 2026-10-09 — 계정 source 전환 2단계: User 조회·검증 관계 저장

기준 커밋 `b5eb734`의 1단계 입력 계약을 실제 저장 경로에 연결했다.
`verification_results`의 버전은 `0.2.0`을 유지한다. 입력 검증 관계는
`source_account_id`, DB 저장 관계와 일반 snapshot은 기존 `source_id`를 사용한다.

### 변환과 트랜잭션 순서

```text
그래프 잠금·현재 revision 조회
    ↓
verification_id·원본 artifact/해시로 중복 반영 확인
    ↓
빈 갱신 no-op / 기준 revision 확인
    ↓
같은 run_id·graph_id의 기존 User 조회
    ↓
properties.account_id → User node_id 대응 검증
    ↓
VerificationInputUpdate → 저장용 VerificationUpdate/GraphEdge
    ↓
기존 User·Resource instance 참조 및 노드·관계 ID 충돌 검사
    ↓
노드·관계 저장 → verification 반영 기록 → revision 증가
```

위 과정은 하나의 쓰기 트랜잭션 안에서 수행한다. 같은 원본 artifact·해시의
verification_id 재요청은 기존처럼 no-op이며, 오래된 revision이나 일부 ID만
이미 반영된 요청은 계정 조회 전에 거절한다.

User 속성은 현재 저장 형식인 `properties_json`을 읽어 복원한다.
새 DB 속성·인덱스·제약조건은 추가하지 않는다. 모든 해당 그래프의 User가
유효한 account_id·node_id와 중복 없는 대응을 가져야 하며, 여러 관계는 한 번
조회한 인덱스를 공유한다. 계정의 대소문자·공백을 정규화하거나 `user:` 등의
접두사로 node_id를 계산하지 않는다. source는 실제 접근 계정이고 자원 소유자와
혼동하지 않는다.

### 실패와 식별값 보호

- source 계정 없음, 기존 User 대응 중복, 잘못된 User 속성/JSON/node_id는
  `GraphUpdateReferenceError`이며 공개 응답은 `GRAPH_UPDATE_REFERENCE_INVALID`다.
- verifier가 같은 입력에서 새 User를 제시해도 해당 노드를 관계 source로 쓰지
  않는다. source는 갱신 전에 해당 그래프에 존재한 User여야 한다.
- User 노드 갱신에 유효한 `account_id`가 없으면 거절한다. 기존 User의
  account_id 변경이나 새 User·한 입력의 User 사이 계정 중복은
  `GraphUpdateConflictError` / `GRAPH_UPDATE_CONFLICT`로 거절한다.
  식별값을 유지하는 User 속성 갱신과 새 고유 계정의 노드 단독 갱신은 허용한다.
- target은 기존 Resource instance node_id여야 하며, 신규 Resource 생성은
  계속 금지한다. 변환된 source_id로 기존 relationship_id의 source·target·종류를
  대조하므로 동일 ID의 연결 정보를 바꿀 수 없다.
- 계정 조회와 사전 검증 실패는 노드·관계 저장, verification 반영 기록,
  revision 증가 전에 발생한다. 이후 저장 단계 오류도 같은 트랜잭션의 실패로
  처리하도록 기존 구조를 유지한다.

1단계의 일괄 차단 guard와 테스트용 node_id 사전 변환은 제거했다.
공개 repository 입력은 `VerificationInputUpdate`로 통일하고,
`VerificationUpdate`는 트랜잭션 내부 저장 모델로만 사용한다.
입력 관계의 target·종류·속성·basis·EvidenceRef 및 artifact/해시를 보존한다.

### 검증 결과와 다음 작업

```text
verification repository + entrypoint 회귀
80 passed

knowledge_graph 기본 회귀
243 passed, 4 skipped

knowledge_graph + safety_policy + reporter 기본 회귀
744 passed, 4 skipped
```

1단계 대비 테스트 54건을 추가했다. account_id와 node_id가 다른 임의 식별값,
접두사·대소문자·공백 보존, account_id 문자열과 target node_id 문자열의 일치,
VERIFIED_ACCESS·VERIFIED_DENIAL, 다중 계정, run/graph 범위 격리,
계정 누락·중복·잘못된 저장 속성, User 갱신의 식별값 충돌,
기존 관계 충돌·revision·중복 반영을 검증했다.
공개 entrypoint에서 실제 repository 구현과 DB 대역을 연결해 성공 제어 응답과
누락/중복 계정의 실패 제어 응답도 확인했다.

실제 Neo4j 통합 4건은 이번 단계에서 활성화하지 않았고, 대역 검증과 실제
DB 검증을 구분한다. 실제 DB에서 이 변환·저장·rollback·snapshot 경로를
확인하는 작업은 4단계에 남긴다.

변경 파일은 KG의 `neo4j_repository.py`, `tests/test_verification_repository.py`,
`tests/test_verification_entrypoint.py`, `README.md`뿐이다.
Schema·출력·의존성·다른 모듈·공용 명세는 수정하지 않았다.
다음 3단계는 Reporter의 `verification_results` 입력 사본·fixture·해시·테스트를
같은 0.2.0의 `source_account_id` 계약에 맞추는 작업이다.

## 2026-10-09 — 계정 source 전환 4단계: 실제 Neo4j 통합 검증

기준 커밋 `3143752`에서 실제 DB 검증을 보강했다. 기존 통합 테스트 4건을
유지하고 실패·범위 격리·rollback 8건을 추가해 총 12건으로 구성했다.
이 단계는 `tests/test_neo4j_integration.py`와 이 README만 변경했다.
구현 코드·Schema·공개 출력·의존성·다른 모듈은 변경하지 않았다.

### 확인한 동작

- 임시 semantic 입력에서 원본 `account_id=account_user`와
  `node_id=opaque-user-B`를 분리하고, 자원 소유자는 별도 User A로 구성했다.
  `VERIFIED_ACCESS`·`VERIFIED_DENIAL`은 소유자가 아니라 실제 접근 계정 B의
  node_id에 연결된다. 원본 계정 ID는 보존하며, `verification_results.json`의
  적용 전후 파일 해시는 같다.
- DB 저장 관계와 공개 `structure_snapshot`은 기존 `source_id`를 사용하며,
  검증 입력 전용 필드인 `source_account_id`를 snapshot에 노출하지 않는다.
- 정상 반영 예시에서 노드는 5개 그대로, 관계는 3→5개, 반영 기록은 0→1개,
  revision은 1→2로 바뀐다. 같은 입력의 재반영은 관계·기록·revision을
  추가하지 않는다.
- 없는 계정·같은 범위의 중복 account_id는
  `GRAPH_UPDATE_REFERENCE_INVALID`로 거절한다. 다른 run 또는 다른 graph의
  동일 계정은 조회 대상에 포함하지 않고, 그 범위에만 있는 계정도 사용할 수 없다.
  외부 범위의 그래프 내용·건수·revision·반영 기록은 그대로 유지된다.
- 오래된 revision, 기존 관계 ID의 source 변경, 같은 verification_id의 다른
  해시 재사용은 각각 `GRAPH_REVISION_MISMATCH`, `GRAPH_UPDATE_CONFLICT`,
  `VERIFICATION_CONFLICT`로 거절한다.
- 실패 전후 실제 저장 노드·관계·workflow·관찰 레코드, 건수, revision,
  반영 기록 전체와 공개 snapshot을 비교해 변경이 없음을 확인했다.
- rollback 테스트에서는 실제 쓰기 트랜잭션 안에서 신규 User·검증 관계·반영
  기록 생성과 revision 증가를 확인한 뒤 commit 직전에 오류를 주입했다.
  기존 User 속성 갱신까지 모두 취소되며, 같은 입력의 재시도는 정상 반영되고
  이후 반복 호출은 no-op이다. 오류 주입은 테스트에만 있고 구현 코드는 바꾸지 않았다.

질의 테스트 helper는 iteration과 기대 revision을 별도로 지정할 수 있도록
보강했다. 실패 후 새 iteration으로 같은 revision의 snapshot을 조회하므로
이미 공개된 결과 파일을 덮어쓰지 않는다. 입력 변형은 pytest 임시 디렉터리의
소유 fixture 복사본에서만 수행하며, 저장소 fixture와 실제 `runs/`는 변경하지 않는다.

### 검증 결과

```text
실제 Neo4j 통합 테스트
12 passed

knowledge_graph 기본 회귀
243 passed, 12 skipped

knowledge_graph 전체, 실제 Neo4j 통합 활성화
255 passed

knowledge_graph + safety_policy + reporter 기본 회귀
778 passed, 12 skipped

같은 담당 3개 모듈, 실제 Neo4j 통합 활성화
790 passed
```

`neo4j:5.26.31-community` 일회성 컨테이너의 loopback 임시 포트와 테스트 전용
인증을 사용했다. 기존 DB·볼륨이나 host 디렉터리는 연결하지 않았다.
테스트 종료 후 DB의 잔여 노드가 0개임을 확인하고, 해당 컨테이너와 연결된
임시 볼륨 2개를 정리했다. 컨테이너·임시 볼륨의 제거도 확인했다.
이번 결과는 KG의 소유 fixture로 수행한 실제 DB 통합 검증이며,
verifier의 실제 산출물 수신이나 전체 pipeline 연결 완료를 뜻하지 않는다.

다음 5단계는 KG·Reporter의 최종 변경 이력을 정리하고 verifier 담당자에게
0.2.0 동시 필드 전환, source의 실제 접근 계정 의미, 기존 Resource instance
target, 기존 필드 거절 및 실제 산출물 수신 검증 항목을 전달하는 작업이다.

## 2026-10-09 — 계정 source 전환 5단계: 최종 정리·생산자 전달

기준 커밋 `b2aec96`의 구현·검증 결과를 정리했다. 이번 단계는
`modules/knowledge_graph/README.md`와 `modules/reporter/README.md`만 변경한다.
기존 이력은 보존하며 코드·Schema·fixture·테스트·의존성·공용 명세는 변경하지 않는다.
아래 전달 사항은 문서로 준비한 내용이며, 팀원에게 외부 메시지를 발송한 기록은 아니다.

### 완료한 소비자 작업

| 단계 | 커밋 | 완료 내용 |
|---|---|---|
| 1 | `b5eb734` | KG 검증 관계 입력 Schema·해석 전 모델·파싱·fixture·테스트 전환 |
| 2 | `5fe4057` | 같은 run/graph의 account_id→기존 User node_id 조회와 트랜잭션 반영 |
| 3 | `3143752` | Reporter 검증 입력 사본·fixture·참조 해시·독립 테스트 동기화 |
| 4 | `b2aec96` | 실제 Neo4j 계정 변환·범위 격리·revision·snapshot·rollback 검증 |

### 최종 계약과 책임 경계

| 위치 | source 필드 | 값의 의미 |
|---|---|---|
| `verification_results.data.graph_updates.relationships` | `source_account_id` | 실제 접근한 계정의 원본 account_id |
| `semantic_analysis.data.relationships` | `source_id` | 시작 노드의 KG node_id |
| KG·snapshot의 일반 관계 | `source_id` | 시작 노드의 KG node_id |
| KG·snapshot의 `VERIFIED_ACCESS`·`VERIFIED_DENIAL` | `source_id` | KG가 계정을 해석한 뒤 연결한 User node_id |

검증 관계의 `target_id`는 같은 run/graph에 이미 존재하는 Resource instance
node_id다. Resource 종류 이름·실제 상품 번호·URL을 대신 넣지 않는다.
관계는 `VERIFIED_ACCESS` 또는 `VERIFIED_DENIAL`, `basis=verified`와 실제 실행
EvidenceRef를 사용하며 나머지 필드와 기존 근거·revision 검증은 유지한다.

- verifier: 기존 pipeline에서 실제 접근 계정과 대상 Resource instance node_id,
  검증 결과·근거를 전달한다. User node_id를 계산하거나 Neo4j에 직접 접근하지 않는다.
- KG: `run_id`·`graph_id` 안의 기존 User `properties.account_id`를 정확히
  대응시킨다. 계정 누락·중복·참조 충돌은 거절하고 전체 반영을 트랜잭션으로 처리한다.
- Reporter: 자기 입력 계약·해시·ID·근거·상태를 검증하고 기존 리포트·평가를
  생성한다. 검증 관계의 계정을 User node_id로 바꾸거나 DB에 반영하지 않는다.
  개발 평가의 semantic/snapshot 참조 검증은 별도이며 계속 유지한다.

버전 `0.2.0` 안에서 필드를 동시 전환하는 합의다. 검증 관계의 이전 `source_id`,
두 source 필드의 동시 입력, 필드 누락은 거절하며 자동 호환·접두사 추측은 없다.
일반 그래프 관계의 `source_id`까지 일괄 변경해서는 안 된다.

### verifier 담당자 전달 내용

> 검증 관계에는 User node_id 대신 실제 접근 계정의 원본 `source_account_id`를
> 전달해 주세요. B가 A의 자원에 접근했다면 source는 B의 account_id이고,
> target은 A 자원의 기존 Resource instance node_id입니다. KG가 같은 run/graph의
> 기존 User를 찾아 연결합니다. `schema_version=0.2.0`은 유지하며, KG·Reporter
> 소비자는 전환과 독립 검증을 완료했습니다. 생산자 전환 후 실제 동일 run의
> 결과 파일로 연결 검증이 필요합니다.

`b2aec96`에서 생산자 출력 Schema의 `graphUpdateEdge`는 아직 `source_id`를
요구하지만 KG·Reporter 입력 사본은 `source_account_id`를 요구한다.
빈 `relationships=[]`가 통과하더라도 관계 필드 전환 완료의 증거는 아니다.
생산자 출력 Schema·직렬화·fixture·테스트 전환은 verifier 담당 작업으로 남긴다.
공용 `docs/spec/m7-verifier.md`도 검증 관계를 일반 GraphEdge로 설명하므로,
검증 관계 전용 필드의 원본 명세 반영은 담당자·관리자에게 전달할 후속 항목이다.
이 단계에서 다른 모듈이나 `docs/spec/`을 직접 수정하지 않는다.

이전 node_id 값을 새 필드명으로 단순히 바꿔 쓰지 않는다. 실제 실행 계정의
원본 ID로 새 결과를 생성하고 완료 artifact를 덮어쓰지 않는다.
새 파일의 정확한 SHA-256을 직접 소비자에게 전달하고 그 파일을 참조하는
`input_refs`도 실제 바이트 해시로 맞춘다. 같은 verification_id의 다른 해시
재사용은 KG에서 충돌로 처리하므로 이전 반영 기록을 덮어쓰는 전환은 하지 않는다.
기존 pipeline의 입력 구성과 target 전달 방식은 유지하며 새 입력 파일을 요구하지 않는다.

### 실제 산출물 연결 검증 체크리스트 — 후속 작업

- [ ] verifier 출력 계약·직렬화·소유 fixture가 `source_account_id`로 전환됐는지 확인
- [ ] 같은 run의 실제 입력·출력과 정확한 파일 해시, source revision·계획 해시·근거 참조 확보
- [ ] 계정 ID와 node_id가 다른 User B가 A 자원에 접근한 사례로 KG·Reporter 수신 확인
- [ ] KG 반영 뒤 실제 저장 관계·snapshot의 source는 B의 User node_id, target은 기존 instance인지 확인
- [ ] 정상 반영의 revision 증가와 동일 입력 재반영 no-op, 잘못된 계정·참조 거절 확인
- [ ] Reporter `report`의 후보·계획·Policy·검증 연결, 미검증 상태 보존과 출력 참조 해시 확인
- [ ] development `evaluate`가 필요하면 정답과 해당 입력 계약에 맞는 snapshot을 별도로 확보해 확인
- [ ] 원본 명세 갱신과 연결 검증 결과를 각 담당자와 공유한 뒤 pipeline 완료 여부 판단

이 체크리스트는 아직 수행 완료로 표시하지 않는다. 소유 fixture 검증과 생산자의
실제 산출물 수신은 구분하며, 실제 `runs/`나 계정 비밀정보를 문서·Git에 포함하지 않는다.

### 검증 기록과 남은 범위

4단계의 실제 DB 검증 기록은 통합 12건, KG 전체 255건, 담당 3개 모듈 790건 통과다.
이번 문서 단계에서는 Neo4j를 재실행하지 않으며 실제 DB 결과는 4단계 기록을 참조한다.

5단계 문서 수정 후 기본 회귀를 재실행했다.

```bash
.venv/bin/python -m pytest modules/knowledge_graph/ modules/safety_policy/ modules/reporter/ -q
```

```text
778 passed, 12 skipped
```

skip 12건은 opt-in 실제 Neo4j 통합 테스트이며 이번 단계에서는 활성화하지 않았다.
`git diff --check`도 통과했고 변경 파일은 두 README뿐이다.

소비자 측 구현·검증·문서 정리는 5단계로 마무리하고, 이후 작업은 생산자 출력 전환,
실제 동일 run 결과 수신, 전체 pipeline 연결 확인이다.
