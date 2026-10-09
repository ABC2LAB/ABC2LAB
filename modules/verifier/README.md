# verifier

| 항목 | 값 |
|---|---|
| module_id | `verifier` |
| 공개 operation | `verify` (안전 게이트 + allow 재현 실행·Check 판정 + 실물 collector 세션 창구 연결) |
| 명세 | `docs/spec/m7-verifier.md`, `02-common-contract.md`, `03-runner-layout.md` |
| 계약 버전 | `schema_version = 0.2.0` (입력 test_scenarios·safety_decisions·crawl_result, 출력 verification_results) |

허용된 재현 계획만 대상 웹에 실행하고 실제 요청·응답·상태 근거로 판정한다. **safety_policy가 allow한 것만**,
`effective_origins`·`limits` 안에서만 실행한다. block·require_approval은 요청 없이 미실행으로 남긴다. 미실행·판단불가를
"취약점 없음"으로 기록하지 않는다(절대 규칙 9). 후보는 access_analyzer가 만들지만 verifier 판정은 후보 가설을 전제하지
않고 실제 근거로만 한다(명세 m7).

## 입력·출력

| 방향 | 파일/창구 | 생산자/소비자 |
|---|---|---|
| 입력 | `test_scenarios.json` | scenario_generator |
| 입력 | `safety_decisions.json` | safety_policy |
| 입력 | `crawl_result.json` | collector |
| 입력(창구) | 세션 공개 창구 | collector(런너가 주입) |
| 출력 | `verification_results.json` | 소비자: knowledge_graph, reporter |

- 입력 Schema는 `schemas/input/`의 사본으로, **생산자 실제 출력 Schema를 그대로 미러링**한다(더 엄격하게 하지 않음). 0.2.0 기준 title·description 외 동일하다(정규화 비교). `0.1.0` 입력은 `INPUT_CONTRACT_INVALID`(failed, 전송 0건). `crawl_result`의 `Page.account_id`는 m1 명세 표엔 없고 collector가 추가한 필드라 사본에도 포함한다(#35까지 반영).
- 다른 모듈의 Schema·코드를 import/$ref 하지 않는다.

## 공개 호출

```python
from modules.verifier.entrypoint import run

result = run(
    operation="verify",
    input_paths=[  # test_scenarios·safety_decisions·crawl_result (순서 무관, artifact_type으로 분류)
        "artifacts/iteration-000/scenario_generator/test_scenarios.json",
        "artifacts/iteration-000/safety_policy/safety_decisions.json",
        "artifacts/iteration-000/collector/crawl_result.json",
    ],
    output_dir="runs/<run_id>/artifacts/iteration-000/verifier",
    context={"run_id": "run_example", "iteration": 0, "mode": "development",
             "run_root": "runs/run_example", "source_graph_revision": 1},
    session_executor=None,  # 런너가 collector 세션 창구 구현을 주입(명세 03). 없으면 allow는 SESSION_UNAVAILABLE로 미실행
)
```

- `context` 키: `run_id`·`iteration`·`mode`·`run_root`·`source_graph_revision`(기준 KG revision, 명세 m7 실행 인자). 그 밖의 키는 거절.
- `session_executor`는 선택 주입 인자다(세션 공개 창구). CLI에서는 주입하지 않는다.
- 반환값·CLI stdout 한 줄: `{status, artifact_path, artifact_id, sha256, errors}`.

CLI:

```bash
.venv/bin/python -m modules.verifier.entrypoint verify \
  --mode development --source-graph-revision 1 \
  --input-path artifacts/iteration-000/scenario_generator/test_scenarios.json \
  --input-path artifacts/iteration-000/safety_policy/safety_decisions.json \
  --input-path artifacts/iteration-000/collector/crawl_result.json \
  --run-id run_example --iteration 0
```

## 안전 게이트 (실행 허용 조건, 순서대로)

1. 입력 3종을 artifact_type으로 분류·Schema+의미 검증·run_id 대응 확인. 잘못된 입력을 정상 결과로 숨기지 않는다.
2. **계획 해시 게이트(전역)**: `test_scenarios` 파일의 실제 바이트 SHA-256 == `safety_decisions.scenarios_sha256`. 다르면 **어떤 요청도 보내지 않고** `failed`(data=null, `SCENARIOS_HASH_MISMATCH`).
3. 시나리오별 Policy 판정(`scenario_id`로 대응):
   - **block·require_approval** → 요청 전송 없이 `blocked`(execution_status=not_executed, steps=[]).
   - **allow** → 각 step의 계정이 `effective_account_ids`·crawl 계정·역할·세션과 맞는지 확인(미실행 pre-flight). 어긋나면 `indeterminate`(not_executed)로 `ACCOUNT_SCOPE_MISMATCH`·`ROLE_MISMATCH`·`SESSION_INVALID`.
4. 게이트를 통과한 allow는 세션 창구가 주입됐으면 실행한다(아래 "재현 실행"). 창구가 없으면 `indeterminate`+`not_executed`+`SESSION_UNAVAILABLE`.

- **판정 누락 처리**: 시나리오에 대응하는 Policy 판정이 없으면 `VerificationItem`을 만들 수 없다(`policy_decision`·`decision_id`가 필수라 results에 넣을 값이 없다). 숨기지 않고 envelope `errors`에 `POLICY_DECISION_MISSING`(item_ref=scenario_id)로 남기고 `status=partial`로 둔다.

## 재현 실행 (allow)

- 각 단계마다 `url_template`의 `{binding_id}`·경로 파라미터를 치환(치환값은 경로 한 조각으로 인코딩)하고 query를 붙여 **최종 URL을 만든 뒤, 전송 직전** origin이 `effective_origins`에 있는지 검사한다. 밖이면 보내지 않는다(`ORIGIN_OUT_OF_SCOPE`).
- **리다이렉트는 자동으로 따르지 않는다**(기본 `max_redirects=0`). 0보다 크면 hop마다 Location origin을 다시 검사해 안쪽이면 따라가고 `max_requests`에 센다. 따라갔을 때 `request_url`·`request_ref`·`response_status`·`response_ref`는 **마지막 hop 기준**, 앞 hop 근거는 `evidence_refs`에 순서대로.
- `limits.max_requests`(리다이렉트 포함 실제 전송 수)·`max_duration_ms`(주입 시계)를 넘으면 남은 단계는 `skipped`로 두고 보내지 않는다(`LIMIT_EXCEEDED`).
- `state_change≠none` 단계는 보내지 않는다 — `allow_state_change=false`면 `STATE_CHANGE_NOT_ALLOWED`, true여도 초기화 훅이 PR3이라 `STATE_RESET_UNAVAILABLE`.
- `body_ref`가 필요한 단계는 원문을 역참조하지 않으므로 보내지 않는다(`BODY_REF_UNAVAILABLE`). 본문 뺀 요청으로 판정하지 않는다.
- 세션 무효·만료·통신 오류는 `indeterminate`(`SESSION_INVALID`/`SESSION_EXPIRED`/`TRANSPORT_ERROR`)로 남긴다. failure로 합치지 않는다. lease는 계정별로 캐시하되 단계마다 `is_valid()`를 다시 확인하고, 끝나면 반드시 release한다.
- `ExecutedStep.request_ref`·`response_ref`는 EvidenceWriter로 만든 비밀 제거 근거다. 미전송 단계는 두 참조와 `request_url`이 null.
- **execution_status 규칙**(result 분류와 독립, `execution_status_for`): 중단 없이 전부 전송되면 `completed`, 중단됐으면 전송 0건은 `not_executed`·1건 이상은 `error`.

## Check 평가와 result 분류

`assertions`는 "모두 참이면 위반 재현"으로 읽는다(생산자 해석). `subject_ref`는 샘플대로 단계 ID 또는 계정 ID. HTTP 상태 코드 하나만으로 success를 확정하지 않는다(명세 m7 51·96행).

| kind | PR2 평가 | 방법 |
|---|---|---|
| `session_valid` | 평가 | subject 계정의 `lease.is_valid()`. 어느 단계에도 안 쓰인 계정은 `passed=null`(안 써본 세션을 유효로 기록하지 않음) |
| `response_status` | 평가 | subject 단계 응답 status_code |
| `response_json` | 평가 | subject 단계 응답 body에 JSON Pointer(selector) |
| `resource_state`·`resource_owner`·`baseline_match` | **미평가** | `passed=null` → 그 시나리오 indeterminate. 완전 평가는 뒤 PR |

- operator: `exists`(값/경로 존재) · `eq` · `ne` · `in`(observed ∈ expected) · `contains`(expected ∈ observed). 관찰값이 없으면(`exists` 제외) `passed=null`(불일치 failure로 단정하지 않음). `subject_ref`를 못 찾으면 `passed=null`.
- **status-only 규칙(m7 51행)**: assertions가 전부 참이어도 참인 조건이 `response_status`·`session_valid`뿐이면(응답 내용 미확인) success 대신 `indeterminate`(`ASSERTION_STATUS_ONLY`). 응답 내용을 본 조건(`response_json` 등)이 하나 이상 참이어야 success로 올린다.
- **result 분류**: preconditions 중 하나라도 거짓/판단불가 → `indeterminate`(전제 미충족·판단불가, failure 아님). 모두 참이면 assertions 중 하나라도 판단불가 → `indeterminate`, 모두 참 → `success`, 하나 이상 거짓 → `failure`.
- **CheckResult 부착**: subject가 단계면 그 단계의 `check_results`에, 계정이면 그 계정이 처음 쓰인 단계에, 둘 다 못 찾으면 첫 단계에 붙인다. `observed`는 비밀 제거(민감 키 selector로 뽑은 스칼라도 가림).
- `graph_updates`는 `basis=verified`와 실제 실행 근거가 있는 success만 출처로 삼는다. 지금은 **빈 배열**이다(PR3-c에서 채움). 관계 형태는 KG 입력 `verificationRelationship`과 같다: `source_account_id`=실제 요청한 계정의 원본 ID, `target_id`=그 시나리오 `Scenario.resource_ids`(Resource instance node_id) 중 하나. 경로는 access_analyzer `Candidate.resource_ids` → scenario_generator `Scenario.resource_ids` → verifier `target_id`.

## 실패 처리

- 출력 타입이 `verification_results` 하나뿐이라 **출력 경로를 신뢰할 수 있으면** 실패도 `status=failed`·`data=null` 파일로 공개한다(미지원 operation, 입력 누락·여분·계약 위반, 해시 불일치, 자기 출력 검증 실패 등). 종료코드 2.
- **경로 자체가 불확실하면**(잘못된 `run_id`·`run_root`, output_dir 불일치, `..`·symlink, 폴더 생성 불가) 파일을 쓰지 않고 반환값으로만 알린다. 종료코드 3. 입력 경로는 run_root 안이어야 한다.
- 완료 파일은 한 번만 공개한다(`ARTIFACT_EXISTS`). 종료코드: 0 completed / 1 partial / 2 failed(파일 있음) / 3 파일 없음.

## 세션 공개 창구 규약 (collector ↔ verifier, 둘 다 최민준)

- verifier는 `executor.py`의 Protocol(`SessionExecutor`·`SessionLease`)로만 세션을 쓴다. collector 코드·브라우저/세션 객체를 import하지 않고, 런너가 구현(또는 테스트 대역)을 주입한다(명세 03). 실물 구현은 collector `session_gateway.py`(`open_session_executor`).
- 실행 모델: **리스가 요청을 대신 전송**한다 — verifier가 resolve한 요청을 넘기면 창구가 그 계정 세션으로 보내고 응답을 돌려준다. 세션 쿠키·토큰은 창구(collector) 안에만 머물고 전달 JSON·근거에 넣지 않는다.
- 규약: `lease(account_id)` → `is_valid()` · `send()`(자동 리다이렉트 끔) · `release()`. 전체 규약 표·만료 신호는 collector README "세션 공개 창구" 절. 요점:
  - `is_valid()`는 창구가 세션을 들고 있고 만료 감지가 없었는가다(대상 앱 요청 없음). 실제 세션 만료는 `send()` 응답 신호로 잡아 `SessionExpiredError` → verifier가 indeterminate(`SESSION_EXPIRED`)로 둔다.
  - `lease()`의 로그인 요청은 세션 준비라 `limits.max_requests`에 세지 않는다(대상 앱으로 가는 요청은 `send()`뿐).
  - collector가 send에서 허용 origin을 한 번 더 검사한다(verifier `effective_origins`와 별개의 2차 방어).

## 명세(v0.1) 대비

- 출력 `graph_updates`의 node/edge는 `basis`를 `const "verified"`로, `evidence_refs`를 `minItems:1`로 **명세 m7("basis=verified와 실제 실행 근거만")에 맞춰** KG 입력보다 좁게 둔다(reporter 입력 사본과는 같다). 우리 출력이 같거나 더 엄격해 소비자 입력을 항상 통과한다.
- 출력 `graph_updates.relationships`는 명세 표의 GraphEdge(`source_id`) 대신 **VerificationRelationship**(`source_account_id`)이다(2026-10-10). KG·reporter 입력이 같은 0.2.0 안에서 먼저 전환했고, verifier가 맞췄다. 이전 `source_id`는 소비자가 거절한다. 필드 표는 m7 "VerificationRelationship".
- 입력 3종은 생산자 0.2.0만 받는다(2026-10-10, 0.2 2단계). `test_scenarios`의 `Scenario.resource_ids`는 Schema로 받기만 하고 실행 로직은 아직 읽지 않는다(PR3-c에서 `target_id`로 씀).
- `max_redirects` 기본 **0**: 명세는 리다이렉트 처리를 세부로 규정하지 않는데, 로그인 리다이렉트를 자동으로 따라가면 최종 200을 "접근 성공"으로 **오탐**한다. 그래서 기본은 따라가지 않고 3xx를 그 단계 응답으로 기록한다(`configs/verifier.toml`에서 조정).
- 비밀값 제거는 collector와 같은 기준(민감 키 이름·cookie/authorization 헤더 → `***`). **다른 점**: verifier는 계정 비밀번호를 쥐지 않으므로(세션은 collector 창구 안) 알려진 비밀값 스크럽 목록이 보통 비어 있고 구조적 마스킹만 적용한다. 근거 파일 이름에는 회차를 담는다(verifier는 회차마다 다시 실행).
- `session_valid` Check 의미가 약해짐: 명세 m7의 "session_ref 능동 재확인" 대신 **"창구가 세션 보유 중 + 만료 감지 없음"**이다. 실제 세션 만료는 `send()` 응답 신호(로그인 리다이렉트 등, collector README)로 잡는다.

### 한계 (라이브 1회 결과)

- 같은 IDOR 시나리오(`GET /api/users/{id}`)를 docker vulnerable(:8001)·secure(:8000)에 각각 실행: vulnerable은 **success**(위반 재현), secure는 403을 그대로 받아 **indeterminate**(거짓 success를 만들지 않음). 산출물·근거에서 비밀값 0건, 세션 쿠키는 창구 안에만.
- 403으로 거부하는 앱에서는 현재 Check 세트(`response_status`·`response_json`)로는 failure가 아니라 indeterminate가 나온다(거부 응답엔 내용 조건을 평가할 값이 없어 그 Check가 `passed=null`). 깔끔한 failure 판정은 `baseline_match` 등 Check 3종 평가가 들어와야 한다(PR3-c 뒤 작업).

## PR 분할

- **PR1**: 계약 Schema·utils·안전 게이트. HTTP 전송 없음.
- **PR2**: 세션 창구 Protocol로 allow 실행 — URL resolve·`effective_origins`(최종 URL)·리다이렉트 재검사·`limits`·state_change·body_ref·세션 오류, `assertions`/`preconditions`(Check) 평가, ExecutedStep/CheckResult 근거, result 분류(success/failure/indeterminate). 세션 대역으로 개발·테스트.
- **PR3-a**: 실제 collector 세션 창구(`session_gateway.py`) 바인딩 + 라이브 실행 1회 확인.
- **0.2 2단계(이것)**: 입력 사본 3종 0.2.0 미러 + 검증 관계 `source_account_id` 전환. 실행 로직 변경 없음.
- **PR3-b**: 상태 변경 시 테스트 앱 DB 초기화(reset 훅, config).
- **PR3-c**: `graph_updates`(KG node_id 매핑) + Check 3종 평가 + 하드닝.

## 독립 실행·테스트

```bash
.venv/bin/python -m pytest modules/verifier/
```

다른 모듈의 실제 구현 없이 자기 fixture·세션 대역(`ScriptedExecutor`, 주입 시계 `FakeClock`)으로 검증한다. 게이트 실패(block·require_approval·해시 불일치·범위 밖·세션 무효)와 범위 밖 리다이렉트·바인딩에서 세션 창구 `send` 호출이 0건임을 테스트로 고정한다.
