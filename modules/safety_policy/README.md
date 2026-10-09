# safety_policy

`test_scenarios.json`을 LLM과 독립된 규칙으로 평가해
`safety_decisions.json`을 생성하는 모듈이다.

## 현재 상태 — 2026-10-10

기준 커밋은 `b664976`이다. PR #52·#53 계약 호환과 정책 설정 개선 2단계까지의
작업 상태를 기록한다. 정책 준비 기능은 공개 `run()`·CLI에 연결됐으며,
동일 run의 기존 정책 재사용은 다음 3단계 작업으로 남아 있다.

| 구분 | 현재 상태 |
|---|---|
| 독립 구현 | 공개 `evaluate`·CLI, 외부 정책 설정 검증·private 자동 배치, 6개 assessment, 승인 기록 재평가·원자 저장 구현 완료 |
| 공개 계약 | `test_scenarios` 입력·`safety_decisions` 출력 `0.2.0`, 필수 `Scenario.resource_ids` 수용 완료 |
| 소비자 호환 | Verifier·Reporter의 `safety_decisions` 입력 사본 `0.2.0` 동기화 완료 |
| 독립 회귀 | `218 passed` |
| 실제 전체 연결 | 미완료. 같은 run의 계획→판정→검증→리포트 연결 검증 필요 |

승인 기록의 입력 검증·계획 바인딩·재평가는 구현됐지만, 실제 사용자 승인 수집·
진위 확인을 연결하는 runner·UI 통합은 후속 작업이다. 현재 명세는
[m6-safety_policy](../../docs/spec/m6-safety_policy.md)를 참고한다.

## 공개 operation

- `evaluate`: 시나리오별 실행 허용·차단·승인 요청 판정

공개 `evaluate` operation과 CLI까지 구현되어 있다. 입력·Policy 설정을 검증하고
독립 규칙으로 평가한 뒤 `safety_decisions.json`을 원자적으로 공개한다.

## 1단계 구현 범위

- `test_scenarios.json` 입력 JSON Schema
- `safety_decisions.json` 출력 JSON Schema
- completed·partial·failed 상태 계약
- 시나리오·단계·Check·Binding·판정 ID 검증
- 단계 순서와 선행 단계 Binding 참조 검증
- 제한된 URL Binding 표현과 JSON Pointer·헤더 selector 검증
- 6개 assessment와 최종 판정의 일관성 검증
- 입력 계획 SHA-256·input_ref·scenario_id 출력 대응 검증
- 불변 내부 dataclass 모델
- 신뢰 경로, SHA-256, 원자 저장 유틸리티
- 독립 fixture와 단위 테스트

## 계약 파일

- 입력: `schemas/input/test_scenarios.schema.json`
- 출력: `schemas/output/safety_decisions.schema.json`
- 공개 산출물 계약 버전: `0.2.0`

`test_scenarios.json` 입력과 `safety_decisions.json` 출력은 `0.2.0`만 지원한다.
`0.1.0`과 미지원 버전은 거절하며 완료된 입력 파일을 묵시적으로 변환하지 않는다.
자기 설정 `policy_config`와 승인 기록 `approval_record`의 Schema 버전은 각각
`0.1.0`을 유지한다. `policy_version`은 적용한 안전 규칙의 버전이며 공개 산출물의
`schema_version`과 별도로 관리한다.

일반 객체의 미정의 키는 거절한다. `status=completed`는 빈 `errors`와 유효한
`data`, `partial`은 하나 이상의 오류와 유효한 `data`, `failed`는 하나 이상의
오류와 `data=null`을 요구한다.

### Scenario.resource_ids

`data.scenarios[].resource_ids`는 필수 문자열 배열이며 최소 1개, 각 항목은
빈 문자열이 아니어야 한다. 필드 누락·`null`·빈 배열·잘못된 항목 타입은 거절한다.
빈 `scenarios` 배열과 `failed`의 `data=null`은 기존 계약대로 허용한다.

값은 scenario_generator가 원본 후보의 `resource_ids`에서 값·순서 그대로
복사한 KG Resource instance node_id다. 접두사나 구분자를 가정하지 않는
불투명 문자열이며, Safety Policy는 ID를 생성·해석·정렬·중복 제거하지 않는다.
후보와의 값 일치는 생산자가 보장하므로 후보 파일을 추가 입력으로 받거나
Neo4j를 조회하지 않는다.

기존 6개 assessment와 판정 규칙, 내부 모델, 출력 Schema는 유지한다.
전체 시나리오 파일의 SHA-256에는 이 필드도 포함된다. 자원 ID의 값·순서·개수가
바뀌면 기존 승인은 새 계획에 사용할 수 없으며 새로운 승인이 필요하다.

### 현재 연결 상태

2026-10-10, 기준 커밋 `695b3c8`의 공개 Schema·fixture 기준이다.

| 경계 | 생산자 / 소비자 버전 | 확인 결과 |
|---|---|---|
| scenario_generator → safety_policy | `0.2.0` / `0.2.0` | `resource_ids`를 포함한 공개 시나리오 2개를 입력 loader가 수용 |
| safety_policy → reporter | `0.2.0` / `0.2.0` | 공개 판정 2개의 Reporter 입력 Schema 검증 통과 |
| safety_policy → verifier | `0.2.0` / `0.2.0` | 입력 Schema 사본 일치 및 공개 판정 2개의 Schema 수용 확인 |

공개 fixture 수신과 독립 회귀 검증이며 동일 run의 전체 pipeline 실행 완료를
의미하지 않는다. Verifier의 시나리오 입력·검증 관계 출력 계약 전환은 완료됐고,
실제 `graph_updates` 생성과 동일 run의 전체 연결 검증은 남아 있다.

## 판정 계약

assessment는 다음 6개 항목을 모두 포함한다.

- `target_scope`
- `test_accounts`
- `request_budget`
- `state_change`
- `data_impact`
- `service_impact`

`allow`는 6개 항목이 모두 `pass`일 때만 유효하다. `block` 항목이 있으면 최종
판정도 `block`이어야 한다. `block`과 `require_approval`은 실행 허용이 아니며
`approval_ref`를 가질 수 없다.

## 2단계 입력 adapter

`evaluate_adapter.parse_evaluate_request()`는 다음 공개 인자를 검증한다.

- `input_paths`의 유일한 키는 `test_scenarios`
- 입력 descriptor는 `path`, `sha256`만 허용
- 입력 경로는 현재 iteration의 scenario_generator 산출물 경로와 정확히 일치
- `context` 필수 키는 `run_id`, `iteration`, `mode`, `run_root`
- 선택 키는 `approval_record`이며 경로·해시 검증은 기존 승인 규약을 따른다
- 호출자의 `policy_config` descriptor와 미정의 키는 거절
- 출력 경로는 현재 iteration의 safety_policy 산출물 폴더와 정확히 일치
- 상대 경로 이탈, 절대 외부 경로, 다른 run 경로, 잘못된 SHA-256 형식 거절

실행 인자 검증은 `EvaluationArguments`를 반환하며 정책 파일을 읽거나 배치하지
않는다. `prepare_evaluate_request()`가 검증된 run_root로 정책 준비를 호출하고,
사본의 경로·해시를 내부 `EvaluationRequest`에 바인딩한다. 두 모델은 모듈 내부
타입이며 Runner가 전달하는 공용 context 클래스가 아니다.

`service.prepare_evaluation()`은 실제 파일 SHA-256과 Schema를 검증하고 envelope의
`run_id`, `iteration`, `mode`가 실행 context와 일치하는지 확인한다. 유효한
`partial` 입력은 원본 오류와 함께 보존하고 `failed` 입력은 평가 대상으로 받지
않는다.

## 3단계 Policy 평가 규칙

`policy.evaluate_policy()`는 대상 앱 값이 없는 일반 규칙 엔진이다. 호출자가
`PolicyConfiguration`으로 다음 값을 제공해야 한다.

- 허용 origin과 origin별 경로 prefix
- 테스트 계정별 허용 역할과 세션 필요 여부
- 시나리오별 최대 요청 수와 실행 시간
- origin·경로 prefix·HTTP method별 상태 변경·데이터·서비스 영향 규칙

대상 범위·계정·요청 예산·상태 변경·데이터 영향·서비스 영향을 서로 독립적으로
평가한다. 등록되지 않은 요청 규칙은 `GET`이어도 영향 항목을 `unknown`으로
판정하며 자동 허용하지 않는다. 하나라도 `block`이면 최종 `block`, `block`은
없지만 `require_approval` 또는 `unknown`이 있으면 최종 `require_approval`, 6개
항목이 모두 `pass`인 경우에만 `allow`를 발행한다.

허용 판정에는 실제 사용한 origin·계정과 Policy 제한을 넣는다. 미허용 판정의
실행 범위와 제한은 비워 실행 가능한 판정처럼 보이지 않게 한다.

## 4단계 공개 실행·출력 저장

`entrypoint.run(operation, input_paths, output_dir, context)`는 `evaluate`만 지원한다.
호출자는 정책 설정 내용이나 private 파일 descriptor를 `context`에 전달하지
않는다. 설정 원본 위치는 `SAFETY_POLICY_CONFIG_PATH` 환경변수로 제공한다.

```python
context = {
    "run_id": "run_demo_001",
    "iteration": 0,
    "mode": "development",
    "run_root": "/trusted/runs/run_demo_001",
}
```

모듈이 원본을 검증한 뒤 자기 `private/safety_policy/policy.json`에 같은 바이트로
배치하고 내부에서 경로·SHA-256을 준비한다. Runner는 private 파일 생성·복사나
설정 해시 계산을 담당하지 않는다. 상대 원본 경로는 현재 작업 디렉터리 기준이며,
환경변수가 없거나 비어 있으면 기본 설정으로 대체하지 않는다. `.env` 파일을
자동으로 읽지 않으므로 실행 프로세스에 환경변수를 실제로 제공해야 한다.

Policy 설정은 `schemas/input/policy_config.schema.json`으로 검증하며 다음 값을
포함한다.

- `policy_id`, `policy_version`
- 허용 origin·경로 prefix
- 테스트 계정·역할·세션 필요 여부
- 최대 요청 수·실행 시간
- origin·경로·HTTP method별 상태 변경·데이터·서비스 영향 규칙

비밀번호·쿠키·토큰은 설정 계약에 없으며 미정의 키는 거절한다. 설정 파일의
경로 이탈·외부 symlink·해시 불일치도 평가 전에 거절한다.

처리 순서는 실행 인자·경로 검증 → Policy 설정 검증·사본 배치 → 시나리오 입력
검증 → 규칙 평가 → 출력 envelope 생성
→ 출력 Schema·시나리오 ID·입력 해시 재검증 → 임시 파일 flush·close → rename이다.
정상 입력은 `completed`, 유효한 partial 입력은 원본 오류를 보존한 `partial`,
평가·설정 실패는 `data=null`인 `failed`로 공개한다. 입력 계약이나 입력 파일
자체를 신뢰할 수 없으면 출력 파일 없이 실패 제어 응답을 반환한다.

설정 준비 단계의 누락·JSON/Schema 오류·기존 사본 충돌은 `CONFIG_INVALID`,
사본 해시 불일치는 `CONFIG_HASH_MISMATCH`, 저장 실패는 `STORAGE_FAILED` 제어
응답을 반환하고 판정 파일을 생성하지 않는다. 설정 사본을 준비한 뒤 평가에서
발견한 Policy 의미 오류는 기존처럼 failed 산출물로 공개한다. 완료 출력이 이미
있으면 정책을 배치하기 전에 `OUTPUT_EXISTS`로 거절한다.

현재 2단계에서는 기존 정책 사본을 덮어쓰거나 자동 재사용하지 않는다.
동일 run의 다음 회차·승인 후 재평가를 위한 같은 정책 재사용은 3단계에서
연결한다. 기존 사본을 임의로 삭제·교체하여 이 제한을 우회하지 않는다.

완료 제어 응답은 `operation`, `status`, `artifact_id`, `output_path`, `sha256`,
`errors`를 반환한다. 입력이나 설정이 평가 도중 변경되면 출력을 공개하지 않는다.

초기 4단계 구현에서는 `safety_decisions.json`의 v0.1 출력 Schema를 유지했다.
현재 지원 버전은 위 계약 파일 절을 따른다. Policy 설정 해시는 실행 입력에서만
검증하며 출력에는 기존 `policy_id`·`policy_version`을 기록한다. 현재 설정 해시는
호출자가 아니라 모듈이 준비한 내부 참조에서 검증한다.

## 5단계 승인 기록 재평가

승인 기록은 선택 입력이다. 기록이 없거나 `context.approval_record=null`이면 기존
사전 평가 결과를 그대로 발행한다. 기록이 있으면 내용 자체를 `context`에 넣지
않고 `private/safety_policy/approvals/` 아래 파일의 상대 경로와 SHA-256만 전달한다.

```python
context["approval_record"] = {
    "path": "private/safety_policy/approvals/approval_<id>.json",
    "sha256": "<64자리 소문자 SHA-256>",
}
```

승인 기록은 `schemas/input/approval_record.schema.json`으로 검증하고 다음 값에
정확히 묶는다.

- `run_id`, `iteration`
- `test_scenarios.json`의 정확한 바이트 SHA-256
- `policy_id`, `policy_version`
- 승인 대상 `scenario_id` 목록
- 승인자 식별자와 승인·만료 시각

승인으로 바꿀 수 있는 항목은 명시적인 `require_approval`뿐이다. `block`과
`unknown`은 승인 기록이 있어도 허용하지 않으며, 이미 허용된 시나리오에 승인을
붙이는 것도 거절한다. 검증된 승인으로 모든 assessment가 `pass`가 된 경우에만
새 `allow`를 발행하고 기존 출력 필드 `approval_ref`에 `approval_id`를 기록한다.
상태 변경 승인이 포함된 경우 verifier가 강제할 `limits.allow_state_change`도
`true`로 설정한다.

승인 파일이 평가 중 바뀌면 결과를 공개하지 않는다. 승인 기록 생성과 사용자
진위 확인은 향후 runner·UI 통합에서 이 입력 계약에 맞춰 연결하며, 현재 모듈은
미리 만들어진 독립 fixture로 재평가 경계를 검증한다. 초기 5단계 구현에서도
당시 `safety_decisions.json` 출력 Schema를 유지했다.

### CLI

```bash
export SAFETY_POLICY_CONFIG_PATH=/absolute/path/to/policy.json

.venv/bin/python -m modules.safety_policy.entrypoint evaluate \
  --run-root runs/run_demo_001 \
  --run-id run_demo_001 \
  --iteration 0 \
  --mode development
```

승인 기록을 적용할 때는 전용 경로를 선택 인자로 전달한다.

```bash
.venv/bin/python -m modules.safety_policy.entrypoint evaluate \
  --run-root runs/run_demo_001 \
  --run-id run_demo_001 \
  --iteration 0 \
  --mode development \
  --approval-record private/safety_policy/approvals/approval_demo_001.json
```

CLI는 시나리오·선택 승인 파일 해시만 계산한 뒤 공개 `run()`을 호출한다.
정책 로딩·검증·private 배치는 함수 호출과 같은 준비 로직에서 수행한다.

## 테스트

저장소 루트에서 실행한다.

```bash
.venv/bin/python -m pytest modules/safety_policy/
```

현재 결과: `218 passed`.

실제 비밀값과 실행 결과는 fixture나 Git에 저장하지 않는다.

## 변경 이력

아래 기록의 테스트 수치·연결 상태·다음 작업은 작성 당시 기준이다.
현재 계약과 연결 상태는 위 계약 파일 절을 따른다.

### 2026-10-09 — 6단계: 최종 회귀와 직접 연결 계약 확인

기준 커밋 `1b123bd`에서 사전 판정·승인 재평가·계획 해시·출력 저장의 기존 테스트를 다시 실행했다. 구현 코드·Schema·fixture·의존성은 변경하지 않고 검증 결과와 연계 조건을 기록했다.

| 경계 | 생산자 / 소비자 버전 | 확인 결과 |
|---|---|---|
| scenario_generator → safety_policy | `0.1.0` / `0.2.0` | 현재 공개 시나리오 fixture는 버전 불일치로 거절. 생산자 전환 필요 |
| safety_policy → reporter | `0.2.0` / `0.2.0` | 공개 판정 fixture의 입력 Schema 검증 통과. Schema 사본은 최상위 제목·설명·`$id`를 제외하면 일치 |
| safety_policy → verifier | `0.2.0` / `0.1.0` | 현재 verifier 입력 사본은 0.2 판정을 거절. 소비자 전환 필요 |

서로 다른 시나리오 파일의 SHA-256이나 승인 기록을 맞추기 위해 원본 산출물을 수정하지 않았다. `policy_config`·`approval_record`의 독립 0.1 계약과 `policy_version`도 유지한다.

검증 결과:

```text
safety_policy
145 passed

knowledge_graph + safety_policy + reporter 기본 테스트
661 passed, 4 skipped

같은 담당 3개 모듈, 실제 Neo4j 통합 테스트 활성화
665 passed

modules/ 전체 테스트
1456 passed, 4 skipped
```

기본 합동 검증은 `.venv/bin/python -m pytest modules/knowledge_graph modules/safety_policy modules/reporter -q -rs`, 전체 모듈 검증은 `.venv/bin/python -m pytest modules -q -rs --tb=short`로 실행했다. 기본 실행의 skip 4건은 KG의 실제 Neo4j 통합 테스트이며, 별도 일회성 DB 실행에서는 모두 통과했다. 전체 모듈 재실행은 Chromium·로컬 테스트 서버를 사용할 수 있는 환경에서 수행했다.

정상·partial·failed, 구버전 거절, 6개 assessment와 최종 판정 대응, 승인으로 block·unknown을 허용하지 않는 규칙, 변경된 계획의 승인 거절, 실제 출력 해시·input_ref·원자 저장을 회귀 확인했다. AST 검사에서 Python 파일 28개의 다른 모듈 import 0건, Schema 4개의 외부 `$ref` 0건을 확인했다.

담당 모듈 내부 수정 1~6단계는 완료했다. 다만 독립 테스트 통과가 위 두 연계 버전 차이의 해소를 의미하지는 않는다. scenario_generator·verifier 담당자가 각각 자기 계약을 반영한 뒤 같은 실행의 계획·판정·검증 결과로 연결을 확인해야 한다. 다른 모듈·공용 명세·실제 실행 결과는 수정하지 않았다.

### 2026-10-09 — 공개 입출력 계약 0.2.0 동기화

공통 명세 `docs/spec/02-common-contract.md`의 팀 합의에 맞춰
`test_scenarios.json` 입력 Schema, `safety_decisions.json` 출력 Schema와
출력 adapter의 envelope 버전을 `0.2.0`으로 동기화했다. 버전 이외의 공개
필드·타입·허용값과 안전 판단 의미는 변경하지 않았다.

- `0.1.0`과 미지원 버전은 입력·출력 검증에서 거절한다. 구버전 입력은
  `CONTRACT_INVALID` 제어 응답을 반환하고 판정 파일을 생성하지 않는다.
- 정상·partial·failed 독립 fixture의 산출물 버전을 갱신했다. 정상 판정의
  `input_refs`·`scenarios_sha256`과 승인 fixture의 계획 해시는 변경된
  시나리오 파일의 정확한 바이트로 다시 계산했다.
- partial 판정 fixture에도 대응하는 시나리오 참조와 실제 해시를 넣어
  Schema뿐 아니라 입력·출력 대응 검증을 추가했다.
- `policy_config`·`approval_record` Schema는 `0.1.0`을 유지한다.
  `policy_version`은 실제 설정값을 그대로 출력하며 산출물 버전과 구분한다.
- 6개 assessment, allow/block/require_approval 판정, 승인으로 block·unknown을
  허용하지 않는 규칙, 실행 범위·제한·경로 검증·원자 저장은 그대로 유지했다.
- 공개 `evaluate`와 CLI의 상태별 `0.2.0` 출력, 입력 참조·계획 해시·완료 응답
  해시 보존, 승인 재평가, 구버전 입력·출력 거절 회귀 검증을 보강했다.

승인 fixture 갱신은 독립 테스트 데이터에만 적용했다. 실제 승인 기록을 새 계획에
자동으로 연결하지 않는다. 버전 변경만으로 파일 바이트가 달라져도 이전 계획의
승인은 새 계획에 사용할 수 없으며 새 승인이 필요하다. 이전 `0.1.0` 파일
바이트의 해시로 묶인 승인 기록이 현재 계획에서 거절되는 것을 검증했다.

이번 변경은 `modules/safety_policy/**` 안에서만 수행했다. 기준 커밋
`dd6c4cc`의 scenario_generator 출력과 verifier·reporter 입력은 아직
`0.1.0`이다. 각 담당자의 `0.2.0` 전환과 실제 산출물 수신 검증 전에는
전체 파이프라인 연결 완료로 보지 않는다. 다른 모듈의 산출물·Schema와 실제
실행 결과는 수정하지 않았다.

검증 명령과 결과:

```bash
.venv/bin/python -m pytest modules/safety_policy -q
# 145 passed

.venv/bin/python -m pytest modules/knowledge_graph modules/access_analyzer modules/safety_policy modules/reporter -q
# 528 passed, 4 skipped
```

skip 4건은 실제 Neo4j를 사용하는 KG 통합 테스트다. 연관 모듈 회귀는 각 모듈의
독립 fixture 검증이며 위 버전 차이가 해소되었다는 의미는 아니다.

다음 단계는 reporter의 공개 입출력 계약을 `0.2.0`에 맞추는 작업이다.

### 2026-10-10 — Scenario.resource_ids 호환 수정과 합동 회귀

명세 `docs/spec/m5-scenario_generator.md`의 소비자 미러 기준에 맞춰
입력 사본을 동기화했다. 구현 커밋은 `91425cd`, 합동 검증 기준은 `645efaf`다.

- 입력 Schema에 필수 `resource_ids`를 추가하고 버전 `0.2.0`을 유지했다.
  최상위 `$id`·제목·설명을 제외하면 SG 출력 Schema와 일치한다.
- 정상 시나리오 fixture에 자원 ID를 추가하고 판정 fixture의 `input_refs`·
  `scenarios_sha256`, 승인 fixture의 계획 해시를 실제 파일 바이트로 갱신했다.
  기존 빈 partial·failed fixture는 유지했다. 실제 승인 기록은 수정하지 않았다.
- 독립 테스트 20건을 추가했다. 입력 제약, 불투명 ID·순서·중복 값 보존,
  정상/partial 처리와 ID 값·순서·개수 변경 후 기존 승인 거절을 검증했다.
- 판정 Python 코드·6개 규칙·내부 모델·출력 계약·설정 및 의존성은 변경하지 않았다.

검증 결과:

```text
safety_policy 전체: 165 passed
knowledge_graph + safety_policy + reporter: 826 passed, 12 skipped
```

합동 명령은 `.venv/bin/python -m pytest modules/knowledge_graph modules/safety_policy modules/reporter -q -rs`다.
skip 12건은 활성화하지 않은 KG 실제 Neo4j 테스트이며 이번 변경에서 DB는 실행하지 않았다.
SG 공개 시나리오 2개의 Schema·의미 검증, 입력 불변, 정상/partial/failed·계획 해시·
승인 회귀를 확인했다. Python 28개 파일의 다른 모듈 import와 Schema 4개의
외부 `$ref`는 각각 0건이다.

소비자 호환 수정·회귀·README 정리는 마무리한다. Verifier 담당자의 입력
`test_scenarios`·`safety_decisions` 0.2 전환과 검증 관계 `source_account_id`
전환 이후, 같은 run의 실제 산출물로 전체 연결을 검증해야 한다.

### 2026-10-10 — PR #52·#53 병합 후 현재 상태·명세 동기화

기준 커밋 `695b3c8`에서 현재 연결 표를 갱신하고
`docs/spec/m6-safety_policy.md`에 기존 구현·공개 계약과 완료 범위를 반영했다.
이전 단계의 버전 불일치 기록은 당시 이력으로 보존했다.

- Verifier의 `test_scenarios`·`safety_decisions` 입력 0.2 전환 완료를 반영했다.
- 필수 자원 ID의 불투명 값 보존, 전체 계획 SHA-256·승인 바인딩, 구버전 거절을
  명세에 기록했다. 기존 6개 규칙·출력 계약은 유지한다.
- 독립 구현·공개 fixture 호환과 실제 동일 run 연결 완료를 구분했다.
  사용자 승인 창구의 통합·진위 확인은 별도 후속 작업이다.

```bash
.venv/bin/python -m pytest modules/knowledge_graph modules/safety_policy modules/reporter modules/verifier -q -rs
# 897 passed, 12 skipped
```

skip 12건은 이번에 활성화하지 않은 KG 실제 Neo4j 테스트다. 이번 변경은 문서만
갱신하며 코드·Schema·fixture·테스트·의존성·실제 승인 기록을 변경하지 않는다.

### 2026-10-10 — 정책 설정 준비 1단계: 외부 설정 검증·private 배치

기준 커밋 `5ecbba4`에서 정책 설정을 모듈이 직접 준비하는 내부 기능을 추가했다.
이번 단계는 `config_adapter.prepare_policy_configuration(run_root)`와 자기
유틸리티·테스트만 구현하며, 공개 `run()`·CLI에는 아직 연결하지 않았다.
따라서 기존 실행 창구의 `context.policy_config`와 사전 private 파일 요구는
2단계 연결 전까지 그대로 유지된다.

- `SAFETY_POLICY_CONFIG_PATH` 환경변수로 원본 정책 JSON 경로를 받는다.
  상대 경로는 현재 작업 디렉터리 기준이다. 미설정·빈 값·파일 누락·읽기 실패는
  오류로 처리하며 기본 정책이나 자동 허용 설정을 생성하지 않는다.
- 원본을 한 번 읽고 UTF-8 JSON·중복 키·비유한 숫자·최상위 object와 기존
  `policy_config` Schema `0.1.0`을 검증한다. 검증한 동일 바이트를 저장하므로
  원본 파일의 공백·줄바꿈과 규칙 값을 변경하지 않는다.
- 자기 `private/safety_policy/policy.json`에 임시 파일 write·flush·fsync·close 후
  기존 파일을 대체하지 않는 원자적 공개로 배치한다. 사본의 실제 SHA-256과
  원본 바이트 해시가 일치해야 경로·해시를 담은 불변 내부 참조를 반환한다.
- 존재하는 정책은 덮어쓰지 않는다. 동시 작성으로 파일이 먼저 공개된 경우도
  보존한다. 저장 실패 시 임시 파일을 정리하며 private 경로의 외부·다른 모듈
  symlink를 거절한다. 동일 정책 재사용은 3단계에서 추가한다.
- 준비 기능·바이트 저장 테스트 31건을 추가했다. 기존 설정 loader 호환,
  설정 누락·JSON/Schema 오류·읽기/저장 실패·해시 불일치·원본 변경·동시 공개
  및 symlink 경계를 검증했다.

```bash
.venv/bin/python -m pytest modules/safety_policy/tests/test_policy_preparation.py modules/safety_policy/tests/test_config_adapter.py modules/safety_policy/tests/test_utils.py -q
# 44 passed

.venv/bin/python -m pytest modules/safety_policy -q
# 196 passed

.venv/bin/python -m pytest modules/knowledge_graph modules/safety_policy modules/reporter modules/verifier -q -rs
# 928 passed, 12 skipped
```

합동 회귀의 skip 12건은 활성화하지 않은 KG 실제 Neo4j 통합 테스트다.
이번 단계에서 실제 pipeline 연결이나 Neo4j 실행은 수행하지 않았다.

수정은 `modules/safety_policy/**` 안에서만 수행했다. 기존 6개 안전 판정 규칙,
입출력 Schema `0.2.0`, 정책·승인 Schema `0.1.0`, 승인 로직·의존성·공개 호출
규약은 변경하지 않았다. 실제 run 폴더나 승인 기록은 읽거나 변경하지 않았다.
다음 단계는 공개 `run()`·CLI에 준비 기능을 연결하고 호출자의 필수
`context.policy_config` 전달을 제거하는 작업이다.

### 2026-10-10 — 정책 설정 준비 2단계: 공개 run·CLI 연결

기준 커밋 `b664976`의 정책 준비 기능을 공개 실행 창구에 연결했다.

- `context`에서 `policy_config`를 제거했다. 필수 실행 값 4개와 선택 승인
  descriptor만 받으며 기존 Policy descriptor·미정의 키는 묵시적으로 무시하지
  않고 `CONTRACT_INVALID`로 거절한다. 호출자의 mapping은 변경하지 않는다.
- 실행 인자·입출력·승인 경로를 먼저 검증하고, 기존 완료 출력도 먼저 거절한다.
  이어 환경변수의 정책을 검증·배치하고 내부 `EvaluationRequest`에 사본 경로와
  해시를 묶는다. CLI는 private 정책 파일 존재 확인·해시 계산을 하지 않는다.
- 공개 실행에서 설정 준비 오류를 실패 제어 응답으로 반환한다. 준비 이후의
  기존 Policy 의미 오류·승인 오류·출력 저장 실패 처리와 판정 규칙은 유지했다.
- 내부 모델을 실행 인자와 정책이 바인딩된 평가 요청으로 구분했다. Policy 경로
  상수는 설정 adapter가 소유하도록 옮겨 입력·설정 adapter의 순환 의존을 피했다.
- 테스트 준비는 외부 정책 파일·환경변수를 제공하고 private 정책은 실제 모듈이
  생성하게 바꿨다. 기존 입력·Policy·승인·출력 회귀를 새 내부 요청 준비 방식에
  맞추고 함수/CLI 동일 결과, 필수 환경변수, legacy context 거절, 준비 실패,
  내부 참조·필수 실행 키 검증 등 22건을 추가했다.

```bash
.venv/bin/python -m pytest modules/safety_policy -q
# 218 passed

.venv/bin/python -m pytest modules/knowledge_graph modules/safety_policy modules/reporter modules/verifier -q -rs
# 950 passed, 12 skipped
```

합동 회귀의 skip 12건은 활성화하지 않은 KG 실제 Neo4j 통합 테스트다.
동일 run의 실제 전체 pipeline·사용자 승인 창구 연결은 이번 검증 범위가 아니다.

기존 1단계 이력과 공개 JSON Schema·버전·fixture 파일·안전 규칙·승인 판정·
의존성은 유지했다. 변경 범위는 `modules/safety_policy/**`다. Runner 담당자는
기존 `context.policy_config` 전달을 제거하고 실행 프로세스에 환경변수를 제공해야
하며, 다른 모듈·Runner·공통 명세 파일은 직접 수정하지 않았다.
동일 run의 기존 정책 사본 재사용·정책 변경 보호는 다음 3단계에서 추가한다.
