# scenario_generator

검증 후보(`vulnerability_candidates.json`)와 원본 수집 결과(`crawl_result.json`)를 받아
후보마다 **재현 계획**(`test_scenarios.json`)을 만든다. 계정·요청 순서·동적 값 바인딩·판정 조건을 담는다.

- 기준 명세: `docs/spec/m5-scenario_generator.md` (공통: `02-common-contract.md`, `03-runner-layout.md`)
- 공개 operation: `generate`
- 지원 `schema_version`: `0.2.0` (입력 `crawl_result`·`vulnerability_candidates`, 출력 `test_scenarios`)
- 직접 소비자: safety_policy · verifier · reporter
- 이 모듈은 요청을 보내지 않는다. 실행 허용은 safety_policy가 정한다.

## 입력과 출력

| 구분 | 파일 | 생산자 | 이 모듈의 Schema |
|---|---|---|---|
| 입력 | `vulnerability_candidates.json` | access_analyzer | `schemas/input/vulnerability_candidates.schema.json` |
| 입력 | `crawl_result.json` | collector | `schemas/input/crawl_result.schema.json` |
| 출력 | `test_scenarios.json` | scenario_generator | `schemas/output/test_scenarios.schema.json` |

입력 Schema는 생산자 출력 Schema를 그대로 복사한 사본이다. 계약이 바뀌면 생산자와 합의한 뒤 같이 고친다.
출력 샘플: `tests/fixtures/runs/run_demo_001/artifacts/iteration-000/scenario_generator/test_scenarios.json`

## 실행

레포 루트에서 실행한다 (공통 `.venv`).

### Python 호출

```python
entrypoint.run(operation, input_paths, output_dir, context, *, drafter=None)
```

| 인자 | 내용 |
|---|---|
| `operation` | `"generate"` |
| `input_paths` | `{"vulnerability_candidates": 경로, "crawl_result": 경로}` — 두 키 모두 필요 |
| `output_dir` | 저장할 폴더. `run_root` 안이어야 한다. 파일명은 `test_scenarios.json` 고정 |
| `context` | 필수: `run_root`, `run_id`, `iteration`, `mode` / 선택: `expected_sha256`(`{입력종류: 소문자 hex}`). JSON으로 표현되는 값만 받는다(명세 03). `drafter` 키가 있으면 `ValueError` |
| `drafter` (keyword-only) | 초안 생성기 객체(`ScenarioDrafter`). 주면 설정 파일을 읽지 않는다. 안 주면 아래 "drafter 설정"으로 만든다. 테스트·CLI용 |

응답(dict, CLI는 stdout JSON): `status`, `artifact_id`, `output_path`, `sha256`, `scenario_count`, `error_count`.

종료 코드(CLI): 파일을 썼으면 `0`(결과 상태는 응답의 `status`), 쓰지 못했으면 `2`.
파일을 쓰지 못하면 `OutputWriteError`(`RuntimeError` 하위)를 던진다. 없는 파일을 완료라고 알리지 않는다.

### drafter 설정

우선순위: **`drafter=` 인자 > `SCENARIO_GENERATOR_CONFIG_PATH` > `modules/scenario_generator/configs/scenario_generator.toml`**.
경로는 현재 폴더(레포 루트) 기준이다. 설정은 context 검사 다음, 입력을 읽기 전에 읽는다(`utils/config.py`).

`[llm]` 표 하나만 받는다. 표 밖의 키와 아래에 없는 키는 거절한다.

| 키 | 타입·범위 | 쓰는 provider | 내용 |
|---|---|---|---|
| `provider` | 문자열 `none` · `ollama` · `replay` (필수) | 전부 | 초안 생성기 선택 |
| `model_id` | 문자열, 공백만은 안 됨 | ollama (필수) | 실제 설치한 모델 태그 |
| `base_url` | 문자열 | ollama | Ollama 주소 |
| `temperature` | 수(정수·실수), 0 이상, bool 안 됨 | ollama | 모델 옵션. `data.model_info`에 기록 |
| `timeout_seconds` | 수(정수·실수), 0보다 큼, bool 안 됨 | ollama | 호출 타임아웃(초) |
| `seed` | 정수, bool 안 됨 | ollama | 재현용. `data.model_info`에 기록 |
| `model_version` | 문자열 | ollama | 빼면 `model_id`를 기록 |
| `drafts_path` | 문자열, 있는 파일 | replay (필수) | 미리 적어 둔 초안 파일. 현재 폴더 기준 |

ollama 키를 빼면 `OllamaDrafterConfig`의 기본값을 쓴다(기본값은 그 한 곳에만 있다). 커밋된 파일에 적힌 값은 안내용이라 빼도 동작이 같다.

| provider | 동작 |
|---|---|
| `none` (커밋된 기본값) | drafter 없음 → `DRAFTER_NOT_CONFIGURED`로 `failed` 파일을 쓴다(네트워크 0) |
| `ollama` | `OllamaScenarioDrafter` — 로컬 Ollama `/api/generate` |
| `replay` | `ReplayScenarioDrafter` — `drafts_path`의 초안을 그대로 쓴다(개발·테스트, `model_info.model_id = replay_file`) |

기본값을 `none`으로 두는 이유: LLM 설정 없이 결과가 나온 것처럼 보이면 안 된다(결과가 조용히 0건이 되는 것 방지).

다음은 `run()` 호출 즉시 `ValueError`이고 출력 파일을 쓰지 않는다. 메시지에는 파일 이름·키 이름만 넣고 값은 넣지 않는다(설정 파일 없음만 경로 전체).

- 설정 파일 없음 / TOML 형식 오류 / `[llm]` 표 없음 / 정의되지 않은 키(`[llm]` 밖·안)
- 타입 틀림(문자열 키에 문자열이 아님, 수 키에 bool·문자열, `seed`에 정수가 아님)
- 범위 밖(`temperature` < 0, `timeout_seconds` ≤ 0)
- `provider` 없음·허용값 밖
- ollama인데 `model_id`가 비었거나 공백
- replay인데 `drafts_path`가 비었거나 없는 파일
- drafter를 만들다 실패(초안 파일 내용 깨짐 등). `ValueError`로 감싸고 원인은 예외 체인(`__cause__`)에 남긴다

설정 파일은 provider와 안 맞는 키를 허용한다(커밋된 파일이 모든 키를 나열한 템플릿이라서). CLI는 안 맞는 조합이면 exit 2(아래 "CLI").

**실제 실행**: 커밋된 `configs/scenario_generator.toml`은 고치지 않는다. 복사본을 git에 올라가지 않는 곳에 만들고 env로 가리킨다.

```bash
cp modules/scenario_generator/configs/scenario_generator.toml <로컬 경로>/scenario_generator.toml
# 복사본에서 provider = "ollama", model_id = "<설치한 모델 태그>"로 바꾼다(ollama list로 확인)
export SCENARIO_GENERATOR_CONFIG_PATH=<로컬 경로>/scenario_generator.toml
```

### 런너 사용 예

런너는 `drafter`를 넘기지 않는다. 설정 경로는 env(`SCENARIO_GENERATOR_CONFIG_PATH`)로만 지정한다.

```python
from modules.scenario_generator import entrypoint as scenario_generator_entrypoint

response = scenario_generator_entrypoint.run("generate", input_paths, output_dir, context)
```

`run()`이 밖으로 내는 예외(전부 새 출력 파일을 쓰지 않음):

- `ValueError`: operation·context·`input_paths` 키 오류, drafter 설정 오류(설정 파일·drafts 파일을 읽지 못함, drafter를 만들다 난 실패 포함)
- `OutputWriteError`(`RuntimeError` 하위): 출력 경로가 `run_root` 밖이거나 이미 있거나 저장 실패
- `OutputContractError`(`RuntimeError` 하위): 만든 출력이 자기 출력 Schema를 어김(이 모듈의 버그를 막는 마지막 검사)

`DrafterError`는 `run()` 밖으로 나오지 않는다. 설정 모드에서는 `ValueError`로 감싸고, 초안 생성 중 실패는 후보별 `DRAFTER_FAILED`로 기록한다.

### CLI

```
.venv/bin/python -m modules.scenario_generator.entrypoint generate \
  --run-root runs/<run_id> --run-id <run_id> --iteration 0 --mode development \
  --candidates <vulnerability_candidates.json> --crawl-result <crawl_result.json> \
  --output-dir runs/<run_id>/artifacts/iteration-000/scenario_generator \
  [--expected-sha256 <입력종류>=<해시>] \
  [--config <설정 TOML>] \
  [--drafts <초안 파일>] [--llm-provider replay|ollama] [--model-id <설치한 모델 태그>] [--base-url <Ollama 주소>] \
  [--temperature <수>] [--seed <정수>] [--timeout <초>]
```

drafter 옵션은 `--llm-provider` `--drafts` `--model-id` `--base-url` `--temperature` `--seed` `--timeout`이다.

- **설정 파일 모드**: drafter 옵션을 하나도 안 주면 설정 파일을 쓴다(`--config` > `SCENARIO_GENERATOR_CONFIG_PATH` > 기본 경로). `--config`는 이 프로세스의 env로 넘긴다
- **CLI 모드**: 하나라도 주면 그 옵션으로 drafter를 만들고 설정 파일은 읽지 않는다. 값은 설정 파일과 같은 타입·범위 검사를 거친다

| 옵션 | 내용 |
|---|---|
| `--llm-provider` | `replay` 또는 `ollama`. 기본값 없음 |
| `--drafts` | replay 초안 파일. `--llm-provider` 없이 주면 replay |
| `--model-id` | ollama일 때 필수. 실제 설치한 모델 태그 |
| `--base-url` / `--temperature` / `--seed` / `--timeout` | ollama 옵션. 빼면 `OllamaDrafterConfig` 기본값 |

직접 준 drafter 옵션이 조용히 무시되는 조합은 전부 exit 2(파일 없음)다.

- provider 없이 ollama 옵션(`--model-id` 등)만 줌
- `--llm-provider replay`인데 `--drafts`가 없음, 또는 ollama 옵션을 같이 줌
- `--llm-provider ollama`인데 `--drafts`를 같이 줌, 또는 `--model-id`가 없음
- `--config`와 drafter 옵션을 같이 줌(`--config`가 무시되므로)
- 값이 타입·범위 검사를 통과하지 못함(예: `--timeout 0`)

바뀐 점(10/10): `--llm-provider` 기본값 `replay` → 없음. `--drafts`만 주면 전처럼 replay다. `--base-url`·`--temperature`·`--timeout`의 CLI 기본값을 없앴다(빼면 `OllamaDrafterConfig` 기본값이라 동작은 같다). drafter 옵션을 하나도 안 주면 전에는 drafter 없이 `DRAFTER_NOT_CONFIGURED`였고, 이제는 설정 파일을 쓴다(커밋된 기본값 none이면 결과는 같다).

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
4. **초안 검증** (`scenario_validator.py`): Schema + 의미 검사. 통과한 초안만 시나리오가 된다.
   통과한 뒤 JSON이 아닌 응답 단계를 가리키는 `response_json` 조건을 버리고(`ASSERTION_NON_JSON_RESPONSE`), 버린 게 있으면 다시 검증한다
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
| `ASSERTION_NON_JSON_RESPONSE` | JSON이 아닌 응답 단계를 가리키는 `response_json` 조건을 버림. 시나리오는 발행한다(메시지에는 버린 check_id만) | candidate_id |
| `DRAFTER_NOT_CONFIGURED` | 초안 생성기(LLM)가 설정되지 않음(`provider = "none"`) | null |

## 초안 검증 규칙

Schema(필드·타입·enum, 미정의 키 거절) 외에 다음을 코드로 확인한다.

- `steps`와 `assertions`가 비어 있지 않다
- verifier가 평가하는 세 조건을 모두 쓴다. 명세 m7은 HTTP 200만으로 위반을 확정하지 않는다
  - `preconditions`: steps에 쓰인 **계정마다** `session_valid`(`subject_ref`=account_id, `operator=eq`, `expected=true`). `exists`는 세션이 죽어도(false) 참이라 거절한다
  - `assertions`: **실행 계정 단계 하나에** `response_status`와 `response_json`이 함께 있다. 그 단계 원본 응답이 JSON이 아니면 `response_status`만 있으면 된다
- 단계 응답이 JSON인지는 원본 요청 `response.content_type`에 `json`이 들어 있는지로 정한다(collector 근거 파일·세션 창구와 같은 기준, null이면 JSON 아님).
  JSON이 아닌 단계의 `response_json`은 verifier가 적용할 수 없어 판단불가가 되므로 검증 통과 뒤 버리고 `ASSERTION_NON_JSON_RESPONSE`로 남긴다.
  검증은 버리기 전에 하므로, 그런 조건의 selector 문법이 틀리면 버려지기 전에 `DRAFT_INVALID`가 된다
- `response_status`·`response_json` 조건의 `subject_ref`는 steps에 있는 `step_id`다(응답은 단계에서만 나온다)
- `resource_state`·`resource_owner`·`baseline_match`는 Schema상 허용하지만 verifier가 아직 평가하지 않아(판단불가) 필수 조건으로 치지 않는다
- `steps.order`가 0..N-1을 순서대로 유일하게 채운다. `step_id`·`binding_id`·`check_id`가 중복이 아니다
- 바인딩은 **앞선 단계**만 가리킨다. 파라미터의 `binding_ref`와 `url_template`의 `{binding_id}`는 정의된 바인딩만 쓴다
- 바인딩 selector: 본문은 JSON Pointer 문법, 헤더는 소문자 이름. `response_json` 조건의 selector도 JSON Pointer
- 단계의 계정은 후보의 **실행 계정 또는 기준 계정**만 쓰고, `role_id`·`session_ref`가 그 계정과 같다
- `source_request_id`가 `crawl_result`에 있고, `method`가 원본 요청과 같고, `body_ref`는 null이거나 원본 요청의 것이다
- `url_template`의 scheme·host·port가 `crawl_result.target_url`과 같다. 주소 부분에는 바인딩·사용자 정보를 쓸 수 없다
- `scenario_id`·`candidate_id`·`expected_basis`·`resource_ids`는 프로그램이 원본 후보에서 채운다. 초안에 이 키가 있으면 값이 원본과 같아도 거절하고, 채운 값이 원본 후보와 다르면 거절한다

## 초안 생성기(LLM) 연결

`scenario_drafter.ScenarioDrafter` 규격만 만족하면 바꿔 끼울 수 있다.

```python
class ScenarioDrafter(Protocol):
    model_info: dict | None                      # test_scenarios.json의 data.model_info에 그대로 들어감
    def draft(self, request: dict) -> Draft: ...  # Draft(scenario, input_tokens, output_tokens)
```

- 초안은 `{"preconditions", "steps", "assertions"}` 세 키만 가진 객체여야 한다
- 호출 실패는 `DrafterError`로 감싸서 던진다(메시지에 비밀값·프롬프트 원문 금지)
- 구현체는 두 개다.

| drafter | 파일 | 용도 |
|---|---|---|
| `ReplayScenarioDrafter` | `replay_drafter.py` | 개발·테스트. `drafts_path`(설정)·`--drafts`(CLI) 파일의 초안을 그대로 돌려준다. `model_info.model_id`는 `replay_file`로 남아 LLM이 아님을 드러낸다 |
| `OllamaScenarioDrafter` | `ollama_drafter.py` | 실제 로컬 모델. 표준 라이브러리 `urllib`로 Ollama `/api/generate`를 부른다(추가 패키지 없음, 폐쇄망용) |

- Ollama 프롬프트 버전은 `PROMPT_VERSION`(현재 `ollama-scenario-v4`)이고 `model_info.prompt_version`에 기록된다.
  v4(10/11): parameter 객체 예시를 넣고 입력 parameter가 출력과 같은 모양이라고 적었다.
  프롬프트에는 중첩 레코드(ScenarioStep·RequestPlan·ParameterValue·Binding·Check)의 정확한 필드와 아래 "초안 검증 규칙"의 판정 조건 요구를 적었다
- LLM에게 주는 입력은 `build_draft_request`가 만든 것뿐이다(헤더·쿠키·응답 본문·근거 경로·민감 파라미터 값 제외)
  - `source_requests[].parameters`는 출력 ParameterValue와 같은 `{name, location, value, binding_ref: null}` 모양으로 보여 준다.
    `is_sensitive`는 출력에 없는 키라 빼고, 민감 값은 null이다. 입력 모양이 출력과 다르면 LLM이 입력을 베끼다 필수 키(`binding_ref`)를 빠뜨렸다(10/11 실행 DRAFT_INVALID 2건)
- 모델 출력은 데이터다. 모양이 맞아도 `scenario_validator`가 전부 다시 검증하고, 통과하지 못하면 `DRAFT_INVALID`로 버린다

## 명세(v0.1) 대비 변경

필드 정의는 `docs/spec/m5-scenario_generator.md` "변경 이력"에도 같은 내용으로 적었다.

| 날짜 | 파일·필드 | 변경 | 근거·합의 |
|---|---|---|---|
| 2026-10-09 | 출력 `test_scenarios` `schema_version` | `0.1.0` → `0.2.0` | 11개 파일 전원 동시 전환(02 공통 계약). 필드 변경은 아래 `resource_ids`뿐 |
| 2026-10-09 | 출력 `test_scenarios` `Scenario.resource_ids` | 새 필수 필드. `array<nonEmptyString>`, `minItems 1`, 위치는 `expected_basis` 다음. 원본 후보 `Candidate.resource_ids`(KG Resource instance `node_id`)를 순서·값 그대로 복사한다. 프로그램이 채우고, LLM 초안에 이 키가 있으면 값이 같아도 `DRAFT_INVALID`, 원본과 다르면 거절 | verifier가 검증 결과를 KG 자원 노드에 잇는 키(KG `target_id` = 기존 Resource instance node_id). 소비자 이동찬(safety_policy·reporter)과 합의, verifier(최민준). 소비자 입력 사본 미러 전까지 소비자 입력 검증에서 거절된다 |
| 2026-10-09 | 입력 `crawl_result`·`vulnerability_candidates` | `0.2.0`만 받는다. `0.1.0`은 묵시 변환 없이 `INPUT_VERSION_UNSUPPORTED`(failed) | 생산자 출력 Schema를 그대로 복사(`cmp` 동일). 생산자 `run_demo_001` 샘플이 입력 adapter 통과(수동 확인) |

## 명세 해석과 미정 사항

명세에 직접 적혀 있지 않아 이 모듈이 정한 것이다. "확정"은 소비자·생산자의 구현(README·코드)과 맞춘 것, "미정"은 합의 전 임시이다.

| 항목 | 이 모듈의 현재 처리 | 상태 | 근거·확인 상대 |
|---|---|---|---|
| `assertions`의 의미 | "모두 참이면 위반이 재현됨" | 확정 | verifier README "Check 평가와 result 분류"에 같은 해석으로 구현 |
| `Check.subject_ref` (`response_status`·`response_json`·`session_valid`) | 응답 조건은 step_id, `session_valid`는 account_id | 확정 | verifier `execution.py` Check 평가 |
| `Check.subject_ref`·selector (`resource_state`·`resource_owner`·`baseline_match`) | 정하지 않음. 필수 조건으로 치지 않는다 | 미정 | verifier(최민준). verifier가 아직 평가하지 않음 |
| `Binding`의 위치 | 값을 **쓰는** 단계의 `bindings`에 두고 `source_step_id`로 앞 단계를 가리킨다 | 확정 | verifier는 URL을 만들 때 그 단계의 `bindings`만 읽는다. 이 모듈도 그 단계의 `bindings`에 정의된 바인딩만 쓰게 검증한다 |
| `url_template`의 `{binding_id}` 와 `parameters(location=path)` | `url_template`의 `{...}`는 바인딩 ID만 허용 | 확정(더 엄격) | verifier는 `{경로 파라미터 이름}` 치환도 지원하지만, 이 모듈은 바인딩만 쓴다 |
| 근거 요청의 계정 | `source_request_ids`의 요청이 실행 계정의 것일 필요는 없다 | 확정 | access_analyzer README: 기준(소유자) 계정의 요청을 실행 계정이 재현한다 |
| 후보와 시나리오의 수 | 후보 1개당 시나리오 1개. `scenario_id = "scenario_" + candidate_id` | 미정 | 이경준 |
| 기준 계정 단계 | 기준(소유자) 계정으로 보내는 단계를 steps에 둘 수 있다(필수 아님) | 미정 | 이경준. 명세 m7의 정상 기준 비교를 위해 필수로 바꿀지 |
| `method`·`url`·계정 제한 | 위 "초안 검증 규칙"의 해석 | 미정 | 이경준 |

## 소비자·생산자 연결 상태 (10/9 기준)

이웃 모듈의 현재 구현 때문에 결과가 달라지는 것들이다.

| 대상 | 현재 동작 | 이 모듈에 주는 영향 |
|---|---|---|
| access_analyzer | 후보 규칙은 `rule_same_role_other_owner`(authorization / horizontal_access) 하나 | 업무 흐름(workflow) 후보는 아직 들어오지 않는다 |
| access_analyzer ↔ knowledge_graph | access_analyzer 입력·출력 모두 `0.2.0`. `Candidate.resource_ids`는 KG Resource instance `node_id`다 | 이 모듈은 그 값을 `Scenario.resource_ids`로 그대로 복사한다. 형식은 가정하지 않는다 |
| safety_policy | 등록된 요청 규칙이 없으면 GET이어도 `require_approval`. URL에 fragment·공백·`//`·`.`/`..` 세그먼트·잘못된 `%` 인코딩이 있으면 허용하지 않음 | 이 모듈은 위 URL 형식을 미리 거르지 않는다. 허용 여부는 safety_policy가 정한다 |
| verifier | `state_change≠none` 단계와 `body_ref`가 있는 단계는 보내지 않는다(DB 초기화 훅·본문 역참조가 다음 PR). `resource_state`·`resource_owner`·`baseline_match`는 평가하지 않는다 | 지금 끝까지 판정되는 것은 **GET + `state_change=none` 읽기 시나리오**뿐이다. 그 밖은 판단불가(`indeterminate`) |
| verifier ↔ knowledge_graph | KG 0.2는 검증 관계의 target으로 앞 단계에서 받은 Resource instance `node_id`만 받는다 | `test_scenarios` 0.2.0의 `Scenario.resource_ids`로 전달한다(위 "명세(v0.1) 대비 변경"). verifier가 이 값을 `graph_updates`에 쓰는 것은 verifier 쪽 작업이다 |

생산자 샘플로 교차 확인한 결과(10/9): collector·access_analyzer `run_demo_001` 샘플이 이 모듈 입력 adapter를 통과한다(Schema 오류 0).
`resource_ids`를 넣은 `test_scenarios` 샘플은 safety_policy·verifier·reporter 입력 사본이 이 필드를 미러하기 전까지 거절된다
(`additionalProperties`. verifier 사본은 `schema_version`도 아직 0.1.0).

## 한계

- `created_at`·`observed_at`이 실제 날짜 형식인지는 검사하지 않는다 (`date-time` 형식 검사에 추가 패키지가 필요)
- `resource_ids`·`workflow_id`는 `crawl_result`로 검증할 수 없어 그대로 전달한다
- 응답 본문을 저장하지 않으므로 바인딩 selector가 실제 응답에 있는지는 이 모듈이 알 수 없다 (verifier가 실행 시 확인)

## 테스트

```
.venv/bin/python -m pytest modules/scenario_generator
```

다른 모듈의 실제 코드 없이 `tests/fixtures/`의 입력과 대역 drafter로 정상·partial·failed·입력 오류·함정 케이스를 검증한다.
drafter 설정·CLI 조합은 `tests/test_sg_drafter_config.py`가 본다(Ollama 전송은 기록용 대역, 네트워크 없음).
`tests/conftest.py`가 autouse로 `SCENARIO_GENERATOR_CONFIG_PATH`를 `tests/fixtures/configs/provider_none.toml`에 고정하므로 로컬 설정은 테스트에 영향이 없다.
테스트는 계정·요청 ID에 기대지 않고 fixture에서 값을 읽는다.

## 의존성

공통 잠금 환경의 `jsonschema`만 쓴다. 이 폴더 `requirements.txt`에는 추가 선언이 없다.
