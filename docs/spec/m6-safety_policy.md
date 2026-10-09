# Safety Policy · safety_policy

[ABC2LAB_인터페이스_명세서_v0.1](00-index.md) · [공통 계약 · 파일 형식과 전달 규칙](02-common-contract.md) · [실행 제어 · runner와 폴더 구조](03-runner-layout.md)

<aside>
🛡️

- 구현 폴더: `modules/safety_policy/`.
- 공개 operation: `evaluate`.
- 담당 범위: LLM과 독립된 사전 정의 규칙으로 실행 허용·차단·승인 요청을 결정하고, 검증기가 강제할 범위·제한과 판정 근거를 내보낸다.
</aside>

**읽는 순서:** 독립 동작·수정 책임 → 입력 변화 기준 → 입력 → 구현할 일 → 출력 → 완료 기준 → 주의사항. 상세 JSON·중첩 레코드는 접힌 제목에서 확인한다.

## 현재 구현·연결 상태 — 2026-10-10

기준 커밋 `695b3c8`의 구현·공개 계약을 반영했다. PR #52·#53 병합 후 상태이며,
이번 저장소 문서 갱신이 노션 원본의 export 갱신을 의미하지는 않는다.

| 구분 | 현재 상태 |
| --- | --- |
| 독립 구현 | 공개 `evaluate`·CLI, 6개 assessment, 승인 기록 재평가·원자 저장 구현 완료 |
| 공개 계약 | `test_scenarios` 입력·`safety_decisions` 출력 `0.2.0`, 필수 Scenario.resource_ids 수용 완료 |
| 내부 설정 계약 | policy_config·approval_record는 각각 `0.1.0`; policy_version은 별도 안전 규칙 버전 |
| 소비자 호환 | Verifier·Reporter의 safety_decisions 입력 사본 `0.2.0` 동기화 및 공개 fixture Schema 수용 확인 |
| 독립 회귀 | Safety Policy `165 passed`; KG·Safety Policy·Reporter·Verifier 합동 `897 passed, 12 skipped` |
| 실제 전체 연결 | 미완료. 동일 run의 계획·판정·검증·리포트 연결 및 사용자 승인 창구 통합 필요 |

skip 12건은 이번에 활성화하지 않은 KG 실제 Neo4j 테스트다. 상세 구현 이력은
[모듈 README](../../modules/safety_policy/README.md)를 따른다. 계약 호환 확인과
독립 승인 fixture 검증을 실제 사용자 승인 수집·진위 확인 완료로 표시하지 않는다.

## 독립 동작·수정 책임

**이 모듈 담당자의 전체 책임:** 명세에 맞는 입력을 받으면 입력 검증·변환·실제 처리·출력 변환·출력 검증·저장·오류 처리·설정·모듈별 의존성 선언·테스트·동작 확인을 자기 폴더 안에서 끝낸다. 실행·테스트는 팀 공통 Python과 루트 잠금 환경에서 수행한다. 외부 모듈의 내부 코드·공유 도구에 의존하지 않는다.

| 영역 | 변경·작성 책임 | 참조·사용 경계 |
| --- | --- | --- |
| 자기 구현·도구·타입·설정·의존성 선언·테스트 | `modules/safety_policy/**`는 safety_policy 담당 수정 | 파서·검증·저장·해시·경로·adapter도 자기 utils와 schemas에서 구현한다. 다른 모듈 코드를 import하지 않는다. |
| 입력 계약·입력 검증 | safety_policy 담당이 자기 `schemas/input/`과 검증을 관리 | 입력 원본은 읽기 전용. 생산자 출력 계약과 일치시키고 원본 오류는 생산자에게 요청한다. |
| 자기 출력 계약·출력 검증 | safety_policy 담당이 자기 `schemas/output/`·출력·근거·문서를 작성·검증 | 합의한 출력 Schema·필드 의미·ID·근거를 만족한 파일만 완료로 공개한다. |
| 입력·외부 참조 | 계획은 scenario_generator가 작성한다. 사용자가 제공한 허용 범위·제한·승인 기록과 자기 Policy 설정을 safety_policy가 직접 검증한다. | 명시된 파일·근거·공개 창구만 사용한다. 다른 모듈 내부 구현·전역 가변 상태를 읽지 않는다. |
| 출력 변경·수신자 조율 | 변경 제안·출력 구현은 safety_policy / 입력 대응은 아래 직접 소비자 | 소비자 내부 알고리즘은 알 필요가 없다. 그 파일을 실제 읽는 담당자와만 버전·필드·의미·변경 시점을 합의한다. |
| Safety 규칙·Policy 버전·평가 근거 | safety_policy가 평가하고 allow/block/require_approval을 발행 | LLM은 실행 여부를 결정하지 않는다. 모듈 설정으로 전역 허용 범위를 확대하지 않는다. |
| 승인 기록과 계획 해시 | 승인 의사는 사용자, 승인 창구·기록 수집·진위/계획 대응 검증은 safety_policy | 승인 대기를 직접 allow로 덮어쓰지 않고 실제 승인 후 새 평가를 발행한다. |

**모듈 내부 경계:** 입력 adapter → 내부 처리 → 출력 adapter → 자기 출력 검증 → 원자적 저장 → 완료 통지. 내부 고도화가 계약을 유지하면 다른 담당자에게 수정을 요구하지 않는다.

**입력·출력 계약과 수정 책임:** [공통 계약 · 파일 형식과 전달 규칙](02-common-contract.md) · **독립 실행·모듈 폴더 구조:** [실행 제어 · runner와 폴더 구조](03-runner-layout.md)

## 공통 환경에서의 독립 개발

**개발 기준·관리자 초기 설정·환경 설치:** [개발 기준 · 환경 일원화와 GitHub 초기 설정](01-dev-standard.md)

- 같은 `.python-version`과 루트 `requirements.lock.txt`로 만든 저장소 루트 `.venv`에서 실행·테스트한다. 독립 검증은 자기 입력 fixture·공개 창구 대역으로 수행한다.
- `modules/safety_policy/requirements.txt`에는 실제 사용하는 직접 의존성과 지원 버전을 선언한다. 추가·버전 변경은 GitHub 관리자에게 제안하고 후보 잠금 환경에서 자기 모듈을 검증한다.
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
- [ ]  계획 개수·요청·영향의 변화에 모든 시나리오의 근거와 판정을 생성한다.
- [ ]  불명확한 영향·계획 해시/계정 오류·승인 대기를 allow로 대체하지 않는다.
- [ ]  허용된 빈 배열·null·partial, 잘못된 Schema/버전/ID/참조/해시를 구분한다. 입력 오류를 성공·정상 빈 결과로 숨기지 않는다.
- [ ]  자기 출력의 Schema·작성자·필수 필드·ID/근거 대응을 검증하고 자기 경로에 원자적으로 공개한다. 저장 실패를 완료로 알리지 않는다.
- [ ]  위 입력 변화와 의존성 실패를 자기 모듈 테스트에서 검증한다. 다른 모듈 실제 구현 대신 공개 입력 fixture·인터페이스 대역으로 독립 검증할 수 있다.
- [ ]  내부 최적화 이후에도 자기 입력·출력 계약 검증을 통과한다. 계약 변경 시 직접 소비자와 실제 출력 샘플의 수신 검증을 함께 확인한다.

## 입력

| 입력 파일·정보 | 작성·제공 주체 | 사용할 내용 |
| --- | --- | --- |
| test_scenarios.json | `scenario_generator` | data.scenarios의 계정·세션·요청·순서·바인딩·사전조건·state_change·필수 resource_ids. 정확한 파일 바이트 전체를 해시 계산에 사용한다. |
| 신뢰된 사용자 실행 인자 / 자기 Policy 설정 | `사용자 실행 인자 / modules 내 자기 configs` | 허용 origin·경로·테스트 계정, 요청·시간·상태 변경 제한, Policy ID·버전. |
| 실제 사용자 승인 기록 | `승인 UI/CLI / 승인 후 재평가` | 승인이 이루어진 경우에만 해당 기록을 확인한다. 승인 대기에서는 승인 완료로 취급하지 않는다. |

**입력 파일의 전체 필드:** [test_scenarios.json 필드](m5-scenario_generator.md).

### 현재 입력·승인 경계

- `Scenario.resource_ids`는 비어 있지 않은 문자열 배열이며 최소 1개다. SG가 원본
  Candidate.resource_ids의 값·순서를 그대로 복사한다. 누락·null·빈 배열·잘못된
  항목 타입은 거절하지만 ID 형식·순서·중복에 별도 제약을 추가하지 않는다.
- Safety Policy는 자원 ID를 생성·해석·정렬·중복 제거하지 않는다. 후보와의 값 일치는
  생산자가 보장하므로 후보 파일·KG 조회를 추가 입력으로 요구하지 않는다.
- 전체 계획 파일의 SHA-256에 resource_ids도 포함된다. 값·순서·개수가 바뀌면
  기존 계획 해시·승인을 재사용하지 않고 새 계획·판정·필요한 승인을 받는다.
- 승인 입력은 계획 해시·Policy ID/버전·승인 대상·승인/만료 시각 등에 바인딩한다.
  block·unknown은 승인 기록만으로 allow로 바꾸지 않는다. 기록 생성·사용자 진위
  확인을 연결하는 runner·UI 통합은 후속 작업이다.
- 공개 실행 산출물 `0.1.0`과 미지원 버전은 거절한다. 구버전 계획을 묵시적으로
  변환하지 않으며 완료된 산출물·실제 승인 기록을 수정하지 않는다.

## 구현할 일

1. 시나리오별 대상 범위·테스트 계정·요청 예산·상태 변경·데이터 영향·서비스 영향을 독립적으로 평가한다.
2. 각 항목의 Policy 규칙 ID·결과·이유를 assessment에 기록한다.
3. 전체 판정을 allow·block·require_approval로 분류하고 실제 실행 범위·제한을 설정한다.
4. 판정한 test_scenarios.json의 SHA-256과 Policy 버전을 safety_decisions.json에 묶어 출력한다.
5. 사용자 승인 기록을 확인한 경우 다시 평가하여 새 allow 결정을 발급한다. 변경된 계획은 새 계획·판정으로 처리한다.

## 출력

| 작성 파일 | 생성 operation | 출력 변경 협의 대상 — 직접 소비자 |
| --- | --- | --- |
| `safety_decisions.json` | `evaluate` | verifier · reporter |

정상·부분 완료 파일의 `data` 필드는 아래와 같다. `status=failed`이면 `data=null`로 기록한다. 공통 메타데이터·상태 규칙은 [공통 파일 형식](02-common-contract.md)을 적용한다.

## 완료 기준

아래 표시는 모듈 독립 구현과 소유 승인 fixture·신뢰된 승인 입력 검증 기준이다.
실제 사용자 승인 창구 또는 전체 pipeline 통합 완료를 뜻하지 않는다.

- [x]  LLM과 독립된 규칙으로 allow·block·require_approval을 결정한다.
- [x]  판정에 정확한 계획 파일 SHA-256, Policy 버전, 평가 근거와 실행 제한을 묶는다.
- [x]  승인 대기는 실행하지 않으며 실제 승인 기록 확인 후 재평가로 새 allow 결정을 발급한다.
- [ ]  같은 run의 계획·판정·검증·리포트 및 실제 사용자 승인 창구를 연결해 검증한다.

## 구현 주의사항

- 실행 여부는 Policy가 결정한다. LLM의 제안·state_change 선언·기대 동작 설명을 그대로 실행 허가로 사용하지 않는다.
- allow는 6개 assessment 항목이 모두 pass인 경우에만 가능하다. block 항목이 있으면 전체 판정도 block이다. unknown·승인 필요 항목이 남으면 allow로 판정하지 않는다.
- require_approval은 실행 허용이 아니다. approval_ref 문자열만으로 승인을 인증하지 않고 실제 사용자 기록을 확인한다.
- 승인 뒤 URL·단계·파라미터가 바뀌면 새 계획과 판정이 필요하다. 원래 해시를 바뀐 계획에 재사용하지 않는다.
- GET이라는 이유만으로 상태 변경이 없다고 단정하지 않는다. 판단하기 어려운 영향은 차단·승인 요청 경로로 처리한다.

## 입력·출력 JSON 필드

### safety_decisions.json

- 고정 값: `artifact_type=safety_decisions`, `producer=safety_policy`, `schema_version=0.2.0`.
- 예상 Schema 경로: `modules/safety_policy/schemas/output/safety_decisions.schema.json`.
- 예상 출력 fixture 경로: `modules/safety_policy/tests/fixtures/runs/run_demo_001/artifacts/iteration-000/safety_policy/safety_decisions.json`.

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `data.policy_id` | `string` | 필수 | 사용한 Policy 식별자. |
| `data.policy_version` | `string` | 필수 | 사용한 Policy 버전. |
| `data.scenarios_sha256` | `string` | 필수 | 판정한 test_scenarios.json의 정확한 바이트 해시. 실행 직전에 대조한다. |
| `data.decisions` | `array<SafetyDecision>` | 필수 | 시나리오별 사전 규칙 판정. |

## 중첩 레코드 필드

출력 배열·객체의 항목마다 아래 필수 필드를 적용한다. `properties`·`match_key` 등 명시된 JSON map은 확장 가능하고 일반 객체는 미정의 키를 거절한다.

### SafetyDecision

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `decision_id` | `string` | 필수 | Policy 판정 ID. |
| `scenario_id` | `string` | 필수 | 판정한 시나리오 ID. |
| `decision` | `enum: allow, block, require_approval` | 필수 | 실행 허용·차단·사용자 승인 요청. |
| `reason_codes` | `array<string>` | 필수 | 판정 사유 코드 목록. |
| `reason` | `string` | 필수 | 사용자가 이해할 수 있는 판정 사유. |
| `assessment` | `SafetyAssessment` | 필수 | 6개 사전 평가 항목별 Policy 판정과 근거. allow는 모두 pass인 경우에만 가능하다. |
| `effective_origins` | `array<string>` | 필수 | 검증기가 실제 요청을 보낼 수 있는 origin 목록. |
| `effective_account_ids` | `array<string>` | 필수 | 실행 가능한 계정 ID 목록. |
| `limits` | `PolicyLimits` | 필수 | 실행 중에도 강제해야 하는 제한. |
| `approval_ref` | `string / null` | 필수 | 실제 사용자 승인 기록 참조. 승인 요청·미승인이면 null. |

### SafetyAssessment

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `target_scope` | `PolicyAssessmentItem` | 필수 | 대상 origin·경로 범위 평가. |
| `test_accounts` | `PolicyAssessmentItem` | 필수 | 허용 테스트 계정·역할·세션 대응 평가. |
| `request_budget` | `PolicyAssessmentItem` | 필수 | 계획 요청 수·재시도와 누적 실행 제한 평가. |
| `state_change` | `PolicyAssessmentItem` | 필수 | 실제 요청의 상태 변경 가능성 평가. |
| `data_impact` | `PolicyAssessmentItem` | 필수 | 생성·수정·삭제·민감 데이터 처리 등 데이터 영향 평가. |
| `service_impact` | `PolicyAssessmentItem` | 필수 | 요청량·연산·외부 효과 등 서비스 영향 평가. |

### PolicyAssessmentItem

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `status` | `enum: pass, block, require_approval, unknown` | 필수 | Policy 규칙으로 평가한 항목 결과. unknown은 안전이 확인되었다는 뜻이 아니다. |
| `rule_id` | `string` | 필수 | 이 평가에 적용한 Policy 규칙 ID. LLM의 기대 동작 규칙과 구분한다. |
| `reason` | `string` | 필수 | 해당 항목의 평가 근거. 비밀값을 포함하지 않는다. |

### PolicyLimits

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `max_requests` | `integer` | 필수 | 리다이렉트·재시도를 포함한 최대 실제 요청 수. |
| `max_duration_ms` | `integer` | 필수 | 시나리오 전체 실행 제한 시간. |
| `allow_state_change` | `boolean` | 필수 | 상태 변경 요청 허용 여부. |

**재사용하는 계약 필드:** [ArtifactRef](02-common-contract.md), [ErrorItem](02-common-contract.md), [RuntimeMetrics](02-common-contract.md).

## 변경 이력

| 날짜 | 문서 변경 | 기준·영향 |
| --- | --- | --- |
| 2026-10-10 | 현재 구현·소비자 연결 상태, 입출력 0.2.0, Scenario.resource_ids 및 계획 해시·승인 바인딩을 반영 | `695b3c8`, PR #52·#53 반영. 기존 6개 규칙·출력 필드·설정/승인 계약 유지 |

기존 책임 경계와 독립 안전 판정 원칙은 유지하고 상세 과거 이력은 모듈 README에
보존한다. 합동 회귀는 `897 passed, 12 skipped`이며 실제 전체 연결·사용자 승인
창구·Neo4j 테스트는 이번 검증 범위가 아니다. 이번 변경은 기존 계약의 문서 반영이며
노션 원본 반영·export 기준일 갱신은 별도 관리 작업으로 남긴다.

---

[ABC2LAB_인터페이스_명세서_v0.1](00-index.md) · [공통 계약 · 파일 형식과 전달 규칙](02-common-contract.md) · [실행 제어 · runner와 폴더 구조](03-runner-layout.md) ·
