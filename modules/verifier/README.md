# verifier

| 항목 | 값 |
|---|---|
| module_id | `verifier` |
| 공개 operation | `verify` (이 PR은 **안전 게이트**까지 — 실제 HTTP 전송은 PR2) |
| 명세 | `docs/spec/m7-verifier.md`, `02-common-contract.md`, `03-runner-layout.md` |
| 계약 버전 | `schema_version = 0.1.0` |

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

- 입력 Schema는 `schemas/input/`의 사본으로, **생산자 실제 출력 Schema를 그대로 미러링**한다(더 엄격하게 하지 않음). `crawl_result`의 `Page.account_id`는 m1 명세 표엔 없고 collector가 추가한 필드라 사본에도 포함한다(#35까지 반영).
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
    session_executor=None,  # 런너가 collector 세션 창구 구현을 주입(명세 03). 이 PR은 게이트만이라 쓰지 않음
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
   - **allow** → 각 step의 계정이 `effective_account_ids`·crawl 계정·역할·세션과 맞는지 확인. 어긋나면 `indeterminate`(not_executed)로 사유 코드(`ACCOUNT_SCOPE_MISMATCH`·`ROLE_MISMATCH`·`SESSION_INVALID`)를 남긴다.
4. 이 PR(1)은 **실행 기능이 없어** 게이트를 통과한 allow도 실행하지 않고 `indeterminate`+`not_executed`로 두되, 진짜 판단불가와 구분되게 **`EXECUTION_PENDING`** 코드를 남긴다. PR2에서 실제 실행(success/failure)로 대체한다.

- **판정 누락 처리**: 시나리오에 대응하는 Policy 판정이 없으면 `VerificationItem`을 만들 수 없다(`policy_decision`·`decision_id`가 필수라 results에 넣을 값이 없다). 숨기지 않고 envelope `errors`에 `POLICY_DECISION_MISSING`(item_ref=scenario_id)로 남기고 `status=partial`로 둔다.
- `graph_updates`는 `basis=verified`와 실제 실행 근거(evidence_refs≥1)가 있는 성공 결과만 출처로 삼는다. 이 PR은 실행이 없어 항상 비어 있다.

## 실패 처리

- 출력 타입이 `verification_results` 하나뿐이라 **출력 경로를 신뢰할 수 있으면** 실패도 `status=failed`·`data=null` 파일로 공개한다(미지원 operation, 입력 누락·여분·계약 위반, 해시 불일치, 자기 출력 검증 실패 등). 종료코드 2.
- **경로 자체가 불확실하면**(잘못된 `run_id`·`run_root`, output_dir 불일치, `..`·symlink, 폴더 생성 불가) 파일을 쓰지 않고 반환값으로만 알린다. 종료코드 3. 입력 경로는 run_root 안이어야 한다.
- 완료 파일은 한 번만 공개한다(`ARTIFACT_EXISTS`). 종료코드: 0 completed / 1 partial / 2 failed(파일 있음) / 3 파일 없음.

## 세션 공개 창구 규약 (collector ↔ verifier, 둘 다 최민준 — 실물 구현 PR3)

- verifier는 `executor.py`의 Protocol(`SessionExecutor`·`SessionLease`)로만 세션을 쓴다. collector 코드·브라우저/세션 객체를 import하지 않고, 런너가 구현(또는 테스트 대역)을 주입한다(명세 03).
- 실행 모델: **리스가 요청을 대신 전송**한다 — verifier가 resolve한 요청을 넘기면 창구가 그 계정 세션으로 보내고 응답을 돌려준다. 세션 쿠키·토큰은 창구(collector) 안에만 머물고 전달 JSON·근거에 넣지 않는다.
- 규약: `lease(account_id)` → `is_valid()`(session_ref 있어도 실제 유효성 재확인) · `send()`(자동 리다이렉트 끔) · `release()`. 접근·만료·대여/반납·종료·오류 규약은 collector와 구현 전 확정한다.

## 명세(v0.1) 대비 (출력)

출력 `graph_updates`의 node/edge는 `basis`를 `const "verified"`로, `evidence_refs`를 `minItems:1`로 **명세 m7("basis=verified와 실제 실행 근거만")에 맞춰** 소비자(KG·reporter)보다 좁게 둔다. 우리 출력이 더 엄격해 소비자 입력을 항상 통과한다.

## PR 분할

- **PR1(이것)**: 계약 Schema·utils·안전 게이트. HTTP 전송 없음.
- **PR2**: 세션 창구로 allow 실행 — url_template·parameters·bindings resolve, `effective_origins`·`limits`·리다이렉트 재검사, `assertions`(Check) 평가, ExecutedStep/CheckResult 근거, result 분류(success/failure/indeterminate), graph_updates 생성.
- **PR3**: 실제 collector 세션 창구 바인딩 + 상태 변경 시 테스트 앱 DB 초기화(reset 훅, config) + 하드닝.

## 독립 실행·테스트

```bash
.venv/bin/python -m pytest modules/verifier/
```

다른 모듈의 실제 구현 없이 자기 fixture·세션 대역(CountingExecutor)으로 검증한다. 게이트 실패(block·require_approval·해시 불일치·범위 밖·세션 무효)에서 세션 창구 `send` 호출이 0건임을 테스트로 고정한다.
