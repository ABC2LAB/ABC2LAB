# 지식 그래프 저장소 · knowledge_graph

[ABC2LAB_인터페이스_명세서_v0.1](00-index.md) · [공통 계약 · 파일 형식과 전달 규칙](02-common-contract.md) · [실행 제어 · runner와 폴더 구조](03-runner-layout.md)

<aside>
🕸️

- 구현 폴더: `modules/knowledge_graph/`.
- 공개 operation: `ingest`, `query`, `apply_verification`.
- 담당 범위: Neo4j 연결, 노드·관계·업무 흐름 적재, 정형 읽기 질의 실행, 검증 근거 반영과 시각화용 데이터 제공을 담당한다.
</aside>

**읽는 순서:** 독립 동작·수정 책임 → 입력 변화 기준 → 입력 → 구현할 일 → 출력 → 완료 기준 → 주의사항. 상세 JSON·중첩 레코드는 접힌 제목에서 확인한다.

## 현재 구현·연결 상태 — 2026-10-10

기준 커밋 `695b3c8`의 구현·공개 계약을 반영했다. PR #52·#53 병합 후 상태이며,
이번 저장소 문서 갱신이 노션 원본의 export 갱신을 의미하지는 않는다.

| 구분 | 현재 상태 |
| --- | --- |
| 독립 구현 | `ingest`·`query`·`apply_verification`, 공개 함수·CLI, 원자 저장 구현 완료 |
| 모델·질의 | Resource Type/Instance, 원본 계정·역할 ID, RequestObservation 및 요청 provenance 처리 완료 |
| 공개 계약 | 입력 `semantic_analysis`·`graph_query`·`verification_results`, 출력 `graph_query_result` 모두 `0.2.0` |
| 검증 관계 호환 | Verifier의 `source_account_id` 출력 전환 및 KG의 기존 User 조회·변환 구현 완료 |
| 독립 회귀 | KG `243 passed, 12 skipped`; KG·Safety Policy·Reporter·Verifier 합동 `897 passed, 12 skipped` |
| 실제 전체 연결 | 미완료. Verifier는 아직 `graph_updates`를 빈 배열로 생성하며, 실제 동일 run의 관계 수신·DB 반영 검증 필요 |

skip 12건은 이번에 활성화하지 않은 실제 Neo4j 통합 테스트다. 기존 실제 DB 검증과
상세 구현 이력은 [모듈 README](../../modules/knowledge_graph/README.md)를 따른다.
계약 호환 확인·소유 fixture 검증을 전체 pipeline 연결 완료로 표시하지 않는다.

## 독립 동작·수정 책임

**이 모듈 담당자의 전체 책임:** 명세에 맞는 입력을 받으면 입력 검증·변환·실제 처리·출력 변환·출력 검증·저장·오류 처리·설정·모듈별 의존성 선언·테스트·동작 확인을 자기 폴더 안에서 끝낸다. 실행·테스트는 팀 공통 Python과 루트 잠금 환경에서 수행한다. 외부 모듈의 내부 코드·공유 도구에 의존하지 않는다.

| 영역 | 변경·작성 책임 | 참조·사용 경계 |
| --- | --- | --- |
| 자기 구현·도구·타입·설정·의존성 선언·테스트 | `modules/knowledge_graph/**`는 knowledge_graph 담당 수정 | 파서·검증·저장·해시·경로·adapter도 자기 utils와 schemas에서 구현한다. 다른 모듈 코드를 import하지 않는다. |
| 입력 계약·입력 검증 | knowledge_graph 담당이 자기 `schemas/input/`과 검증을 관리 | 입력 원본은 읽기 전용. 생산자 출력 계약과 일치시키고 원본 오류는 생산자에게 요청한다. |
| 자기 출력 계약·출력 검증 | knowledge_graph 담당이 자기 `schemas/output/`·출력·근거·문서를 작성·검증 | 합의한 출력 Schema·필드 의미·ID·근거를 만족한 파일만 완료로 공개한다. |
| 입력·외부 참조 | 의미 분석 결과는 semantic_analyzer, 질의는 access_analyzer, 검증 결과는 verifier가 작성한다. KG가 각 입력과 자기 DB 상태를 검증한다. | 명시된 파일·근거·공개 창구만 사용한다. 다른 모듈 내부 구현·전역 가변 상태를 읽지 않는다. |
| 출력 변경·수신자 조율 | 변경 제안·출력 구현은 knowledge_graph / 입력 대응은 아래 직접 소비자 | 소비자 내부 알고리즘은 알 필요가 없다. 그 파일을 실제 읽는 담당자와만 버전·필드·의미·변경 시점을 합의한다. |
| Neo4j·Cypher·DB 속성 변환·인덱스·revision | knowledge_graph만 적재·질의·검증 반영을 수행 | access_analyzer는 query_key/parameters를 요청한다. reporter는 실제 snapshot을 읽는다. |
| graph_updates와 구조 snapshot | 검증 원본은 읽고 실제 반영 여부·중복 방지는 KG가 책임진다. | verifier 결과 파일을 고치지 않는다. JSON 없는 operation에 새 임의 파일을 추가하지 않는다. |

**모듈 내부 경계:** 입력 adapter → 내부 처리 → 출력 adapter → 자기 출력 검증 → 원자적 저장 → 완료 통지. 내부 고도화가 계약을 유지하면 다른 담당자에게 수정을 요구하지 않는다.

**입력·출력 계약과 수정 책임:** [공통 계약 · 파일 형식과 전달 규칙](02-common-contract.md) · **독립 실행·모듈 폴더 구조:** [실행 제어 · runner와 폴더 구조](03-runner-layout.md)

## 공통 환경에서의 독립 개발

**개발 기준·관리자 초기 설정·환경 설치:** [개발 기준 · 환경 일원화와 GitHub 초기 설정](01-dev-standard.md)

- 같은 `.python-version`과 루트 `requirements.lock.txt`로 만든 저장소 루트 `.venv`에서 실행·테스트한다. 독립 검증은 자기 입력 fixture·공개 창구 대역으로 수행한다.
- `modules/knowledge_graph/requirements.txt`에는 실제 사용하는 직접 의존성과 지원 버전을 선언한다. 추가·버전 변경은 GitHub 관리자에게 제안하고 후보 잠금 환경에서 자기 모듈을 검증한다.
- 루트 네 환경 파일은 관리자만 수정한다. 자기 Python 버전이나 별도 lock을 만들어 팀 기준을 변경하지 않는다.
- 의존성 변경은 직접 JSON 소비자 외에도 같은 패키지·하위 의존성의 영향을 받는 모듈과 호환성을 확인한다. JSON 출력 변경은 기존 직접 소비자 목록을 따른다.
- 필요한 환경변수는 자기 `.env.example`·configs·README에 기록한다. 실제 비밀값과 실행 결과는 Git에 올리지 않는다.

## 입력 변화·고도화 완료 기준

- [ ]  기준 Python·공통 잠금 파일로 환경을 동기화하고 같은 환경에서 자기 실행·테스트 명령을 확인한다.
- [ ]  의존성 변경이 있으면 자기 requirements 제안과 관리자 잠금 파일 갱신·영향 검증이 같은 병합본에 반영된다.
- [ ]  입력 Schema·지원 버전·ID·참조·해시를 자기 모듈이 검증하고 잘못된 입력을 정상 결과로 숨기지 않는다.
- [ ]  입력 파싱부터 정상/부분/실패 처리, 출력 Schema·의미 검증과 실제 저장까지 담당자가 동작을 확인한다. 중앙 검증 도구에 책임을 넘기지 않는다.
- [ ]  팀 공통 잠금 환경과 자기 설정·tests/fixtures로 명세 입력을 받아 독립 실행할 수 있다. 다른 모듈의 실제 구현이 없어도 공개 입력 fixture로 검증한다.
- [ ]  자기 utils·타입·Schema는 자기 폴더 안에 둔다. 공유 구현 폴더와 다른 모듈의 내부 파일을 import하지 않는다.
- [ ]  노드·관계·질의·결과 행의 개수와 순서 변화에 ID·query_key·revision으로 대응한다.
- [ ]  조회 실패와 정상 빈 rows를 구분하고 DB 장애·검증 중복 반영을 처리한다.
- [ ]  허용된 빈 배열·null·partial, 잘못된 Schema/버전/ID/참조/해시를 구분한다. 입력 오류를 성공·정상 빈 결과로 숨기지 않는다.
- [ ]  자기 출력의 Schema·작성자·필수 필드·ID/근거 대응을 검증하고 자기 경로에 원자적으로 공개한다. 저장 실패를 완료로 알리지 않는다.
- [ ]  위 입력 변화와 의존성 실패를 자기 모듈 테스트에서 검증한다. 다른 모듈 실제 구현 대신 공개 입력 fixture·인터페이스 대역으로 독립 검증할 수 있다.
- [ ]  내부 최적화 이후에도 자기 입력·출력 계약 검증을 통과한다. 계약 변경 시 직접 소비자와 실제 출력 샘플의 수신 검증을 함께 확인한다.

## 입력

| 입력 파일·정보 | 작성·제공 주체 | 사용할 내용 |
| --- | --- | --- |
| semantic_analysis.json | `semantic_analyzer` | ingest: data.nodes·relationships·workflows를 적재하고 normalized_requests를 내부 RequestObservation으로 보존한다. |
| graph_query.json | `access_analyzer` | query: data.graph_id·expected_graph_revision·queries의 query_id·query_key·parameters를 읽는다. |
| verification_results.json | `verifier` | apply_verification: data.results와 graph_updates의 출처·근거를 대조해 실제 검증이 뒷받침한 갱신만 반영한다. |
| 실행 인자 / 자기 모듈 설정 | `사용자 실행 인자 / KG 공개 응답` | 현재 run 범위, graph_id·graph_revision, 신뢰된 실행 정보. |

**입력 파일의 전체 필드:** [semantic_analysis.json 필드](m2-semantic_analyzer.md) · [graph_query.json 필드](m4-access_analyzer.md) · [verification_results.json 필드](m7-verifier.md).

## 구현할 일

1. ingest: 의미 분석 결과를 적재하고 graph_id·graph_revision·준비 상태를 자기 공개 완료 응답으로 반환한다.
2. query: 허용된 query_key의 읽기 전용 Cypher 템플릿에 검증한 parameters를 적용한다.
3. 질의별 query_id·query_key·상태·오류·typed rows와 실제 조회 revision을 graph_query_result.json으로 내보낸다.
4. structure_snapshot 요청에는 실제 적재된 노드·관계·업무 흐름을 반환한다.
5. apply_verification: verification_id를 기준으로 새 검증 근거를 한 번 반영하고 revision을 올린다. 갱신 제어 응답은 자기 공개 실행 결과로 반환한다.

## 출력

| 작성 파일 | 생성 operation | 출력 변경 협의 대상 — 직접 소비자 |
| --- | --- | --- |
| `graph_query_result.json` | `query` | access_analyzer · reporter |

정상·부분 완료 파일의 `data` 필드는 아래와 같다. `status=failed`이면 `data=null`로 기록한다. 공통 메타데이터·상태 규칙은 [공통 파일 형식](02-common-contract.md)을 적용한다.

## 완료 기준

아래 표시는 모듈 독립 구현·소유 fixture/저장소 대역 및 기존 DB 검증 기준이다.
같은 run의 실제 생산자 산출물 연결 완료를 뜻하지 않는다.

- [x]  ingest·query·apply_verification을 공개 operation으로 제공한다.
- [x]  허용 query_key와 parameters로만 읽기 질의를 수행하고 query_id·revision을 대응시킨다.
- [x]  실제 DB snapshot을 반환하고, verification_id의 중복 반영을 막는다.
- [ ]  실제 Verifier의 비어 있지 않은 graph_updates로 동일 run 연결을 검증한다.

## 구현 주의사항

- Neo4j 접속 정보와 Cypher 템플릿은 이 폴더가 소유한다. 접근 통제 분석기는 질의 키·파라미터를 요청한다.
- 질의를 graph_id와 run_id 범위에 묶고 읽기 전용 권한·트랜잭션을 사용한다. LLM이 생성한 raw Cypher를 직접 실행하지 않는다.
- properties의 JSON map과 Neo4j 속성 타입은 같지 않다. 허용 속성·중첩 값의 저장 규칙과 조회 복원 규칙을 함께 정의한다.
- 원본 관찰·추론의 출처를 보존한다. 미실행 후보·판단불가를 실제 접근 성공 관계로 적재하지 않는다.
- 구조 평가의 snapshot은 실제 DB 적재 결과여야 한다. 적재 전 semantic_analysis.json만 반환하여 적재 정확도를 평가하지 않는다.
- ingest·apply_verification의 준비 상태·revision은 공개 호출의 제어 응답이다. 추가 결과 JSON 파일명을 만들지 않는다.

## Resource Type/Instance와 검증 반영 계약 — 0.2.0

Resource는 종류와 인스턴스를 모두 표현한다. Resource `properties`는 아래 필수
필드를 가지며 추가 속성은 허용한다. 상세 중첩 계약은
[m2의 ResourceProperties·ResourceMatchKey](m2-semantic_analyzer.md)를 따른다.

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `resource_key` | 비어 있지 않은 `string` | 필수 | 자원 종류 키. 실제 자원 번호와 구분한다. |
| `resource_scope` | `enum: type, instance` | 필수 | 자원 종류 전체 또는 특정 인스턴스. |
| `match_key` | `ResourceMatchKey / null` | 필수 | type이면 null, instance이면 하나 이상의 `{key, value}` 문자열 식별값을 포함한다. |

- 복합 식별값을 지원한다. 식별자 배열의 key 순서가 달라도 같은 인스턴스로
  판단하며 같은 match_key를 서로 다른 node_id로 제시하면 ingest를 거절한다.
- Resource node_id는 생산자가 생성한다. KG는 불투명 ID로 검증·저장하며
  종류명·URL·상품 번호로 ID를 추측하거나 재생성하지 않는다.
- 민감 식별값으로 인스턴스를 만들지 않는다. 기존 Resource 속성 누락·0.1 산출물은
  묵시 변환하지 않고 semantic_analysis 0.2 재생성과 새 KG ingest를 요구한다.
- 원본 Resource 속성은 보존하고 조회용 속성을 함께 저장한다. `resource_ownership`은
  instance만 반환하며 `role_resource_access`는 type·instance·미연결을 구분한다.
- RequestObservation은 내부 저장 구조이므로 structure_snapshot의 공개 노드에 포함하지 않는다.

`apply_verification`의 입력 관계는 [m7의 VerificationRelationship](m7-verifier.md)를
사용하며, 일반 GraphEdge와 구분한다.

| 위치 | source 필드 | 값의 의미 |
| --- | --- | --- |
| `verification_results.data.graph_updates.relationships` | `source_account_id` | 실제 요청한 계정의 원본 account_id |
| semantic 관계·KG 저장 관계·snapshot | `source_id` | KG node_id |

KG는 같은 run/graph의 기존 User `properties.account_id`를 조회해 저장용 source_id로
변환한다. 계정 누락·중복, 다른 run/graph, 잘못된 참조는 거절한다. 검증 관계는
`VERIFIED_ACCESS`·`VERIFIED_DENIAL`, `basis=verified`와 실제 실행 근거만 사용하고,
target_id는 같은 run/graph에 이미 존재하는 Resource instance node_id여야 한다.
Verifier의 신규 Resource 생성·Resource Type target은 허용하지 않는다.

revision 확인·계정 조회·참조 검증·저장은 하나의 쓰기 트랜잭션에서 처리한다.
동일 검증의 재반영과 빈 갱신은 no-op이며, 실제 유효 변경에 대해서만 revision을
증가시킨다. 입력 검증 관계의 이전 source_id·두 source 필드 동시 입력은 거절하고
일반 관계의 source_id 계약은 유지한다. 버전은 `0.2.0`을 유지한다.

## 입력·출력 JSON 필드

### graph_query_result.json

- 고정 값: `artifact_type=graph_query_result`, `producer=knowledge_graph`, `schema_version=0.2.0`.
- 예상 Schema 경로: `modules/knowledge_graph/schemas/output/graph_query_result.schema.json`.
- 예상 출력 fixture 경로: `modules/knowledge_graph/tests/fixtures/runs/run_demo_001/artifacts/iteration-000/knowledge_graph/graph_query_result.json`.

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `data.graph_id` | `string` | 필수 | 실제로 조회한 KG ID. |
| `data.graph_revision` | `integer` | 필수 | 실제로 조회한 KG revision. |
| `data.results` | `array<OwnershipResult / AccessResult / FlowResult / SnapshotResult>` | 필수 | query_id·query_key로 원본과 대응되는 결과 목록. |

## 중첩 레코드 필드

출력 배열·객체의 항목마다 아래 필수 필드를 적용한다. `properties`·`match_key` 등 명시된 JSON map은 확장 가능하고 일반 객체는 미정의 키를 거절한다.

### OwnershipResult

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `query_id` | `string` | 필수 | 원본 graph_query의 query_id. |
| `query_key` | `string: resource_ownership` | 필수 | 원본 질의 키. |
| `status` | `enum: completed, failed` | 필수 | 이 질의 처리 상태. |
| `rows` | `array<OwnershipRow>` | 필수 | 조회 소유 관계. 일치 결과가 없으면 빈 배열. |
| `errors` | `array<ErrorItem>` | 필수 | 질의 오류 목록. 정상 질의면 빈 배열. |

### OwnershipRow

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `resource_id` | 비어 있지 않은 `string` | 필수 | 조회 Resource instance의 불투명 KG node_id. |
| `resource_key` | 비어 있지 않은 `string` | 필수 | 자원 종류 키. |
| `resource_scope` | `const: instance` | 필수 | 소유 조회는 인스턴스만 반환한다. |
| `match_key` | `ResourceMatchKey` | 필수 | 자원 종류와 하나 이상의 문자열 식별값. |
| `owner_account_id` | `string` | 필수 | 관찰·추론한 소유 계정 ID. |
| `basis` | `enum: observed, inferred, verified` | 필수 | 소유 관계의 출처. |
| `evidence_refs` | `array<EvidenceRef>` | 필수 | 소유 관계 근거 참조. |

### AccessResult

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `query_id` | `string` | 필수 | 원본 graph_query의 query_id. |
| `query_key` | `string: role_resource_access` | 필수 | 원본 질의 키. |
| `status` | `enum: completed, failed` | 필수 | 이 질의 처리 상태. |
| `rows` | `array<AccessRow>` | 필수 | 조회 접근 관계. |
| `errors` | `array<ErrorItem>` | 필수 | 질의 오류 목록. |

### AccessRow

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `account_id` | `string` | 필수 | 관찰 실행 계정의 원본 ID. User node_id가 아니다. |
| `role_id` | `string` | 필수 | 관찰 실행 역할의 원본 ID. Role node_id가 아니다. |
| `endpoint_id` | `string` | 필수 | 관찰 Endpoint ID. |
| `resource_id` | `string / null` | 필수 | 연결된 Resource의 불투명 KG node_id. 미연결이면 null. |
| `resource_key` | `string / null` | 필수 | 연결된 자원 종류 키. 미연결이면 null. |
| `resource_scope` | `enum: type, instance / null` | 필수 | 연결된 자원의 범위. 미연결이면 null. |
| `match_key` | `ResourceMatchKey / null` | 필수 | instance이면 식별값, type·미연결이면 null. |
| `action` | `string` | 필수 | 관찰 행위 의미. |
| `request_ids` | `array<nonEmptyString>` (1개 이상, 중복 금지) | 필수 | 원본 수집 요청 provenance. 후보의 source_request_ids 구성에 사용한다. |
| `access_observed` | `boolean` | 필수 | 실제로 접근한 사실 여부. 접근 허용 정책과 별개이다. |
| `evidence_refs` | `array<EvidenceRef>` | 필수 | 접근 관찰 근거 참조. |

미연결 AccessRow는 resource_id·resource_key·resource_scope·match_key가 모두 null이다.
동일 계정·역할·Endpoint·Resource·행위의 관찰은 합치고 request_ids·evidence_refs는
중복 없이 누적한다. 빈 rows와 조회 실패는 기존 상태 계약으로 구분한다.

### FlowResult

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `query_id` | `string` | 필수 | 원본 graph_query의 query_id. |
| `query_key` | `string: workflow_dependencies` | 필수 | 원본 질의 키. |
| `status` | `enum: completed, failed` | 필수 | 이 질의 처리 상태. |
| `rows` | `array<FlowRow>` | 필수 | 조회 업무 의존 관계. |
| `errors` | `array<ErrorItem>` | 필수 | 질의 오류 목록. |

### FlowRow

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `workflow_id` | `string` | 필수 | 업무 흐름 ID. |
| `before_step_id` | `string` | 필수 | 선행 단계 ID. |
| `after_step_id` | `string` | 필수 | 후행 단계 ID. |
| `condition` | `string` | 필수 | 단계 사이 조건 설명. |
| `basis` | `enum: observed, inferred, verified` | 필수 | 조건의 출처. |
| `evidence_refs` | `array<EvidenceRef>` | 필수 | 조건 근거 참조. |

### SnapshotResult

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `query_id` | `string` | 필수 | 원본 graph_query의 query_id. |
| `query_key` | `string: structure_snapshot` | 필수 | 원본 질의 키. |
| `status` | `enum: completed, failed` | 필수 | 이 질의 처리 상태. |
| `rows` | `array<GraphSnapshot>` | 필수 | 해당 revision의 실제 적재 스냅샷. 정상 요청에서는 1개. |
| `errors` | `array<ErrorItem>` | 필수 | 질의 오류 목록. |

### GraphSnapshot

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `nodes` | `array<GraphNode>` | 필수 | 실제 KG에 적재된 노드 스냅샷. |
| `relationships` | `array<GraphEdge>` | 필수 | 실제 KG에 적재된 관계 스냅샷. |
| `workflows` | `array<Workflow>` | 필수 | KG의 업무 흐름·순서 정보를 JSON으로 표현한 스냅샷. |

**재사용하는 계약 필드:** [ArtifactRef](02-common-contract.md), [ErrorItem](02-common-contract.md), [EvidenceRef](02-common-contract.md), [GraphEdge](m2-semantic_analyzer.md), [GraphNode](m2-semantic_analyzer.md), [RuntimeMetrics](02-common-contract.md), [Workflow](m2-semantic_analyzer.md), [WorkflowDependency](m2-semantic_analyzer.md), [WorkflowStep](m2-semantic_analyzer.md).

## 변경 이력

| 날짜 | 문서 변경 | 기준·영향 |
| --- | --- | --- |
| 2026-10-10 | README의 Resource Type/Instance·요청 provenance·계정 source 변환을 명세에 반영하고 현재 구현·연결 상태와 완료 기준을 갱신 | `695b3c8`, PR #52·#53 반영. 기존 구현·0.2.0 계약을 기록하며 새 필드나 버전을 도입하지 않음 |

기존 책임 경계·공개 operation·실패 처리 규칙은 유지한다. 상세 과거 구현·DB 검증
이력은 모듈 README에 보존한다. 합동 회귀는 `897 passed, 12 skipped`이며 실제
Neo4j 12건과 동일 run 전체 연결은 이번 검증에 포함하지 않았다. 노션 원본 반영·
export 기준일 갱신은 별도 관리 작업으로 남긴다.

---

[ABC2LAB_인터페이스_명세서_v0.1](00-index.md) · [공통 계약 · 파일 형식과 전달 규칙](02-common-contract.md) · [실행 제어 · runner와 폴더 구조](03-runner-layout.md) ·
