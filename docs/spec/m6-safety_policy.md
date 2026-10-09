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

구현 기준 커밋 `b040ba7`과 작업 트리의 정책 설정 준비 5단계 문서를 반영했다.
PR #52·#53 계약 호환 이후 정책 설정 준비 1~5단계의 모듈 내부 작업은 완료했다.
이번 저장소 문서 갱신이 노션 원본 반영이나 export 기준일 갱신을 의미하지는 않는다.

| 구분 | 현재 상태 |
| --- | --- |
| 독립 구현 | 공개 `evaluate`·CLI, 외부 정책 검증·private 사본 자동 배치·동일 정책 재사용, 6개 assessment, 승인 기록 재평가·원자 저장 구현 완료 |
| 공개 계약 | `test_scenarios` 입력·`safety_decisions` 출력 `0.2.0`, 필수 Scenario.resource_ids 수용 완료 |
| 내부 설정 계약 | policy_config·approval_record는 각각 `0.1.0`; policy_version은 별도 안전 규칙 버전 |
| 소비자 호환 | Verifier·Reporter의 safety_decisions 입력 사본 `0.2.0` 동기화 및 공개 fixture Schema 수용 확인 |
| 실행 설정 | 필수 `SAFETY_POLICY_CONFIG_PATH`; 호출자의 `context.policy_config`는 거절 |
| 운영 안내 | `.env.example`·README에 원본 준비·사본 유지·오류 대응·승인 재평가 절차 정리 완료 |
| 독립 회귀 | Safety Policy `304 passed`; KG·Safety Policy·Reporter·Verifier 합동 `1036 passed, 12 skipped` |
| 실제 전체 연결 | 미완료. Runner의 새 설정 전달 방식 적용, 동일 run의 계획·판정·검증·리포트 연결 및 사용자 승인 창구 통합 필요 |

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
| 입력·외부 참조 | 계획은 scenario_generator, 정책 원본은 신뢰된 팀·운영자가 작성한다. safety_policy가 명시된 입력·정책·승인 기록을 직접 검증한다. | 명시된 파일·근거·공개 창구만 사용한다. 다른 모듈 내부 구현·전역 가변 상태를 읽지 않는다. |
| 정책 원본·private 사본 | 정책 내용은 팀·운영자, 원본 검증·사본 배치·재사용·해시 확인은 safety_policy | Runner는 환경변수로 원본 경로만 제공한다. 정책 private 파일을 직접 생성·복사하거나 사본 해시를 전달하지 않는다. |
| 출력 변경·수신자 조율 | 변경 제안·출력 구현은 safety_policy / 입력 대응은 아래 직접 소비자 | 소비자 내부 알고리즘은 알 필요가 없다. 그 파일을 실제 읽는 담당자와만 버전·필드·의미·변경 시점을 합의한다. |
| Safety 규칙·Policy 버전·평가 근거 | safety_policy가 평가하고 allow/block/require_approval을 발행 | LLM은 실행 여부를 결정하지 않는다. 모듈 설정으로 전역 허용 범위를 확대하지 않는다. |
| 승인 기록과 계획 해시 | 승인 의사는 사용자, 전달된 기록의 계약·해시·계획 대응 검증과 재평가는 safety_policy | 실제 승인 수집·사용자 진위 확인을 연결하는 Runner·UI 통합은 후속 작업이다. 승인 대기를 직접 allow로 덮어쓰지 않는다. |

**모듈 내부 경계:** 실행 인자·경로 검증 → 정책 원본 검증·사본 준비 → 시나리오 입력 검증 → 내부 평가 → 출력 adapter·재검증 → 원자적 저장 → 완료 통지. 내부 고도화가 계약을 유지하면 다른 담당자에게 수정을 요구하지 않는다. 이번 공개 호출 설정 변경에 필요한 Runner 대응은 아래에 명시한다.

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
| 신뢰된 실행 인자 | `호출자 / Runner` | run_id·iteration·mode·run_root와 입력 경로·정확한 SHA-256, 출력 경로. 정책 내용·사본 descriptor는 받지 않는다. |
| 정책 원본 JSON | `신뢰된 팀·운영자` | 환경변수 SAFETY_POLICY_CONFIG_PATH로 경로 제공. 허용 origin·경로·테스트 계정·요청/시간 제한·요청 영향 규칙·Policy ID/버전을 명시한다. |
| 실제 사용자 승인 기록 | `승인 창구 / 승인 후 재평가` | 선택 파일의 경로·SHA-256을 받는다. 승인이 이루어진 경우에만 기록을 확인하며 승인 대기를 승인 완료로 취급하지 않는다. 실제 창구 연결은 후속 작업이다. |

**입력 파일의 전체 필드:** [test_scenarios.json 필드](m5-scenario_generator.md).

### 현재 입력·승인 경계

- `Scenario.resource_ids`는 비어 있지 않은 문자열 배열이며 최소 1개다. SG가 원본
  Candidate.resource_ids의 값·순서를 그대로 복사한다. 누락·null·빈 배열·잘못된
  항목 타입은 거절하지만 ID 형식·순서·중복에 별도 제약을 추가하지 않는다.
- Safety Policy는 자원 ID를 생성·해석·정렬·중복 제거하지 않는다. 후보와의 값 일치는
  생산자가 보장하므로 후보 파일·KG 조회를 추가 입력으로 요구하지 않는다.
- 전체 계획 파일의 SHA-256에 resource_ids도 포함된다. 값·순서·개수가 바뀌면
  기존 계획 해시·승인을 재사용하지 않고 새 계획·판정·필요한 승인을 받는다.
- 승인 입력은 run_id·iteration·정확한 계획 해시·Policy ID/버전·승인 대상·승인자·승인/만료 시각에 바인딩한다.
  block·unknown은 승인 기록만으로 allow로 바꾸지 않는다. 기록 생성·사용자 진위
  확인을 연결하는 runner·UI 통합은 후속 작업이다.
- 공개 실행 산출물 `0.1.0`과 미지원 버전은 거절한다. 구버전 계획을 묵시적으로
  변환하지 않으며 완료된 산출물·실제 승인 기록을 수정하지 않는다.

## 정책 설정 준비·공개 호출 규약

### 원본 작성과 환경변수

정책의 허용 범위·계정·규칙·제한은 신뢰된 팀·운영자가 정한다. Safety Policy는
그 정책을 검증·준비·평가하며, 원본 JSON이나 누락된 설정을 자동 생성하지 않는다.
LLM이나 Runner가 진단 결과에서 허용 정책을 자동으로 만들지 않는다.

- 필수 환경변수는 `SAFETY_POLICY_CONFIG_PATH`다. 미설정·빈 값·원본 누락·읽기 실패를
  기본 정책이나 자동 허용으로 대체하지 않는다.
- 원본은 run 밖에 둘 수 있다. 상대 경로는 실행 작업 디렉터리 기준이며 운영 환경에서는
  절대 경로를 권장한다. 원본 파일은 읽기 전용 입력으로 취급한다.
- 모듈은 `.env`·`.env.example`을 자동으로 읽지 않는다. 호출 프로세스의 환경에
  실제로 주입해야 한다. 환경변수 예시는
  [.env.example](../../modules/safety_policy/.env.example)을 참고한다.
- 원본은 UTF-8 JSON object이며 중복 키·비유한 숫자·미정의 키를 거절한다.
  [policy_config.schema.json](../../modules/safety_policy/schemas/input/policy_config.schema.json)
  `0.1.0`으로 검증한다. 필수 키는 `schema_version`, `policy_id`, `policy_version`,
  `allowed_targets`, `test_accounts`, `limits`, `request_rules`다.
- `test_accounts`에는 원본 account_id·역할 ID·세션 필요 여부를 적는다.
  비밀번호·쿠키·토큰은 넣지 않는다. 테스트 fixture의 정책은 운영 기본 정책이 아니다.

### 공개 run·CLI 입력

`entrypoint.run(operation, input_paths, output_dir, context)`는 `evaluate`만 지원한다.

| 인자 | 현재 규약 |
| --- | --- |
| `input_paths` | 유일한 키는 `test_scenarios`; 값은 `path`, `sha256`만 가진 descriptor다. |
| 입력 경로 | run_root 상대 `artifacts/iteration-<NNN>/scenario_generator/test_scenarios.json`. 현재 회차와 정확히 일치해야 한다. |
| `output_dir` | 현재 run의 `artifacts/iteration-<NNN>/safety_policy`와 정확히 대응해야 한다. |
| `context` 필수 키 | `run_id`, `iteration`, `mode`, `run_root`. iteration은 0 이상 정수, mode는 diagnosis/development, run_root는 이름이 run_id와 같은 기존 신뢰 디렉터리다. |
| `context` 선택 키 | `approval_record`만 허용한다. 생략/null 또는 `path`, `sha256` descriptor다. 파일은 `private/safety_policy/approvals/approval_<id>.json` 규약을 따른다. |

`<NNN>`은 iteration을 최소 3자리로 표시한 값이다. descriptor의 SHA-256은 정확한
파일 바이트를 해시한 64자리 소문자 16진수다. 호출자의 `context.policy_config`나
미정의 키는 무시하지 않고 `CONTRACT_INVALID`로 거절한다. 내부 정책 경로·해시는
모듈이 준비하며 공개 context 모델이나 공개 JSON 필드로 추가하지 않는다.

실행 인자·입출력·승인 경로와 기존 출력 여부를 먼저 확인하고, 이어 정책 사본을
준비한다. 실제 시나리오 파일의 Schema·버전·run_id·iteration·mode·해시를 검증한 뒤
평가한다. CLI도 같은 공개 `run()`을 사용하며 시나리오·선택 승인 파일의 해시만
계산한다. CLI 종료 코드는 completed/partial이면 0, failed이면 1이다.
종료 코드 0이 모든 시나리오의 실행 허용을 뜻하지는 않는다.

### private 사본과 같은 run의 정책 유지

- 사본 경로는 `runs/<run_id>/private/safety_policy/policy.json`이다. Safety Policy가
  검증한 원본과 동일한 바이트를 임시 파일 write·flush·fsync·close 후 기존 파일을
  대체하지 않는 원자적 공개로 배치하고 실제 사본 SHA-256을 확인한다.
- 사본은 신뢰된 run_root의 고정 경로에만 둔다. 경로 이탈과 외부·다른 모듈로
  향하는 symlink를 거절한다. 정책 사본 배치와 판정 파일 공개는 별도 단계다.
- 이후 정책 준비에서도 환경변수의 원본을 다시 읽고 검증한다. 사본이 있다는
  이유로 설정 누락·잘못된 원본을 무시하거나 사본만으로 실행하지 않는다.
- 원본과 사본의 SHA-256이 같으면 사본을 다시 쓰지 않고 재사용한다. 다른 원본
  경로라도 바이트가 같으면 가능하다. Policy ID·버전이 같아도 바이트가 다르면
  거절하며 공백·줄바꿈 변경도 정책 변경으로 취급한다.
- 손상되거나 다른 사본은 자동 복구·덮어쓰기하지 않는다. 정책 변경은 새 run에서
  적용한다. 기존 사본을 임의 삭제·교체해 같은 run의 변경 보호를 우회하지 않는다.
- 동시 준비로 사본이 먼저 배치된 경우 경로를 다시 검증하고 같은 해시의 사본만
  재사용한다. 평가 뒤 계획·정책 사본·승인 기록이 바뀌면 결과 공개를 거절한다.

### 승인 후 재평가와 불변 산출물

다음 iteration과 승인 후 재평가는 같은 정책을 재사용할 수 있다. 이미 공개된
completed·partial·failed 판정 파일은 모두 불변이다. 새 iteration에서 SG가 새
계획을 발행하고 그 계획의 정확한 SHA-256·run_id·iteration·Policy ID/버전에
바인딩된 새 승인 기록으로 재평가한다. CLI iteration 값만 바꾸거나 이전 승인을
그대로 재사용하지 않는다. 기존 계획·판정·실제 승인 기록도 수정하지 않는다.

승인으로 바꿀 수 있는 항목은 명시적인 `require_approval`뿐이다. 승인 기록이
있어도 block·unknown을 허용하지 않으며 모든 assessment가 pass가 된 경우에만
새 allow를 발행한다. 실제 승인 수집·사용자 진위 확인은 Runner·UI 연결 시
구체화하며 독립 승인 fixture 검증과 구분한다.

### 실패 제어 응답과 실패 산출물

| 코드 | 주요 발생 조건·처리 |
| --- | --- |
| `CONTRACT_INVALID` | 잘못된 실행 인자·입력 계약·미지원 버전·legacy context.policy_config를 거절한다. |
| `CONFIG_INVALID` | 환경변수·원본 파일·UTF-8/JSON/Schema 오류 또는 사본이 파일이 아닌 경우다. 기본 정책으로 대체하지 않는다. |
| `CONFIG_HASH_MISMATCH` | 원본과 사본이 다르거나 평가 시 정책 사본 해시가 내부 참조와 다르다. 기존 사본을 보존한다. |
| `PATH_INVALID` | 입력·실행 경로 접근 실패, 신뢰 경로 이탈·사본 symlink를 거절한다. 원본 정책 읽기 실패는 CONFIG_INVALID다. |
| `STORAGE_FAILED` | 사본·결과 저장/읽기 실패다. retryable=true라도 이미 공개된 파일은 덮어쓰지 않는다. |
| `OUTPUT_EXISTS` | 현재 회차의 판정 파일이 이미 있다. 공개 호출에서는 정책 준비 전에 거절한다. |
| `OUTPUT_INVALID` | 출력 검증이나 평가 후 계획·정책 사본·승인 기록의 해시 재검증에 실패해 결과 공개를 차단한다. |

정책 준비·입력 검증 실패는 새 판정 파일 없는 failed 제어 응답이며 `artifact_id`,
`output_path`, `sha256`은 null이다. 준비 이후 Policy 의미 오류·승인 오류는
errors가 있고 `data=null`인 failed 산출물로 공개될 수 있다. 제어 응답의 status·
errors·output_path를 함께 확인하고 실패를 정상 빈 판정이나 실행 허용으로 숨기지 않는다.
일부 오류 코드는 발생 단계에 따라 제어 응답 또는 실패 산출물에 기록될 수 있다.

### Runner 연결 조건과 남은 작업

Runner는 원본 경로를 실행 환경에 제공하고 기존 `context.policy_config` 전달을
제거해야 한다. 정책 private 폴더에 직접 쓰거나 사본 해시를 계산하지 않는다.
새 설정 전달 방식의 실제 Runner 반영, 사용자 승인 창구와 진위 확인 연결,
동일 run의 계획→판정→검증→리포트 전체 실행 검증은 아직 미완료다.
이번 명세 반영은 Runner·다른 모듈 코드·공개 JSON Schema를 변경하지 않는다.

## 구현할 일

아래 평가 작업 전에 위 실행 인자 검증·정책 준비를 수행한다.

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
- [x]  외부 정책 원본을 검증하고 자기 private 사본 배치·동일 정책 재사용·변경 거절을 수행한다.
- [x]  공개 run·CLI의 설정 준비·재사용·실패 경로를 독립 fixture와 임시 run으로 검증한다.
- [ ]  Runner에 환경변수와 새 context 전달 규약을 적용해 연결을 확인한다.
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
| 2026-10-10 | 정책 설정 준비 1단계: 외부 원본 검증·자기 private 사본 원자 배치·실제 해시 확인 | `b664976`, Safety Policy 196 passed. 당시 공개 run·CLI 연결 전이며 JSON 계약은 유지 |
| 2026-10-10 | 정책 설정 준비 2단계: 공개 run·CLI 연결, 호출자의 policy_config descriptor 제거·거절, 준비 실패 제어 응답 | `28ca650`, 218 passed. Runner의 환경변수·context 전달 대응 필요 |
| 2026-10-10 | 정책 설정 준비 3단계: 동일 바이트 사본 재사용·동시 배치 재검증·같은 run의 정책 변경 거절 | `95fa8ef`, 240 passed. 정책 변경은 새 run, 기존 사본·완료 산출물 보존 |
| 2026-10-10 | 정책 설정 준비 4단계: 공개 run·CLI main의 동일 32종 사례를 각각 검증하는 회귀 64건 추가 | `b040ba7`, 304 passed / 합동 1036 passed, 12 skipped. 실제 전체 연결·별도 CLI 프로세스 검증은 아님 |
| 2026-10-10 | 정책 설정 준비 5단계: .env.example·README 운영 안내·승인 재평가 예시·변경 이력 정리 | 구현 기준 `b040ba7` + 작업 트리 문서. 기본 정책·비밀값·자동 .env 로딩 없음 |
| 2026-10-10 | 1~5단계 종료 후 본 명세에 현재 구현·호출 설정·사본 책임·재사용·오류·미완료 연결을 일괄 반영 | 명세 문서만 갱신. 공개 JSON 0.2.0, 정책/승인 Schema 0.1.0, 6개 assessment·안전 판단 의미 유지 |

기존 책임 경계와 독립 안전 판정 원칙은 유지하고 상세 과거 이력은 모듈 README에
보존한다. 최초 `695b3c8` 문서 동기화 때의 합동 회귀 `897 passed, 12 skipped`는
당시 기록이다. 이번 1~5단계 정리의 검증 명령·결과는 다음과 같다.

```bash
.venv/bin/python -m pytest modules/safety_policy -q
# 304 passed

.venv/bin/python -m pytest modules/knowledge_graph modules/safety_policy modules/reporter modules/verifier -q -rs
# 1036 passed, 12 skipped
```

skip 12건은 활성화하지 않은 KG 실제 Neo4j 테스트다. 실제 전체 연결·사용자 승인
창구는 이번 검증 범위가 아니다. 이번 수정은 `docs/spec/m6-safety_policy.md`만
변경하며 코드·Schema·테스트·fixture·의존성을 변경하지 않는다. 노션 원본 반영과
export 기준일 갱신은 별도 관리 작업으로 남긴다.

---

[ABC2LAB_인터페이스_명세서_v0.1](00-index.md) · [공통 계약 · 파일 형식과 전달 규칙](02-common-contract.md) · [실행 제어 · runner와 폴더 구조](03-runner-layout.md) ·
