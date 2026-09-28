# Test Shop — AI 웹 취약점 진단 시스템 평가용 테스트 웹앱

씨투랩 "AI 기반 웹 취약점 진단 시스템" 프로젝트의 Ground Truth 평가용 테스트 웹 애플리케이션입니다.
회원가입/로그인/상품조회/장바구니/주문/주문조회/마이페이지/관리자 기능을 갖춘 쇼핑몰이며,
Guest/User/Admin 3단계 권한에 따라 접근 가능한 화면·API가 달라집니다.

## 빠른 실행

### 로컬 실행

**기본 (포트 8000)**:
```bash
pip install -r requirements.txt
VULN_MODE=secure uvicorn app.main:app --reload --port 8000
```
브라우저에서 http://localhost:8000 접속.

**포트 변경**:
```bash
# 포트 3000으로 실행
VULN_MODE=secure uvicorn app.main:app --reload --port 3000

# 외부 접근 허용 + 포트 5000
VULN_MODE=secure uvicorn app.main:app --reload --port 5000
```

### 테스트 계정
| 역할 | 이메일 | 비밀번호 |
|---|---|---|
| Admin | admin@test.local | Admin1234! |
| User (Alice, 샘플 주문 1건 보유) | alice@test.local | Alice1234! |
| User (Bob) | bob@test.local | Bob1234! |

### Docker (secure/vulnerable 두 인스턴스 동시 실행)

**기본 (포트 8000/8001)**:
```bash
docker compose up --build
```
- http://localhost:8000 → `VULN_MODE=secure` (정답: 취약점 없음)
- http://localhost:8001 → `VULN_MODE=vulnerable` (V1~V5 취약점 활성화)

**포트 변경**:
```bash
# .env 파일 생성
cat > .env << EOF
SECURE_PORT=3000
VULN_PORT=3001
EOF

# 실행
docker compose up --build
```
- http://localhost:3000 → secure
- http://localhost:3001 → vulnerable

또는 한 줄로:
```bash
SECURE_PORT=9000 VULN_PORT=9001 docker compose up --build
```

각 모드는 **별도의 SQLite DB**를 사용하므로 서로 데이터가 섞이지 않습니다.

## VULN_MODE 동작 방식

동일한 코드베이스가 환경변수 하나로 "정답(secure)"과 "취약점 주입(vulnerable)" 두 가지 상태를
오갑니다. 코드 내 `# [VULN:Vx]` 주석으로 분기 위치를 표시해 두었습니다.

```python
# app/routers/orders.py 예시
if VULN_MODE != "vulnerable" and not is_owner_or_admin:
    return HTMLResponse("403 Forbidden", status_code=403)
```

이렇게 하면 **같은 진단 시스템을 두 모드에 각각 돌려서**:
- `vulnerable` 모드 결과로 **탐지율(Recall)** — V1~V5 중 몇 개를 찾아냈는가
- `secure` 모드 결과로 **오탐률(Precision/False Positive)** — 아무 취약점도 없는데 있다고 보고했는가

를 함께 측정할 수 있습니다.

## Ground Truth 파일 (`ground_truth/`)

| 파일 | 내용 |
|---|---|
| `pages.json` | 전체 페이지(HTML) 목록, URL/타이틀/필요 최소 권한 |
| `endpoints.json` | 폼 제출·API 엔드포인트 목록, 파라미터, 필요 권한, UI 링크 여부 |
| `flows.json` | 역할별 대표 사용자 행동 흐름 (크롤러 커버리지 평가용) |
| `vulnerabilities.json` | **의도적으로 주입된 취약점 5종의 정답지** + 오탐 유도용 decoy 3종 |

## 주입된 취약점 요약 (`vulnerabilities.json` 상세)

| ID | 이름 | 엔드포인트 | 카테고리 |
|---|---|---|---|
| V1 | 주문 상세 IDOR | `GET /orders/{id}` | Broken Access Control |
| V2 | 사용자 프로필 API IDOR | `GET /api/users/{id}` | Broken Access Control (숨은 API) |
| V3 | 회원목록 API 역할체크 누락 | `GET /admin/api/users` | Broken Function Level Access Control |
| V4 | 주문 CSV 내보내기 인증 누락 | `GET /admin/export/orders` | Missing Access Control + 숨은 엔드포인트 |
| V5 | 장바구니 가격 변조 | `POST /cart/add` | Improper Client-Side Trust |

V2, V4는 어떤 화면에도 링크되어 있지 않은 엔드포인트라, 단순 링크 크롤링으로는
'발견' 자체가 안 됩니다 — Knowledge Graph 구축 단계에서 JS 분석이나 네트워크
트레이싱, API 스펙 추론이 얼마나 이런 엔드포인트를 잡아내는지 평가하는 용도입니다.

`vulnerabilities.json`의 `decoys`는 겉보기에 취약해 보이지만 실제로는 항상 안전하게
막혀 있는 지점입니다(경로 유사성으로 인한 오탐 유도용).

## 디렉토리 구조

```
testapp/
├── app/
│   ├── main.py            # FastAPI 앱 진입점
│   ├── config.py          # VULN_MODE 등 설정
│   ├── models.py          # User/Product/CartItem/Order/OrderItem
│   ├── auth.py             # 비밀번호 해시, 세션 사용자 조회
│   ├── seed.py             # 초기 데이터
│   ├── routers/
│   │   ├── pages.py        # 홈, 상품상세
│   │   ├── auth_routes.py  # 회원가입/로그인/로그아웃
│   │   ├── cart.py         # 장바구니 (V5 포함)
│   │   ├── orders.py       # 주문/주문조회 (V1 포함)
│   │   ├── mypage.py       # 마이페이지 (V2 포함)
│   │   └── admin.py        # 관리자 기능 (V3, V4 포함)
│   ├── templates/          # Jinja2 HTML 템플릿
│   └── static/style.css
├── ground_truth/            # 정답 데이터 (JSON)
├── requirements.txt
├── Dockerfile
└── docker-compose.yml
```
