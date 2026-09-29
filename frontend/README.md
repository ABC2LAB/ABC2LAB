# ABC2LAB Frontend

React + Vite + JavaScript로 구성된 ABC2LAB 보안 분석 대시보드입니다. 현재 단계는 UI 초기 환경과 mock 데이터만 포함하며 백엔드 API 및 Neo4j 연결은 포함하지 않습니다.

## 요구 환경

- Node.js 24 이상
- npm 11 이상
- Docker 실행 시 Docker Engine 및 Docker Compose v2

## 로컬 실행

```bash
cd frontend
npm install
npm run dev
```

브라우저에서 `http://localhost:5173`으로 접속합니다.

## 품질 검사 및 빌드

```bash
cd frontend
npm run lint
npm run build
npm run preview
```

`preview` 서버는 `http://localhost:4173`에서 실행됩니다.

## Docker 실행

저장소 루트에서 다음 명령을 실행합니다.

```bash
docker compose up --build
```

브라우저에서 `http://localhost:5173`으로 접속합니다. 종료 시 `docker compose down`을 실행합니다.

## 화면 경로

| 화면 | 경로 |
| --- | --- |
| 대시보드 | `/dashboard` |
| Knowledge Graph | `/knowledge-graph` |
| 취약점 분석 결과 | `/vulnerabilities` |
| 취약점 상세 정보 | `/vulnerabilities/:vulnerabilityId` |

예시 상세 경로는 `/vulnerabilities/VULN-2026-001`입니다.

## 디렉터리 구조

```text
src/
├── components/
│   ├── charts/          # 대시보드 차트
│   ├── common/          # 페이지 헤더, 배지, 지표 카드
│   ├── graph/           # Knowledge Graph 표현
│   ├── layout/          # 사이드바와 상단 내비게이션
│   └── vulnerability/   # 취약점 테이블
├── data/                # 화면용 mock 데이터
├── pages/               # 라우트 단위 4개 화면
├── styles/              # 전역 디자인과 반응형 스타일
└── types/               # 공통 JSDoc 데이터 타입
```

## 데이터 연동 시 유의사항

- `src/data/mockData.js`가 모든 화면 데이터를 제공합니다.
- 공통 데이터 계약은 `src/types/models.js`의 JSDoc typedef에 정의되어 있습니다.
- API 연결 단계에서는 mock 모듈을 서비스 계층으로 대체하되 화면 컴포넌트가 직접 HTTP 요청을 수행하지 않도록 구성합니다.
