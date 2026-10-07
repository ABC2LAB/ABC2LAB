# reporter

인가·비즈니스 로직 진단 결과를 정리하고 개발용 성능 지표를 계산하는 모듈이다.
외부 템플릿이나 프런트엔드 의존성 없이 로컬 HTML도 생성한다.

## 공개 operation

- `report`: 후보·시나리오·Safety 판정·검증 결과를 연결해
  `diagnosis_report.json`과 로컬 HTML을 생성한다.
- `evaluate`: development 모드에서 수집·의미 분석·KG snapshot·진단 결과를
  `ground_truth.json`과 비교해 `evaluation_results.json`을 생성한다.

`diagnosis_report.json`과 HTML의 직접 사용자는 로컬 진단 사용자이고,
`evaluation_results.json`의 직접 사용자는 개발 평가 사용자다. 다른 진단 모듈은
reporter 출력을 다음 단계 입력으로 사용하지 않는다.

공개 함수는 다음 시그니처를 유지한다.

```python
from modules.reporter.entrypoint import run

response = run(operation, input_paths, output_dir, context)
```

완료 응답은 `status`, `artifact_id`, `output_path`, `sha256`, `errors`를 반환한다.
`report` 성공 응답에는 `report_path`도 포함한다. 입력·저장 오류로 파일을 발행하지
못하면 경로와 해시는 `null`이고 `status=failed`이다.

## 계약과 경로

- 계약 버전: `0.1.0`
- 입력 Schema: `schemas/input/`
- 출력 Schema: `schemas/output/`
- 계약 JSON: `runs/<run_id>/artifacts/iteration-<NNN>/reporter/`
- 로컬 HTML: `runs/<run_id>/reports/diagnosis_report-iteration-<NNN>.html`
- Ground Truth: `datasets/<dataset_id>/ground_truth.json`

`report` 입력은 다음 네 파일이다.

- `vulnerability_candidates.json`
- `test_scenarios.json`
- `safety_decisions.json`
- `verification_results.json`

`evaluate`는 위 파일에 `crawl_result.json`, `semantic_analysis.json`,
`graph_query_result.json`, `ground_truth.json`을 더 사용한다. Ground Truth는
`evaluate`에서만 읽으며 실제 진단과 다른 모듈로 전달하지 않는다.

입력 descriptor는 `path`, `sha256`만 허용한다. 실행 JSON은 `run_root`, Ground
Truth는 `project_root` 기준 상대 경로여야 하며 상위 경로·절대경로·외부 symlink를
거절한다. 지원 버전, envelope, ID·참조·revision·파일 해시도 처리 전에 검증한다.

`report` context는 `run_id`, `iteration`, `mode`, `run_root`, `target_url`을 정확히
받는다. `evaluate`는 여기에 `project_root`, `matching_profile`을 추가로 요구하고
`mode=development`만 허용한다. 대상 URL, 데이터셋, 계정 ID, 엔드포인트 값은
코드에 고정하지 않는다.

## 진단 분류와 HTML

리포트 항목 상태는 다음 여섯 가지다.

- `confirmed`: 규칙 기반 기대조건 위반과 실제 실행 근거가 모두 확인됨
- `suspected`: 위반 정황은 있으나 기대조건 또는 근거가 확정에 부족함
- `not_confirmed`: 유효한 검증에서 위반이 재현되지 않음
- `indeterminate`: 입력·실행·근거 부족으로 판단할 수 없음
- `policy_blocked`: Safety Policy가 실행을 차단함
- `approval_pending`: 사용자 승인을 기다려 실행하지 않음

미실행·판단불가를 취약점 없음이나 오탐으로 바꾸지 않는다. HTML은 계약 JSON을
그대로 표현하는 파생 출력이며 동적 문자열을 HTML escape한다. 외부 스크립트,
외부 스타일, 네트워크 요청을 사용하지 않아 폐쇄망에서 파일로 바로 열 수 있다.
인쇄 스타일이 포함되어 브라우저의 인쇄 기능으로 PDF 저장도 가능하다.

## 개발 평가

기본 매칭 규칙은 `default-v1`이다. GT 내부 ID와 분석 ID 문자열을 직접 비교하지
않고 Page path, Endpoint method+path template, Parameter 위치·이름, Role 이름,
Resource 정규화 키로 매칭한다.

평가는 구조 엔티티 7종, 관계, 업무 흐름, 후보 recall/precision, 확정
recall/precision을 기록한다. 모든 지표는 분자·분모를 함께 남기고 분모가 0이거나
측정할 수 없으면 `value=null`이다. 정책 차단·승인 대기·판단불가도 별도 집계하며
전체 정답 양성 분모에서 제외하지 않는다.

## CLI

명령은 저장소 루트에서 실행한다. 입력 경로는 명세의 producer별 고정 경로로
구성하고 CLI가 실제 파일 SHA-256을 계산한 뒤 공개 `run()`을 호출한다.

진단 리포트:

```bash
.venv/bin/python -m modules.reporter.entrypoint report \
  --run-root runs/<run_id> \
  --run-id <run_id> \
  --iteration 0 \
  --mode diagnosis \
  --target-url http://target.internal
```

개발 평가:

```bash
.venv/bin/python -m modules.reporter.entrypoint evaluate \
  --run-root runs/<run_id> \
  --run-id <run_id> \
  --iteration 0 \
  --mode development \
  --target-url http://target.internal \
  --project-root . \
  --dataset-id <dataset_id> \
  --matching-profile default-v1
```

`--output-dir`은 생략할 수 있다. 지정한다면 현재 회차의
`artifacts/iteration-<NNN>/reporter`와 정확히 같아야 한다. CLI는 completed와
partial일 때 종료 코드 0, failed일 때 1을 반환한다.

## 실패 처리

- 잘못된 인자·Schema·ID·참조: `CONTRACT_INVALID`
- 경로 이탈·입력 파일 없음: `PATH_INVALID`
- 전달 해시와 실제 파일 불일치: `INPUT_HASH_MISMATCH`
- 불변 출력이 이미 존재함: `OUTPUT_EXISTS`
- 출력 검증 실패: `OUTPUT_INVALID`
- 저장 실패: `STORAGE_FAILED`

유효한 partial 입력은 원본 오류와 누락 범위를 출력에 보존한다. 사용할 결과가
없으면 `data=null`인 failed 계약 JSON을 발행할 수 있다. 입력 자체를 신뢰할 수
없거나 저장하지 못한 경우에는 파일 없이 실패 제어 응답을 반환한다. 완료 파일은
덮어쓰지 않는다.

## 독립 테스트

다른 모듈 실제 코드 없이 공개 계약 fixture로 정상·partial·failed, 잘못된 입력,
불변 저장, HTML escape, CLI 동작을 검증한다.

```bash
.venv/bin/python -m pytest modules/reporter/
```

추가 실행 의존성은 없다. 팀 공통 Python과 루트 잠금 환경을 사용한다.
