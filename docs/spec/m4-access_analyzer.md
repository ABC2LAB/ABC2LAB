# 접근 통제 분석기 · access_analyzer

[ABC2LAB_인터페이스_명세서_v0.1](00-index.md) · [공통 계약 · 파일 형식과 전달 규칙](02-common-contract.md) · [실행 제어 · runner와 폴더 구조](03-runner-layout.md)

<aside>
🔎

- 구현 폴더: `modules/access_analyzer/`.
- 공개 operation: `prepare_queries`, `analyze`.
- 담당 범위: KG 질의 요청 구성, 역할·계정·자원 접근 분석, 업무 흐름 조건 분석과 검증 후보 생성을 담당한다. Rule 분석과 필요한 복잡한 판단의 LLM 보조를 이 폴더에 둔다.
</aside>

**읽는 순서:** 독립 동작·수정 책임 → 입력 변화 기준 → 입력 → 구현할 일 → 출력 → 완료 기준 → 주의사항. 상세 JSON·중첩 레코드는 접힌 제목에서 확인한다.

## 독립 동작·수정 책임

**이 모듈 담당자의 전체 책임:** 명세에 맞는 입력을 받으면 입력 검증·변환·실제 처리·출력 변환·출력 검증·저장·오류 처리·설정·모듈별 의존성 선언·테스트·동작 확인을 자기 폴더 안에서 끝낸다. 실행·테스트는 팀 공통 Python과 루트 잠금 환경에서 수행한다. 외부 모듈의 내부 코드·공유 도구에 의존하지 않는다.

| 영역 | 변경·작성 책임 | 참조·사용 경계 |
| --- | --- | --- |
| 자기 구현·도구·타입·설정·의존성 선언·테스트 | `modules/access_analyzer/**`는 access_analyzer 담당 수정 | 파서·검증·저장·해시·경로·adapter도 자기 utils와 schemas에서 구현한다. 다른 모듈 코드를 import하지 않는다. |
| 입력 계약·입력 검증 | access_analyzer 담당이 자기 `schemas/input/`과 검증을 관리 | 입력 원본은 읽기 전용. 생산자 출력 계약과 일치시키고 원본 오류는 생산자에게 요청한다. |
| 자기 출력 계약·출력 검증 | access_analyzer 담당이 자기 `schemas/output/`·출력·근거·문서를 작성·검증 | 합의한 출력 Schema·필드 의미·ID·근거를 만족한 파일만 완료로 공개한다. |
| 입력·외부 참조 | KG 결과·graph 상태는 knowledge_graph가 제공한다. access_analyzer가 입력·revision·자기 실행 인자와 규칙을 검증한다. | 명시된 파일·근거·공개 창구만 사용한다. 다른 모듈 내부 구현·전역 가변 상태를 읽지 않는다. |
| 출력 변경·수신자 조율 | 변경 제안·출력 구현은 access_analyzer / 입력 대응은 아래 직접 소비자 | 소비자 내부 알고리즘은 알 필요가 없다. 그 파일을 실제 읽는 담당자와만 버전·필드·의미·변경 시점을 합의한다. |
| 질의 계획 | access_analyzer가 query_key·parameters·query_id를 작성 | Cypher 실행·DB 연결은 knowledge_graph 소유다. 평가 snapshot 질의도 이 작성 주체를 따른다. |
| 분석 Rule·선택적 LLM 보조·후보 | access_analyzer가 가설과 기대 근거를 생성 | 추론 후보를 취약점 확정으로 변경하지 않는다. 공개 질의 키/row 의미 변경은 KG와 영향받는 소비자가 공동 검토한 계약 변경 PR로 처리한다. |

**모듈 내부 경계:** 입력 adapter → 내부 처리 → 출력 adapter → 자기 출력 검증 → 원자적 저장 → 완료 통지. 내부 고도화가 계약을 유지하면 다른 담당자에게 수정을 요구하지 않는다.

**입력·출력 계약과 수정 책임:** [공통 계약 · 파일 형식과 전달 규칙](02-common-contract.md) · **독립 실행·모듈 폴더 구조:** [실행 제어 · runner와 폴더 구조](03-runner-layout.md)

## 공통 환경에서의 독립 개발

**개발 기준·관리자 초기 설정·환경 설치:** [개발 기준 · 환경 일원화와 GitHub 초기 설정](01-dev-standard.md)

- 같은 `.python-version`과 루트 `requirements.lock.txt`로 만든 저장소 루트 `.venv`에서 실행·테스트한다. 독립 검증은 자기 입력 fixture·공개 창구 대역으로 수행한다.
- `modules/access_analyzer/requirements.txt`에는 실제 사용하는 직접 의존성과 지원 버전을 선언한다. 추가·버전 변경은 GitHub 관리자에게 제안하고 후보 잠금 환경에서 자기 모듈을 검증한다.
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
- [ ]  역할·소유 자원·업무 조건과 후보 수가 바뀌어도 ID·근거·기대 조건을 갖춘 후보를 생성한다.
- [ ]  질의 실패·partial·오래된 revision을 정상적인 후보 0개와 구분한다.
- [ ]  허용된 빈 배열·null·partial, 잘못된 Schema/버전/ID/참조/해시를 구분한다. 입력 오류를 성공·정상 빈 결과로 숨기지 않는다.
- [ ]  자기 출력의 Schema·작성자·필수 필드·ID/근거 대응을 검증하고 자기 경로에 원자적으로 공개한다. 저장 실패를 완료로 알리지 않는다.
- [ ]  위 입력 변화와 의존성 실패를 자기 모듈 테스트에서 검증한다. 다른 모듈 실제 구현 대신 공개 입력 fixture·인터페이스 대역으로 독립 검증할 수 있다.
- [ ]  내부 최적화 이후에도 자기 입력·출력 계약 검증을 통과한다. 계약 변경 시 직접 소비자와 실제 출력 샘플의 수신 검증을 함께 확인한다.

## 입력

| 입력 파일·정보 | 작성·제공 주체 | 사용할 내용 |
| --- | --- | --- |
| 실행 인자 / 자기 규칙 설정 | `사용자 실행 인자 / modules 내 자기 configs` | prepare_queries: KG 준비 상태·graph_id·graph_revision, 분석 범위와 Rule 설정. |
| graph_query_result.json | `knowledge_graph` | analyze: data.graph_revision와 results의 query_id·query_key·status·errors·rows. |

**입력 파일의 전체 필드:** [graph_query_result.json 필드](m3-knowledge_graph.md).

## 구현할 일

1. prepare_queries: 허용된 질의 키와 파라미터를 graph_query.json에 작성한다.
2. KG query가 생성한 graph_query_result.json을 analyze 입력으로 받는다. 내부 KG 코드나 DB에 직접 접근하지 않는다.
3. 질의 ID·키·revision을 대응시키고, 역할별 접근·자원 소유권·업무 의존 조건을 분석한다.
4. 위반 가설, 실행 계정·역할, 대상 자원, 원본 요청과 기대 조건의 근거를 vulnerability_candidates.json에 작성한다.

## 출력

| 작성 파일 | 생성 operation | 출력 변경 협의 대상 — 직접 소비자 |
| --- | --- | --- |
| `graph_query.json` | `prepare_queries` | knowledge_graph |
| `vulnerability_candidates.json` | `analyze` | scenario_generator · reporter |

정상·부분 완료 파일의 `data` 필드는 아래와 같다. `status=failed`이면 `data=null`로 기록한다. 공통 메타데이터·상태 규칙은 [공통 파일 형식](02-common-contract.md)을 적용한다.

## 완료 기준

- [ ]  prepare_queries와 analyze를 분리하고 knowledge_graph와 합의한 질의·응답 계약을 각각 검증한다.
- [ ]  질의 ID·키·graph_revision을 검증하고, 오래된 결과로 후보를 만들지 않는다.
- [ ]  후보에 원본 요청·계정·자원·기대 조건의 근거를 연결하고 후보를 취약점 확정으로 기록하지 않는다.

## 구현 주의사항

- 후보는 검증 대상이다. 관찰 접근 또는 추론한 조건만으로 취약점을 확정하지 않는다.
- 기대 조건은 expected_basis=rule·inferred·unknown으로 구분한다. 추론·불명확한 기대를 확정 정책으로 취급하지 않는다.
- 동일 역할의 다른 계정과 소유 자원을 구분한다. 역할 이름만으로 수평 인가 관계를 판단하지 않는다.
- Neo4j 연결과 실제 Cypher 실행은 knowledge_graph에 맡긴다. query_id·query_key·revision이 대응하지 않으면 오래된 결과로 후보를 만들지 않는다.
- 질의 실패·부분 결과를 후보 없음으로 단정하지 않는다. 작업 오류와 분석하지 못한 범위를 공통 상태·errors로 추적한다.

## 입력·출력 JSON 필드

### graph_query.json

- 고정 값: `artifact_type=graph_query`, `producer=access_analyzer`, `schema_version=0.2.0`(전원 동시 전환).
- 예상 Schema 경로: `modules/access_analyzer/schemas/output/graph_query.schema.json`.
- 예상 출력 fixture 경로: `modules/access_analyzer/tests/fixtures/runs/run_demo_001/artifacts/iteration-000/access_analyzer/graph_query.json`.

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `data.graph_id` | `string` | 필수 | 조회 대상 KG ID. run_id와 대응되며 다른 진단 KG를 조회하지 않는다. |
| `data.expected_graph_revision` | `integer / null` | 필수 | 기대하는 KG revision. null이면 현재 revision을 조회하고 응답에서 실제 값을 받는다. |
| `data.queries` | `array<OwnershipQuery / AccessQuery / FlowQuery / SnapshotQuery>` | 필수 | 허용된 템플릿 키와 파라미터를 사용하는 읽기 질의 목록. |

### vulnerability_candidates.json

- 고정 값: `artifact_type=vulnerability_candidates`, `producer=access_analyzer`, `schema_version=0.2.0`(전원 동시 전환).
- 예상 Schema 경로: `modules/access_analyzer/schemas/output/vulnerability_candidates.schema.json`.
- 예상 출력 fixture 경로: `modules/access_analyzer/tests/fixtures/runs/run_demo_001/artifacts/iteration-000/access_analyzer/vulnerability_candidates.json`.

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `data.source_graph_revision` | `integer` | 필수 | 후보 분석에 사용한 KG revision. |
| `data.candidates` | `array<Candidate>` | 필수 | 검증 후보 목록. 후보가 없으면 빈 배열. |
| `data.model_info` | `ModelInfo / null` | 필수 | 복잡한 후보 분석에 사용한 LLM 정보. Rule만 사용하면 null. |

## 중첩 레코드 필드

출력 배열·객체의 항목마다 아래 필수 필드를 적용한다. `properties`·`match_key` 등 명시된 JSON map은 확장 가능하고 일반 객체는 미정의 키를 거절한다.

### OwnershipParameters

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `account_ids` | `array<string>` | 필수 | 조회할 계정 ID. 빈 배열이면 해당 run 범위 전체. |
| `resource_ids` | `array<string>` | 필수 | 조회할 자원 ID. 빈 배열이면 해당 run 범위 전체. |

### OwnershipQuery

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `query_id` | `string` | 필수 | 질의 왕복 연결 ID. |
| `query_key` | `string: resource_ownership` | 필수 | 저장소가 소유한 읽기 전용 Cypher 템플릿 키. |
| `parameters` | `OwnershipParameters` | 필수 | 형식이 정해진 질의 파라미터. |

### AccessParameters

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `role_ids` | `array<string>` | 필수 | 조회 역할 ID. 빈 배열이면 해당 run 범위 전체. |

### AccessQuery

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `query_id` | `string` | 필수 | 질의 왕복 연결 ID. |
| `query_key` | `string: role_resource_access` | 필수 | 역할·계정·자원 접근 관계 조회 키. |
| `parameters` | `AccessParameters` | 필수 | 형식이 정해진 질의 파라미터. |

### FlowParameters

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `workflow_ids` | `array<string>` | 필수 | 조회 업무 흐름 ID. 빈 배열이면 해당 run 범위 전체. |

### FlowQuery

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `query_id` | `string` | 필수 | 질의 왕복 연결 ID. |
| `query_key` | `string: workflow_dependencies` | 필수 | 업무 흐름·의존 관계 조회 키. |
| `parameters` | `FlowParameters` | 필수 | 형식이 정해진 질의 파라미터. |

### SnapshotParameters

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `include_evidence_refs` | `boolean` | 필수 | 실제 적재 구조의 근거 참조를 반환할지 여부. |

### SnapshotQuery

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `query_id` | `string` | 필수 | 질의 왕복 연결 ID. |
| `query_key` | `string: structure_snapshot` | 필수 | 실제 적재된 구조를 반환하는 평가·감사용 조회 키. |
| `parameters` | `SnapshotParameters` | 필수 | 형식이 정해진 질의 파라미터. |

### Candidate

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `candidate_id` | `string` | 필수 | 검증 후보 ID. 이후 시나리오·결과·리포트의 연결 키. |
| `category` | `enum: authorization, business_logic` | 필수 | 인가 또는 비즈니스 로직 분류. |
| `vulnerability_type` | `string` | 필수 | 구체 유형. 예: horizontal_access, workflow_step_bypass. |
| `rule_id` | `string` | 필수 | 후보를 생성한 규칙 식별자. |
| `actor_account_id` | `string` | 필수 | 재현을 제안하는 실행 계정 ID. |
| `actor_role_id` | `string` | 필수 | 실행 계정 역할 ID. |
| `reference_account_id` | `nonEmptyString / null` | 필수 | 정상 소유자·상위 역할 등 비교 기준 계정. 없으면 null(빈 문자열 금지). |
| `resource_ids` | `array<nonEmptyString>` (minItems 1) | 필수 | 검증 대상 자원 ID. **KG Resource instance node_id**(graph_query_result의 instance `resource_id`를 그대로). |
| `source_request_ids` | `array<nonEmptyString>` (minItems 1) | 필수 | 후보와 연결된 수집 요청 ID. 못 채우면 후보를 발행하지 않는다. |
| `workflow_id` | `nonEmptyString / null` | 필수 | 관련 업무 흐름 ID. 없으면 null(빈 문자열 금지). |
| `hypothesis` | `string` | 필수 | 검증할 위반 가설. |
| `expected_behavior` | `string` | 필수 | 정상 동작의 기대 조건 설명. |
| `expected_basis` | `enum: rule, inferred, unknown` | 필수 | 기대 조건의 근거. 추론·불명확한 기대를 자동 확정 정책으로 취급하지 않는다. |
| `evidence_refs` | `array<EvidenceRef>` | 필수 | 후보 가설을 뒷받침하는 관찰·분석 근거. |

**재사용하는 계약 필드:** [ArtifactRef](02-common-contract.md), [ErrorItem](02-common-contract.md), [EvidenceRef](02-common-contract.md), [ModelInfo](02-common-contract.md), [RuntimeMetrics](02-common-contract.md).

## 변경 이력 (0.1.0 → 0.2.0)

출력 `graph_query.json`·`vulnerability_candidates.json`의 `schema_version`을 0.2.0으로 올렸다(전원 동시 전환). 필드 의미 변경은 아래뿐이다.

- **`Candidate.resource_ids` = KG Resource instance node_id**: graph_query_result(0.2.0, #41)의 OwnershipRow는 instance만 오고, Rule A가 그 `resource_id`(불투명 node_id)를 그대로 담는다. 형식을 가정하지 않는다. `minItems 1`.
- **`Candidate.source_request_ids` `minItems 1`**: 근거 수집 요청(소유자 접근 행의 `request_ids`)을 못 채우면 후보를 발행하지 않고 `CANDIDATE_INCOMPLETE` 오류로 둔다(status=partial).
- **`reference_account_id`·`workflow_id` 빈 문자열 금지**(`nonEmptyString / null`).
- 입력 `graph_query_result`는 KG 생산자 계약 0.2.0을 미러한다(필드 정의는 [m3-knowledge_graph](m3-knowledge_graph.md), 버전 0.2.0). type 범위 접근 행은 instance 소유 행과 연결되지 않아(다른 노드) 후보에 쓰이지 않는다.

---

[ABC2LAB_인터페이스_명세서_v0.1](00-index.md) · [공통 계약 · 파일 형식과 전달 규칙](02-common-contract.md) · [실행 제어 · runner와 폴더 구조](03-runner-layout.md) ·