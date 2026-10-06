# semantic_analyzer

의미 분석기. `crawl_result.json`을 받아 요청을 정규화하고, LLM으로 자원·행위·역할 의미와
업무 흐름을 추론해 `semantic_analysis.json`을 만든다.

- 공개 operation: `analyze`
- 입력: `crawl_result.json` (collector)
- 출력: `semantic_analysis.json` (직접 소비자: knowledge_graph, reporter(개발 평가))
- 계약 버전: `schema_version = 0.1.0` (명세 v0.1, `docs/spec/m2-semantic_analyzer.md`)

## 설계 경계
- **정규화·구조 그래프는 규칙(LLM 없음)** → `basis=observed`.
  - 정규화: Endpoint(method+path_template, 숫자·UUID 세그먼트 → `{id}`), Parameter(location·value_type·is_sensitive).
  - 구조 관계: `HAS_ROLE`(User→Role), `ACCESS`(Role→Endpoint), `CALL`(Page/Action→Endpoint), `USE`(Endpoint→Parameter).
- **자원·행위 의미와 업무 흐름은 LLM으로 추론** → `basis=inferred`.
  - `action_meaning`, Resource 노드, `REFERENCE`(Endpoint→Resource), `OWNS`(User→Resource, 경로 id 접근 시), `workflows`.
- 원본 `request_id`·`account_id`·`role_id`는 보존하고 새 ID로 바꾸지 않는다.
- `ground_truth.json`은 읽지 않는다. 인증·비밀값은 값 없이(이름·null) 다룬다.

## LLM
LLM 호출은 `llm/adapter.py` 한 곳에 모은다. 이번 PR은 네트워크 0인 `FakeClient`만 제공한다
(의미 추론을 method+경로로 결정적으로 흉내낸다). 실제 provider(예: Ollama)는 같은 `LlmClient`
프로토콜로 다음 PR에서 붙이며, 그때 `requirements.txt`에 선언하고 관리자 lock 갱신을 요청한다.

## 독립 실행
```bash
# 레포 루트에서
.venv/bin/python -m modules.semantic_analyzer.entrypoint analyze \
  --input modules/semantic_analyzer/tests/fixtures/runs/run_demo_001/artifacts/iteration-000/collector/crawl_result.json \
  --output-dir <쓰기 가능한 디렉토리>
```
결과 요약(건수·상태)을 JSON으로 출력한다. 결과 파일 자체(`data/`·`runs/`)는 직접 열어보지 않는다.

## 테스트
```bash
.venv/bin/python -m pytest modules/semantic_analyzer -q
```
다른 모듈의 실제 코드 없이 자기 입력 fixture와 FakeClient로 정상/partial/failed·입력 변화·참조 무결성을 검증한다.

## 상태 처리
- `completed`: 정상(errors=[]).
- `partial`: 입력이 partial이면 유효 data만 처리하고 사유를 errors에 남긴다.
- `failed`: 입력을 못 읽거나(Schema·버전·해시·upstream failed) 자기 출력이 참조 무결성을 어기면 `data=null`로 공개한다.

## 폴더
```
entrypoint.py            # run(operation, input_paths, output_dir, context) + CLI
service.py               # 정규화 + 구조 그래프 + (LLM) 의미 추론
llm/adapter.py           # LlmClient 프로토콜 + FakeClient
utils/                   # normalize·ids·io(원자적 저장)·envelope·validation
schemas/input|output/    # 입력(collector 계약 사본)·출력 JSON Schema
configs/default.toml     # 실행 설정(이번엔 fake)
tests/                   # 테스트 + 입력 fixture
```
