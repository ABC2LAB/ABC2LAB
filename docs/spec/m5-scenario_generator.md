# 시나리오 생성기 · scenario_generator

[ABC2LAB_인터페이스_명세서_v0.1](00-index.md) · [공통 계약 · 파일 형식과 전달 규칙](02-common-contract.md) · [실행 제어 · runner와 폴더 구조](03-runner-layout.md)

<aside>
🧩

- 구현 폴더: `modules/scenario_generator/`.
- 공개 operation: `generate`.
- 담당 범위: 검증 후보와 원본 관찰을 바탕으로 LLM이 계정·자원·요청 순서·동적 값·판정 조건을 포함한 재현 계획을 생성하도록 한다.
</aside>

**읽는 순서:** 독립 동작·수정 책임 → 입력 변화 기준 → 입력 → 구현할 일 → 출력 → 완료 기준 → 주의사항. 상세 JSON·중첩 레코드는 접힌 제목에서 확인한다.

## 독립 동작·수정 책임

**이 모듈 담당자의 전체 책임:** 명세에 맞는 입력을 받으면 입력 검증·변환·실제 처리·출력 변환·출력 검증·저장·오류 처리·설정·모듈별 의존성 선언·테스트·동작 확인을 자기 폴더 안에서 끝낸다. 실행·테스트는 팀 공통 Python과 루트 잠금 환경에서 수행한다. 외부 모듈의 내부 코드·공유 도구에 의존하지 않는다.

| 영역 | 변경·작성 책임 | 참조·사용 경계 |
| --- | --- | --- |
| 자기 구현·도구·타입·설정·의존성 선언·테스트 | `modules/scenario_generator/**`는 scenario_generator 담당 수정 | 파서·검증·저장·해시·경로·adapter도 자기 utils와 schemas에서 구현한다. 다른 모듈 코드를 import하지 않는다. |
| 입력 계약·입력 검증 | scenario_generator 담당이 자기 `schemas/input/`과 검증을 관리 | 입력 원본은 읽기 전용. 생산자 출력 계약과 일치시키고 원본 오류는 생산자에게 요청한다. |
| 자기 출력 계약·출력 검증 | scenario_generator 담당이 자기 `schemas/output/`·출력·근거·문서를 작성·검증 | 합의한 출력 Schema·필드 의미·ID·근거를 만족한 파일만 완료로 공개한다. |
| 입력·외부 참조 | 후보는 access_analyzer, 원본 수집 JSON·근거는 collector가 작성한다. 이 모듈이 명시된 입력 참조와 자기 모델·실행 설정을 검증한다. | 명시된 파일·근거·공개 창구만 사용한다. 다른 모듈 내부 구현·전역 가변 상태를 읽지 않는다. |
| 출력 변경·수신자 조율 | 변경 제안·출력 구현은 scenario_generator / 입력 대응은 아래 직접 소비자 | 소비자 내부 알고리즘은 알 필요가 없다. 그 파일을 실제 읽는 담당자와만 버전·필드·의미·변경 시점을 합의한다. |
| 계획·steps·bindings·확인 조건 | scenario_generator가 후보와 원본 근거에 따라 생성 | 생성된 계획은 verifier가 실행에 맞게 몰래 수정하지 않는다. 변경 계획은 새 산출물로 발행한다. |
| 실행 허용 | safety_policy가 독립 규칙으로 결정 | 자기 state_change 추정이나 LLM 문장을 실행 허가로 사용하지 않는다. |

**모듈 내부 경계:** 입력 adapter → 내부 처리 → 출력 adapter → 자기 출력 검증 → 원자적 저장 → 완료 통지. 내부 고도화가 계약을 유지하면 다른 담당자에게 수정을 요구하지 않는다.

**입력·출력 계약과 수정 책임:** [공통 계약 · 파일 형식과 전달 규칙](02-common-contract.md) · **독립 실행·모듈 폴더 구조:** [실행 제어 · runner와 폴더 구조](03-runner-layout.md)

## 공통 환경에서의 독립 개발

**개발 기준·관리자 초기 설정·환경 설치:** [개발 기준 · 환경 일원화와 GitHub 초기 설정](01-dev-standard.md)

- 같은 `.python-version`과 루트 `requirements.lock.txt`로 만든 저장소 루트 `.venv`에서 실행·테스트한다. 독립 검증은 자기 입력 fixture·공개 창구 대역으로 수행한다.
- `modules/scenario_generator/requirements.txt`에는 실제 사용하는 직접 의존성과 지원 버전을 선언한다. 추가·버전 변경은 GitHub 관리자에게 제안하고 후보 잠금 환경에서 자기 모듈을 검증한다.
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
- [ ]  후보 수·단계 수·계정·자원·동적 값의 변화에도 steps 순서·이전 단계 바인딩·참조 무결성을 검증한다.
- [ ]  생성 실패를 가짜 정상 계획으로 대체하지 않고 partial/failed와 미처리 후보를 추적한다.
- [ ]  허용된 빈 배열·null·partial, 잘못된 Schema/버전/ID/참조/해시를 구분한다. 입력 오류를 성공·정상 빈 결과로 숨기지 않는다.
- [ ]  자기 출력의 Schema·작성자·필수 필드·ID/근거 대응을 검증하고 자기 경로에 원자적으로 공개한다. 저장 실패를 완료로 알리지 않는다.
- [ ]  위 입력 변화와 의존성 실패를 자기 모듈 테스트에서 검증한다. 다른 모듈 실제 구현 대신 공개 입력 fixture·인터페이스 대역으로 독립 검증할 수 있다.
- [ ]  내부 최적화 이후에도 자기 입력·출력 계약 검증을 통과한다. 계약 변경 시 직접 소비자와 실제 출력 샘플의 수신 검증을 함께 확인한다.

## 입력

| 입력 파일·정보 | 작성·제공 주체 | 사용할 내용 |
| --- | --- | --- |
| vulnerability_candidates.json | `access_analyzer` | data.candidates의 candidate_id·actor 계정/역할·resource_ids·source_request_ids·workflow_id·expected_basis와 근거. |
| crawl_result.json 및 근거 파일 참조 | `collector / input_refs` | 선택한 source_request_id의 요청·응답, 계정·역할·session_ref와 본문 참조. 명시된 참조로 읽는다. |
| 실행 인자 / 자기 모델 설정 | `사용자 실행 인자` | 실행 식별 정보, 모델·프롬프트 설정과 신뢰된 대상 범위. 모델 설정은 configs 파일 없이 CLI 옵션으로 받는다(`--llm-provider replay\|ollama`, `--model-id`, `--base-url`, `--temperature`, `--seed`, `--timeout`). 신뢰된 대상 범위는 `crawl_result.target_url`의 origin이다. |

**입력 파일의 전체 필드:** [vulnerability_candidates.json 필드](m4-access_analyzer.md) · [crawl_result.json 필드](m1-collector.md).

## 구현할 일

1. 후보와 실제로 존재하는 원본 요청·계정·자원·근거를 대조한다.
2. LLM으로 사전조건, 요청 단계와 순서, 단계별 실행 계정·역할·세션 참조를 구성한다.
3. 이전 응답에서 추출할 동적 값과 다음 요청의 바인딩, 응답·자원 상태·최종 결과의 확인 조건을 명시한다.
4. 생성 결과의 타입·ID·순서·참조 대응을 확인하고 test_scenarios.json을 출력한다.

## 출력

| 작성 파일 | 생성 operation | 출력 변경 협의 대상 — 직접 소비자 |
| --- | --- | --- |
| `test_scenarios.json` | `generate` | safety_policy · verifier · reporter |

정상·부분 완료 파일의 `data` 필드는 아래와 같다. `status=failed`이면 `data=null`로 기록한다. 공통 메타데이터·상태 규칙은 [공통 파일 형식](02-common-contract.md)을 적용한다.

## 완료 기준

- [ ]  후보와 실제 원본 요청·계정·역할·자원·세션 참조를 대응시킨다.
- [ ]  단계 순서 0..N-1과 이전 단계에서만 가져오는 바인딩을 검증한다.
- [ ]  상태 확인용 HTTP 요청도 steps에 포함하고 실행 허용 결정은 Safety Policy에 맡긴다.

## 구현 주의사항

- candidate_id·source_request_id·account_id·role_id·session_ref가 원본과 대응해야 한다. ID가 존재하더라도 계정·역할 관계가 다르면 거절한다.
- steps.order는 0..N-1로 유일하게 정렬한다. 바인딩은 현재 단계보다 앞선 단계에서만 가져온다.
- 동적 값은 제한된 {binding_id} 치환, JSON Pointer 또는 헤더 추출로 표현한다. 조건과 바인딩을 임의 실행 코드로 만들지 않는다.
- request.parameters는 원본 요청의 해당 위치에 적용할 변경값이다. 일반 헤더·본문 형식은 원본에서 복원하고 인증·CSRF·동적 비밀값은 보호된 실행 컨텍스트에서 주입한다.
- Check는 조건 비교 규약이다. 상태 확인에 HTTP 요청이 필요하면 steps에 명시하여 Policy 예산과 실제 실행 근거에 포함한다.
- state_change는 생성기의 추정이다. 최종 안전 평가와 실행 허용은 safety_policy가 담당한다.
- 바인딩은 그 값을 **쓰는** 단계의 `bindings`에 두고 `source_step_id`로 앞 단계를 가리킨다. verifier는 단계마다 그 단계의 `bindings`만 읽어 `url_template`·`binding_ref`를 치환한다.
- `url_template`의 `{...}`는 `binding_id`만 쓴다. scheme·host·port는 `crawl_result.target_url`과 같아야 하고 주소 부분에는 바인딩·사용자 정보를 쓸 수 없다.
- 단계의 계정은 후보의 실행 계정 또는 기준 계정만 쓴다. `method`는 원본 요청과 같고 `body_ref`는 null이거나 원본 요청의 것이다.
- `scenario_id`(`"scenario_" + candidate_id`)·`candidate_id`·`expected_basis`는 프로그램이 원본 후보에서 채운다. LLM 초안은 `preconditions`·`steps`·`assertions`만 만든다. 후보 1개당 시나리오 1개다.
- **판정 조건 필수 규칙.** HTTP 상태 코드만으로 위반을 확정하지 않는다(m7). 다음을 만족하지 않는 초안은 버린다.
    - `preconditions`: steps에 쓰인 계정마다 `session_valid`(`subject_ref`=account_id, `operator=eq`, `expected=true`). `exists`는 세션이 무효여도 참이 되므로 쓰지 않는다.
    - `assertions`: 실행 계정 단계 하나에 `response_status`와 `response_json`이 함께 있다.
    - `resource_state`·`resource_owner`·`baseline_match`는 쓸 수 있지만 verifier가 아직 평가하지 않아(판단불가) 필수 조건을 대신하지 못한다. 이 세 종류의 `subject_ref`·`selector` 규약은 verifier와 합의 전이다.
- LLM 입력에는 헤더·쿠키·응답 본문·근거 파일 경로·민감 파라미터 값을 넣지 않는다. LLM 초안은 데이터로 보고 위 규칙과 Schema로 전부 다시 검증한다.

## 입력·출력 JSON 필드

### test_scenarios.json

- 고정 값: `artifact_type=test_scenarios`, `producer=scenario_generator`.
- 예상 Schema 경로: `modules/scenario_generator/schemas/output/test_scenarios.schema.json`.
- 예상 출력 fixture 경로: `modules/scenario_generator/tests/fixtures/runs/run_demo_001/artifacts/iteration-000/scenario_generator/test_scenarios.json`.

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `data.scenarios` | `array<Scenario>` | 필수 | 생성한 다단계 시나리오 목록. |
| `data.model_info` | `ModelInfo / null` | 필수 | 시나리오 생성 LLM 정보. |

## 중첩 레코드 필드

출력 배열·객체의 항목마다 아래 필수 필드를 적용한다. `properties`·`match_key` 등 명시된 JSON map은 확장 가능하고 일반 객체는 미정의 키를 거절한다.

### Scenario

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `scenario_id` | `string` | 필수 | 재현 시나리오 ID. |
| `candidate_id` | `string` | 필수 | 원본 검증 후보 ID. |
| `expected_basis` | `enum: rule, inferred, unknown` | 필수 | 원본 후보의 기대 조건 근거. |
| `preconditions` | `array<Check>` | 필수 | 실행 전 확인할 세션·자원·기준 상태 조건. 하나라도 거짓·판단불가면 verifier는 재현 여부를 판정하지 않는다(indeterminate). |
| `steps` | `array<ScenarioStep>` | 필수 | 순서가 있는 재현 요청 목록. 비어 있으면 안 된다. |
| `assertions` | `array<Check>` | 필수 | 위반 재현 여부를 판단할 응답·상태·최종 결과 조건. **모두 참이면 위반이 재현된 것**으로 읽는다. 비어 있으면 안 된다. |

### ScenarioStep

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `step_id` | `string` | 필수 | 시나리오 내 단계 ID. |
| `order` | `integer` | 필수 | 0부터 시작하는 요청 실행 순서. |
| `source_request_id` | `string` | 필수 | 기반 수집 요청 ID. 존재하는 요청을 근거로 생성한다. |
| `account_id` | `string` | 필수 | 이 단계 실행 계정 ID. |
| `role_id` | `string` | 필수 | 실행 계정 역할 ID. |
| `session_ref` | `string / null` | 필수 | 실행에 필요한 세션 참조. |
| `request` | `RequestPlan` | 필수 | 실행 가능한 요청 계획. |
| `bindings` | `array<Binding>` | 필수 | 선행 응답에서 추출할 전달값 정의. |
| `state_change` | `enum: none, possible, expected, unknown` | 필수 | 생성기가 추정한 상태 변경 가능성. Policy가 독립적으로 재평가한다. |

### RequestPlan

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `method` | `string` | 필수 | 재현할 HTTP Method. |
| `url_template` | `string` | 필수 | 대상 URL 템플릿. {binding_id}만 제한적으로 치환하고 실행 전 scope를 다시 검사한다. |
| `parameters` | `array<ParameterValue>` | 필수 | 리터럴 또는 동적 바인딩 파라미터. |
| `body_ref` | `EvidenceRef / null` | 필수 | 필요한 수집 요청 본문 참조. 공유본의 비밀값으로 인증하지 않는다. |

### ParameterValue

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `name` | `string` | 필수 | 변경·지정할 파라미터 이름. |
| `location` | `enum: path, query, body` | 필수 | 변경 위치. 인증 헤더·쿠키 변경은 이 필드로 허용하지 않는다. |
| `value` | `JsonValue / null` | 필수 | 리터럴 값. binding_ref를 사용하면 null. |
| `binding_ref` | `string / null` | 필수 | 이전 단계 추출값 참조. 리터럴 값을 사용하면 null. |

### Binding

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `binding_id` | `string` | 필수 | 동적 전달값 ID. |
| `source_step_id` | `string` | 필수 | 이 값을 추출할 선행 단계 ID. |
| `source_part` | `enum: response_body, response_header` | 필수 | 응답 본문 또는 응답 헤더. |
| `selector` | `string` | 필수 | 본문은 JSON Pointer, 헤더는 헤더 이름. 실행 코드가 아니다. |

### Check

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `check_id` | `string` | 필수 | 사전조건·판정 조건 ID. |
| `kind` | `enum: session_valid, response_status, response_json, resource_state, resource_owner, baseline_match` | 필수 | 검증기가 구현한 조건 종류. |
| `subject_ref` | `string` | 필수 | 세션·단계·자원 등 검사 대상 참조. `response_status`·`response_json`은 steps의 `step_id`, `session_valid`는 steps에 쓰인 `account_id`. |
| `selector` | `string / null` | 필수 | JSON Pointer 또는 검사 위치. 필요 없으면 null. |
| `operator` | `enum: exists, eq, ne, in, contains` | 필수 | 허용된 비교 연산. |
| `expected` | `JsonValue` | 필수 | 기대 비교값. |

**재사용하는 계약 필드:** [ArtifactRef](02-common-contract.md), [ErrorItem](02-common-contract.md), [EvidenceRef](02-common-contract.md), [ModelInfo](02-common-contract.md), [RuntimeMetrics](02-common-contract.md).

## 변경 이력

필드·타입·enum(출력 계약)은 v0.1 그대로다. 아래는 의미·검증 규칙을 구현에 맞춰 적은 것이다.

| 날짜 | 변경 | 근거 |
| --- | --- | --- |
| 2026-10-08 | 판정 조건 필수 규칙(`session_valid` 사전조건, 실행 계정 단계의 `response_status`+`response_json`), `assertions` 의미, `Check.subject_ref` 규약, 바인딩 위치, 초안 검증 규칙, 모델 설정을 CLI 옵션으로 받는 것을 명시 | #40(판정 조건 강화), verifier Check 평가 구현(#38) |

---

[ABC2LAB_인터페이스_명세서_v0.1](00-index.md) · [공통 계약 · 파일 형식과 전달 규칙](02-common-contract.md) · [실행 제어 · runner와 폴더 구조](03-runner-layout.md) ·