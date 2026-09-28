# ABC2LAB — AI 기반 웹 취약점 진단 시스템

웹앱을 자동 탐색해 구조를 Knowledge Graph로 만들고, 그 위에서 접근통제 문제를 추론하는 프로젝트입니다.

## 현재 구성

- Python 3.13
- FastAPI
- Neo4j
- Playwright 기반 웹 크롤러
- React + Vite + JavaScript 프론트엔드
- Knowledge Graph 기반 웹 구조 표현 및 분석

현재 프론트엔드는 정적 mock 데이터를 중심으로 구성되어 있으며, API 및 Neo4j 연동은 단계적으로 추가합니다.

## 개발 환경 준비

Python 3.13, [uv](https://docs.astral.sh/uv/), Docker가 필요합니다.

레포지토리 루트에서 다음 명령어를 실행합니다.

```bash
uv sync --frozen --all-groups
```

`.venv`가 생성되고 `uv.lock`에 고정된 의존성이 설치됩니다.

크롤러를 사용하는 경우 처음 한 번 Playwright Chromium을 설치합니다.

```bash
uv run playwright install chromium
```

환경변수 파일을 생성합니다.

```bash
cp .env.example .env
```

`.env`에 필요한 값을 입력합니다.

`.env` 파일은 Git에 커밋하지 않습니다.

## Docker 실행

레포지토리 루트에서:

```bash
docker compose up -d --build
```

Docker Compose를 통해 다음 서비스를 실행합니다.

- Frontend
- Secure 테스트 앱
- Vulnerable 테스트 앱
- Neo4j

프론트엔드는 다음 주소에서 확인할 수 있습니다.

```text
http://localhost:5173
```

프론트엔드에 대한 자세한 실행 방법과 구조는 [frontend/README.md](frontend/README.md)를 참고하세요.

## 프론트엔드만 실행

프론트엔드만 로컬 개발 서버로 실행하려면:

```bash
cd frontend
npm install
npm run dev
```

## 테스트 실행

테스트는 항상 레포지토리 루트에서 실행합니다.

전체 테스트:

```bash
uv run pytest
```

특정 모듈 테스트 예시:

```bash
uv run pytest crawler/
```

각 모듈의 테스트 코드는 해당 모듈의 `tests/` 디렉토리에 둡니다.

예:

```text
crawler/tests/
analyzer/tests/
kg/tests/
common/tests/
```

## 크롤러 의존성을 제외하고 설치

Playwright가 필요하지 않은 경우:

```bash
uv sync --frozen --group dev
```

## 의존성 변경

새로운 패키지를 추가할 때는 `uv add`를 사용합니다.

예:

```bash
uv add 패키지명==버전
```

개발 의존성 그룹에 추가하는 경우:

```bash
uv add --group dev 패키지명==버전
```

의존성을 변경한 경우 다음 두 파일을 함께 커밋합니다.

```text
pyproject.toml
uv.lock
```

`uv.lock`은 직접 수정하지 않습니다.