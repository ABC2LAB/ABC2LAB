# ABC2LAB — AI 기반 웹 취약점 진단 시스템

웹앱을 자동 탐색해 구조를 Knowledge Graph로 만들고, 그 위에서 접근통제 문제를 추론한다.

## 준비

Python 3.13, [uv](https://docs.astral.sh/uv/), Docker가 필요하다.

```bash
uv sync --frozen --all-groups   # .venv 생성 + uv.lock 그대로 설치
uv run playwright install chromium   # 크롤러를 돌릴 때만, 처음 한 번
cp .env.example .env            # 값 채우기. .env는 커밋하지 않는다
docker compose up -d --build    # 테스트 앱(secure/vulnerable) + Neo4j
```

크롤러가 필요 없으면 `uv sync --frozen --group dev`로 playwright를 빼고 설치한다.

## 실행·테스트 (항상 레포 루트에서)

```bash
uv run pytest                   # 전체 테스트
uv run pytest crawler/          # 모듈 하나만
```

## 의존성 바꿀 때

`uv add 패키지==버전` (그룹이면 `--group dev` 등)으로 추가하고, `pyproject.toml`과 `uv.lock`을 같이 커밋한다.
`uv.lock`은 손으로 고치지 않는다.
