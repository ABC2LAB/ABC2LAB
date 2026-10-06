# safety_policy

`test_scenarios.json`을 LLM과 독립된 규칙으로 평가해
`safety_decisions.json`을 생성하는 모듈이다.

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
- 계약 버전: `0.1.0`

일반 객체의 미정의 키는 거절한다. `status=completed`는 빈 `errors`와 유효한
`data`, `partial`은 하나 이상의 오류와 유효한 `data`, `failed`는 하나 이상의
오류와 `data=null`을 요구한다.

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
- `context`는 `run_id`, `iteration`, `mode`, `run_root`, `policy_config`만 허용
- `policy_config` descriptor는 `path`, `sha256`만 허용
- Policy 설정 경로는 `private/safety_policy/policy.json`으로 제한
- 출력 경로는 현재 iteration의 safety_policy 산출물 폴더와 정확히 일치
- 상대 경로 이탈, 절대 외부 경로, 다른 run 경로, 잘못된 SHA-256 형식 거절

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
`context.policy_config`에는 Policy 설정 내용이 아니라 신뢰 경로 안의 상대 경로와
정확한 파일 SHA-256만 전달한다.

```python
context = {
    "run_id": "run_demo_001",
    "iteration": 0,
    "mode": "development",
    "run_root": "/trusted/runs/run_demo_001",
    "policy_config": {
        "path": "private/safety_policy/policy.json",
        "sha256": "<64자리 소문자 SHA-256>",
    },
}
```

Policy 설정은 `schemas/input/policy_config.schema.json`으로 검증하며 다음 값을
포함한다.

- `policy_id`, `policy_version`
- 허용 origin·경로 prefix
- 테스트 계정·역할·세션 필요 여부
- 최대 요청 수·실행 시간
- origin·경로·HTTP method별 상태 변경·데이터·서비스 영향 규칙

비밀번호·쿠키·토큰은 설정 계약에 없으며 미정의 키는 거절한다. 설정 파일의
경로 이탈·외부 symlink·해시 불일치도 평가 전에 거절한다.

처리 순서는 입력 adapter → Policy 설정 adapter → 규칙 평가 → 출력 envelope 생성
→ 출력 Schema·시나리오 ID·입력 해시 재검증 → 임시 파일 flush·close → rename이다.
정상 입력은 `completed`, 유효한 partial 입력은 원본 오류를 보존한 `partial`,
평가·설정 실패는 `data=null`인 `failed`로 공개한다. 입력 계약이나 입력 파일
자체를 신뢰할 수 없으면 출력 파일 없이 실패 제어 응답을 반환한다.

완료 제어 응답은 `operation`, `status`, `artifact_id`, `output_path`, `sha256`,
`errors`를 반환한다. 입력이나 설정이 평가 도중 변경되면 출력을 공개하지 않는다.

`safety_decisions.json`의 v0.1 출력 Schema는 변경하지 않는다. Policy 설정 해시는
실행 입력에서만 검증하며 출력에는 기존 `policy_id`·`policy_version`을 기록한다.

### CLI

```bash
.venv/bin/python -m modules.safety_policy.entrypoint evaluate \
  --run-root runs/run_demo_001 \
  --run-id run_demo_001 \
  --iteration 0 \
  --mode development
```

CLI도 같은 고정 입력·설정·출력 경로를 사용하며 파일 해시를 계산한 뒤 공개
`run()`을 호출한다.

## 테스트

저장소 루트에서 실행한다.

```bash
.venv/bin/python -m pytest modules/safety_policy/
```

실제 비밀값과 실행 결과는 fixture나 Git에 저장하지 않는다.
