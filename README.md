# ABC2LAB — 인가·비즈니스 로직 취약점 특화 온프레미스 DAST

폐쇄망 안에서 설치해 돌리는 진단 도구입니다. 소유 관계·권한 경계를 알아야 판정되는 인가·비즈니스 로직 취약점(IDOR·권한 상승·업무 흐름 우회)을 대상으로 합니다.

입출력 계약·개발 기준은 노션 `ABC2LAB_인터페이스_명세서_v0.1`을 따릅니다.

## 파이프라인

8개 모듈이 각자 `modules/<module_id>/` 안에서 개발되고, 정해진 JSON 파일로만 이어집니다.

| 모듈 | operation | 출력 | 담당 |
|---|---|---|---|
| `collector` | collect | `crawl_result.json` | 최민준 |
| `semantic_analyzer` | analyze | `semantic_analysis.json` | 이경준 |
| `knowledge_graph` | ingest · query · apply_verification | `graph_query_result.json` | 이동찬 |
| `access_analyzer` | prepare_queries · analyze | `graph_query.json` · `vulnerability_candidates.json` | 최민준 |
| `scenario_generator` | generate | `test_scenarios.json` | 이경준 |
| `safety_policy` | evaluate | `safety_decisions.json` | 이동찬 |
| `verifier` | verify | `verification_results.json` | 최민준 |
| `reporter` | report · evaluate | `diagnosis_report.json` · `evaluation_results.json` | 이동찬 |

collector는 `modules/collector/`에서 명세 형식 `crawl_result.json`과 근거 파일(응답·DOM)을 공개합니다(같은 역할 복수 계정 지원). `target-app/`은 테스트용 쇼핑몰입니다.

## 표준 개발 환경

| 항목 | 기준 |
|---|---|
| OS | Ubuntu 24.04 LTS · x86_64. Windows는 WSL2의 Ubuntu에서 실행 |
| Python | CPython 3.12.13 (`.python-version`) |
| uv | 0.12.23 |
| 가상환경 | 레포 루트의 `.venv/` 하나 |
| 패키지 | 루트 `requirements.lock.txt` (버전·해시 고정) |

테스트 앱·Neo4j는 Docker로 띄웁니다.

uv 설치:

```bash
curl -LsSf https://astral.sh/uv/0.12.23/install.sh | sh
uv --version   # uv 0.12.23
```

이미 다른 버전이 있으면 `uv self update 0.12.23`으로 맞춥니다.

## 최초 설치

레포 루트에서:

```bash
uv python install
uv venv .venv
uv pip sync --python .venv/bin/python --require-hashes requirements.lock.txt
uv pip check --python .venv/bin/python
.venv/bin/python -c 'import sys; print(sys.version); print(sys.executable)'
```

마지막 줄이 `3.12.13`과 이 레포의 `.venv/bin/python`을 출력하면 됩니다.

collector용 브라우저를 처음 한 번 설치합니다.

```bash
.venv/bin/python -m playwright install chromium
.venv/bin/python -m playwright install-deps chromium
```

`install-deps`는 시스템 라이브러리를 apt로 설치하므로 **sudo 권한이 필요합니다**(비밀번호를 묻습니다).

환경변수 파일을 만들고 값을 채웁니다. `.env`는 커밋하지 않습니다.

```bash
cp .env.example .env
cp modules/collector/configs/collector.example.toml modules/collector/configs/collector.toml
```

collector 계정 로그인 ID·비밀번호는 `modules/collector/.env.example`의 키를 `.env`에 채웁니다. `collector.toml`은 비밀값이 없지만 대상마다 다른 로컬 설정이라 커밋하지 않습니다(git 제외).

## pull 후 동기화

```bash
git pull
uv pip sync --python .venv/bin/python --require-hashes requirements.lock.txt
uv pip check --python .venv/bin/python
```

`.python-version`이 바뀌었으면 `.venv`를 새 Python으로 다시 만든 뒤 동기화합니다.

```bash
uv python install
uv venv --clear .venv
uv pip sync --python .venv/bin/python --require-hashes requirements.lock.txt
```

## 테스트와 실행

항상 레포 루트에서 실행합니다. 모듈 폴더 안에서 실행하면 import가 깨집니다.

```bash
.venv/bin/python -m pytest                                              # 전체
.venv/bin/python -m pytest modules/collector/                           # 모듈 하나
.venv/bin/python -m modules.collector.entrypoint collect --mode development  # collector → runs/<run_id>/
```

`uv run pytest`도 같은 `.venv`를 그대로 씁니다. 각 모듈의 테스트는 `modules/<module_id>/tests/`에 둡니다.
`modules/`에는 `__init__.py`를 두지 않습니다. import는 `modules.<module_id>.…` 절대 경로로 쓰고, pytest는 `pytest.ini` 설정으로 같은 이름을 씁니다.

## 의존성 변경

- 루트 `.python-version`·`.gitignore`·`requirements.txt`·`requirements.lock.txt`는 GitHub 관리자만 고칩니다.
- 패키지가 필요하면 자기 `modules/<module_id>/requirements.txt`에 `패키지==버전`으로 적어 PR로 제안합니다. 관리자가 잠금 파일을 다시 만들어 같은 PR에 넣습니다.
- 이 레포는 pyproject 기반 uv 프로젝트가 아니므로 `uv add`·`uv sync`·`uv lock`은 쓰지 않습니다. 잠금 파일은 손으로 고치지 않습니다.

관리자의 잠금 파일 재생성·검사:

```bash
uv pip compile --python .venv/bin/python --generate-hashes requirements.txt -o requirements.lock.txt
uv pip sync --python .venv/bin/python --require-hashes requirements.lock.txt
uv pip check --python .venv/bin/python
```

## Docker 실행

레포 루트에서:

```bash
docker compose up -d --build
```

다음 서비스가 `127.0.0.1`에만 열립니다.

- Secure 테스트 앱 (기본 8000)
- Vulnerable 테스트 앱 (기본 8001)
- Neo4j (기본 7474·7687)

## 파이프라인 실행 (`pipeline.py`)

루트 `pipeline.py`가 8개 모듈의 공개 `entrypoint.run()`을 순서대로 부르고 경로·실행 인자만 잇습니다(명세 03). 1차 구현은 iteration 0 한 회차만 돕니다.

### 실행 전제

- 테스트 앱·Neo4j 컨테이너가 떠 있어야 합니다(위 "Docker 실행").
- collector 브라우저가 설치돼 있어야 합니다(위 "최초 설치"의 `playwright install chromium`).
- ollama가 떠 있고, 실제 설치한 모델 태그(`ollama list`로 확인)를 각 모듈 설정에 적어야 합니다. 모델 태그를 코드·커밋 파일에 박지 않습니다.
- 환경변수는 **knowledge_graph가 읽는 `NEO4J_*` 키만** export합니다. `.env` 전체를 export하지 않습니다(collector 계정 값까지 셸에 올라갑니다). 값에 따옴표·특수문자가 있으면 아래 명령이 깨질 수 있으니 KG 연결이 실패하면 먼저 확인합니다.

  ```bash
  set -a; source <(grep -E '^NEO4J_(URI|USERNAME|PASSWORD|DATABASE)=' .env); set +a
  ```
- 모듈별 설정 위치는 각 모듈이 자기 환경변수로 읽습니다. 런너는 이 값을 읽거나 넘기지 않습니다. 아래 셋은 `.env`에서 export하지 않고(`.env`에서는 `NEO4J_*`만) 셸에서 직접 지정합니다.

| 모듈 | 환경변수 | 비고 |
|---|---|---|
| collector | `COLLECTOR_CONFIG_PATH` · `COLLECTOR_SECRETS_PATH` | 없으면 `modules/collector/configs/collector.toml` · `.env`. 계정 값은 collector가 파일에서 직접 읽음 |
| scenario_generator | `SCENARIO_GENERATOR_CONFIG_PATH` | 커밋된 기본값은 `provider = "none"`이라 복사본을 만들어 가리킴 |
| safety_policy | `SAFETY_POLICY_CONFIG_PATH` | 필수, 기본값 없음. 운영자가 쓴 policy JSON을 run_root 밖에 두고 가리킴. safety_policy가 `run_root/private/safety_policy/`에 사본을 직접 만들고, 같은 run에서는 원본 해시가 같을 때만 재사용 |
| semantic_analyzer | `SEMANTIC_ANALYZER_CONFIG_PATH` | #57 머지 후. 그 전에는 커밋된 기본 설정(fake) |

### 명령과 인자

```bash
.venv/bin/python pipeline.py --mode development --target-url <대상 URL> \
  --dataset-id <dataset_id> --matching-profile <matching_profile>
.venv/bin/python pipeline.py --mode diagnosis --target-url <대상 URL>
```

| 인자 | 필수 | 내용 |
|---|---|---|
| `--mode` | 예 | `development` 또는 `diagnosis` |
| `--target-url` | 예 | reporter에 넘기는 대상 URL. **collector 설정의 `target_url`과 글자 하나까지 같아야 합니다**(끝 슬래시 포함). reporter.evaluate가 `crawl_result`의 값과 문자열로 비교합니다 |
| `--run-id` | 아니오 | 없으면 시각+난수. 영문·숫자로 시작하고 영문·숫자·`._-`만 |
| `--runs-dir` | 아니오 | 기본 `runs`. 결과는 `<runs-dir>/<run_id>/` |
| `--project-root` | 아니오 | ground_truth 경로 기준. 기본 레포 루트 |
| `--dataset-id` | development만 | `datasets/<dataset_id>/ground_truth.json`(개발 평가 전용) |
| `--matching-profile` | development만 | reporter.evaluate 매칭 프로파일 |

- `development`에서 `--dataset-id`·`--matching-profile`이 빠지면 CLI 오류(종료 코드 2).
- `diagnosis`에서 둘 중 하나라도 주면 CLI 오류(종료 코드 2). 조용히 무시하지 않습니다. diagnosis는 reporter.evaluate를 부르지 않고 ground_truth를 열지도 해시하지도 않습니다.

### 단계 순서와 중단·계속 규칙

| # | 단계 | 실패하면 |
|---|---|---|
| 1 | `collector.collect` | 멈춤 |
| 2 | `semantic_analyzer.analyze` | 멈춤 |
| 3 | `knowledge_graph.ingest` | 멈춤(`is_ready`가 참이 아니어도) |
| 4 | `access_analyzer.prepare_queries` | 멈춤 |
| 5 | `knowledge_graph.query` | 멈춤(ingest와 graph_id가 다르면 실패) |
| 6 | `access_analyzer.analyze` | 멈춤 |
| 7 | `scenario_generator.generate` | 멈춤 |
| 8 | `safety_policy.evaluate` | 멈춤 |
| 9 | `verifier.verify` | 멈춤 |
| 10 | `knowledge_graph.apply_verification` | **계속**: 뒤 단계(report·evaluate)가 이 결과를 입력으로 쓰지 않음. 다음 회차만 막힘 |
| 11 | `reporter.report` | **계속**: 뒤 단계(evaluate)가 `diagnosis_report.json`을 입력으로 쓰지 않음 |
| 12 | `reporter.evaluate` | development에서만 |

- 실패는 모듈 반환 `status=failed`, 결과 파일 경로 없음, 반환 경로가 규칙 경로(`artifacts/iteration-<NNN>/<producer>/<파일>`)와 다름, 파일 SHA-256이 반환값과 다름, 모듈 예외를 모두 포함합니다.
- 멈추면 뒤 단계는 부르지 않고 요약에 `skipped`(사유: 앞 단계 이름)로 남깁니다. "0건"으로 쓰지 않습니다. `partial`은 계속합니다.
- 세션 창구는 `verifier.verify` 직전에 열고 verify가 끝나면 닫습니다. 열지 못하면(`ValueError`·`RuntimeError`·`OSError`) `session_executor=None`으로 verify를 부릅니다. verifier는 allow 시나리오를 보내지 않고 `execution_status=not_executed`·`result=indeterminate`·`steps=[]`, 오류 `SESSION_UNAVAILABLE`로 남깁니다(미실행).

### 종료 코드와 요약

| 종료 코드 | 경우 |
|---|---|
| `0` | 모든 단계 completed |
| `1` | partial이 있거나 세션 창구를 열지 못함(실패·skipped는 없음) |
| `2` | failed 또는 skipped가 있음, CLI 오류, `<runs-dir>/<run_id>`가 이미 있음(새 `--run-id`로 다시 실행) |

stdout에는 요약 JSON 한 줄만 나오고 로그는 stderr로 갑니다. 요약 필드:

- `run_id` · `mode` · `iteration` · `run_root` · `exit_code`
- `halted_by`: 멈춘 단계 이름(없으면 null)
- `session_window`: `not_opened` · `opened` · `failed`, `session_window_error`: 열기 실패 예외 타입
- `steps[]`: `step`(`<module>.<operation>`), `status`(`completed`·`partial`·`failed`·`skipped`), `artifact`(run_root 기준 상대 경로), `sha256`, `error_codes`, `error_count`, `detail`(런너가 붙인 사유), `control`(응답에 있으면 `graph_id`·`graph_revision`·`previous_graph_revision`·`is_ready`·`is_applied`·`report_path`)

### 전제가 없을 때 멈추는 곳

| 아직 안 된 것 | 증상 |
|---|---|
| `SCENARIO_GENERATOR_CONFIG_PATH`를 안 줌 | 커밋된 기본값이 `none`이라 `scenario_generator.generate`가 `DRAFTER_NOT_CONFIGURED`로 failed |
| `SAFETY_POLICY_CONFIG_PATH`를 안 줌·파일 오류 | `safety_policy.evaluate`가 `CONFIG_INVALID`로 failed(런너는 Policy 파일을 찾거나 만들지 않음) |
| #57 머지 전·`SEMANTIC_ANALYZER_CONFIG_PATH`를 안 줌 | 커밋된 기본 설정(fake: 경로 세그먼트 기반 규칙 추론, `model_info=null`)으로 돈다. 후보는 나오지만 추론 품질은 실제 모델보다 낮음 |
| collector 창구의 Playwright 예외 감싸기 | 브라우저 쪽 문제면 런너가 요약 없이 traceback으로 끝남(파이썬 기본 종료 코드 1이라 위 표의 1과 섞임) |

### 런너 규칙

- `modules.<module_id>.entrypoint`만 불러옵니다. 모듈 내부(service·utils·schemas)는 import하지 않습니다.
- 산출물 JSON 본문은 읽지 않습니다. 모듈 반환값·파일 존재·파일 SHA-256만 봅니다.
- `<runs-dir>/<run_id>`가 이미 있으면 거절합니다(완료 파일 불변).
- `.env`·`NEO4J_*`·계정 값을 읽거나 넘기지 않고 로그에도 남기지 않습니다. 모듈 소유 경로(`private/<module_id>/`)에 쓰지 않습니다.
- 테스트: `tests/pipeline/`(가짜 모듈). 한계: 가짜 모듈은 지금의 모듈 반환 모양을 흉내 낸 것이라, 모듈 반환 모양이 바뀌면 실제 실행에서만 드러납니다.

## 환경 검증 기록

새 clone·새 `.venv`에서 "최초 설치" 명령과 전체 테스트를 실행한 결과입니다.

| 날짜 | 검증 커밋 | OS | Python | uv | 결과 |
|---|---|---|---|---|---|
| 2026-10-06 | `cc1541c` (feature/minjun-setup) | Ubuntu 24.04.1 (WSL2) x86_64 | 3.12.13 | 0.12.23 | 잠금 19개 `--require-hashes` 설치 · `uv pip check` 통과 · 초기 패키지 import 성공 · pytest 205 passed |
| 2026-10-06 | `bc80025` (main 기준 커밋) | 위와 같음 | 3.12.13 | 0.12.23 | `cc1541c`와 README.md 외 동일(`git diff --stat`)이라 위 결과를 그대로 적용 |

main·dev 브랜치 보호 적용(2026-10-06): PR 필수 · 승인 1개 · 승인 뒤 커밋을 올리면 승인 해제 · force push·삭제 금지.
