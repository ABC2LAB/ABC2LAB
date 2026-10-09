# 재현·검증기 · verifier

[ABC2LAB_인터페이스_명세서_v0.1](00-index.md) · [공통 계약 · 파일 형식과 전달 규칙](02-common-contract.md) · [실행 제어 · runner와 폴더 구조](03-runner-layout.md)

<aside>
🧪

- 구현 폴더: `modules/verifier/`.
- 공개 operation: `verify`.
- 담당 범위: 허용된 재현 계획만 대상 웹에 실행하고 실제 요청·응답·상태 근거를 비교한다. 정책 미실행·위반 재현·미재현·판단불가를 구분하여 기록한다.
</aside>

**읽는 순서:** 독립 동작·수정 책임 → 입력 변화 기준 → 입력 → 구현할 일 → 출력 → 완료 기준 → 주의사항. 상세 JSON·중첩 레코드는 접힌 제목에서 확인한다.

## 독립 동작·수정 책임

**이 모듈 담당자의 전체 책임:** 명세에 맞는 입력을 받으면 입력 검증·변환·실제 처리·출력 변환·출력 검증·저장·오류 처리·설정·모듈별 의존성 선언·테스트·동작 확인을 자기 폴더 안에서 끝낸다. 실행·테스트는 팀 공통 Python과 루트 잠금 환경에서 수행한다. 외부 모듈의 내부 코드·공유 도구에 의존하지 않는다.

| 영역 | 변경·작성 책임 | 참조·사용 경계 |
| --- | --- | --- |
| 자기 구현·도구·타입·설정·의존성 선언·테스트 | `modules/verifier/**`는 verifier 담당 수정 | 파서·검증·저장·해시·경로·adapter도 자기 utils와 schemas에서 구현한다. 다른 모듈 코드를 import하지 않는다. |
| 입력 계약·입력 검증 | verifier 담당이 자기 `schemas/input/`과 검증을 관리 | 입력 원본은 읽기 전용. 생산자 출력 계약과 일치시키고 원본 오류는 생산자에게 요청한다. |
| 자기 출력 계약·출력 검증 | verifier 담당이 자기 `schemas/output/`·출력·근거·문서를 작성·검증 | 합의한 출력 Schema·필드 의미·ID·근거를 만족한 파일만 완료로 공개한다. |
| 입력·외부 참조 | 계획은 scenario_generator, 판정은 safety_policy, 수집 원본·세션 창구는 collector가 제공한다. verifier가 이 입력과 자기 실행 인자를 직접 검증한다. | 명시된 파일·근거·공개 창구만 사용한다. 다른 모듈 내부 구현·전역 가변 상태를 읽지 않는다. |
| 출력 변경·수신자 조율 | 변경 제안·출력 구현은 verifier / 입력 대응은 아래 직접 소비자 | 소비자 내부 알고리즘은 알 필요가 없다. 그 파일을 실제 읽는 담당자와만 버전·필드·의미·변경 시점을 합의한다. |
| 제한적 재현·결과·검증 근거 | verifier가 허용된 계획의 실제 실행과 결과를 작성 | 계획·Policy·인증 원본을 수정하지 않는다. 공개 창구로 세션을 사용하고 만료를 보고한다. |
| KG 갱신 요청 | verifier가 실제 근거가 있는 graph_updates를 자기 결과 파일에 담는다. | DB 반영·중복 방지·revision 갱신은 knowledge_graph가 수행한다. verifier는 DB를 직접 갱신하지 않는다. |

**모듈 내부 경계:** 입력 adapter → 내부 처리 → 출력 adapter → 자기 출력 검증 → 원자적 저장 → 완료 통지. 내부 고도화가 계약을 유지하면 다른 담당자에게 수정을 요구하지 않는다.

**입력·출력 계약과 수정 책임:** [공통 계약 · 파일 형식과 전달 규칙](02-common-contract.md) · **독립 실행·모듈 폴더 구조:** [실행 제어 · runner와 폴더 구조](03-runner-layout.md)

## 공통 환경에서의 독립 개발

**개발 기준·관리자 초기 설정·환경 설치:** [개발 기준 · 환경 일원화와 GitHub 초기 설정](01-dev-standard.md)

- 같은 `.python-version`과 루트 `requirements.lock.txt`로 만든 저장소 루트 `.venv`에서 실행·테스트한다. 독립 검증은 자기 입력 fixture·공개 창구 대역으로 수행한다.
- `modules/verifier/requirements.txt`에는 실제 사용하는 직접 의존성과 지원 버전을 선언한다. 추가·버전 변경은 GitHub 관리자에게 제안하고 후보 잠금 환경에서 자기 모듈을 검증한다.
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
- [ ]  단계·계정·바인딩·응답 변화에 실제 실행 근거로 판정하고 HTTP 200만으로 확정하지 않는다.
- [ ]  계획/Policy 불일치·세션 만료·block·require_approval에서는 허용되지 않은 요청을 보내지 않고 미검증 사유를 보존한다.
- [ ]  허용된 빈 배열·null·partial, 잘못된 Schema/버전/ID/참조/해시를 구분한다. 입력 오류를 성공·정상 빈 결과로 숨기지 않는다.
- [ ]  자기 출력의 Schema·작성자·필수 필드·ID/근거 대응을 검증하고 자기 경로에 원자적으로 공개한다. 저장 실패를 완료로 알리지 않는다.
- [ ]  위 입력 변화와 의존성 실패를 자기 모듈 테스트에서 검증한다. 다른 모듈 실제 구현 대신 공개 입력 fixture·인터페이스 대역으로 독립 검증할 수 있다.
- [ ]  내부 최적화 이후에도 자기 입력·출력 계약 검증을 통과한다. 계약 변경 시 직접 소비자와 실제 출력 샘플의 수신 검증을 함께 확인한다.

## 입력

| 입력 파일·정보 | 작성·제공 주체 | 사용할 내용 |
| --- | --- | --- |
| test_scenarios.json | `scenario_generator` | data.scenarios의 원본 candidate_id, 단계·계정·세션·요청·바인딩·사전조건·assertions, resource_ids(검증 관계 `target_id`의 출처). |
| safety_decisions.json | `safety_policy` | data.scenarios_sha256·Policy 버전과 decisions의 scenario_id·decision_id·판정·범위·limits. |
| crawl_result.json / 세션 공개 창구 | `collector / 명시된 참조` | 원본 요청 형식, 계정·역할·session_ref와 보호된 실행 컨텍스트. |
| 실행 인자 / 기준 KG revision | `사용자 실행 인자 / 후보·계획 출처 참조` | 신뢰된 실행·경로 범위와 시나리오의 기준 graph_revision. |

**입력 파일의 전체 필드:** [test_scenarios.json 필드](m5-scenario_generator.md) · [safety_decisions.json 필드](m6-safety_policy.md) · [crawl_result.json 필드](m1-collector.md).

## 구현할 일

1. 계획 파일 해시, 시나리오·Policy 판정 ID, 계정·역할·세션 대응을 검사한다.
2. block·require_approval은 요청을 보내지 않고 steps=[]인 미실행 결과를 만든다.
3. allow는 세션·사전조건을 확인한 뒤 선언된 순서와 바인딩에 따라 제한적으로 요청을 실행한다.
4. 실제 응답·자원 내용·상태·업무 결과를 비교하고 전송 요청·수신 응답 근거를 기록한다.
5. 시나리오별 결과와 실제 검증이 뒷받침하는 graph_updates를 verification_results.json에 함께 작성한다.
6. 검증·저장한 같은 결과 파일의 경로를 완료 응답으로 알린다. reporter와 knowledge_graph는 이 파일을 읽기 전용으로 받는다.

## 출력

| 작성 파일 | 생성 operation | 출력 변경 협의 대상 — 직접 소비자 |
| --- | --- | --- |
| `verification_results.json` | `verify` | knowledge_graph · reporter |

정상·부분 완료 파일의 `data` 필드는 아래와 같다. `status=failed`이면 `data=null`로 기록한다. 공통 메타데이터·상태 규칙은 [공통 파일 형식](02-common-contract.md)을 적용한다.

## 완료 기준

- [ ]  계획 해시·Policy 판정·계정·역할·세션 대응을 확인하고 allow만 실행한다.
- [ ]  block·require_approval은 요청 전송 없이 미실행 사유와 빈 steps로 보존한다.
- [ ]  실제 요청·응답 근거와 결과 분류를 기록하고, 검증 근거가 있는 graph_updates만 같은 출력 파일에 담는다.

## 구현 주의사항

- 실행 직전 계획의 정확한 해시가 Policy.scenarios_sha256과 같아야 한다. 계획·판정 파일을 읽는 것과 실행 허가 검증을 함께 구현한다.
- 허용 origin·계정·요청 수·시간·상태 변경 제한과 사용자가 제공한 실행 인자의 경로 범위를 실행 중에도 강제한다. 리다이렉트·재시도·바인딩 이후 URL도 검사한다.
- HTTP 200이나 본문 유사도만으로 취약점을 확정하지 않는다. 정상 기준·자원 내용·상태 변화·최종 업무 결과를 함께 확인한다.
- 세션 만료·CSRF·통신·동적 상태 오류는 위반 재현과 구분한다. session_ref가 있어도 실제 유효성을 다시 확인한다. 세션 창구는 자기 adapter와 같은 계약의 테스트 대역으로 사용하며 collector 내부 코드·브라우저/세션 객체에 의존하지 않는다. 접근 방식·요청/응답·오류·만료·대여/반납·종료 규약은 collector와 연결 구현 전에 확정한다.
- 완료 단계의 request_ref에는 바인딩 적용 후 실제 요청, response_ref에는 실제 응답을 보존한다. 미전송 단계는 두 참조와 request_url을 null로 기록한다.
- graph_updates는 basis=verified와 실제 실행 근거를 가진 관찰만 포함한다. 미실행·판단불가를 접근 성공 관계로 만들지 않는다.
- verification_results.json은 이 모듈이 한 번 작성한다. KG와 리포터는 읽는 수신자이며, 별도 공용 갱신 JSON을 추가하지 않는다.

## 입력·출력 JSON 필드

### verification_results.json

- 고정 값: `artifact_type=verification_results`, `producer=verifier`, `schema_version=0.2.0`(전원 동시 전환).
- 예상 Schema 경로: `modules/verifier/schemas/output/verification_results.schema.json`.
- 예상 출력 fixture 경로: `modules/verifier/tests/fixtures/runs/run_demo_001/artifacts/iteration-000/verifier/verification_results.json`.

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `data.source_graph_revision` | `integer` | 필수 | 시나리오·후보의 기준 KG revision. |
| `data.scenarios_sha256` | `string` | 필수 | 실행 직전 검증한 시나리오 파일 해시. |
| `data.results` | `array<VerificationItem>` | 필수 | 시나리오별 결과. 정책 미실행 후보도 포함한다. |
| `data.graph_updates` | `GraphUpdates` | 필수 | 동일 파일 안의 KG 반영 데이터. 별도 공용 갱신 파일을 만들지 않는다. |

### 재현 결과와 실행 상태의 대응

| result | execution_status | 의미와 리포트 처리 |
| --- | --- | --- |
| `success` | `completed` | 유효 실행에서 위반 재현 조건 충족. 기대 정책·근거도 충분하면 confirmed, 부족하면 suspected/indeterminate. |
| `failure` | `completed` | 이 시나리오에서 유효 검증 후 위반 미재현. not_confirmed이며 전체 웹의 취약점 없음을 뜻하지 않음. |
| `blocked` | `not_executed` | Policy block·require_approval 때문에 요청 미전송. policy_blocked 또는 approval_pending. |
| `indeterminate` | `error / completed / not_executed` | 세션·통신·사전조건·근거 부족 등으로 판단불가. 취약점 없음·오탐으로 합치지 않음. |

## 중첩 레코드 필드

출력 배열·객체의 항목마다 아래 필수 필드를 적용한다. `properties`·`match_key` 등 명시된 JSON map은 확장 가능하고 일반 객체는 미정의 키를 거절한다.

### VerificationItem

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `verification_id` | `string` | 필수 | 검증 결과 ID. KG 반영 중복 방지 키. |
| `candidate_id` | `string` | 필수 | 검증 후보 ID. |
| `scenario_id` | `string` | 필수 | 시나리오 ID. |
| `decision_id` | `string` | 필수 | 따른 Policy 판정 ID. |
| `policy_decision` | `enum: allow, block, require_approval` | 필수 | 원본 Policy 판정. 결과와 함께 사유를 추적한다. |
| `execution_status` | `enum: completed, not_executed, error` | 필수 | 실제 실행 완료·미실행·실행 오류 구분. |
| `result` | `enum: success, failure, blocked, indeterminate` | 필수 | success=위반 재현 조건 충족, failure=유효 검증에서 미재현, blocked=정책 미실행, indeterminate=판단불가. |
| `reason` | `string` | 필수 | 결과 사유. 실패·미실행을 사이트 전체의 취약점 없음으로 일반화하지 않는다. |
| `steps` | `array<ExecutedStep>` | 필수 | 실제로 수행하거나 오류를 확인한 단계 기록. 정책 미실행이면 빈 배열. |
| `evidence_refs` | `array<EvidenceRef>` | 필수 | 최종 판정 근거 참조. |
| `errors` | `array<ErrorItem>` | 필수 | 세션 만료·통신 실패 등 판단불가 사유. |

### ExecutedStep

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `step_id` | `string` | 필수 | 원본 시나리오 단계 ID. |
| `status` | `enum: completed, error, skipped` | 필수 | 이 단계 실행 상태. |
| `request_url` | `string / null` | 필수 | 실제로 실행한 URL. 요청을 보내지 않았으면 null. |
| `response_status` | `integer / null` | 필수 | 응답 상태 코드. 응답이 없으면 null. |
| `request_ref` | `EvidenceRef / null` | 필수 | 동적 값 적용 후 실제 전송한 요청의 비밀값 제거 근거. 미전송이면 null. |
| `response_ref` | `EvidenceRef / null` | 필수 | 실제로 받은 응답 근거. 미수신이면 null. |
| `check_results` | `array<CheckResult>` | 필수 | 응답·상태 검사 결과. |
| `evidence_refs` | `array<EvidenceRef>` | 필수 | 실제 실행 근거 참조. |
| `errors` | `array<ErrorItem>` | 필수 | 실행 오류 목록. |

### CheckResult

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `check_id` | `string` | 필수 | 실행한 조건 ID. |
| `passed` | `boolean / null` | 필수 | 조건 충족 여부. 판단하지 못하면 null. |
| `observed` | `JsonValue` | 필수 | 비밀값을 제거한 실제 관찰값. |
| `description` | `string` | 필수 | 조건 평가 근거 설명. |

### GraphUpdates

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `source_verification_ids` | `array<string>` | 필수 | 이 갱신을 뒷받침하는 실행 검증 결과 ID. |
| `nodes` | `array<GraphNode>` | 필수 | 새로 확인한 노드. `basis=verified`·`evidence_refs` 1개 이상. `node_type`에 **Resource 금지**(verifier는 신규 Resource를 만들지 않고 앞 단계 node_id를 target으로 쓴다). |
| `relationships` | `array<VerificationRelationship>` | 필수 | 새로 확인한 검증 관계. 일반 GraphEdge(`source_id`)가 아니라 아래 VerificationRelationship이다. KG 입력 `verificationRelationship`과 같은 필드라 소비자 입력을 항상 통과한다. |

### VerificationRelationship

KG 입력(`modules/knowledge_graph/schemas/input/verification_results.schema.json`의 `verificationRelationship`)과 같은 필드·순서다. 일반 그래프 관계의 `source_id`(KG node_id)와 달리 source는 계정 원본 ID다.

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `relationship_id` | `string`(빈 문자열 금지) | 필수 | 검증 관계 ID. |
| `source_account_id` | `string`(빈 문자열 금지) | 필수 | 실제로 요청을 보낸(접근한) 계정의 원본 `account_id`. User node_id가 아니다. 출처는 그 결과 시나리오의 실행 계정 단계(`steps[].account_id`). KG가 같은 run/graph의 User(`properties.account_id`)로 해석해 연결한다. |
| `target_id` | `string`(빈 문자열 금지) | 필수 | 대상 Resource instance의 KG node_id. 출처는 그 결과 시나리오의 `Scenario.resource_ids`([m5](m5-scenario_generator.md) 0.2.0) 중 하나다. Resource 종류 이름·URL·상품 번호를 대신 넣지 않는다. |
| `relation_type` | `enum: VERIFIED_ACCESS, VERIFIED_DENIAL` | 필수 | 접근 재현 / 거부 확인. |
| `properties` | `object` (값은 JsonValue) | 필수 | 추가 속성 JSON map. |
| `basis` | `const: verified` | 필수 | 실제 실행으로 확인한 관계만. |
| `evidence_refs` | `array<EvidenceRef>` (1개 이상) | 필수 | 실제 실행 근거. KG 입력은 개수 제한이 없고 reporter 입력은 1개 이상이다. verifier는 1개 이상으로 둔다. |

- **`target_id` 경로:** access_analyzer `Candidate.resource_ids`(KG Resource instance node_id) → scenario_generator `Scenario.resource_ids`(순서·값 그대로 복사) → verifier 검증 관계 `target_id`. verifier는 값을 만들거나 형식을 해석하지 않는다.
- 이전 `source_id`, 두 source 필드의 동시 입력은 KG·reporter가 거절한다(자동 변환 없음).
- 현재 `graph_updates`는 빈 배열이다. 위 관계를 실제로 채우는 것은 PR3-c다.

**재사용하는 계약 필드:** [ArtifactRef](02-common-contract.md), [ErrorItem](02-common-contract.md), [EvidenceRef](02-common-contract.md), [GraphNode](m2-semantic_analyzer.md), [RuntimeMetrics](02-common-contract.md).

## 동작 규칙 (구현)

- **실행 허용**: `test_scenarios` 파일 SHA-256 == `safety_decisions.scenarios_sha256`이고 Policy 판정이 `allow`일 때만 전송한다. block·require_approval은 요청 없이 `blocked`(`steps=[]`). allow라도 계정·역할·세션·`effective_account_ids`가 안 맞으면 미실행 indeterminate.
- **execution_status**: 중단 없이 전부 전송=`completed`, 중단 시 전송 0건=`not_executed`·1건 이상=`error`(result 분류와 독립).
- **`max_redirects` 기본 0**: 로그인 리다이렉트를 자동으로 따라가면 최종 200을 접근 성공으로 오탐하므로 3xx를 그 단계 응답으로 기록한다(`configs/verifier.toml`에서 조정). 따라갈 때는 hop마다 `effective_origins` 재검사.
- **status-only 규칙**: 참인 assertion이 `response_status`·`session_valid`뿐이면(응답 내용 미확인) success 대신 indeterminate. 응답 내용을 본 조건이 하나 이상 참이어야 success.
- **result 분류**: precondition 중 거짓/판단불가 → indeterminate. 모두 참이면 assertion 하나라도 판단불가 → indeterminate, 모두 참 → success, 하나 이상 거짓 → failure.
- **CheckResult 부착**: subject가 단계면 그 단계에, 계정이면 그 계정이 처음 쓰인 단계에, 못 찾으면 첫 단계에. `observed`는 비밀 제거.
- **세션 공개 창구**(collector 소유): verifier는 Protocol로만 쓰고 collector 코드를 import하지 않는다(런너 주입). `session_valid` Check 의미는 "창구 세션 보유 + 만료 감지 없음"이며 실제 만료는 send 응답 신호로 잡는다. 창구 `lease` 로그인 요청은 세션 준비라 `limits.max_requests`에 세지 않는다. 규약은 [m1-collector](m1-collector.md) "세션 공개 창구".

## 변경 이력 (0.1.0 → 0.2.0)

출력 `verification_results.json`의 `schema_version`을 0.2.0으로 올렸다(전원 동시 전환). 필드 의미 변경은 아래뿐이고 나머지는 버전 상수만 바뀌었다.

- **`graph_updates` 제약을 KG 입력(0.2.0)에 맞춰 좁힘**: 관계 `relation_type`은 `VERIFIED_ACCESS`·`VERIFIED_DENIAL`만, 노드 `node_type`에 Resource 금지. `basis=verified`·`evidence_refs≥1`은 유지. 현재 graph_updates는 빈 배열이며 node_id 매핑은 PR3-c에서 채운다.
- 입력 `test_scenarios`·`safety_decisions`·`crawl_result`는 각 생산자 계약 0.2.0을 미러한다(필드 정의는 [m5](m5-scenario_generator.md)·[m6](m6-safety_policy.md)·[m1](m1-collector.md), 버전 0.2.0). **2026-10-10에 반영했다**(그 전까지 verifier 입력 사본은 0.1.0이었다). 생산자 출력 Schema와 title·description 외 같고, `test_scenarios`에는 `Scenario.resource_ids`가 들어 있다. `0.1.0` 입력은 묵시 변환 없이 `INPUT_CONTRACT_INVALID`(failed, 전송 0건)로 거절한다.
- **검증 관계 source를 `source_id` → `source_account_id`로 전환**(2026-10-10): `graph_updates.relationships`를 KG 입력 `verificationRelationship`과 같은 형태로 맞췄다(위 VerificationRelationship 표). 같은 0.2.0 안에서 KG·reporter와 동시 전환한 합의이며 버전은 올리지 않는다.

---

[ABC2LAB_인터페이스_명세서_v0.1](00-index.md) · [공통 계약 · 파일 형식과 전달 규칙](02-common-contract.md) · [실행 제어 · runner와 폴더 구조](03-runner-layout.md) ·