# scenario_generator

검증 후보(`vulnerability_candidates.json`)와 원본 수집 결과(`crawl_result.json`)를 받아
후보마다 **재현 계획**(`test_scenarios.json`)을 만든다. 계정·요청 순서·동적 값 바인딩·판정 조건을 담는다.

- 기준 명세: `docs/spec/m5-scenario_generator.md` (공통: `02-common-contract.md`, `03-runner-layout.md`)
- 공개 operation: `generate`
- 지원 `schema_version`: `0.1.0`
- 직접 소비자: safety_policy · verifier · reporter
- 이 모듈은 요청을 보내지 않는다. 실행 허용은 safety_policy가 정한다.

## 입력과 출력

| 구분 | 파일 | 생산자 | 이 모듈의 Schema |
|---|---|---|---|
| 입력 | `vulnerability_candidates.json` | access_analyzer | `schemas/input/vulnerability_candidates.schema.json` |
| 입력 | `crawl_result.json` | collector | `schemas/input/crawl_result.schema.json` |
| 출력 | `test_scenarios.json` | scenario_generator | `schemas/output/test_scenarios.schema.json` |

입력 Schema는 생산자 출력 계약의 사본이다. 계약이 바뀌면 생산자와 합의한 뒤 같이 고친다.
출력 샘플: `tests/fixtures/runs/run_demo_001/artifacts/iteration-000/scenario_generator/test_scenarios.json`

## 실행

레포 루트에서 실행한다 (공통 `.venv`).

```
.venv/bin/python -m modules.scenario_generator.entrypoint generate \
  --run-root runs/<run_id> --run-id <run_id> --iteration 0 --mode development \
  --candidates <vulnerability_candidates.json> --crawl-result <crawl_result.json> \
  --output-dir runs/<run_id>/artifacts/iteration-000/scenario_generator \
  [--drafts <초안 파일>] [--expected-sha256 <입력종류>=<해시>]
```

Python 호출: `entrypoint.run(operation, input_paths, output_dir, context)`

| 인자 | 내용 |
|---|---|
| `operation` | `"generate"` |
| `input_paths` | `{"vulnerability_candidates": 경로, "crawl_result": 경로}` — 두 키 모두 필요 |
| `output_dir` | 저장할 폴더. `run_root` 안이어야 한다. 파일명은 `test_scenarios.json` 고정 |
| `context` | 필수: `run_root`, `run_id`, `iteration`, `mode` / 선택: `expected_sha256`(`{입력종류: 소문자 hex}`), `drafter` |

응답(dict, CLI는 stdout JSON): `status`, `artifact_id`, `output_path`, `sha256`, `scenario_count`, `error_count`.

종료 코드(CLI): 파일을 썼으면 `0`(결과 상태는 응답의 `status`), 쓰지 못했으면 `2`.
파일을 쓰지 못하면 `OutputWriteError`를 던진다. 없는 파일을 완료라고 알리지 않는다.

> 호출 규약(인자 이름·응답·종료 코드)은 `pipeline.py` 담당과 아직 합의 전이다. 위 내용은 이 모듈의 제안이다.

## 상태 규칙

| status | 조건 | errors / data |
|---|---|---|
| `completed` | 오류 없음. 후보가 0개여도 해당 | `[]` / `scenarios`(빈 배열 가능) |
| `partial` | 일부 후보 실패, 또는 입력이 `partial` | 1개 이상 / 만든 시나리오 |
| `failed` | 입력 오류·상류 `failed`·LLM 미설정, 또는 후보가 있는데 시나리오가 하나도 없음 | 1개 이상 / `null` |

입력 문제는 가짜 정상으로 숨기지 않고 `failed` 파일로 남긴다.

## 처리 순서

1. **입력 adapter** (`input_adapter.py`): 경로(run 루트 안)·해시·엄격한 JSON(중복 키·NaN 거절)·`schema_version`·Schema·`run_id`·상류 상태 확인
2. **후보 대조** (`candidate_matcher.py`): 실행·기준 계정, 역할, 근거 요청, `session_ref`가 `crawl_result`와 맞는지 확인. ID만 존재하고 관계가 다르면 거절
3. **초안 생성** (`scenario_drafter.py`): LLM에게 줄 입력을 만든다(헤더·쿠키·응답 본문·근거 경로·비밀 파라미터 값은 제외)
4. **초안 검증** (`scenario_validator.py`): Schema + 의미 검사. 통과한 초안만 시나리오가 된다
5. **출력 adapter** (`output_adapter.py`) → **원자적 저장** (`utils/atomic_io.py`): 완료 파일은 덮어쓰지 않는다

후보 하나가 실패해도 나머지는 계속 처리하고, 실패는 `item_ref=candidate_id`로 `errors`에 남긴다.

## 오류 코드

| 코드 | 의미 | item_ref |
|---|---|---|
| `INPUT_UNREADABLE` `INPUT_PATH_UNSAFE` `INPUT_JSON_INVALID` `INPUT_VERSION_UNSUPPORTED` `INPUT_SCHEMA_INVALID` `INPUT_HASH_MISMATCH` `INPUT_RUN_MISMATCH` `INPUT_DUPLICATE_ID` | 입력 파일을 쓸 수 없음 | null |
| `INPUT_UPSTREAM_FAILED` | 상류 산출물이 `failed` | null |
| `INPUT_UPSTREAM_PARTIAL` | 상류 산출물이 `partial` — 누락 범위는 상류 errors를 따른다 | null |
| `CANDIDATE_ID_DUPLICATE` | `candidate_id` 중복(해당 후보 모두 제외) | candidate_id |
| `CANDIDATE_ACCOUNT_INVALID` | 실행·기준 계정 또는 역할 관계가 원본과 다름 | candidate_id |
| `CANDIDATE_REQUEST_INVALID` | 근거 요청이 없거나 계정·역할·세션이 어긋남 | candidate_id |
| `DRAFTER_FAILED` | 초안 생성 실패 (`retryable`은 drafter가 알려 준 값) | candidate_id |
| `DRAFT_INVALID` | 초안이 검증을 통과하지 못함 (위치와 규칙만 적고 값은 적지 않음) | candidate_id |
| `DRAFTER_NOT_CONFIGURED` | 초안 생성기(LLM)가 설정되지 않음 | null |

## 초안 검증 규칙

Schema(필드·타입·enum, 미정의 키 거절) 외에 다음을 코드로 확인한다.

- `steps`와 `assertions`가 비어 있지 않다
- `assertions`에 `response_status`·`session_valid` 말고 응답 내용·자원 상태를 보는 조건(`response_json`·`resource_state`·`resource_owner`·`baseline_match`)이 최소 1개 있다. 명세 m7은 HTTP 200만으로 위반을 확정하지 않으므로, 상태 코드뿐인 계획은 verifier에서 판단불가가 된다
- `response_status`·`response_json` 조건의 `subject_ref`는 steps에 있는 `step_id`다(응답은 단계에서만 나온다)
- `steps.order`가 0..N-1을 순서대로 유일하게 채운다. `step_id`·`binding_id`·`check_id`가 중복이 아니다
- 바인딩은 **앞선 단계**만 가리킨다. 파라미터의 `binding_ref`와 `url_template`의 `{binding_id}`는 정의된 바인딩만 쓴다
- 바인딩 selector: 본문은 JSON Pointer 문법, 헤더는 소문자 이름. `response_json` 조건의 selector도 JSON Pointer
- 단계의 계정은 후보의 **실행 계정 또는 기준 계정**만 쓰고, `role_id`·`session_ref`가 그 계정과 같다
- `source_request_id`가 `crawl_result`에 있고, `method`가 원본 요청과 같고, `body_ref`는 null이거나 원본 요청의 것이다
- `url_template`의 scheme·host·port가 `crawl_result.target_url`과 같다. 주소 부분에는 바인딩·사용자 정보를 쓸 수 없다
- `scenario_id`·`candidate_id`·`expected_basis`는 프로그램이 원본 후보에서 채운다. 초안에 이 키가 있으면 거절한다

## 초안 생성기(LLM) 연결

`scenario_drafter.ScenarioDrafter` 규격만 만족하면 바꿔 끼울 수 있다.

```python
class ScenarioDrafter(Protocol):
    model_info: dict | None                      # test_scenarios.json의 data.model_info에 그대로 들어감
    def draft(self, request: dict) -> Draft: ...  # Draft(scenario, input_tokens, output_tokens)
```

- 초안은 `{"preconditions", "steps", "assertions"}` 세 키만 가진 객체여야 한다
- 호출 실패는 `DrafterError`로 감싸서 던진다(메시지에 비밀값·프롬프트 원문 금지)
- **실제 LLM 클라이언트는 아직 없다.** 모델·API 키 위치·패키지가 정해지면 추가한다 (새 패키지는 이 폴더 `requirements.txt` 선언 + 관리자 lock 재생성)
- 개발·테스트용 `replay_drafter.ReplayScenarioDrafter`는 `--drafts` 파일의 초안을 그대로 돌려준다. `model_info.model_id`는 `replay_file`로 남아 LLM이 아님을 드러낸다

## 명세 해석과 미정 사항

명세에 직접 적혀 있지 않아 이 모듈이 정한 것이다. 소비자와 합의되기 전까지 임시이다.

| 항목 | 이 모듈의 현재 처리 | 확인 필요 |
|---|---|---|
| 후보와 시나리오의 수 | 후보 1개당 시나리오 1개. `scenario_id = "scenario_" + candidate_id` | 이경준 |
| `assertions`의 의미 | "모두 참이면 위반이 재현됨"으로 읽는다(명세: 위반 재현 여부를 판단할 조건) | verifier |
| `Check.subject_ref`의 내용 | `response_status`·`response_json`은 step_id인지 검사한다. `session_valid`는 계정 ID를 쓰고(verifier 구현 기준) 검사하지 않는다. `resource_state`·`resource_owner`·`baseline_match`의 subject_ref·selector 규약은 정해지지 않았다 | verifier |
| `url_template`의 `{binding_id}` 와 `parameters(location=path)` | 검증은 정의된 바인딩만 쓰는지까지만. 샘플은 `{binding_id}`만 쓴다 | verifier |
| `Binding`의 위치 | 값을 **쓰는** 단계의 `bindings`에 두고 `source_step_id`로 앞 단계를 가리킨다 | verifier |
| 기준 계정 단계 | 기준(소유자) 계정으로 보내는 단계를 steps에 둘 수 있다 | 이경준 |
| 근거 요청의 계정 | `source_request_ids`의 요청이 반드시 실행 계정의 것일 필요는 없다 | access_analyzer |
| `method`·`url`·계정 제한 | 위 "초안 검증 규칙"의 해석 | 이경준 |

## 한계

- `created_at`·`observed_at`이 실제 날짜 형식인지는 검사하지 않는다 (`date-time` 형식 검사에 추가 패키지가 필요)
- `resource_ids`·`workflow_id`는 `crawl_result`로 검증할 수 없어 그대로 전달한다
- 응답 본문을 저장하지 않으므로 바인딩 selector가 실제 응답에 있는지는 이 모듈이 알 수 없다 (verifier가 실행 시 확인)

## 테스트

```
.venv/bin/python -m pytest modules/scenario_generator
```

다른 모듈의 실제 코드 없이 `tests/fixtures/`의 입력과 대역 drafter로 정상·partial·failed·입력 오류·함정 케이스를 검증한다.
테스트는 계정·요청 ID에 기대지 않고 fixture에서 값을 읽는다.

## 의존성

공통 잠금 환경의 `jsonschema`만 쓴다. 이 폴더 `requirements.txt`에는 추가 선언이 없다.
