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

주요 조건:

- source verification이 실행 가능한 결정이어야 함
- verification 실행이 완료되어야 함
- update의 `basis`는 `verified`
- 실제 실행 EvidenceRef와 연결되어야 함
- `source_graph_revision`이 현재 revision과 일치해야 함
- 관계 source는 기존 User, target은 기존 Resource instance여야 함
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
160 passed, 4 skipped
```

skip 4건은 실제 Neo4j가 필요한 통합 테스트다.

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
