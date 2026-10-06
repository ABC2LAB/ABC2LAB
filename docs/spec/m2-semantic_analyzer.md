# 의미 분석기 · semantic_analyzer

[ABC2LAB_인터페이스_명세서_v0.1](00-index.md) · [공통 계약 · 파일 형식과 전달 규칙](02-common-contract.md) · [실행 제어 · runner와 폴더 구조](03-runner-layout.md)

<aside>
🧠

- 구현 폴더: `modules/semantic_analyzer/`.
- 공개 operation: `analyze`.
- 담당 범위: 수집 요청 정규화, LLM 의미 추론, 자원·행위·역할 관계와 업무 흐름 분석 결과 생성을 담당한다.
</aside>

**읽는 순서:** 독립 동작·수정 책임 → 입력 변화 기준 → 입력 → 구현할 일 → 출력 → 완료 기준 → 주의사항. 상세 JSON·중첩 레코드는 접힌 제목에서 확인한다.

## 독립 동작·수정 책임

**이 모듈 담당자의 전체 책임:** 명세에 맞는 입력을 받으면 입력 검증·변환·실제 처리·출력 변환·출력 검증·저장·오류 처리·설정·모듈별 의존성 선언·테스트·동작 확인을 자기 폴더 안에서 끝낸다. 실행·테스트는 팀 공통 Python과 루트 잠금 환경에서 수행한다. 외부 모듈의 내부 코드·공유 도구에 의존하지 않는다.

| 영역 | 변경·작성 책임 | 참조·사용 경계 |
| --- | --- | --- |
| 자기 구현·도구·타입·설정·의존성 선언·테스트 | `modules/semantic_analyzer/**`는 semantic_analyzer 담당 수정 | 파서·검증·저장·해시·경로·adapter도 자기 utils와 schemas에서 구현한다. 다른 모듈 코드를 import하지 않는다. |
| 입력 계약·입력 검증 | semantic_analyzer 담당이 자기 `schemas/input/`과 검증을 관리 | 입력 원본은 읽기 전용. 생산자 출력 계약과 일치시키고 원본 오류는 생산자에게 요청한다. |
| 자기 출력 계약·출력 검증 | semantic_analyzer 담당이 자기 `schemas/output/`·출력·근거·문서를 작성·검증 | 합의한 출력 Schema·필드 의미·ID·근거를 만족한 파일만 완료로 공개한다. |
| 입력·외부 참조 | collector가 작성한 수집 JSON·근거를 읽는다. 이 모듈의 입력 Schema·내부 타입·파싱과 검증은 semantic_analyzer가 책임진다. | 명시된 파일·근거·공개 창구만 사용한다. 다른 모듈 내부 구현·전역 가변 상태를 읽지 않는다. |
| 출력 변경·수신자 조율 | 변경 제안·출력 구현은 semantic_analyzer / 입력 대응은 아래 직접 소비자 | 소비자 내부 알고리즘은 알 필요가 없다. 그 파일을 실제 읽는 담당자와만 버전·필드·의미·변경 시점을 합의한다. |
| 관찰 ID와 분석 노드/업무 흐름 | 원본 ID는 계승하고 새 분석 레코드는 자기 명세에 따라 생성 | KG의 driver·저장 스키마를 맞추려고 수집 원본을 수정하지 않는다. |
| 모델·프롬프트·정규화·내부 추론 모델 | semantic_analyzer 내부에서 관리 | 관찰/추론/검증 basis와 공개 노드·관계·워크플로 의미를 유지한다. |

**모듈 내부 경계:** 입력 adapter → 내부 처리 → 출력 adapter → 자기 출력 검증 → 원자적 저장 → 완료 통지. 내부 고도화가 계약을 유지하면 다른 담당자에게 수정을 요구하지 않는다.

**입력·출력 계약과 수정 책임:** [공통 계약 · 파일 형식과 전달 규칙](02-common-contract.md) · **독립 실행·모듈 폴더 구조:** [실행 제어 · runner와 폴더 구조](03-runner-layout.md)

## 공통 환경에서의 독립 개발

**개발 기준·관리자 초기 설정·환경 설치:** [개발 기준 · 환경 일원화와 GitHub 초기 설정](01-dev-standard.md)

- 같은 `.python-version`과 루트 `requirements.lock.txt`로 만든 저장소 루트 `.venv`에서 실행·테스트한다. 독립 검증은 자기 입력 fixture·공개 창구 대역으로 수행한다.
- `modules/semantic_analyzer/requirements.txt`에는 실제 사용하는 직접 의존성과 지원 버전을 선언한다. 추가·버전 변경은 GitHub 관리자에게 제안하고 후보 잠금 환경에서 자기 모듈을 검증한다.
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
- [ ]  요청·계정·자원의 값과 개수가 바뀌어도 정규화·의미 추론 후 같은 공개 구조를 출력한다.
- [ ]  LLM 형식 오류·근거 부족을 검증하고 부분 결과/실패를 기록한다. Ground Truth는 사용하지 않는다.
- [ ]  허용된 빈 배열·null·partial, 잘못된 Schema/버전/ID/참조/해시를 구분한다. 입력 오류를 성공·정상 빈 결과로 숨기지 않는다.
- [ ]  자기 출력의 Schema·작성자·필수 필드·ID/근거 대응을 검증하고 자기 경로에 원자적으로 공개한다. 저장 실패를 완료로 알리지 않는다.
- [ ]  위 입력 변화와 의존성 실패를 자기 모듈 테스트에서 검증한다. 다른 모듈 실제 구현 대신 공개 입력 fixture·인터페이스 대역으로 독립 검증할 수 있다.
- [ ]  내부 최적화 이후에도 자기 입력·출력 계약 검증을 통과한다. 계약 변경 시 직접 소비자와 실제 출력 샘플의 수신 검증을 함께 확인한다.

## 입력

| 입력 파일·정보 | 작성·제공 주체 | 사용할 내용 |
| --- | --- | --- |
| crawl_result.json | `collector` | data.roles·accounts·pages·actions·requests와 요청·응답의 evidence_refs. |
| 실행 인자 / 자기 모델 설정 | `사용자 실행 인자 / modules 내 자기 configs` | 실행 식별 정보, 의미 분석 모델과 프롬프트 설정. |

**입력 파일의 전체 필드:** [crawl_result.json 필드](m1-collector.md).

## 구현할 일

1. 원본 request_id·account_id·role_id를 보존하면서 Endpoint·Parameter를 정규화한다.
2. LLM으로 요청의 자원·행위 의미와 역할 관계를 추론한다.
3. User·Role·Page·Action·Endpoint·Parameter·Resource 노드와 관계, 업무 단계·의존 조건을 구성한다.
4. 관찰·추론 출처와 근거를 연결하고 semantic_analysis.json을 생성한다.

## 출력

| 작성 파일 | 생성 operation | 출력 변경 협의 대상 — 직접 소비자 |
| --- | --- | --- |
| `semantic_analysis.json` | `analyze` | knowledge_graph · reporter(개발 평가) |

정상·부분 완료 파일의 `data` 필드는 아래와 같다. `status=failed`이면 `data=null`로 기록한다. 공통 메타데이터·상태 규칙은 [공통 파일 형식](02-common-contract.md)을 적용한다.

## 완료 기준

- [ ]  원본 요청·계정·역할 ID와 근거 참조를 보존한다.
- [ ]  nodes·relationships·workflows를 필드 계약에 맞게 출력한다.
- [ ]  observed·inferred·verified를 구분하며 Ground Truth를 분석 입력에 사용하지 않는다.

## 구현 주의사항

- basis=observed·inferred·verified를 구분한다. LLM의 추론을 관찰된 사실이나 검증된 접근으로 바꾸어 기록하지 않는다.
- 관찰 접근인 ACCESS와 정상 허용 정책은 구분한다. 관찰한 업무 순서를 반드시 지켜야 하는 업무 정책으로 단정하지 않는다.
- User 계정 ID와 Role ID를 분리하고, 원본 요청 ID를 새 ID로 교체하지 않는다.
- ground_truth.json을 읽거나 프롬프트에 넣지 않는다. 인증·비밀값을 제거한 데이터와 명시된 근거만 사용한다.

## 입력·출력 JSON 필드

### semantic_analysis.json

- 고정 값: `artifact_type=semantic_analysis`, `producer=semantic_analyzer`.
- 예상 Schema 경로: `modules/semantic_analyzer/schemas/output/semantic_analysis.schema.json`.
- 예상 출력 fixture 경로: `modules/semantic_analyzer/tests/fixtures/runs/run_demo_001/artifacts/iteration-000/semantic_analyzer/semantic_analysis.json`.

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `data.normalized_requests` | `array<NormalizedRequest>` | 필수 | 정규화한 요청과 기능·자원 의미. |
| `data.nodes` | `array<GraphNode>` | 필수 | 적재할 노드 목록. |
| `data.relationships` | `array<GraphEdge>` | 필수 | 적재할 관계 목록. |
| `data.workflows` | `array<Workflow>` | 필수 | 정상 업무 흐름과 단계 의존 관계 분석 결과. |
| `data.model_info` | `ModelInfo / null` | 필수 | 의미 분석에 사용한 LLM 정보. 미사용이면 null. |

## 중첩 레코드 필드

출력 배열·객체의 항목마다 아래 필수 필드를 적용한다. `properties`·`match_key` 등 명시된 JSON map은 확장 가능하고 일반 객체는 미정의 키를 거절한다.

### ParameterDefinition

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `parameter_id` | `string` | 필수 | 정규화한 파라미터 ID. |
| `name` | `string` | 필수 | 정규화한 파라미터 이름. |
| `location` | `enum: path, query, body, header, cookie` | 필수 | 파라미터 위치. |
| `value_type` | `enum: string, number, boolean, object, array, null, unknown` | 필수 | 관찰·정규화한 값 타입. |
| `is_sensitive` | `boolean` | 필수 | 비밀 파라미터인지 여부. |

### NormalizedRequest

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `request_id` | `string` | 필수 | 원본 수집 요청 ID. 새 ID로 바꾸지 않는다. |
| `endpoint_id` | `string` | 필수 | 정규화한 Endpoint 노드 ID. |
| `method` | `string` | 필수 | 정규화한 대문자 HTTP Method. |
| `path_template` | `string` | 필수 | 정규화한 경로. 예: /api/orders/{order_id}. |
| `account_id` | `string` | 필수 | 원본 실행 계정 ID. |
| `role_id` | `string` | 필수 | 원본 실행 역할 ID. |
| `action_meaning` | `string` | 필수 | 추론한 행위 의미. 예: read_order, pay_order. |
| `resource_ids` | `array<string>` | 필수 | 연결된 Resource 노드 ID. |
| `parameters` | `array<ParameterDefinition>` | 필수 | 정규화한 파라미터 정의. |
| `basis` | `enum: observed, inferred, verified` | 필수 | 관찰 사실인지 추론인지 출처 구분. |
| `evidence_refs` | `array<EvidenceRef>` | 필수 | 정규화·추론의 근거 참조. |

### GraphNode

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `node_id` | `string` | 필수 | KG 노드 ID. 동일 진단에서 안정적으로 유지한다. |
| `node_type` | `enum: User, Role, Page, Action, Endpoint, Parameter, Resource` | 필수 | RFP의 주요 노드 종류. |
| `properties` | `map<string, JsonValue>` | 필수 | JSON으로 표현 가능한 노드 속성. Neo4j 객체를 직접 넣지 않는다. |
| `basis` | `enum: observed, inferred, verified` | 필수 | observed=관찰, inferred=추론, verified=검증 결과. |
| `evidence_refs` | `array<EvidenceRef>` | 필수 | 노드 속성의 근거 참조. |

### GraphEdge

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `relationship_id` | `string` | 필수 | KG 관계 ID. 중복 적재 방지 키. |
| `source_id` | `string` | 필수 | 시작 노드 ID. |
| `target_id` | `string` | 필수 | 끝 노드 ID. |
| `relation_type` | `enum: HAS_ROLE, ACCESS, CALL, USE, REFERENCE, OWNS, PRECEDES, REQUIRES, VERIFIED_ACCESS, VERIFIED_DENIAL` | 필수 | 관계 종류. 새 종류 추가는 계약 버전 변경 대상. |
| `properties` | `map<string, JsonValue>` | 필수 | 행위·조건 등 관계 속성. |
| `basis` | `enum: observed, inferred, verified` | 필수 | 관찰·추론·검증 결과를 구분하는 출처. |
| `evidence_refs` | `array<EvidenceRef>` | 필수 | 관계의 근거 참조. |

### WorkflowStep

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `step_id` | `string` | 필수 | 업무 흐름 내부 단계 ID. |
| `order` | `integer` | 필수 | 관찰·추론한 단계 순서. |
| `action` | `string` | 필수 | 업무 행위 의미. |
| `request_ids` | `array<string>` | 필수 | 이 단계와 연결된 원본 요청 ID. |

### WorkflowDependency

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `before_step_id` | `string` | 필수 | 선행 단계 ID. |
| `after_step_id` | `string` | 필수 | 후행 단계 ID. |
| `condition` | `string` | 필수 | 단계 사이의 조건 설명. 실행 코드가 아니다. |
| `basis` | `enum: observed, inferred, verified` | 필수 | 순서·조건의 관찰 또는 추론 출처. |
| `evidence_refs` | `array<EvidenceRef>` | 필수 | 조건을 뒷받침하는 근거 참조. |

### Workflow

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `workflow_id` | `string` | 필수 | 업무 흐름 ID. |
| `name` | `string` | 필수 | 업무 흐름 표시명. |
| `role_ids` | `array<string>` | 필수 | 관찰된 실행 역할 목록. |
| `steps` | `array<WorkflowStep>` | 필수 | 업무 단계 목록. |
| `dependencies` | `array<WorkflowDependency>` | 필수 | 단계 순서·조건 목록. |
| `basis` | `enum: observed, inferred, verified` | 필수 | 흐름 전체의 관찰·추론 출처. |
| `evidence_refs` | `array<EvidenceRef>` | 필수 | 업무 흐름 분석 근거 참조. |

**재사용하는 계약 필드:** [ArtifactRef](02-common-contract.md), [ErrorItem](02-common-contract.md), [EvidenceRef](02-common-contract.md), [ModelInfo](02-common-contract.md), [RuntimeMetrics](02-common-contract.md).

---

[ABC2LAB_인터페이스_명세서_v0.1](00-index.md) · [공통 계약 · 파일 형식과 전달 규칙](02-common-contract.md) · [실행 제어 · runner와 폴더 구조](03-runner-layout.md) ·