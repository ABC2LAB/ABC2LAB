# Test Shop — 웹 기능 및 API 완전 명세서

## 목차

1. [아키텍처](#아키텍처)
2. [인증 & 세션](#인증--세션)
3. [전체 페이지 목록](#전체-페이지-목록)
4. [API 엔드포인트 상세](#api-엔드포인트-상세)
5. [데이터 모델](#데이터-모델)
6. [주의사항 및 특수 기능](#주의사항-및-특수-기능)

---

## 아키텍처

### 기술 스택
- **백엔드**: FastAPI 0.115.0 (Python)
- **ORM**: SQLAlchemy 2.0.35
- **데이터베이스**: SQLite (개발용)
- **템플릿**: Jinja2
- **세션**: `starlette.middleware.sessions.SessionMiddleware`
- **배포**: Uvicorn (ASGI)

### 폴더 구조
```
app/
├── main.py           # FastAPI 진입점, 미들웨어 설정
├── config.py         # 환경변수, VULN_MODE 설정
├── database.py       # DB 엔진, SessionLocal
├── models.py         # SQLAlchemy ORM 모델
├── auth.py           # 비밀번호 해시, 세션 조회
├── seed.py           # 초기 데이터 삽입
├── routers/          # 라우트 모듈
│   ├── pages.py      # 홈, 상품 상세
│   ├── auth_routes.py# 회원가입, 로그인, 로그아웃
│   ├── cart.py       # 장바구니 관리
│   ├── orders.py     # 주문 생성, 주문 조회
│   ├── mypage.py     # 마이페이지, 프로필 API
│   └── admin.py      # 관리자 기능
├── templates/        # HTML 템플릿
└── static/           # CSS 등 정적 파일
```

---

## 인증 & 세션

### 세션 방식

**세션 스토어**: 클라이언트 쿠키 (서명됨)
```python
# main.py
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
```

**세션에 저장되는 정보**:
```python
request.session["user_id"]  # User.id (int)
request.session["role"]     # "user" | "admin" (str)
```

### 비밀번호 해싱

방식: PBKDF2-SHA256 (iterataions=260,000)
```python
# auth.py
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 260_000)
    return f"{salt.hex()}${dk.hex()}"
```

### 사용자 조회

```python
# auth.py - 모든 보호된 엔드포인트에서 공통 사용
def get_current_user(request: Request, db: Session) -> Optional[User]:
    uid = request.session.get("user_id")
    if not uid:
        return None
    return db.query(User).filter(User.id == uid).first()
```

**반환값**: 
- 로그인 상태 → `User` 객체 (id, email, name, role 등)
- 비로그인 상태 → `None` (Guest)

### 테스트 계정 (자동 생성)

| 이메일 | 비밀번호 | 역할 | 추가정보 |
|---|---|---|---|
| admin@test.local | Admin1234! | admin | 주소: 서울시 강남구 테스트로 1 |
| alice@test.local | Alice1234! | user | 주소: 서울시 마포구 앨리스로 10, 주문#1 보유 |
| bob@test.local | Bob1234! | user | 주소: 부산시 해운대구 밥로 20 |

이 계정들은 `seed.py`에서 앱 시작 시 자동으로 DB에 삽입됩니다.

---

## 전체 페이지 목록

### 1. 공개 페이지 (Guest 접근 가능)

#### `GET /` — 홈 / 상품 목록
- **템플릿**: `templates/index.html`
- **출력**: 전체 상품 카드 그리드
- **필요 권한**: 없음 (Guest OK)
- **QueryString 파라미터**: 없음
- **특이사항**: 모든 상품을 표시 (검색/필터링 없음)

#### `GET /products/{product_id}` — 상품 상세
- **템플릿**: `templates/product_detail.html`
- **경로 파라미터**: `product_id` (정수, Product.id)
- **출력**: 상품명, 설명, 가격, 재고, 이모지
- **필요 권한**: 없음
- **액션**:
  - 상품 상세 페이지에 "장바구니 담기" 버튼 있음 (비로그인 시 비활성화)
  - 클릭 시 `POST /cart/add` 트리거

#### `GET /signup` — 회원가입 폼
- **템플릿**: `templates/signup.html`
- **출력**: 이메일, 비밀번호, 이름, 주소 입력 필드
- **필요 권한**: 없음 (로그인 상태면 자동 리다이렉트 → `/`)

#### `GET /login` — 로그인 폼
- **템플릿**: `templates/login.html`
- **출력**: 이메일, 비밀번호 입력 필드 + 테스트 계정 안내
- **필요 권한**: 없음 (로그인 상태면 자동 리다이렉트 → `/`)

---

### 2. 로그인 필수 페이지 (User 이상)

#### `GET /cart` — 장바구니 조회
- **템플릿**: `templates/cart.html`
- **출력**: CartItem 목록 (상품명, 단가, 수량, 소계) + 합계
- **필요 권한**: user 이상
- **액션**:
  - 수량 변경 → `POST /cart/update`
  - 삭제 → `POST /cart/remove`
  - 주문 진행 → 상세는 [Checkout](#get-checkout--주문서-작성) 참조

#### `GET /checkout` — 주문서 작성
- **템플릿**: `templates/checkout.html`
- **출력**: 장바구니 아이템 재확인 + 배송지 입력 폼
- **필요 권한**: user 이상
- **필수 데이터**: 세션의 현재 사용자가 최소 1개 CartItem 소유
- **액션**:
  - "결제하기" 버튼 → `POST /orders`

#### `GET /orders` — 주문 목록
- **템플릿**: `templates/orders_list.html`
- **출력**: 현재 사용자의 Order 목록 (주문번호, 날짜, 상태, 금액)
- **필요 권한**: user 이상
- **필터**: 자동으로 현재 사용자(`user_id == session["user_id"]`)의 주문만 표시
- **정렬**: created_at 내림차순 (최신부터)

#### `GET /orders/{order_id}` — 주문 상세
- **템플릿**: `templates/order_detail.html`
- **경로 파라미터**: `order_id` (정수, Order.id)
- **출력**: 주문자, 주문일시, 배송지, OrderItem 목록, 합계
- **필요 권한**: user 이상
- **접근통제**:
  - **Secure 모드**: `order.user_id == session["user_id"]` 또는 `role == "admin"` → 200, 아니면 403
  - **Vulnerable 모드**: 로그인만 되어 있으면 200 (**V1 IDOR 취약점**)

#### `GET /mypage` — 마이페이지
- **템플릿**: `templates/mypage.html`
- **출력**: 이메일(읽기 전용), 이름, 주소 입력 폼
- **필요 권한**: user 이상
- **특이사항**: 항상 현재 세션 사용자 정보만 수정 (URL/파라미터로 대상을 받지 않음)

---

### 3. 관리자 전용 페이지 (Admin 필수)

#### `GET /admin` — 관리자 대시보드
- **템플릿**: `templates/admin/dashboard.html`
- **출력**: 회원수, 상품수, 주문수, 총 매출 통계 + 관리 섹션 링크
- **필요 권한**: admin
- **접근통제**: 항상 강제 (모든 모드에서)

#### `GET /admin/users` — 회원 관리
- **템플릿**: `templates/admin/users.html`
- **출력**: 사용자 목록 테이블 (id, 이메일, 이름, 역할)
- **필요 권한**: admin
- **주의**: 페이지는 서버렌더링이지만, JS에서 `GET /admin/api/users` 를 fetch로 호출함 (V3 접근통제 테스트용)

#### `GET /admin/products` — 상품 관리 목록
- **템플릿**: `templates/admin/products.html`
- **출력**: 상품 목록 테이블 + "상품 추가" 버튼
- **필요 권한**: admin
- **액션**: 각 상품 행에 "수정", "삭제" 버튼

#### `GET /admin/products/new` — 상품 등록 폼
- **템플릿**: `templates/admin/product_form.html`
- **출력**: 상품명, 설명, 가격, 재고, 카테고리, 이모지 입력 필드
- **필요 권한**: admin

#### `GET /admin/products/{product_id}/edit` — 상품 수정 폼
- **템플릿**: `templates/admin/product_form.html`
- **경로 파라미터**: `product_id` (정수)
- **출력**: 위와 동일하되, 기존 값이 pre-fill됨
- **필요 권한**: admin

#### `GET /admin/orders` — 주문 관리
- **템플릿**: `templates/admin/orders.html`
- **출력**: 전체 주문 목록 테이블 (주문번호, 주문자 이메일, 날짜, 상태, 금액)
- **필요 권한**: admin
- **특이사항**: User의 `/orders`와 달리, 전체 사용자의 주문을 표시

---

## API 엔드포인트 상세

### 공개 API (모두)

#### `POST /signup` — 회원가입
**요청**:
```
Method: POST
Content-Type: application/x-www-form-urlencoded
Body:
  email: string (required, email format)
  password: string (required)
  name: string (required)
  address: string (optional, default: "")
```

**응답**:
- **성공 (200)**: 302 리다이렉트 → `/login`
  - 새 사용자가 DB에 삽입됨 (role="user")
- **실패 (400)**: 같은 이메일이 이미 존재 → 폼에 에러 메시지 표시

**비밀번호 정책**: 특수 문자/대소문자/숫자 체크 없음 (테스트용이므로 간단함)

#### `POST /login` — 로그인
**요청**:
```
Method: POST
Content-Type: application/x-www-form-urlencoded
Body:
  email: string (required)
  password: string (required)
```

**응답**:
- **성공 (200)**: 302 리다이렉트 → `/`
  - `session["user_id"]`와 `session["role"]` 저장
  - 응답 `Set-Cookie` 헤더에 세션 쿠키 포함
- **실패 (401)**: 이메일 없음 또는 비밀번호 불일치 → 폼에 에러 메시지

**세션 쿠키**:
```
Set-Cookie: session=<서명된 JSON>; Path=/; HttpOnly
```

#### `POST /logout` — 로그아웃
**요청**:
```
Method: POST
(세션 쿠키 자동 포함)
```

**응답**:
- **항상 성공 (200)**: 302 리다이렉트 → `/`
  - `request.session.clear()` 실행 (쿠키 무효화)

---

### 상품 조회 API

#### `GET /` — 홈 (상품 목록)
(페이지 설명 참고, API가 아님)

#### `GET /products/{product_id}` — 상품 상세
(페이지 설명 참고, API가 아님)

---

### 장바구니 API (User 필수)

#### `POST /cart/add` — 장바구니에 담기
**요청**:
```
Method: POST
Content-Type: application/x-www-form-urlencoded
Headers:
  Cookie: session=<세션 쿠키>
Body:
  product_id: int (required, Product.id)
  quantity: int (required, default: 1, min: 1)
  price: float (optional, **V5 취약점 대상**)
```

**응답**:
```json
{ "ok": true }
```

**동작**:
1. `get_current_user()`로 현재 사용자 조회
2. 비로그인이면 401
3. product_id 유효성 확인
4. 이미 장바구니에 있는 상품이면 quantity 추가, 없으면 신규 생성
5. 가격 결정:
   - **Secure 모드**: 항상 DB의 product.price 사용 (price 파라미터 무시)
   - **Vulnerable 모드**: price 파라미터가 있으면 그 값 사용 (**V5**)

#### `POST /cart/update` — 수량 변경
**요청**:
```
Method: POST
Content-Type: application/x-www-form-urlencoded
Headers:
  Cookie: session=<세션 쿠키>
Body:
  item_id: int (required, CartItem.id)
  quantity: int (required, min: 1)
```

**응답**:
```json
{ "ok": true }
```

**접근통제** (항상 강제, decoy):
- 요청된 CartItem.user_id != 현재 사용자 id → 403
- 모드와 무관하게 항상 차단

#### `POST /cart/remove` — 장바구니에서 삭제
**요청**:
```
Method: POST
Content-Type: application/x-www-form-urlencoded
Headers:
  Cookie: session=<세션 쿠키>
Body:
  item_id: int (required, CartItem.id)
```

**응답**:
```json
{ "ok": true }
```

**접근통제** (항상 강제, decoy):
- 요청된 CartItem.user_id != 현재 사용자 id → 403

---

### 주문 API (User 필수)

#### `POST /orders` — 주문 생성
**요청**:
```
Method: POST
Content-Type: application/x-www-form-urlencoded
Headers:
  Cookie: session=<세션 쿠키>
Body:
  shipping_address: string (required)
```

**응답**:
- 302 리다이렉트 → `/orders/{new_order_id}`

**동작**:
1. 현재 사용자의 CartItem 모두 조회
2. 각 item의 `price_at_add * quantity` 합산 → Order.total_price
3. Order 생성 (status="paid")
4. 각 CartItem을 OrderItem으로 변환 (상품명/가격 스냅샷 저장)
5. CartItem 전부 삭제
6. 성공하면 리다이렉트

#### `GET /orders` — 주문 목록
(페이지 설명 참고)

#### `GET /orders/{order_id}` — 주문 상세
(페이지 설명 참고, **V1 IDOR 취약점**)

---

### 마이페이지 API (User 필수)

#### `POST /mypage` — 회원정보 수정
**요청**:
```
Method: POST
Content-Type: application/x-www-form-urlencoded
Headers:
  Cookie: session=<세션 쿠키>
Body:
  name: string (required)
  address: string (optional)
```

**응답**: 302 리다이렉트 → `/mypage` (저장 후 재로드)

**특이사항**: 항상 현재 세션 사용자 정보만 수정 (decoy)

#### `GET /api/users/{user_id}` — 사용자 프로필 조회 (API)
**요청**:
```
Method: GET
Headers:
  Cookie: session=<세션 쿠키>
URL Parameters:
  user_id: int (path parameter, User.id)
```

**응답** (성공):
```json
{
  "id": 2,
  "email": "alice@test.local",
  "name": "Alice",
  "address": "서울시 마포구 앨리스로 10",
  "role": "user"
}
```

**접근통제** (**V2 IDOR 취약점**):
- **Secure 모드**:
  - `user_id == 현재 사용자 id` 또는 `role == "admin"` → 200
  - 그 외 → 403
- **Vulnerable 모드**: 로그인만 되어 있으면 200 (역할 체크 없음)

**발견 난이도**: 이 엔드포인트는 어떤 HTML에도 링크되어 있지 않음
- 마이페이지 로드 시 fetch로 호출됨
- 네트워크 트레이싱(Playwright의 `requestfinished` 리스너)으로만 발견 가능
- 또는 강제 접근: `/api/users/1`, `/api/users/2`, ... 순회

---

### 관리자 API (Admin 필수)

#### `GET /admin/api/users` — 회원 목록 (JSON API)
**요청**:
```
Method: GET
Headers:
  Cookie: session=<세션 쿠키>
```

**응답** (성공):
```json
[
  { "id": 1, "email": "admin@test.local", "name": "관리자", "role": "admin" },
  { "id": 2, "email": "alice@test.local", "name": "Alice", "role": "user" },
  { "id": 3, "email": "bob@test.local", "name": "Bob", "role": "user" }
]
```

**접근통제** (**V3: Function Level Access Control**):
- **Secure 모드**: `role == "admin"` → 200, 그 외 → 403
- **Vulnerable 모드**: 로그인만 되어 있으면 200 (역할 체크 빠짐, user도 접근 가능)

**발견**: `/admin/users` HTML 페이지의 `<script>` 태그에서 fetch 호출
```html
<script>
fetch('/admin/api/users').then(r => r.json()).then(console.log);
</script>
```

#### `POST /admin/products` — 상품 신규 등록
**요청**:
```
Method: POST
Content-Type: application/x-www-form-urlencoded
Headers:
  Cookie: session=<세션 쿠키>
Body:
  name: string (required)
  description: string (optional)
  price: float (required)
  stock: int (optional, default: 0)
  category: string (optional, default: "general")
  image_emoji: string (optional, default: "📦")
```

**응답**: 302 리다이렉트 → `/admin/products`

#### `POST /admin/products/{product_id}` — 상품 수정
**요청**:
```
Method: POST
Content-Type: application/x-www-form-urlencoded
Headers:
  Cookie: session=<세션 쿠키>
URL Path:
  product_id: int
Body:
  name, description, price, stock, category, image_emoji
  (형식은 신규 등록과 동일)
```

**응답**: 302 리다이렉트 → `/admin/products`

#### `POST /admin/products/{product_id}/delete` — 상품 삭제
**요청**:
```
Method: POST
Headers:
  Cookie: session=<세션 쿠키>
URL Path:
  product_id: int
```

**응답**: 302 리다이렉트 → `/admin/products`

#### `GET /admin/export/orders` — 주문 CSV 내보내기
**요청**:
```
Method: GET
Headers:
  Cookie: session=<세션 쿠키> (optional)
```

**응답** (성공):
```
Content-Type: text/csv
Content-Disposition: attachment; filename=orders_export.csv

order_id,user_id,user_email,total_price,shipping_address,created_at
1,2,alice@test.local,107000.0,서울시 마포구 앨리스로 10,2026-09-23 07:33:22.713673
```

**접근통제** (**V4: Missing Function Level Access Control + Hidden Endpoint**):
- **Secure 모드**: `role == "admin"` → 200, 그 외 → 403
- **Vulnerable 모드**: 인증 체크 자체가 없음 → 비로그인 상태에서도 200 (**V4**)

**발견 난이도** (매우 높음):
- 어떤 HTML 페이지 메뉴에도 링크 없음
- `/admin/orders` 페이지도 "내보내기" 버튼이 없음
- 강제 접근 (`/admin/export/orders`)하거나, 소스코드 읽음, 또는 API 스펙 추론 필요

---

## 데이터 모델

### User 테이블
```python
class User(Base):
    __tablename__ = "users"
    
    id: int (Primary Key)
    email: str (Unique)
    password_hash: str
    name: str
    address: str (default: "")
    role: str (default: "user", values: "user" | "admin")
    created_at: datetime (auto)
```

### Product 테이블
```python
class Product(Base):
    __tablename__ = "products"
    
    id: int (Primary Key)
    name: str
    description: str (default: "")
    price: float
    stock: int (default: 0)
    category: str (default: "general")
    image_emoji: str (default: "📦", 이모지 표시용)
```

### CartItem 테이블
```python
class CartItem(Base):
    __tablename__ = "cart_items"
    
    id: int (Primary Key)
    user_id: int (Foreign Key → User.id)
    product_id: int (Foreign Key → Product.id)
    quantity: int (default: 1)
    price_at_add: float (담을 당시 가격, V5 취약점 관련)
```

### Order 테이블
```python
class Order(Base):
    __tablename__ = "orders"
    
    id: int (Primary Key)
    user_id: int (Foreign Key → User.id)
    status: str (default: "paid", values: "paid" | "shipped" | "delivered")
    total_price: float (주문 시점의 총액 스냅샷)
    shipping_address: str
    created_at: datetime (auto)
```

### OrderItem 테이블
```python
class OrderItem(Base):
    __tablename__ = "order_items"
    
    id: int (Primary Key)
    order_id: int (Foreign Key → Order.id)
    product_id: int (Foreign Key → Product.id)
    product_name: str (주문 시점의 상품명 스냅샷)
    quantity: int (default: 1)
    price: float (주문 시점의 가격 스냅샷)
```

---

## 주의사항 및 특수 기능

### 1. VULN_MODE에 의한 동작 분기

코드 내 `# [VULN:Vx]` 주석이 있는 곳에서 분기:

```python
from app.config import VULN_MODE

if VULN_MODE == "vulnerable":
    # 취약점 활성화 로직
else:
    # 안전한 로직
```

두 모드는 **같은 코드베이스**이지만 환경변수 하나로 다르게 동작합니다.

### 2. 초기 데이터 시딩

앱 시작 시 `seed.py`가 자동으로 실행되어:
- 3명의 사용자 (admin, alice, bob)
- 6개의 상품
- alice 명의 샘플 주문 1건

을 생성합니다. DB 파일이 없거나 테이블이 비어 있으면 자동 삽입, 이미 있으면 스킵합니다.

### 3. 트랜잭션 & 자동 커밋

SQLAlchemy의 `sessionmaker(autocommit=False, autoflush=False)` 설정으로:
- 명시적 `db.commit()` 필요
- `db.add()` 후 자동 flush 아님

모든 라우트에서 변경 후 `db.commit()`을 호출합니다.

### 4. 에러 처리

HTTP 응답 코드:
- **200**: 성공
- **302**: 리다이렉트 (로그인, 라우팅 등)
- **400**: 입력 검증 실패 (중복 이메일 등)
- **401**: 인증 필요 (로그인 안 됨)
- **403**: 권한 없음 (다른 사용자 자원 접근 등)
- **404**: 자원 없음 (상품 id 잘못됨 등)

### 5. 정적 파일 (CSS)

`/static/style.css`는 간단한 CSS로, 큰 기능은 없지만:
- 반응형 그리드 (상품 목록)
- 테이블 스타일
- 폼 스타일
- 내비게이션 바

정도를 제공합니다.

### 6. 템플릿 상속

모든 HTML은 `base.html`을 상속하며, 네비게이션 바와 인증 상태를 통일합니다:
```html
{% if user %}
  <!-- 로그인 상태: 이름, 역할, 로그아웃 버튼 -->
{% else %}
  <!-- 비로그인: 로그인, 회원가입 링크 -->
{% endif %}
```

### 7. QueryString & Path Parameters

- **Path Parameters** (URL 일부): `/products/{product_id}`, `/orders/{order_id}`
- **QueryString** (없음): 이 앱에서는 검색/필터링이 없어서 QueryString 미사용
- **Form Parameters** (POST 바디): 모든 폼 제출

### 8. JSON API vs 페이지 라우트

| 경로 | 반환 타입 | 용도 |
|---|---|---|
| `/` | HTML | 상품 목록 페이지 |
| `/api/users/{id}` | JSON | 마이페이지의 fetch 호출용 |
| `/admin/api/users` | JSON | 관리자 회원 관리의 fetch 호출용 |
| `/admin/export/orders` | CSV (텍스트) | 다운로드 |

### 9. 데이터베이스 격리 (Docker)

`docker-compose.yml`에서:
- `testapp-secure`: `/data/secure.db` 사용
- `testapp-vulnerable`: `/data/vulnerable.db` 사용

두 모드는 **별도의 데이터베이스**를 가져서 서로 영향 없음.

### 10. 보안상 주의

이 앱은 **테스트용**이므로:
- HTTPS 없음 (localhost 개발 전용)
- CSRF 토큰 없음 (간단함을 위해)
- SQL 인젝션 방지는 SQLAlchemy ORM으로 자동
- Rate limiting 없음
- CORS 미설정 (API가 매우 간단하고 내부 테스트용)

실운영 환경에서는 절대 이 설정을 따르지 마세요.
