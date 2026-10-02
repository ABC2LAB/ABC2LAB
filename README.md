# ABC2LAB — 인가 취약점 특화 온프레미스 DAST

폐쇄망 안에서 설치해 돌리는 진단 도구입니다. 소유 관계·권한 경계를 알아야 판정되는 인가 취약점(IDOR·권한 상승)을 대상으로 합니다.

## 파이프라인

7단계로 구성합니다. 단계 이름과 순서만 정해졌고, 단계 사이 입출력 형식과 폴더 이름은 명세 작성 중입니다(미정).

1. 웹 정보 수집기
2. 의미 분석기
3. 지식 그래프 저장소
4. 접근 통제 분석기
5. 검증 요청 생성기
6. 재현·검증기
7. 평가·리포트

## 현재 구성

- Python 3.13
- Neo4j (도커 서비스만 있음, 연동 코드는 새로 개발)
- Playwright 기반 웹 크롤러

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

- Secure 테스트 앱
- Vulnerable 테스트 앱
- Neo4j

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