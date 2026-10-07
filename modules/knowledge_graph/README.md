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

주요 조건:

- source verification이 실행 가능한 결정이어야 함
- verification 실행이 완료되어야 함
- update의 `basis`는 `verified`
- 실제 실행 EvidenceRef와 연결되어야 함
- `source_graph_revision`이 현재 revision과 일치해야 함
- 관계 source/target이 유효해야 함

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
User ──ACCESS|VERIFIED_ACCESS──> Endpoint
```

반환되는 주요 값은 다음과 같다.

```json
{
  "account_id": "acc_alice",
  "role_id": "role_user",
  "endpoint_id": "endpoint:GET:/orders/{id}",
  "resource_id": "resource:order",
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

Resource가 여러 개이면 Resource별 AccessRow를 생성하며, Resource가 없으면 `resource_id=null`인 row를 생성한다.

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
python -m pytest modules/knowledge_graph/ -q
```

현재 기본 테스트 결과:

```text
121 passed, 4 skipped
```

skip 4건은 실제 Neo4j가 필요한 통합 테스트다.

실제 Neo4j 통합 테스트는 Neo4j 실행 후 명시적으로 활성화한다.

```bash
KG_RUN_NEO4J_INTEGRATION=1 \
NEO4J_URI=bolt://localhost:7687 \
NEO4J_USERNAME=neo4j \
NEO4J_PASSWORD='<configured-password>' \
python -m pytest \
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
- User→Endpoint `ACCESS|VERIFIED_ACCESS` 검증
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

실제 Neo4j 통합 테스트는 현재 Neo4j 미실행으로 4건이 skip된 상태이며, 다음 실제 DB 실행 시 `request_ids`까지 함께 검증한다.

---

## 현재 상태

knowledge_graph 기준으로 semantic analyzer 호환 문제와 A2/A3 관련 KG 책임은 해결된 상태다.

```text
원본 account/role ID 처리        완료
RequestObservation 내부 저장     완료
role_resource_access 호환        완료
AccessRow request_ids 제공        완료
KG 기본 회귀 테스트               완료
A3 실제 Neo4j 재검증              보류
```

외부 소비자 모듈의 입력 Schema 및 `Candidate.source_request_ids` 연결은 각 소비자 모듈의 계약 반영 범위에서 처리한다.