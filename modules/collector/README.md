# collector — 웹 정보 수집기

| 항목 | 내용 |
|---|---|
| operation | `collect` |
| 출력 | `crawl_result.json` (schema_version `0.1.0`) + 근거 파일(응답·DOM) |
| 직접 소비자 | semantic_analyzer·scenario_generator(이경준) · verifier(최민준) · reporter(이동찬, 개발 평가) |
| 담당 | 최민준 |
| 명세 | `docs/spec/m1-collector.md`, `docs/spec/02-common-contract.md` |

**지금 상태**
- `entrypoint.py`가 아래 계약대로 `crawl_result.json`을 공개한다.
- 근거 파일(응답·DOM)은 아직 만들지 않는다. 그래서 `body_ref=null`, `evidence_refs=[]`다. 근거는 다음 PR에서 붙인다.
- 계정은 역할당 하나(`account:<역할>`)다. 복수 계정은 그 다음 PR에서 붙인다.
- 이 문서와 Schema는 소비자 합의용 기준본이다.

**구조**
| 경로 | 내용 |
|---|---|
| `entrypoint.py` | 공개 창구 `run()`과 CLI |
| `service.py` | 역할별 탐색과 시계 역행 집계 |
| `core/` | 기존 탐색 core: 로그인·탐색·캡처·마스킹 |
| `utils/export.py` | 내부 결과를 계약 형식으로 바꾼다 |
| `utils/envelope.py` | 공통 envelope·ErrorItem |
| `utils/storage.py` | 경로 검증·원자적 공개 |
| `utils/validation.py` | 자기 출력 검증 |

## 실행

레포 루트에서 실행한다.

```bash
.venv/bin/python -m modules.collector.entrypoint collect --mode development
# 선택: --run-id <ID>(없으면 시각+난수) --iteration 0 --runs-dir runs --config <설정 파일>
```

- 결과 파일: `runs/<run_id>/artifacts/iteration-<NNN>/collector/crawl_result.json`.
- stdout에는 결과 한 줄(JSON)만 나간다. 로그와 건수 요약은 stderr로 간다.

```json
{"status": "partial", "artifact_path": ".../crawl_result.json", "artifact_id": "crawl_result-…", "sha256": "…", "errors": [ … ]}
```

**종료 코드**
| 코드 | 뜻 |
|---|---|
| 0 | completed |
| 1 | partial |
| 2 | failed. 파일은 공개됨(data=null) |
| 3 | 파일을 쓰지 못함. `artifact_path`·`artifact_id`·`sha256`은 null이고 이유는 `errors`에 있다 |

**코드에서 부르기**: `entrypoint.run(operation, input_paths, output_dir, context)`. 반환값은 CLI stdout과 같은 객체다.
- `operation`: `collect`만 받는다.
- `input_paths`: 비어 있어야 한다.
- `output_dir`: `run_root/artifacts/iteration-<NNN>/collector`와 정확히 같아야 한다. `..`이나 run_root 밖을 가리키는 symlink가 끼면 거절한다.
- `context` 키는 명세 03의 실행 값 네 개뿐이다. 정의되지 않은 키(설정 파일 위치 포함)는 거절한다.

  | 키 | 필수 | 내용 |
  |---|---|---|
  | `run_id` | 필수 | 영문·숫자로 시작, `._-`만 허용 |
  | `iteration` | 필수 | 0 이상의 정수 |
  | `mode` | 필수 | `diagnosis` 또는 `development` |
  | `run_root` | 필수 | `runs/<run_id>`. 폴더 이름이 `run_id`와 같아야 한다 |

**처리 순서**
1. 실행 값을 검증한다.
2. 이미 공개된 파일이 있으면 탐색 전에 거절한다.
3. 탐색한다.
4. 계약 형식으로 바꾼다.
5. 자기 출력을 검증한다(`utils/validation.py`, 계정 비밀번호 노출 포함). 통과하지 못하면 데이터 없이 `failed`로 공개한다.
6. 원자적으로 공개한다: 임시 파일 → fsync → `os.link`. 같은 경로에 파일이 있으면 덮어쓰지 않는다.

**오류 코드**
| 코드 | 언제 | 파일 | retryable |
|---|---|---|---|
| `ACCOUNT_CRAWL_FAILED` | 계정 하나의 로그인·탐색 실패. `item_ref`는 그 계정 ID | partial(다른 결과가 있으면) / failed | true |
| `CONFIG_INVALID` | 설정 키가 빠졌거나 형식이 틀림 | failed | false |
| `BROWSER_LAUNCH_FAILED` | 브라우저를 띄우지 못함 | failed | true |
| `SERVER_CLOCK_REGRESSION` | 대상 서버 시계 역행 | failed | true |
| `OPERATION_UNSUPPORTED` / `INPUT_UNEXPECTED` | `collect`가 아님 / 입력 경로가 있음 | failed | false |
| `OUTPUT_CONTRACT_INVALID` | 자기 출력 검증 실패. 메시지는 검증 문제 위치 | failed | false |
| `CONTEXT_INVALID` / `OUTPUT_PATH_INVALID` / `ARTIFACT_EXISTS` / `OUTPUT_WRITE_FAILED` | 실행 값·출력 위치 문제 | 파일 없음(종료 코드 3) | false |

## 입력

**입력 JSON 없음.** `schemas/input/`도 없다. 대상 URL·역할·계정·로그인 방식은 사용자 설정으로 받는다.
- 지금: 설정 파일의 `CRAWLER_*`. 같은 이름의 프로세스 환경변수가 있으면 그 값이 우선한다. 키 설명은 루트 `.env.example`에 있다.
- 설정 파일 위치: CLI `--config` > 환경변수 `COLLECTOR_CONFIG_PATH` > 기본 `.env`(실행 폴더 기준). `run()`을 직접 부르면 `--config`가 없으니 환경변수나 기본값을 쓴다.
- 복수 계정부터: `configs/collector.toml` + `.env`(아이디·비밀번호만).

계정 원문·비밀번호는 어떤 출력에도 넣지 않는다.

## 출력 계약

- Schema: `schemas/output/crawl_result.schema.json` (Draft 2020-12). 일반 객체는 미정의 키를 거절한다.
- 필드는 명세 표 그대로다. 중첩 레코드도 Schema 안에 다 들어 있다(다른 파일 `$ref` 없음).
- 샘플: `tests/fixtures/runs/run_demo_001/` (가상 데이터).

### status

| status | errors | data |
|---|---|---|
| `completed` | `[]` | 객체 |
| `partial` | 1개 이상 (예: 일부 계정 로그인 실패) | 객체 (남은 계정 결과) |
| `failed` | 1개 이상 (설정 오류·브라우저 실패·서버 시계 역행 등) | `null` |

서버 시계 역행이 감지된 실행은 `failed`로 남긴다.
- 오류 코드는 `SERVER_CLOCK_REGRESSION`, `retryable=true`.
- 결과를 쓰지 않고 다시 돌리라는 절대 규칙 7을 따른 것이다.

### ID 형식

| ID | 형식 | 예 |
|---|---|---|
| role_id | `role:<역할 이름>` | `role:member` |
| account_id | `account:<별칭>` | `account:member_a` |
| page_id | `page:<N>` (실행 전체에서 1부터) | `page:2` |
| action_id | `action:<N>` (실행 전체에서 1부터) | `action:3` |
| request_id | `request:<N>` (실행 전체에서 1부터, 요청이 나간 순서) | `request:4` |
| evidence_id | `evidence:<kind>:<대상 ID>` | `evidence:response:request:4`, `evidence:dom:page:2` |
| artifact_id | `crawl_result-<run_id>-<NNN>-<랜덤>` | `crawl_result-run_demo_001-000-7c3e9a10` |

ID는 불투명 문자열로 다룬다. 소비자는 형식을 파싱하지 말고 참조로만 쓴다.

### 필드 채우는 규칙

**계정·세션**
- 익명 탐색도 계정 하나(`account:guest`)로 둔다. `session_ref`는 `null`이다.
- 로그인한 계정은 브라우저 context마다 불투명 `session_ref`를 붙인다. 요청의 `session_ref`는 그 요청을 낸 계정의 값과 같다.
- 세션 창구(접근·만료·대여/반납)는 verifier와 합의한 뒤 붙인다. 그 전까지 `session_ref`로 세션을 되살릴 수 없다.

**페이지·행동과 요청의 연결**
- 페이지 하나는 계정 하나의 방문이다. 같은 URL도 계정마다 따로 기록한다.
- `page_id=null`: 시작 URL 요청처럼 유발한 페이지가 없을 때.
- `action_id=null`: 페이지 로드·재방문처럼 사용자 행동이 아닌 요청.
- `action_id`와 `page_id`가 둘 다 있으면 그 행동의 `page_id`와 요청의 `page_id`가 같다.
- `Action.kind`
  - 링크 → `navigate`, 폼 → `submit`, 버튼 → `click`.
  - 입력칸에 값을 넣지 않으므로 `input`은 쓰지 않는다.

**파라미터·URL·헤더**
- `parameters`
  - 값 하나에 한 건이다. 같은 키가 반복되면 여러 건이 된다.
  - 위치는 `query`·`body`·`path`·`cookie`. 헤더는 `headers`에만 둔다.
- 경로 파라미터
  - 이름은 `path:<index>`. 0부터 센 경로 세그먼트 위치다(`/items/7` → `path:1`, 값 `"7"`).
  - 숫자·UUID 세그먼트만 파라미터로 본다.
- 민감 이름(password·token·session·csrf 등)과 쿠키는 `is_sensitive=true`, `value=null`이다.
- `url`의 민감 쿼리값은 `***`로 가린 채 둔다.
- 헤더 이름은 소문자다. `cookie`·`set-cookie`·`authorization`·`proxy-authorization`과 민감 이름 헤더는 `value="[REDACTED]"`, `redacted=true`.
- Method는 대문자다.

**근거 참조**

자리마다 올 수 있는 kind가 정해져 있다. 지금 만들지 않는 kind(`request`·`screenshot`)도 나중에 붙일 수 있게 자리를 열어 둔다.

| 자리 | 허용 kind | 지금 채우는 것 |
|---|---|---|
| `ObservedRequest.response.body_ref` | `response` | JSON 응답만 응답 근거를 가리킨다. HTML·그 밖의 응답은 `null` |
| `ObservedRequest.body_ref` | `request` | `null`. 요청 값은 `parameters`에 있다 |
| `ObservedRequest.evidence_refs` | `request`, `response` | `[]` |
| `Page.evidence_refs` | `dom`, `screenshot` | 그 페이지의 DOM 근거 하나. 추출하지 못한 페이지는 `[]` |
| `Action.evidence_refs` | `dom` | 행동이 일어난 페이지의 DOM 근거 하나 |

**그 밖**
- 시각은 UTC RFC3339(`2026-10-06T03:00:05Z`)다.
- `runtime_metrics`: `llm_calls=0`·토큰 0(LLM을 쓰지 않음), `peak_memory_mb=null`(미측정).
- `input_refs=[]`: 입력 산출물이 없다.

## 근거 파일

둘 다 `redacted=true`이고, 값을 가리고 계정 비밀번호를 지운 뒤 저장한다. 한 번 쓰면 바꾸지 않는다.

### 응답 근거 (`kind=response`)

- Schema: `schemas/output/response_evidence.schema.json`.
- 위치: `evidence/collector/response/request-<N>.json`.
- JSON 응답 하나에 파일 하나.

```json
{
  "schema_version": "0.1.0",
  "kind": "response",
  "evidence_id": "evidence:response:request:4",
  "request_id": "request:4",
  "shape": {"items": [{"id": "int", "owner_id": "int", "name": "str"}], "next_cursor": "null|str"},
  "identifiers": [
    {"pointer": "/items/0/id", "value": 7},
    {"pointer": "/items/0/owner_id", "value": 2}
  ],
  "identifiers_truncated": false
}
```

**`shape`**: 키 구조와 타입이다.
- 잎은 `bool|int|float|str|null|dict|list`를 `|`로 이은 타입 이름이고 값은 없다.
- 배열은 원소 모양을 합친 하나만 남긴다.
- 키가 값처럼 생겼으면 자리표시자(`{id}`·`{email}`·`{date}`·`{token}`)로 바꾼다.
- 깊이 6을 넘는 객체·배열은 `dict`/`list`로 끊는다. 키가 100개를 넘으면 `"...": "truncated"`로 표시한다.

**`identifiers`**: 소유 관계를 관찰하려고 남기는 값이다. 아래 둘을 다 만족하는 것만 남긴다.
- 키 이름이 `id`, `*_id`, `*Id` 꼴.
- 값이 0 이상의 정수, 숫자로만 된 문자열, UUID 문자열 중 하나(경로 id 판정과 같은 기준). 응답의 원래 JSON 타입을 그대로 둔다(`"42"`는 문자열로).

**`identifiers` 예외와 상한**
- 민감 키 규칙이 먼저 걸러낸다. `session_id`·`token_id` 같은 키는 남기지 않는다.
- 계정 비밀번호와 같은 값은 지운다.
- `pointer`는 RFC 6901 JSON Pointer다(`~` → `~0`, `/` → `~1`).
- 개수 상한을 넘으면 앞쪽만 남기고 `identifiers_truncated=true`로 둔다.

그 밖의 값(이름·이메일·주소·전화·금액·문자열)과 HTML 응답 본문은 남기지 않는다.

**한계**: 다음은 식별자로 잡지 않는다. collector 고도화 때 다룬다.
1. 문자열 id: slug·username처럼 숫자·UUID가 아닌 id.
2. id 꼴이 아닌 키 이름: `owner`, `userNo`처럼 `id`·`*_id`·`*Id`가 아닌 키.
3. 객체 키 자리의 id: `{"17": {...}}`처럼 id가 키로 오는 응답. shape에서는 `{id}`로 바뀐다.

### DOM 요약 근거 (`kind=dom`)

- Schema: `schemas/output/dom_evidence.schema.json`.
- 위치: `evidence/collector/dom/page-<N>.json`.
- 페이지 하나에 파일 하나이고, 항목은 crawl_result의 전역 `action_id`로 키잉한다.
- `Page.evidence_refs`와 그 페이지 `Action.evidence_refs`는 모두 이 파일을 가리킨다. 행동별 정보는 `actions["<action_id>"]`에서 찾는다.

```json
{
  "schema_version": "0.1.0",
  "kind": "dom",
  "evidence_id": "evidence:dom:page:2",
  "page_id": "page:2",
  "actions": {
    "action:2": {"element": "link", "text": "Item 7", "url": "http://localhost:8080/items/7", "endpoint": "/items/{id}", "is_state_changing": false, "outcome": "enqueued"},
    "action:3": {"element": "form", "method": "POST", "target_url": "http://localhost:8080/items/7/delete", "fields": [{"name": "csrf_token", "type": "hidden"}], "is_state_changing": true, "outcome": "not_executed_state_changing"},
    "action:4": {"element": "button", "label": "Refresh", "is_state_changing": false, "outcome": "executed"}
  }
}
```

**요소별 필드**
| element | 필드 |
|---|---|
| `link` | `text`, 가린 `url`, `endpoint` 템플릿 |
| `form` | `method`, 가린 `target_url`, `fields`(이름+타입) |
| `button` | `label` |

공통으로 `is_state_changing`·`outcome`이 붙는다. HTML 원문과 입력값은 넣지 않는다.

**`outcome` 값**
- `executed` / `enqueued` / `already_visited` / `beyond_max_depth`
- `not_executed_state_changing`: 상태 변경으로 보고 실행하지 않음.
- `blocked_state_changing_request`: 실행했지만 상태 변경 요청을 서버에 닿기 전에 끊음.
- `outside_origin` / `not_visible` / `not_found` / `failed`

## 경로 규칙

| 경로 (`runs/<run_id>/` 기준) | 내용 |
|---|---|
| `artifacts/iteration-<NNN>/collector/crawl_result.json` | 완료 파일. 한 번 공개하면 바꾸지 않는다. 재실행은 새 회차·새 artifact_id |
| `evidence/collector/response/request-<N>.json` | 응답 근거 |
| `evidence/collector/dom/page-<N>.json` | DOM 요약 근거 |
| `private/collector/` | 세션 같은 보호된 내부 상태(세션 창구 합의 뒤) |
| `logs/collector/` | 실행 로그 |

`EvidenceRef.path`는 run_root 기준 상대 경로이고 `evidence/collector/` 아래만 허용한다. `..`·절대 경로·역슬래시·run_root 밖을 가리키는 symlink는 거절한다. 폴더 이름 `<run_id>`는 문서의 `run_id`와 같다.

## 검증

`utils/validation.py`가 Schema 검사와 Schema로 못 잡는 의미 검사를 함께 한다. 문제는 `ValidationIssue(code, location, message)` 목록으로 돌려주고, 빈 목록이면 통과다.

```python
from modules.collector.utils.validation import validate_crawl_result_file

issues = validate_crawl_result_file(artifact_path, run_root, known_secrets=[...])
```

**의미 검사**
- ID 유일, 참조 존재, 요청의 `account_id`·`role_id`·`session_ref`와 계정의 대응.
- 행동과 요청의 페이지 일치(둘 다 있을 때), UTC 시각, Method·헤더 이름 표기.
- 근거 파일: 자리별 허용 kind, 위치·존재·SHA-256·근거 Schema(응답·DOM), 근거 안 ID와 참조의 대응.
- 비밀값: 쿠키·인증 헤더·민감 파라미터를 가렸는지, 직렬화 바이트에 알려진 비밀값(계정 비밀번호 등)이 없는지.

메시지에는 값 대신 위치·키 이름만 넣는다.

## 테스트

```bash
.venv/bin/python -m pytest modules/collector/                            # collector 전체
.venv/bin/python -m pytest modules/collector/tests/test_contract.py     # 계약·fixture
.venv/bin/python -m pytest modules/collector/tests/test_entrypoint.py   # 실행 창구(가짜 로컬 사이트)
```

테스트는 전부 127.0.0.1의 빈 포트에 띄운 가짜 사이트(`tests/sites.py`)로 돈다. 실제 테스트 앱에는 요청하지 않는다.
