# access_analyzer

| 항목 | 값 |
|---|---|
| module_id | `access_analyzer` |
| 공개 operation | `prepare_queries`, `analyze` |
| 명세 | `docs/spec/m4-access_analyzer.md`, `docs/spec/02-common-contract.md`, `docs/spec/03-runner-layout.md` |
| 계약 버전 | 출력(`graph_query`·`vulnerability_candidates`) `0.3.0` · 입력 `graph_query_result` `0.3.0` · 입력 `crawl_result` `0.3.0`(2026-10-15 추가, collector 출력 Schema 사본) |

KG 조회 계획을 세우고(prepare_queries), KG가 돌린 결과와 crawl_result 관찰로 인가·비즈니스 로직 취약점 **후보**를 만든다(analyze).
후보는 **검증 대상이지 확정 취약점이 아니다.** Neo4j 접속·Cypher 실행은 knowledge_graph가 한다. 이 모듈은
허용된 query_key·parameters만 요청하고 결과 JSON을 검증해 쓴다. crawl_result는 KG 결과에 없는 응답 상태·세션 유무를 주는
두 번째 입력이다(수직 규칙용).

## 입력·출력

| 방향 | 파일 | operation | 생산자/소비자 |
|---|---|---|---|
| 출력 | `graph_query.json` | `prepare_queries` | 소비자: knowledge_graph |
| 입력 | `graph_query_result.json` | `analyze` | 생산자: knowledge_graph (필수) |
| 입력 | `crawl_result.json` | `analyze` | 생산자: collector (수직 규칙용. 없으면 수직 규칙만 건너뛰고 partial) |
| 출력 | `vulnerability_candidates.json` | `analyze` | 소비자: scenario_generator, reporter |

- 출력 기준 Schema는 `schemas/output/`, 입력 사본 Schema는 `schemas/input/`(생산자 계약과 합의해 자기 폴더에 둠).
- 다른 모듈의 Schema·코드를 import/$ref 하지 않는다. 각 Schema는 필요한 중첩 정의를 자기 파일에 담는다.

## 공개 호출

```python
from modules.access_analyzer.entrypoint import run

result = run(
    operation="prepare_queries",
    input_paths=[],  # prepare_queries는 입력 JSON을 읽지 않는다
    output_dir="runs/<run_id>/artifacts/iteration-000/access_analyzer",
    context={
        "run_id": "run_example",
        "iteration": 0,
        "mode": "development",
        "run_root": "runs/run_example",
        "graph_id": "graph_example",            # KG ingest 제어 응답에서 받음
        "expected_graph_revision": 1,            # null이면 현재 revision 조회
    },
)
```

- `context` 키는 명세 03 실행 값 + 필요한 graph 상태 여섯 개다: `run_id`·`iteration`·`mode`·`run_root`·`graph_id`·`expected_graph_revision`. 선택 키는 `expected_sha256`(`{입력 artifact_type: 소문자 hex 64자}`, analyze 입력 파일 바이트와 대조) 하나다. 정의되지 않은 키는 거절한다.
- `graph_id`·`expected_graph_revision`은 KG의 공개 제어 응답에서 온다(명세 03 "필요한 graph 상태"). `expected_graph_revision`을 걸면 KG가 revision 불일치 시 1차로 막고, analyze가 결과의 `graph_revision`을 2차로 확인한다.
- 반환값·CLI stdout 한 줄: `{status, artifact_path, artifact_id, sha256, errors}`.

CLI:

```bash
# 질의 계획
.venv/bin/python -m modules.access_analyzer.entrypoint prepare_queries \
  --mode development --graph-id graph_example [--expected-graph-revision 1] \
  [--run-id run_example] [--iteration 0] [--runs-dir runs]

# 후보 분석 (KG가 낸 graph_query_result.json + collector의 crawl_result.json을 입력으로)
.venv/bin/python -m modules.access_analyzer.entrypoint analyze \
  --mode development --graph-id graph_example --expected-graph-revision 1 \
  --input-path artifacts/iteration-000/knowledge_graph/graph_query_result.json \
  --crawl-path artifacts/iteration-000/collector/crawl_result.json \
  --run-id run_example --iteration 0
```

`analyze`는 `graph_query_result.json`(필수)과 `crawl_result.json`(권장)을 입력으로 받는다(`--input-path`·`--crawl-path`는 run_root 기준 상대 또는 절대).
공개 호출에서는 `input_paths` 목록으로 둘을 넘기고, 파일의 `artifact_type`으로 가른다(순서 무관). 입력은 run_root 안에 있어야 하고,
iteration 폴더에 묶지 않는다(다음 회차 analyze가 회차 0의 crawl_result를 그대로 읽는다). crawl_result가 없으면 수직 규칙만 건너뛰고
`CRAWL_INPUT_UNAVAILABLE`로 partial이다.

## 질의 계획 (prepare_queries)

허용된 네 개의 query_key를 각각 한 번씩, 고정된 순서로 담는다. 지금은 run 범위 전체를 조회한다(필터 빈 배열).

| query_key | 용도 |
|---|---|
| `resource_ownership` | 계정·자원 소유 관계 |
| `role_resource_access` | 역할·계정·Endpoint·자원 접근 관계 |
| `workflow_dependencies` | 업무 흐름 단계 의존 관계 |
| `structure_snapshot` | 실제 적재 구조. 명세상 평가·감사용(reporter)이라 analyze 규칙 입력으로 쓰지 않지만, 질의 작성 주체는 access_analyzer다(m4) |

## 후보 규칙 (analyze)

후보는 검증 대상이지 확정 취약점이 아니다. 규칙 입력은 분석 쿼리(`resource_ownership`·`role_resource_access`) 결과와 crawl_result 관찰(응답 상태·계정 세션 유무)이다(`structure_snapshot`은 평가·감사용이라 제외). 규칙 파라미터는 `configs/access_analyzer.toml`(아래 "설정"). 출력 순서는 Rule A → 수직, 각 규칙 안에서 `candidate_id` 정렬이다.

| rule_id | 판정 | 비고 |
|---|---|---|
| `rule_same_role_other_owner` (Rule A, 수평) | `OwnershipRow`로 자원 R의 소유자 Y를 얻고, `AccessRow.role_id`로 Y와 **같은 role_id** 인 다른 계정 X를 찾으면 (X 실행, Y 기준, R 대상) 후보. `(actor, reference)` 순서쌍마다 1개. category=authorization, vulnerability_type=horizontal_access, expected_basis=inferred | 역할 **이름**이 아니라 role_id 동일성으로 판단. 소유자 없는 자원은 OwnershipRow가 없어 자연 제외. crawl_result 없이도 돈다 |
| `rule_vertical_access` (수직: 기능 수준·강제 브라우징) | 역할의 **접근 집합** = 그 역할 계정이 2xx 받은 `endpoint_id` 집합(2xx는 `AccessRow.request_ids`를 crawl_result 응답 상태로 조인). 비인증 역할(crawl 계정이 전부 `session_ref=null`)은 모든 인증 역할보다 낮고, 인증 역할끼리는 접근 집합이 **진부분집합(⊊)** 일 때만 낮다. 낮은 역할 L의 crawl 계정 X마다, 높은 역할만 2xx 받은 엔드포인트 E 중 **비인증 역할이 2xx 받은 것(공개)** 과 **L이 2xx 또는 거절(`denied_status_codes`, 기본 401·403)로 관찰한 것** 을 뺀 나머지가 후보 (X 실행, Y 기준, E 대상). Y는 E에 2xx 행이 있는 **가장 가까운** 높은 역할의 계정(사이에 다른 제공 역할 없음, 여럿이면 role_id·account_id 순). category=authorization, vulnerability_type은 L이 비인증이면 `forced_browsing`, 아니면 `function_level_access`, expected_basis=inferred | 3xx·400·404·405·422·응답 없음 등 그 밖의 상태로만 관찰한 엔드포인트는 인가 판단 근거가 아니라 후보로 남긴다. 접근 집합이 같거나 비교 불가인 인증 역할 쌍은 후보를 내지 않고 경고 로그(`role_order` 설정으로 정할 수 있다). `role_order`(낮→높)에 둘 다 있는 쌍은 설정이 우선이고 관찰이 정반대면 경고 로그. 비인증 최하위는 구조적 사실이라 설정으로 바꾸지 않는다 |

- **`source_request_ids`의 출처**: Rule A는 소유자(reference) 계정 Y가 자원 R에 접근한 `AccessRow`의 `request_ids`, 수직은 기준 계정 Y가 E에서 2xx 받은 행의 **2xx 요청만**(같은 행의 비2xx 요청은 기준 응답이 될 수 없다). verifier가 그 요청을 X로 재현한다. 못 채우면 발행하지 않고 `CANDIDATE_INCOMPLETE`로 남긴다(status=partial).
- 계정·역할 ID는 crawl 원본 ID(#29), 자원 ID는 KG Resource `node_id`(#41)다. 결과 row 값을 그대로 쓰고 우회 변환하지 않는다.
- **`resource_ids`는 KG Resource `node_id`(type 또는 instance)다(2026-10-15 의미 확장).** Rule A는 OwnershipRow가 instance만 오므로 instance 하나. 수직은 기준 계정 2xx 행의 `resource_id` **전부**(정렬·중복 제거)라 목록 엔드포인트면 type, 상세 엔드포인트면 instance가 온다. 소비자는 node_id로 type/instance를 구분할 수 없고(불투명), 구분이 필요하면 KG `structure_snapshot`의 `properties.resource_scope`를 본다. 기준 행이 전부 미연결(resource null)이면 `CANDIDATE_INCOMPLETE`. Schema(`minItems 1`, 문자열)는 그대로다.
- **vulnerability_type 어휘**: `horizontal_access`·`function_level_access`·`forced_browsing`. GT(reporter 평가가 문자열 일치)와 같은 값이고 `vertical_access` 같은 새 이름은 쓰지 않는다.
- **type 접근 행(목록처럼 자원 종류 단위)은 Rule A의 소유자 재현 요청으로 쓰지 않는다.** 소유 행과 접근 행은 `(소유자, resource_id)`로 잇는데, type과 instance는 다른 노드라 resource_id가 겹칠 수 없어 type 행은 이 연결에 걸리지 않는다. 그래서 instance 필터를 따로 두지 않는다. 자원 종류(`resource_key`)로 묶지 않는다(목록 요청이 재현 요청으로 섞임). type 행도 계정·역할 관찰이라 같은 역할 계정 찾기에는 쓴다.
- **candidate_id**: `<rule_id>:<실행>|<기준>|<대상>`. 구성 요소에 `|`·`%`가 있으면 퍼센트 인코딩해 다른 조합이 같은 ID가 되지 않게 한다(`candidate_ids.py`). 보통 ID는 그대로다.
- **보류(6번)**: 관찰 2xx 없이 발견만 된 엔드포인트(`discovered_endpoints`)는 후보로 내지 않는다. SA가 발견 엔드포인트에 ACCESS를 만들지 않아 `role_resource_access` 행 자체가 없고, 내려면 `source_request_ids` `minItems 1`을 깨야 한다(소비자 4곳 합의). 강제 브라우징②로 다음에.

## 실패 처리

- **산출물 타입을 알 수 있으면**(지원 operation) 실패도 `status=failed`·`data=null` 파일로 공개한다(입력 JSON 혼입/누락, 입력 계약 위반, 입력 결과 failed·stale·graph_id 불일치, 자기 출력 검증 실패 등). 실패를 정상 빈 결과로 숨기지 않는다. 종료코드 2.
- **타입·경로가 불확실하면** 파일을 쓰지 않고 반환값으로만 알린다. 종료코드 3. 여기에 해당:
  - 알 수 없는 operation(어떤 산출물을 쓸지 모름) → `OPERATION_UNSUPPORTED`.
  - 잘못된 `run_id`·`run_root`, output_dir가 `run_root/artifacts/iteration-NNN/access_analyzer`가 아님, `..`·symlink로 run 밖을 가리킴, 폴더 생성 불가.
- analyze의 입력 경로는 run_root 안이어야 한다. 밖을 가리키면 `INPUT_PATH_INVALID`로 failed 파일을 공개한다(출력 경로는 신뢰 가능).
- **crawl_result 입력(analyze)**: 경로를 안 줬거나 파일이 `failed`(data null)면 수직 규칙만 건너뛰고 `CRAWL_INPUT_UNAVAILABLE`로 partial(Rule A 후보는 그대로). `partial`(data 유효)은 정상 입력으로 쓴다. 경로를 줬는데 못 읽음·Schema 위반·`run_id` 불일치·`expected_sha256` 불일치면 failed. `expected_sha256`은 선택이고, 있으면 입력 종류별로 파일 바이트의 SHA-256을 대조한다(해시값은 오류 메시지에 넣지 않는다).
- **질의 계획 대응(analyze)**: 같은 실행 값(graph_id·expected_graph_revision)으로 질의 계획을 다시 만들어, 결과의 `(query_id, query_key)` 쌍 집합이 계획과 정확히 같은지 본다. 입력 `input_refs`에는 access_analyzer가 쓴 `graph_query` 참조가 정확히 하나 있어야 한다(ArtifactRef에 producer 필드가 없어 경로의 `access_analyzer/` 폴더로 본다. 그 파일을 열어 해시를 비교하지는 않는다). 계획한 질의 결과 누락·계획에 없는 query_id·짝 바뀜(query_id는 맞는데 query_key가 다름)·같은 query_key 중복·참조 없음은 전부 `QUERY_PLAN_MISMATCH`로 failed(후보 없음). 계획한 질의가 다 있고 그중 일부가 `status=failed`면 지금처럼 partial(`QUERY_RESULT_PARTIAL`)이다.

**analyze 판정 오류 코드**(파일에 기록, 메시지에는 query_id·query_key만 넣고 rows 내용은 넣지 않는다)

| 코드 | 언제 | 파일 | retryable |
|---|---|---|---|
| `INPUT_CONTRACT_INVALID` | 입력이 자기 입력 Schema·의미 검증을 통과하지 못함(graph_query_result·crawl_result 공통. analyze 입력이 아닌 `artifact_type`도 여기) | failed | false |
| `INPUT_HASH_MISMATCH` | context `expected_sha256`에 적힌 입력 종류의 파일 바이트 SHA-256이 기대값과 다름(런너가 넘긴 파일이 바뀜). 어느 쪽이 맞는지 알 수 없어 분석하지 않는다 | failed | false |
| `INPUT_RESULT_FAILED` | 입력 graph_query_result가 failed | failed | true |
| `RUN_ID_MISMATCH` / `GRAPH_ID_MISMATCH` | 입력(graph_query_result 또는 crawl_result)의 run_id·graph_id가 실행 값과 다름 | failed | false |
| `CONFIG_INVALID` | `configs/access_analyzer.toml`(또는 `ACCESS_ANALYZER_CONFIG_PATH`)의 값이 규칙이 쓸 수 있는 모양이 아님(예: `role_order` 중복, `success_status_range` 역순). 규칙 일부만 적용한 결과를 내지 않는다 | failed | false |
| `CRAWL_INPUT_UNAVAILABLE` | crawl_result 입력이 없거나 failed라 crawl 관찰이 필요한 규칙(수직)을 적용하지 않음. Rule A 후보는 그대로 | partial | true |
| `ACCESS_REQUEST_UNRESOLVED` | KG 접근 행의 `request_ids` 중 crawl_result에 없는 request_id가 있어 그 행을 수직 규칙에서 제외함(`item_ref`=request_id, 행 안의 다른 요청도 같이 빠짐). 다른 행으로 만든 후보는 그대로 | partial | false |
| `GRAPH_REVISION_STALE` | 입력 revision이 계획 revision과 다름 | failed | true |
| `QUERY_PLAN_MISMATCH` | 결과 질의 쌍이 계획과 다르거나 graph_query 참조가 하나가 아님(위) | failed | false |
| `QUERY_RESULT_PARTIAL` | 계획한 질의 중 일부가 failed. 입력 오류의 `item_ref`·retryable을 이어받음 | partial | 입력 따라 |
| `CANDIDATE_INCOMPLETE` | 후보를 만들 재료가 모자라 발행하지 않음(`item_ref`=만들려던 candidate_id). Rule A: 소유자 접근 요청을 못 찾음. 수직: 기준 계정의 2xx 행이 전부 자원 미연결이라 `resource_ids`를 못 채움 | partial | true |
| `CANDIDATES_TRUNCATED` | 규칙별 후보 상한(`max_candidates`)을 넘어 잘림(규칙마다 따로 센다) | partial | false |
- 완료 파일은 한 번만 공개한다. 같은 경로에 이미 있으면 덮어쓰지 않고 거절한다(`ARTIFACT_EXISTS`).
- 종료코드: 0 completed / 1 partial / 2 failed(파일 있음) / 3 파일 없음.

## 설정

`configs/access_analyzer.toml`을 읽는다. `ACCESS_ANALYZER_CONFIG_PATH` 환경변수가 있으면 그 파일을 대신 읽는다(대상 앱 값은 커밋된 파일이 아니라 레포 밖 복사본에 적고 환경변수로 가리킨다). 파일이 없으면 코드 기본값이고, 값이 틀리면 analyze는 `CONFIG_INVALID`로 failed다.

| 섹션 | 키 | 기본값 | 의미 |
|---|---|---|---|
| `rules.same_role_other_owner` | `enabled` / `max_candidates` | `true` / `100` | Rule A on/off, 후보 상한 |
| `rules.vertical_access` | `enabled` / `max_candidates` | `true` / `100` | 수직 규칙 on/off, 후보 상한(규칙별) |
| | `role_order` | `[]` | 낮은 → 높은 순서의 role_id. 비우면 관찰(⊊)로 추정. 둘 다 적힌 쌍은 설정이 우선, 관찰과 정반대면 경고 로그 |
| | `success_status_range` | `[200, 299]` | 접근 성공으로 보는 응답 상태 범위(포함). 접근 집합과 "이미 공유된 엔드포인트" 판단 |
| | `denied_status_codes` | `[401, 403]` | 낮은 역할이 이 상태로 관찰한 엔드포인트는 거절을 이미 관찰한 것이라 후보에서 뺌. 그 밖의 상태는 후보 유지 |

## 독립 실행·테스트

저장소 루트의 공통 `.venv`에서 실행한다. 다른 모듈의 실제 구현 없이 자기 fixture·Schema로 검증한다(`tests/helpers.py`의 `make_crawl_artifact`가 리터럴 입력으로 0.3.0 모양 crawl_result를 만든다).

```bash
.venv/bin/python -m pytest modules/access_analyzer/
```

## 한계

- 수직 규칙은 **관찰한 접근 행**만 본다. 발견만 된 엔드포인트(`discovered_endpoints`)는 후보로 내지 않는다(위 "보류(6번)").
- 비인증 계정이 2xx 받은 엔드포인트는 공개로 본다. 그 접근 자체가 취약점이면(비인증에 열린 보호 기능) 이 규칙의 가설이 아니라 "관찰된 교차 접근"이라 다른 규칙 몫이다.
- 비인증 실행 계정의 시나리오는 SG·verifier까지 가지만, collector 세션 창구가 guest의 로그인 리다이렉트를 세션 만료로 읽어 `forced_browsing` 검증이 indeterminate로 남는다. 별도 PR(guest-lease)에서 고친다.
- safety_policy `test_accounts`에 비인증 계정(`requires_session=false`)이 없으면 그 시나리오는 block이고, reporter 평가의 actor 별칭 매핑에 비인증 별칭이 없으면 매칭되지 않는다(둘 다 운영자 설정).

## 명세 대비 변경 이력

출력은 명세 표보다 **좁힌** 것이다. 우리 출력이 더 엄격해 소비자(더 느슨함)의 입력을 항상 통과한다. 입력은 생산자가 올린 계약 버전을 그대로 미러링해 받아들인 것이다. 필드 정의는 `docs/spec/m4-access_analyzer.md` "변경 이력"에도 같은 내용으로 적는다.

| 날짜 | 파일·필드 | 변경 | 근거·합의 |
|---|---|---|---|
| 2026-10-15 | 입력 `crawl_result` | analyze 두 번째 입력으로 추가(0.3.0 사본, collector 출력 Schema와 `cmp` 동일). 없으면 수직 규칙만 건너뛰고 partial. context 선택 키 `expected_sha256` | 수직 규칙에 응답 상태·세션 유무가 필요. 생산자·소비자 둘 다 최민준. m1 소비자 목록·02 표 갱신 |
| 2026-10-15 | 출력 `Candidate.resource_ids` 의미 | "KG Resource instance node_id" → "KG Resource node_id(type 또는 instance)". Schema(`minItems 1`, 문자열) 변경 없음 | 수직 규칙이 목록 엔드포인트의 type 자원도 후보로 냄. 소비자 scenario_generator(이경준, m5 문구)·reporter(이동찬, type 노드도 `name=resource_key`로 평가 확인)와 PR 리뷰로 합의 |
| 2026-10-15 | 출력 `Candidate.vulnerability_type` | `function_level_access`·`forced_browsing` 값 추가 사용(자유 문자열, GT 어휘) | 어휘 추가 없음. reporter 평가는 문자열 일치 |
| 2026-10-07 | `vulnerability_candidates` `Candidate.source_request_ids` | `array<string>` → `minItems:1` | 근거 요청을 못 채우면 후보를 발행하지 않는다(확정 답 2). 생산자: 최민준. 소비자 scenario_generator(이경준)·reporter(이동찬)는 더 느슨해 영향 없음(수동 확인) |
| 2026-10-07 | `vulnerability_candidates` `Candidate.resource_ids` | `array<string>` → `minItems:1` | 같음. TODO(choiamj980818): `workflow_step_bypass` 규칙은 자원 없이 `workflow_id`만 가질 수 있어 그 규칙 추가 시 minItems:1을 재검토한다 |
| 2026-10-07 | `vulnerability_candidates` `Candidate.reference_account_id`·`workflow_id` | `string/null` → `nonEmptyString/null` | 빈 문자열 금지. reporter 입력 Schema와 일치시킴(수동 확인: reporter·scenario_generator 입력 통과) |
| 2026-10-08 | 입력 `graph_query_result` | `schema_version` `0.1.0` → `0.2.0` 수용. OwnershipRow·AccessRow에 `resource_key`·`resource_scope`·`match_key` 추가(OwnershipRow는 instance만), structure_snapshot Resource 노드 properties 규칙. 0.1.0 입력은 묵시 변환 없이 `INPUT_CONTRACT_INVALID`(failed)로 거절 | KG 출력 계약 0.2.0(#41, 이동찬)을 그대로 미러링(수동 확인: KG 출력 Schema와 title·description 외 동일). 출력 `vulnerability_candidates` 계약·버전은 그대로 |

## 연결 때 다른 모듈과 맞춘 것 (전부 해결, 10/7)

입력 사본 Schema는 생산자 실제 출력과 일치. 아래 연결 과제는 모두 머지로 해결됐다.

- **A2 (결과 row의 계정·역할 ID 형식) → #29(이동찬)로 해결.** ownership `owner_account_id`·access `account_id`·`role_id`가 crawl 원본 ID로 나온다(KG observation 기반). endpoint_id는 `endpoint:<METHOD>:<path>`. resource_id는 0.2.0(#41)부터 KG Resource `node_id`(생산자가 만든 불투명 ID, type·instance가 다른 노드)라 형식을 가정하지 않는다.
- **A3 (후보 `source_request_ids`의 출처) → #32(이동찬)로 해결, 입력 사본 미러링 #33.** `AccessRow.request_ids`(원본 수집 요청 ID, `minItems:1`·`uniqueItems`)를 Rule A가 소유자 접근 행에서 읽어 `source_request_ids`를 채운다. 매칭 AccessRow가 없으면 발행하지 않고 errors로 둔다(안전장치).
- **reporter crawl_result Page.account_id → #35로 해결.** (우리 모듈 무관이지만 파이프라인 연결 과제로 추적했던 항목.)
