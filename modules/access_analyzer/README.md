# access_analyzer

| 항목 | 값 |
|---|---|
| module_id | `access_analyzer` |
| 공개 operation | `prepare_queries`, `analyze` (규칙 없는 골격 — 후보는 빈 배열, Rule은 다음 PR) |
| 명세 | `docs/spec/m4-access_analyzer.md`, `docs/spec/02-common-contract.md`, `docs/spec/03-runner-layout.md` |
| 계약 버전 | `schema_version = 0.1.0` |

KG 조회 계획을 세우고(prepare_queries), KG가 돌린 결과로 인가·비즈니스 로직 취약점 **후보**를 만든다(analyze).
후보는 **검증 대상이지 확정 취약점이 아니다.** Neo4j 접속·Cypher 실행은 knowledge_graph가 한다. 이 모듈은
허용된 query_key·parameters만 요청하고 결과 JSON을 검증해 쓴다.

## 입력·출력

| 방향 | 파일 | operation | 생산자/소비자 |
|---|---|---|---|
| 출력 | `graph_query.json` | `prepare_queries` | 소비자: knowledge_graph |
| 입력 | `graph_query_result.json` | `analyze` | 생산자: knowledge_graph |
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

- `context` 키는 명세 03 실행 값 + 필요한 graph 상태 여섯 개다: `run_id`·`iteration`·`mode`·`run_root`·`graph_id`·`expected_graph_revision`. 정의되지 않은 키는 거절한다.
- `graph_id`·`expected_graph_revision`은 KG의 공개 제어 응답에서 온다(명세 03 "필요한 graph 상태"). `expected_graph_revision`을 걸면 KG가 revision 불일치 시 1차로 막고, analyze가 결과의 `graph_revision`을 2차로 확인한다.
- 반환값·CLI stdout 한 줄: `{status, artifact_path, artifact_id, sha256, errors}`.

CLI:

```bash
# 질의 계획
.venv/bin/python -m modules.access_analyzer.entrypoint prepare_queries \
  --mode development --graph-id graph_example [--expected-graph-revision 1] \
  [--run-id run_example] [--iteration 0] [--runs-dir runs]

# 후보 분석 (KG가 낸 graph_query_result.json을 입력으로)
.venv/bin/python -m modules.access_analyzer.entrypoint analyze \
  --mode development --graph-id graph_example --expected-graph-revision 1 \
  --input-path artifacts/iteration-000/knowledge_graph/graph_query_result.json \
  --run-id run_example --iteration 0
```

`analyze`는 `graph_query_result.json` 하나를 입력으로 받는다(`--input-path`는 run_root 기준 상대 또는 절대). 입력은 run_root 안에 있어야 한다.

## 질의 계획 (prepare_queries)

허용된 네 개의 query_key를 각각 한 번씩, 고정된 순서로 담는다. 지금은 run 범위 전체를 조회한다(필터 빈 배열).

| query_key | 용도 |
|---|---|
| `resource_ownership` | 계정·자원 소유 관계 |
| `role_resource_access` | 역할·계정·Endpoint·자원 접근 관계 |
| `workflow_dependencies` | 업무 흐름 단계 의존 관계 |
| `structure_snapshot` | 실제 적재 구조. 명세상 평가·감사용(reporter)이라 analyze 규칙 입력으로 쓰지 않지만, 질의 작성 주체는 access_analyzer다(m4) |

## 실패 처리

- **산출물 타입을 알 수 있으면**(지원 operation) 실패도 `status=failed`·`data=null` 파일로 공개한다(입력 JSON 혼입/누락, 입력 계약 위반, 입력 결과 failed·stale·graph_id 불일치, 자기 출력 검증 실패 등). 실패를 정상 빈 결과로 숨기지 않는다. 종료코드 2.
- **타입·경로가 불확실하면** 파일을 쓰지 않고 반환값으로만 알린다. 종료코드 3. 여기에 해당:
  - 알 수 없는 operation(어떤 산출물을 쓸지 모름) → `OPERATION_UNSUPPORTED`.
  - 잘못된 `run_id`·`run_root`, output_dir가 `run_root/artifacts/iteration-NNN/access_analyzer`가 아님, `..`·symlink로 run 밖을 가리킴, 폴더 생성 불가.
- analyze의 입력 경로는 run_root 안이어야 한다. 밖을 가리키면 `INPUT_PATH_INVALID`로 failed 파일을 공개한다(출력 경로는 신뢰 가능).
- 완료 파일은 한 번만 공개한다. 같은 경로에 이미 있으면 덮어쓰지 않고 거절한다(`ARTIFACT_EXISTS`).
- 종료코드: 0 completed / 1 partial / 2 failed(파일 있음) / 3 파일 없음.

## 독립 실행·테스트

저장소 루트의 공통 `.venv`에서 실행한다. 다른 모듈의 실제 구현 없이 자기 fixture·Schema로 검증한다.

```bash
.venv/bin/python -m pytest modules/access_analyzer/
```

## 명세(v0.1) 대비 변경

출력 계약을 명세 표보다 **좁힌** 것. 우리 출력이 더 엄격해 소비자(더 느슨함)의 입력을 항상 통과한다. 소비자와 합의 전까지 임시이며 파이프라인 연결 때 노션·docs/spec에 반영한다(10/7 팀 규칙).

| 날짜 | 파일·필드 | 변경 | 근거·합의 |
|---|---|---|---|
| 2026-10-07 | `vulnerability_candidates` `Candidate.source_request_ids` | `array<string>` → `minItems:1` | 근거 요청을 못 채우면 후보를 발행하지 않는다(확정 답 2). 생산자: 최민준. 소비자 scenario_generator(이경준)·reporter(이동찬)는 더 느슨해 영향 없음(수동 확인) |
| 2026-10-07 | `vulnerability_candidates` `Candidate.resource_ids` | `array<string>` → `minItems:1` | 같음. TODO(choiamj980818): `workflow_step_bypass` 규칙은 자원 없이 `workflow_id`만 가질 수 있어 그 규칙 추가 시 minItems:1을 재검토한다 |
| 2026-10-07 | `vulnerability_candidates` `Candidate.reference_account_id`·`workflow_id` | `string/null` → `nonEmptyString/null` | 빈 문자열 금지. reporter 입력 Schema와 일치시킴(수동 확인: reporter·scenario_generator 입력 통과) |

## 연결 때 다른 모듈과 맞출 것

모듈 완성 후 파이프라인 연결 때 맞춘다. 그 전까지는 명세 의미대로 만든 fixture로 독립 개발한다.

- **A2 (결과 row의 계정·역할 ID 형식) → #29(이동찬)로 해결.** ownership `owner_account_id`·access `account_id`·`role_id`가 이제 crawl 원본 ID로 나온다(KG observation 기반). resource_id는 `resource:<key>`, endpoint_id는 `endpoint:<METHOD>:<path>`.
- **A3 (상대: knowledge_graph)**: 후보 `source_request_ids`를 채우려면 `AccessRow`에 수집 요청 ID가 필요하다(현재 row에 없음). KG observation에는 request_id가 있어 `AccessRow`에 추가하는 계약 변경(A3(b))은 쉽다. 합의 전에는 못 채우는 후보를 발행하지 않고 errors로 둔다.
