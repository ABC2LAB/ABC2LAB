# semantic_analyzer

의미 분석기. `crawl_result.json`을 받아 요청을 정규화하고, LLM으로 자원·행위·역할 의미와
업무 흐름을 추론해 `semantic_analysis.json`을 만든다.

- 공개 operation: `analyze`
- 입력: `crawl_result.json` (collector)
- 출력: `semantic_analysis.json` (직접 소비자: knowledge_graph, reporter(개발 평가))
- 계약 버전: 입력 `crawl_result` `0.3.0`(collector 출력 Schema를 title·description 외 그대로 미러), 출력 `semantic_analysis` `0.3.0`(2026-10-14 실행 산출물 0.3.0 통일) (명세 v0.1, `docs/spec/m2-semantic_analyzer.md`)

## 설계 경계
- **정규화·구조 그래프는 규칙(LLM 없음)** → `basis=observed`.
  - 정규화: Endpoint(method+path_template, 숫자·UUID 세그먼트 → `{id}`), Parameter(location·value_type·is_sensitive).
  - 구조 관계: `HAS_ROLE`(User→Role), **`ACCESS`(Role→Endpoint + User(계정)→Endpoint)**, `CALL`(Page/Action→Endpoint), `USE`(Endpoint→Parameter).
- **자원·행위 의미와 업무 흐름은 LLM으로 추론** → `basis=inferred`.
  - `action_meaning`, Resource 노드, `REFERENCE`(Endpoint→Resource), `OWNS`(User→Resource, 경로 id 접근 시), `workflows`.
- 원본 `request_id`·`account_id`·`role_id`는 보존하고 새 ID로 바꾸지 않는다.
- `ground_truth.json`은 읽지 않는다. 인증·비밀값은 값 없이(이름·null) 다룬다.

## LLM (provider 교체식)
LLM 호출은 `llm/` 한 곳에 모으고, 설정(`configs/default.toml`의 `[llm] provider`)으로 구현을 바꿔 끼운다.
- `provider = "fake"` (기본): 네트워크 0 `FakeClient`. method+경로로 의미를 결정적으로 흉내낸다. 테스트·CI는 항상 이걸 쓴다.
- `provider = "ollama"`: 로컬 Ollama HTTP API(`/api/generate`)를 **stdlib urllib**로 호출(`llm/ollama_client.py`). **추가 의존성 0.** `model_id`는 설정에 넣는다(예시값 아님).

LLM 출력은 데이터다 — service·validation이 타입·ID·참조를 다시 검증하고, 호출/파싱 실패는 가짜 정상으로 숨기지 않고 `failed`(`LLM_INFERENCE_FAILED`)로 공개한다.

## 명세(v0.1) 대비 바뀐 점과 이유
> 내 **출력 계약(`semantic_analysis.json`, m2)은 그대로**다. 아래는 입력 사본·내부 구현·그래프 의미의 변화다.

| 항목 | 명세 v0.1 | 현재 | 왜 |
|---|---|---|---|
| 입력 `Page.account_id` | m1 Page 표에 **없음** | 입력 스키마 Page에 `account_id`(required) 미러 | collector가 코드/스키마에 `account_id`(관찰 계정)를 추가해 내보냄(collector의 "명세 대비 변경"). 안 받으면 `additionalProperties:false`가 collector 출력을 거절. **명세(docs/spec m1) 반영은 관리자 몫(미반영 상태)** |
| `ACCESS` 관계 | 1차 구현은 **Role→Endpoint 집계만** | Role→Endpoint **+ User(계정)→Endpoint** 둘 다 생성 | KG `role_resource_access` 질의가 **계정 단위 접근**을 보는데 Role만 있으면 결과가 비었음(access_analyzer 피드백). m2 `GraphEdge.relation_type` enum에 `ACCESS`가 이미 있고 source/target 노드타입 제약이 없어 **스키마(계약) 변경은 아니다**. 의미 확장이라 KG(이동찬)와 합의 대상으로 열려 있음(현재 additive) |
| 검증 오류 메시지 | — | jsonschema 메시지를 **위치+제약 종류만** 남김(입력 값 제거) | anyOf/type 등 메시지에 입력 값(URL·토큰)이 섞여 샐 수 있어 차단(절대규칙 2) |
| LLM provider | "다음 PR에서 실제 provider" | `fake`+`ollama` provider 교체식, 의존성 0 | 실모델 연결. 계약(`model_info` 등)은 불변 |

`node_id`(예: `user:account:user_a`)는 **내부 그래프 식별자**다(`utils/ids.py`). reporter는 ID 문자열이 아니라 정규화 키로 매칭하므로 지금은 내부용이며, **다른 모듈이 같은 포맷에 의존하는 것은 아직 공통 계약이 아니다**(그런 요구가 있으면 출력 계약·docs/spec + KG 합의로 다룬다). 계정 식별이 모듈을 넘을 때의 안정 키는 `account_id`이며, 노드 `properties.account_id`에 원본을 보존한다.

## 변경 이력 (순차)
| 시점 | PR | 내용 | 비고 |
|---|---|---|---|
| 1차 | #19 | `analyze` 1차 구현(정규화·구조 그래프·FakeClient·envelope·참조 무결성·원자적 저장) | 25 tests |
| 2차 | #24 | ① 입력 `Page.account_id` 미러 ② 검증 메시지 비밀값 누출 차단 ③ `ACCESS`에 계정→Endpoint 추가 | 스키마 변경(입력 사본)·보안·그래프 의미 |
| 3차 | #28 | Ollama provider 추가(`OllamaClient`+`factory`, `[llm] provider` 토글, 의존성 0) | 기본 fake 유지, 계약 불변 |

> 실모델(Ollama) e2e는 배선·파싱·재검증까지 확인됐고, 유효 초안 생성 품질은 모델·프롬프트 영역으로 후속(GPU 재확인). 상세는 개인 작업 로그/STATUS.

## 독립 실행
```bash
# 레포 루트에서 (기본 provider=fake)
.venv/bin/python -m modules.semantic_analyzer.entrypoint analyze \
  --input modules/semantic_analyzer/tests/fixtures/runs/run_demo_001/artifacts/iteration-000/collector/crawl_result.json \
  --output-dir <쓰기 가능한 디렉토리>
```
결과 요약(건수·상태)을 JSON으로 출력한다. 결과 파일 자체(`data/`·`runs/`)는 직접 열어보지 않는다.
실모델로 돌리려면 Ollama 기동 후 `configs/default.toml`에 `provider="ollama"`, `model_id="<설치한 모델 태그>"`.

## 테스트
```bash
.venv/bin/python -m pytest modules/semantic_analyzer -q
```
다른 모듈의 실제 코드 없이 자기 입력 fixture·FakeClient(및 주입 transport)로 정상/partial/failed·입력 변화·참조 무결성·LLM 실패 처리를 검증한다. 네트워크 0.

## 상태 처리
- `completed`: 정상(errors=[]).
- `partial`: 입력이 partial이면 유효 data만 처리하고 사유를 errors에 남긴다.
- `failed`: 입력을 못 읽거나(Schema·버전·해시·upstream failed), LLM 추론이 실패하거나, 자기 출력이 참조 무결성을 어기면 `data=null`로 공개한다.

## 폴더
```
entrypoint.py            # run(operation, input_paths, output_dir, context) + CLI, provider 선택
service.py               # 정규화 + 구조 그래프 + (LLM) 의미 추론
llm/adapter.py           # LlmClient 프로토콜 + FakeClient + LlmError
llm/ollama_client.py     # OllamaClient (stdlib urllib, 의존성 0)
llm/factory.py           # configs의 provider로 클라이언트 선택(기본 fake)
utils/                   # normalize·ids·io(원자적 저장)·envelope·validation
schemas/input|output/    # 입력(collector 계약 사본)·출력 JSON Schema
configs/default.toml     # 실행 설정([llm] provider: fake|ollama)
tests/                   # 테스트 + 입력 fixture
```
