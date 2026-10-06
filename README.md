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

collector는 `modules/collector/`에서 명세 형식 `crawl_result.json`을 공개합니다(근거 파일·복수 계정은 작업 중). `target-app/`은 테스트용 쇼핑몰입니다.

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
```

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

## 환경 검증 기록

새 clone·새 `.venv`에서 "최초 설치" 명령과 전체 테스트를 실행한 결과입니다.

| 날짜 | 검증 커밋 | OS | Python | uv | 결과 |
|---|---|---|---|---|---|
| 2026-10-06 | `cc1541c` (feature/minjun-setup) | Ubuntu 24.04.1 (WSL2) x86_64 | 3.12.13 | 0.12.23 | 잠금 19개 `--require-hashes` 설치 · `uv pip check` 통과 · 초기 패키지 import 성공 · pytest 205 passed |
| 2026-10-06 | `bc80025` (main 기준 커밋) | 위와 같음 | 3.12.13 | 0.12.23 | `cc1541c`와 README.md 외 동일(`git diff --stat`)이라 위 결과를 그대로 적용 |

main·dev 브랜치 보호 적용(2026-10-06): PR 필수 · 승인 1개 · 승인 뒤 커밋을 올리면 승인 해제 · force push·삭제 금지.
