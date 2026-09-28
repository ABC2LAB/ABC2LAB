# ABC2LAB

웹 취약점 분석 결과를 Knowledge Graph 기반으로 탐색하기 위한 프로젝트입니다.

## 현재 구성

- 백엔드 의존성: Python 3.13, FastAPI, Neo4j 드라이버
- 프론트엔드: React, Vite, JavaScript
- 이번 프론트엔드 단계는 정적 mock 데이터만 사용하며 API/Neo4j 연동을 포함하지 않습니다.

## 프론트엔드 실행

```bash
cd frontend
npm install
npm run dev
```

자세한 실행 및 구조 설명은 [frontend/README.md](frontend/README.md)를 참고하세요.

## Docker 실행

```bash
docker compose up --build
```

`http://localhost:5173`에서 화면을 확인할 수 있습니다.
