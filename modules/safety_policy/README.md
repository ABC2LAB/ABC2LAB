# safety_policy

`test_scenarios.json`을 LLM과 독립된 규칙으로 평가해
`safety_decisions.json`을 생성하는 모듈이다.

## 공개 operation

- `evaluate`: 시나리오별 실행 허용·차단·승인 요청 판정

공개 operation은 후속 단계에서 구현한다. 현재는 계약 기반과 `evaluate` 입력
adapter까지 구현되어 있다.

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
- `context`는 `run_id`, `iteration`, `mode`, `run_root`만 허용
- 출력 경로는 현재 iteration의 safety_policy 산출물 폴더와 정확히 일치
- 상대 경로 이탈, 절대 외부 경로, 다른 run 경로, 잘못된 SHA-256 형식 거절

`service.prepare_evaluation()`은 실제 파일 SHA-256과 Schema를 검증하고 envelope의
`run_id`, `iteration`, `mode`가 실행 context와 일치하는지 확인한다. 유효한
`partial` 입력은 원본 오류와 함께 보존하고 `failed` 입력은 평가 대상으로 받지
않는다.

## 테스트

저장소 루트에서 실행한다.

```bash
.venv/bin/python -m pytest modules/safety_policy/
```

실제 비밀값과 실행 결과는 fixture나 Git에 저장하지 않는다.
