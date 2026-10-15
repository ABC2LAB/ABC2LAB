# scenario_generator

검증 후보(`vulnerability_candidates.json`)와 원본 수집 결과(`crawl_result.json`)를 받아
후보마다 **재현 계획**(`test_scenarios.json`)을 만든다. 계정·요청 순서·동적 값 바인딩·판정 조건을 담는다.

- 기준 명세: `docs/spec/m5-scenario_generator.md` (공통: `02-common-contract.md`, `03-runner-layout.md`)
- 공개 operation: `generate`
- 지원 `schema_version`: 입력 `crawl_result`·`vulnerability_candidates` `0.3.0`, 출력 `test_scenarios` `0.3.0`(입력마다 따로 검사, 묵시 변환 없음)
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
| `provider` | 문자열 `none` · `ollama` · `openrouter` · `replay` (필수) | 전부 | 초안 생성기 선택 |
| `model_id` | 문자열, 공백만은 안 됨 | ollama·openrouter (필수) | 실제 설치한 모델 태그(ollama) 또는 OpenRouter 모델 ID |
| `base_url` | 문자열 | ollama·openrouter | 모델 API 주소 |
| `temperature` | 수(정수·실수), 0 이상, bool 안 됨 | ollama·openrouter | 모델 옵션. `data.model_info`에 기록 |
| `timeout_seconds` | 수(정수·실수), 0보다 큼, bool 안 됨 | ollama·openrouter | 호출 타임아웃(초) |
| `seed` | 정수, bool 안 됨 | ollama·openrouter | 재현용. `data.model_info`에 기록 |
| `model_version` | 문자열 | ollama·openrouter | 빼면 `model_id`를 기록 |
| `drafts_path` | 문자열, 있는 파일 | replay (필수) | 미리 적어 둔 초안 파일. 현재 폴더 기준 |

모델 키를 빼면 `OllamaDrafterConfig`·`OpenRouterDrafterConfig`의 기본값을 쓴다(기본값은 그 두 곳에만 있다. openrouter `base_url` 기본값은 `https://openrouter.ai/api/v1`).
OpenRouter API key는 설정 키가 아니다. `OPENROUTER_API_KEY` 환경변수로만 받는다(TOML에 `api_key`를 쓰면 미정의 키로 거절). 커밋된 파일에 적힌 값은 안내용이라 빼도 동작이 같다.

| provider | 동작 |
|---|---|
| `none` (커밋된 기본값) | drafter 없음 → `DRAFTER_NOT_CONFIGURED`로 `failed` 파일을 쓴다(네트워크 0) |
| `ollama` | `OllamaScenarioDrafter` — 로컬 Ollama `/api/generate` |
| `openrouter` | `OpenRouterScenarioDrafter` — OpenRouter `/chat/completions`. **모델 비교용 선택지**(외부 서비스로 초안 입력이 나간다). 기본·폐쇄망 운영은 ollama |
| `replay` | `ReplayScenarioDrafter` — `drafts_path`의 초안을 그대로 쓴다(개발·테스트, `model_info.model_id = replay_file`) |

기본값을 `none`으로 두는 이유: LLM 설정 없이 결과가 나온 것처럼 보이면 안 된다(결과가 조용히 0건이 되는 것 방지).

다음은 `run()` 호출 즉시 `ValueError`이고 출력 파일을 쓰지 않는다. 메시지에는 파일 이름·키 이름만 넣고 값은 넣지 않는다(설정 파일 없음만 경로 전체).

- 설정 파일 없음 / TOML 형식 오류 / `[llm]` 표 없음 / 정의되지 않은 키(`[llm]` 밖·안)
- 타입 틀림(문자열 키에 문자열이 아님, 수 키에 bool·문자열, `seed`에 정수가 아님)
- 범위 밖(`temperature` < 0, `timeout_seconds` ≤ 0)
- `provider` 없음·허용값 밖
- ollama·openrouter인데 `model_id`가 비었거나 공백
- openrouter인데 `OPENROUTER_API_KEY` 환경변수가 없거나 빔(메시지에 환경변수 이름만)
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
  [--drafts <초안 파일>] [--llm-provider replay|ollama|openrouter] [--model-id <모델 태그·ID>] [--base-url <모델 API 주소>] \
  [--temperature <수>] [--seed <정수>] [--timeout <초>]
```

drafter 옵션은 `--llm-provider` `--drafts` `--model-id` `--base-url` `--temperature` `--seed` `--timeout`이다.

- **설정 파일 모드**: drafter 옵션을 하나도 안 주면 설정 파일을 쓴다(`--config` > `SCENARIO_GENERATOR_CONFIG_PATH` > 기본 경로). `--config`는 이 프로세스의 env로 넘긴다
- **CLI 모드**: 하나라도 주면 그 옵션으로 drafter를 만들고 설정 파일은 읽지 않는다. 값은 설정 파일과 같은 타입·범위 검사를 거친다

| 옵션 | 내용 |
|---|---|
| `--llm-provider` | `replay` · `ollama` · `openrouter`. 기본값 없음 |
| `--drafts` | replay 초안 파일. `--llm-provider` 없이 주면 replay |
| `--model-id` | ollama·openrouter일 때 필수. 실제 모델 태그·ID |
| `--base-url` / `--temperature` / `--seed` / `--timeout` | 모델 옵션(ollama·openrouter). 빼면 각 `DrafterConfig` 기본값 |

openrouter는 CLI에서도 API key를 옵션으로 받지 않는다(셸 기록에 남지 않게). `OPENROUTER_API_KEY` 환경변수를 쓴다.

직접 준 drafter 옵션이 조용히 무시되는 조합은 전부 exit 2(파일 없음)다.

- provider 없이 모델 옵션(`--model-id` 등)만 줌
- `--llm-provider replay`인데 `--drafts`가 없음, 또는 모델 옵션을 같이 줌
- `--llm-provider ollama|openrouter`인데 `--drafts`를 같이 줌, 또는 `--model-id`가 없음
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
3. **초안 생성** (`scenario_drafter.py`): LLM에게 줄 입력을 만든다(헤더·쿠키·응답 본문·근거 경로·비밀 파라미터 값은 제외).
   Ollama drafter는 초안 JSON Schema를 `format`으로 넘겨 초안 모양(키·중첩·허용값)을 디코딩 단계에서 강제한다
4. **조립** (`service.py`): 초안의 선택(원본 요청·`account_role`·바꿀 파라미터·바인딩)에 원본의 계정 식별자·`method`·`url_template`·`body_ref`·`state_change`(원본 method로 추정)와 세션 사전조건을 붙인다
5. **재현 조건** (`reproduction_checks.py`): 재현 단계 앞에 기준 단계를 넣고 판정 조건(assertions)을 만든다. 아래 "재현 조건"
6. **검증** (`scenario_validator.py`): Schema + 의미 검사. 통과한 것만 시나리오가 된다
7. **출력 adapter** (`output_adapter.py`) → **원자적 저장** (`utils/atomic_io.py`): 완료 파일은 덮어쓰지 않는다

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
| `ASSERTION_BASELINE_UNAVAILABLE` | 이 후보로는 기준 응답을 만들 수 없어 재현 조건을 세우지 못함(기준 계정 없음·재현 단계 `state_change≠none`(원본 요청 method가 GET·HEAD·OPTIONS 밖)·근거 응답이 2xx 아님). 시나리오를 만들지 않는다. 기준 계정이 없으면 LLM을 부르지 않는다 | candidate_id |
| `DRAFTER_NOT_CONFIGURED` | 초안 생성기(LLM)가 설정되지 않음(`provider = "none"`) | null |

## 실패 초안 진단 (개발용)

검증을 통과하지 못한 초안(`DRAFT_INVALID`·`DRAFTER_FAILED`)은 `runs/<run_id>/private/scenario_generator/draft_diagnostics.json`에 개발용으로 남긴다. 공개 `test_scenarios.json`에는 초안을 넣지 않고, 실패는 `status=failed`·`data=null`을 유지한다. 저장 실패는 공개 결과를 막지 않는다(로그만 남긴다).

실패 후보마다 레코드 하나:

- `candidate_id`, `failure`(`DRAFT_INVALID`|`DRAFTER_FAILED`), `model_info`(모델·프롬프트 버전)
- `problems`: 검증 문제 전체(공개 오류는 요약본이지만 여기엔 전체를 남긴다)
- `field_diagnosis`: top-level·step·request의 **누락/추가 필드**와 기본 타입·허용값 문제
- `binding_analysis`: step마다 참조한 바인딩(`parameter_referenced`: parameter의 `binding_ref`, path 바인딩 포함 / `url_referenced`: 초안에 없어야 할 `url_template`이 있을 때 그 `{…}`) vs 그 step에 선언된 바인딩 vs **미정의**(참조−선언)
- `draft`: **비밀값 제거 사본**(parameter 값 마스킹, 구조·바인딩은 보존)

목적은 "기대 필드 목록"이 아니라 **실제로 어느 필드·바인딩이 틀렸는지** 사람이 보는 것이다.
`ASSERTION_BASELINE_UNAVAILABLE`은 초안이 틀린 게 아니라 후보·근거 요청 때문이라 진단 레코드를 남기지 않는다(공개 errors에는 남는다).

## 재현 조건 (프로그램 생성)

후보 기대 동작은 "기준 계정(소유자·상위 역할)이 아닌 실행 계정은 거부돼야 한다"(`expected_behavior`)이고, **그 부정이 재현 조건**이다: 실행 계정이 기준 계정과 같은 응답을 받는다. 판정 조건은 LLM이 아니라 프로그램이 만든다(`reproduction_checks.py`). 작은 모델이 기대값을 정상 동작(403)으로 적어 판정이 뒤집혔기 때문이다(6차 입력 5건 중 4건).

1. **재현 단계**: 실행 계정이 후보 `source_request_ids` 중 하나를 보내는 단계 중 마지막. 없으면 `DRAFT_INVALID`
2. **기준 단계**: 재현 단계 바로 앞에 기준 계정이 같은 요청을 보내는 단계를 넣는다
   - `step_id`는 `baseline_<재현 단계 step_id>`, 계정·역할·`session_ref`는 기준 계정 것, 요청(`method`·`url_template`·`parameters`·`body_ref`)과 바인딩은 재현 단계 복사
   - 바인딩 ID와 그것을 쓰는 `url_template`의 `{…}`·`binding_ref`는 `baseline_` 접두어로 바꿔 시나리오 안에서 유일하게 둔다(`source_step_id`는 그대로)
   - 뒤 단계 `order`는 하나씩 민다(0부터 다시 매기지 않는다. 초안 order가 틀렸으면 검증이 잡는다). ID가 겹치면 `DRAFT_INVALID`
   - 세션 사전조건은 기준 계정에도 붙는다
3. **판정 조건** (모두 `subject_ref`=재현 단계, `selector`=null, `operator`=eq)
   - `assertion_status`: `response_status` = 근거 요청에서 관찰한 상태 코드(2xx)
   - `assertion_baseline`: `baseline_match` = 기준 단계 step_id
   - `assertion_owner`: `resource_owner` = 기준 단계 step_id. 근거 응답이 JSON일 때만(식별자 비교는 JSON만)
4. **만들 수 없는 후보**(`ASSERTION_BASELINE_UNAVAILABLE`, 시나리오 없음): 기준 계정 없음(LLM 호출 전), 재현 단계 `state_change≠none`(기준 계정이 먼저 보내면 상태가 바뀐다), 근거 응답이 2xx 아님
5. **`state_change`는 프로그램이 원본 요청 method로 채운다**(`estimate_state_change`): `SAFE_METHODS`(GET·HEAD·OPTIONS, RFC 9110 safe method) → `none`, 그 밖 → `possible`(`expected`·`unknown`은 쓰지 않는다). LLM 초안에는 이 키가 없다(있으면 `DRAFT_INVALID`).
   - 이유: 같은 GET 조회 단계를 6차 입력에서 qwen2.5:1.5b는 5개 중 4개를 `possible`, 7b는 5개 모두 `none`으로 적었다. 10/15 local_web 실행에서는 1.5b가 `GET /api/users/{id}` 후보를 `possible`로 적어 기준 응답을 못 만들었다(`ASSERTION_BASELINE_UNAVAILABLE`). 판정에 들어가는 값이라 모델 크기에 따라 갈리면 안 된다
   - 한계: 키워드성 GET(`/logout`, `/cart/remove` 등)은 collector가 상태 변경 링크로 보고 따라가지 않아 관찰 요청에 없다. 혹시 들어와도 `none`으로 추정되고, 실행 허용은 safety_policy가 독립 재평가한다. 관찰 요청에 상태 변경 정보를 실으려면 `crawl_result` 계약 변경(소비자 4곳)이 필요하다(다음). 4번 가드는 조립된 값을 보므로 그 값이 오면 그대로 쓴다

verifier 판정(m7 "Check 규약"): JSON은 `baseline_match`(본문 비교)·`resource_owner`(식별자 비교)로 success·failure를 낸다. HTML 등 텍스트 응답은 control 단계(실행 계정 자기 자원)가 들어오기 전까지 기록만 해서, 재현 응답이 2xx면 `indeterminate`다(거부 코드면 failure).

## 초안 검증 규칙

LLM 초안은 `steps`의 **선택**만 담는다. 계정·`method`·`url_template`·`body_ref`·`state_change`·세션 사전조건·기준 단계·판정 조건은 프로그램이 원본과 후보에서 만들므로, 다음 검사는 조립 결과에 대한 안전망이다(현재 경계는 [m5 명세](../../docs/spec/m5-scenario_generator.md)).

조립 단계(`service.py`)에서 먼저 거르는 것:

- 초안 키가 정해진 것과 다르다(최상위 `steps`만, step 6개(`step_id`·`order`·`source_request_id`·`account_role`·`request`·`bindings`), `request`는 `parameters`만). 값이 원본과 같아도 `assertions`·`url_template`·`method`·`state_change` 같은 프로그램 몫 키가 있으면 거절한다
- 경로 바인딩: 초안은 URL을 쓰지 않고, 그 단계 원본 요청의 path 파라미터에 `binding_ref`를 건다(`value`=null).
  프로그램은 원본 URL에서 그 파라미터 관찰값과 같은 경로 조각 **하나**를 `{binding_id}`로 바꾸고, 그 파라미터는 출력 `parameters`에서 뺀다.
  다음은 거절한다: 리터럴 path 값(value가 있음), 원본 요청에 없는 path 이름·관찰값이 null인 이름, 같은 조각이 0개 또는 여러 개.
  verifier는 URL에 자리가 없는 path 파라미터를 조용히 무시하므로, 효과 없는 path 변경을 "실행됨"으로 보이게 두지 않는다
- 조립한 steps가 출력 `ScenarioStep` 모양이 아니다(기준 단계를 복사해 만들기 전에 본다)

Schema(필드·타입·enum, 미정의 키 거절) 외에 다음을 코드로 확인한다.

- `steps`와 `assertions`가 비어 있지 않다
- 명세 m7은 HTTP 200만으로 위반을 확정하지 않는다
  - `preconditions`: steps에 쓰인 **계정마다** `session_valid`(`subject_ref`=account_id, `operator=eq`, `expected=true`). `exists`는 세션이 죽어도(false) 참이라 거절한다
  - `assertions`: **재현 단계에** `response_status`와 `baseline_match`가 있고, 근거 응답이 JSON이면 `resource_owner`도 있다. `baseline_match`의 기준 단계는 기준 계정이 같은 근거 요청을 보낸 단계다
- 근거 응답이 JSON인지는 원본 요청 `response.content_type`에 `json`이 들어 있는지로 정한다(collector 근거 파일·세션 창구와 같은 기준, null이면 JSON 아님)
- `response_status`·`response_json`·`resource_state`·`resource_owner`·`baseline_match` 조건의 `subject_ref`는 steps에 있는 `step_id`다(응답은 단계에서만 나온다)
- `baseline_match`·`resource_owner`의 `expected`는 판정 단계와 다른 계정이 실행한 단계의 `step_id`다
- `steps.order`가 0..N-1을 순서대로 유일하게 채운다. `step_id`·`binding_id`·`check_id`가 중복이 아니다
- 바인딩은 **앞선 단계**만 가리킨다. 파라미터의 `binding_ref`와 `url_template`의 `{binding_id}`는 정의된 바인딩만 쓴다
- 바인딩 selector: 본문은 JSON Pointer 문법, 헤더는 소문자 이름. `response_json` 조건의 selector도 JSON Pointer
- 단계의 계정은 후보의 **실행 계정 또는 기준 계정**만 쓰고, `role_id`·`session_ref`가 그 계정과 같다
- `source_request_id`가 `crawl_result`에 있고, `method`가 원본 요청과 같고, `body_ref`는 null이거나 원본 요청의 것이다
- `url_template`의 scheme·host·port가 `crawl_result.target_url`과 같다. 주소 부분에는 바인딩·사용자 정보를 쓸 수 없다.
  바인딩이 없는 `url_template`은 원본 요청 URL과 정확히 같다(프로그램이 채우므로 늘 참이고, 조립 실수를 막는 안전망이다)
- `scenario_id`·`candidate_id`·`expected_basis`·`resource_ids`는 프로그램이 원본 후보에서 채운다. 초안에 이 키가 있으면 값이 원본과 같아도 거절하고, 채운 값이 원본 후보와 다르면 거절한다

## 초안 생성기(LLM) 연결

`scenario_drafter.ScenarioDrafter` 규격만 만족하면 바꿔 끼울 수 있다.

```python
class ScenarioDrafter(Protocol):
    model_info: dict | None                      # test_scenarios.json의 data.model_info에 그대로 들어감
    def draft(self, request: dict) -> Draft: ...  # Draft(scenario, input_tokens, output_tokens)
```

- 초안은 `{"steps"}` 한 키만 가진 객체여야 한다(모양은 `scenario_drafter.build_draft_json_schema()`)
- 호출 실패는 `DrafterError`로 감싸서 던진다(메시지에 비밀값·프롬프트 원문 금지)
- 구현체는 세 개다.

| drafter | 파일 | 용도 |
|---|---|---|
| `ReplayScenarioDrafter` | `replay_drafter.py` | 개발·테스트. `drafts_path`(설정)·`--drafts`(CLI) 파일의 초안을 그대로 돌려준다. `model_info.model_id`는 `replay_file`로 남아 LLM이 아님을 드러낸다 |
| `OllamaScenarioDrafter` | `ollama_drafter.py` | 실제 로컬 모델. 표준 라이브러리 `urllib`로 Ollama `/api/generate`를 부른다(추가 패키지 없음, 폐쇄망용) |
| `OpenRouterScenarioDrafter` | `openrouter_drafter.py` | 모델 비교용 외부 API. `urllib`로 OpenRouter `/chat/completions`를 부른다(추가 패키지 없음). 프롬프트(`SYSTEM_PROMPT`)·`PROMPT_VERSION`·초안 Schema는 Ollama와 같다 |

- Ollama 프롬프트 버전은 `PROMPT_VERSION`(현재 `ollama-scenario-v8`)이고 `model_info.prompt_version`에 기록된다.
  - v4(10/11): parameter 객체 예시를 넣고 입력 parameter가 출력과 같은 모양이라고 적었다.
  - v5(10/12): 초안을 선택으로 좁혔다(계정 식별자·`method`·`body_ref`·세션 사전조건은 프로그램).
  - v6(10/12): URL 복사 지시를 빼고(`url_template`도 프로그램), 경로 바인딩은 path 파라미터의 `binding_ref`로만 표현한다고 적었다.
  - v7(10/14): assertions 지시를 뺐다. 초안은 steps만 내고, 판정 조건과 기준 단계는 프로그램이 만든다(위 "재현 조건")
  - v8(10/15): `state_change` 추정 지시를 뺐다. 프로그램이 원본 요청 method로 채운다(위 "재현 조건" 5번). 초안 Schema에서도 빠져 모델이 낼 수 없다
- Ollama 호출의 `format`은 `"json"`이 아니라 초안 JSON Schema다(구조화 출력). `"json"`은 JSON인지만 강제해서 작은 모델이 키를 잘못 중첩했다(10/12 6차 입력 qwen2.5:1.5b: `bindings`·`state_change`를 `request` 안에).
  Schema의 키는 `DRAFT_*` 상수 순서, 허용값은 출력 Schema에서 가져온다. 모든 객체가 미정의 키를 거절하고 `steps`는 1개 이상이다.
  값(`value`)은 생성 문법을 줄이려고 스칼라와 스칼라 배열까지만 둔다. 의미(ID 존재·순서·참조)는 강제하지 못하므로 조립·`scenario_validator`가 계속 본다.
  `format`에 JSON Schema를 받는 Ollama(0.5 이상)가 필요하다
- OpenRouter 호출은 같은 초안 Schema를 `response_format`(`json_schema`, `strict: true`)으로 넘기고 `provider.require_parameters: true`를 건다.
  구조화 출력을 지원하지 않는 공급자로 라우팅되면 Schema가 조용히 무시되기 때문이다. 그런 모델이면 호출이 실패(`DRAFTER_FAILED`)한다.
  API key는 `Authorization` 헤더에만 싣고 로그·오류 메시지·`model_info`·출력 파일에 넣지 않는다. HTTP 오류는 상태 코드만 남긴다(408·429·5xx는 재시도 가능)
- LLM에게 주는 입력은 `build_draft_request`가 만든 것뿐이다(헤더·쿠키·응답 본문·근거 경로·민감 파라미터 값 제외)
  - `source_requests[].parameters`는 출력 ParameterValue와 같은 `{name, location, value, binding_ref: null}` 모양으로 보여 준다.
    `is_sensitive`는 출력에 없는 키라 빼고, 민감 값은 null이다. 입력 모양이 출력과 다르면 LLM이 입력을 베끼다 필수 키(`binding_ref`)를 빠뜨렸다(10/11 실행 DRAFT_INVALID 2건)
  - path 파라미터는 이름·관찰값을 그대로 보여 준다. 초안이 경로 바인딩을 걸 이름이 필요하다. 헤더·쿠키 파라미터는 보여 주지 않는다
- 모델 출력은 데이터다. 모양이 맞아도 `scenario_validator`가 전부 다시 검증하고, 통과하지 못하면 `DRAFT_INVALID`로 버린다

## 명세 대비 변경 이력

필드 정의는 `docs/spec/m5-scenario_generator.md` "변경 이력"에도 같은 내용으로 적었다.

| 날짜 | 파일·필드 | 변경 | 근거·합의 |
|---|---|---|---|
| 2026-10-09 | 출력 `test_scenarios` `schema_version` | `0.1.0` → `0.2.0` | 11개 파일 전원 동시 전환(02 공통 계약). 필드 변경은 아래 `resource_ids`뿐 |
| 2026-10-09 | 출력 `test_scenarios` `Scenario.resource_ids` | 새 필수 필드. `array<nonEmptyString>`, `minItems 1`, 위치는 `expected_basis` 다음. 원본 후보 `Candidate.resource_ids`(KG Resource instance `node_id`)를 순서·값 그대로 복사한다. 프로그램이 채우고, LLM 초안에 이 키가 있으면 값이 같아도 `DRAFT_INVALID`, 원본과 다르면 거절 | verifier가 검증 결과를 KG 자원 노드에 잇는 키(KG `target_id` = 기존 Resource instance node_id). 소비자 이동찬(safety_policy·reporter)과 합의, verifier(최민준). 소비자 입력 사본 미러 전까지 소비자 입력 검증에서 거절된다 |
| 2026-10-09 | 입력 `crawl_result`·`vulnerability_candidates` | `0.2.0`만 받는다. `0.1.0`은 묵시 변환 없이 `INPUT_VERSION_UNSUPPORTED`(failed) | 생산자 출력 Schema를 그대로 복사(`cmp` 동일). 생산자 `run_demo_001` 샘플이 입력 adapter 통과(수동 확인) |
| 2026-10-14 | 입력 `crawl_result` | `0.3.0`만 받는다(`discovered_endpoints`·`response.identifiers` 추가). **새 필드는 이 모듈이 쓰지 않는다**(Schema로 받기만). `vulnerability_candidates`는 `0.2.0` 그대로 | collector 출력 Schema 그대로 복사(`cmp` 동일), 스키마 변경 PR |
| 2026-10-14 | 출력 `test_scenarios`·입력 `vulnerability_candidates` | `0.2.0` → `0.3.0`(실행 산출물 0.3.0 통일). 필드 변경 없음 | 스키마 변경 PR |
| 2026-10-15 | 출력 `ScenarioStep.state_change` | 필드·enum·`schema_version` 변경 없음(스키마 변경 아님). 값 출처만 LLM 초안 추정 → 프로그램이 원본 요청 method로 결정(GET·HEAD·OPTIONS `none`, 그 밖 `possible`). 초안 step 키 7개 → 6개, 프롬프트 v8 | 1.5b가 GET 조회를 `possible`로 적어 `ASSERTION_BASELINE_UNAVAILABLE`(10/15 local_web 실행. 6차 입력에서도 같은 GET 단계를 1.5b 4/5 `possible`, 7b 5/5 `none`). 판정에 들어가는 값은 프로그램 몫(#79와 같은 방향) |

## 명세 해석과 미정 사항

명세에 직접 적혀 있지 않아 이 모듈이 정한 것이다. "확정"은 소비자·생산자의 구현(README·코드)과 맞춘 것, "미정"은 합의 전 임시이다.

| 항목 | 이 모듈의 현재 처리 | 상태 | 근거·확인 상대 |
|---|---|---|---|
| `assertions`의 의미 | "모두 참이면 위반이 재현됨" | 확정 | verifier README "Check 평가와 result 분류"에 같은 해석으로 구현 |
| `Check.subject_ref` (`response_status`·`response_json`·`session_valid`) | 응답 조건은 step_id, `session_valid`는 account_id | 확정 | verifier `execution.py` Check 평가 |
| `Check.subject_ref`·selector·expected (`resource_state`·`resource_owner`·`baseline_match`) | 세 kind 모두 subject_ref는 step_id. `baseline_match`·`resource_owner`의 expected는 기준 단계 step_id, selector는 JSON Pointer 또는 null. 이 모듈은 selector=null·eq로만 만든다 | 확정 | m5·m7 "Check 규약", verifier README "기준 비교" |
| `Binding`의 위치 | 값을 **쓰는** 단계의 `bindings`에 두고 `source_step_id`로 앞 단계를 가리킨다 | 확정 | verifier는 URL을 만들 때 그 단계의 `bindings`만 읽는다. 이 모듈도 그 단계의 `bindings`에 정의된 바인딩만 쓰게 검증한다 |
| `url_template`의 `{binding_id}` 와 `parameters(location=path)` | `url_template`은 프로그램이 원본 URL로 채우고 `{...}`는 바인딩 ID만 쓴다. 경로 바인딩은 관찰값 조각 하나를 `{binding_id}`로 바꾸고, 출력 `parameters`에는 `location=path`를 남기지 않는다 | 확정(더 엄격) | verifier는 `{경로 파라미터 이름}` 치환도 지원하지만, 이 모듈은 바인딩만 쓴다. verifier는 URL에 자리가 없는 path 파라미터를 무시한다 |
| 근거 요청의 계정 | `source_request_ids`의 요청이 실행 계정의 것일 필요는 없다 | 확정 | access_analyzer README: 기준(소유자) 계정의 요청을 실행 계정이 재현한다 |
| 후보와 시나리오의 수 | 후보 1개당 시나리오 1개. `scenario_id = "scenario_" + candidate_id` | 미정 | 이경준 |
| 기준 계정 단계 | 프로그램이 재현 단계 앞에 기준 계정이 같은 요청을 보내는 단계를 넣는다(위 "재현 조건"). 초안도 setup용 기준 계정 단계를 둘 수 있다 | 확정 | 명세 m7 정상 기준 비교. verifier `baseline_match`·`resource_owner`가 이 단계와 비교한다 |
| `method`·`url`·계정 제한 | 위 "초안 검증 규칙"의 해석 | 미정 | 이경준 |

## 소비자·생산자 연결 상태 (10/9 기준)

이웃 모듈의 현재 구현 때문에 결과가 달라지는 것들이다.

| 대상 | 현재 동작 | 이 모듈에 주는 영향 |
|---|---|---|
| access_analyzer | 후보 규칙은 `rule_same_role_other_owner`(authorization / horizontal_access) 하나 | 업무 흐름(workflow) 후보는 아직 들어오지 않는다 |
| access_analyzer ↔ knowledge_graph | access_analyzer 입력·출력 모두 `0.3.0`. `Candidate.resource_ids`는 KG Resource instance `node_id`다 | 이 모듈은 그 값을 `Scenario.resource_ids`로 그대로 복사한다. 형식은 가정하지 않는다 |
| safety_policy | 등록된 요청 규칙이 없으면 GET이어도 `require_approval`. URL에 fragment·공백·`//`·`.`/`..` 세그먼트·잘못된 `%` 인코딩이 있으면 허용하지 않음 | 이 모듈은 위 URL 형식을 미리 거르지 않는다. 허용 여부는 safety_policy가 정한다 |
| verifier | `state_change≠none` 단계와 `body_ref`가 있는 단계는 보내지 않는다(DB 초기화 훅·본문 역참조가 다음 PR). `baseline_match`·`resource_owner`는 JSON이면 판정, 텍스트(HTML)면 기록만 한다(#78) | 지금 끝까지 판정되는 것은 **GET + `state_change=none` + JSON 응답 읽기 시나리오**다. `state_change`는 10/15부터 프로그램이 method로 채우므로 GET(HEAD·OPTIONS) 조회 후보는 모델과 무관하게 `none`으로 verifier까지 간다. HTML 응답이 2xx면 control 단계 전까지 판단불가(`indeterminate`) |
| verifier ↔ knowledge_graph | KG 0.2는 검증 관계의 target으로 앞 단계에서 받은 Resource instance `node_id`만 받는다 | `test_scenarios` 0.2.0의 `Scenario.resource_ids`로 전달한다(위 "명세 대비 변경 이력"). verifier가 이 값을 `graph_updates`에 쓰는 것은 verifier 쪽 작업이다 |

생산자 샘플로 교차 확인한 결과(10/9): collector·access_analyzer `run_demo_001` 샘플이 이 모듈 입력 adapter를 통과한다(Schema 오류 0).
`resource_ids`를 넣은 `test_scenarios` 샘플은 safety_policy·verifier·reporter 입력 사본이 이 필드를 미러하기 전까지 거절된다
(`additionalProperties`. verifier 사본은 `schema_version`도 아직 0.1.0).

## 한계

- `created_at`·`observed_at`이 실제 날짜 형식인지는 검사하지 않는다 (`date-time` 형식 검사에 추가 패키지가 필요)
- `resource_ids`·`workflow_id`는 `crawl_result`로 검증할 수 없어 그대로 전달한다
- 응답 본문을 저장하지 않으므로 바인딩 selector가 실제 응답에 있는지는 이 모듈이 알 수 없다 (verifier가 실행 시 확인)
- 재현 조건은 기준 계정과 같은 응답을 받는지로만 만든다. 기준 계정이 없는 후보(업무 흐름 등)와 상태 변경 재현은 시나리오가 나오지 않는다(`ASSERTION_BASELINE_UNAVAILABLE`)
- HTML 응답은 control 단계(실행 계정 자기 자원)가 들어오기 전까지 verifier가 판정하지 않는다(재현 응답 2xx면 `indeterminate`)
- `state_change`는 원본 요청 method만 본다. 상태를 바꾸는 GET(`/logout` 같은 키워드성 링크)은 collector가 따라가지 않아 관찰 요청에 없지만, 들어오면 `none`으로 추정된다. 실행 허용은 safety_policy가 독립 재평가하고, 요청별 상태 변경 정보는 `crawl_result` 계약 변경이 있어야 받을 수 있다

## 테스트

```
.venv/bin/python -m pytest modules/scenario_generator
```

다른 모듈의 실제 코드 없이 `tests/fixtures/`의 입력과 대역 drafter로 정상·partial·failed·입력 오류·함정 케이스를 검증한다.
drafter 설정·CLI 조합은 `tests/test_sg_drafter_config.py`가 본다(Ollama 전송은 기록용 대역, 네트워크 없음).
OpenRouter drafter·설정·API key 누출은 `tests/test_sg_openrouter_drafter.py`가 본다(가짜 transport, 네트워크 없음).
`tests/conftest.py`가 autouse로 `SCENARIO_GENERATOR_CONFIG_PATH`를 `tests/fixtures/configs/provider_none.toml`에 고정하므로 로컬 설정은 테스트에 영향이 없다.
테스트는 계정·요청 ID에 기대지 않고 fixture에서 값을 읽는다. 재현 조건 생성(`tests/test_sg_reproduction_checks.py`)은 리터럴 입력으로 규칙 하나씩 본다.

## 의존성

공통 잠금 환경의 `jsonschema`만 쓴다. 이 폴더 `requirements.txt`에는 추가 선언이 없다.
