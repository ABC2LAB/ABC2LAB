from sqlalchemy.orm import Session

from .auth import hash_password
from .config import ADMIN_EMAIL, ADMIN_PASSWORD
from .models import User, Product, Order, OrderItem


def seed(db: Session) -> None:
    if db.query(User).count() > 0:
        return  # 이미 시딩됨

    admin = User(
        email=ADMIN_EMAIL,
        password_hash=hash_password(ADMIN_PASSWORD),
        name="관리자",
        address="서울시 강남구 테스트로 1",
        role="admin",
    )
    alice = User(
        email="alice@test.local",
        password_hash=hash_password("Alice1234!"),
        name="Alice",
        address="서울시 마포구 앨리스로 10",
        role="user",
    )
    bob = User(
        email="bob@test.local",
        password_hash=hash_password("Bob1234!"),
        name="Bob",
        address="부산시 해운대구 밥로 20",
        role="user",
    )
    db.add_all([admin, alice, bob])
    db.commit()

    products = [
        Product(name="무선 이어폰", description="노이즈 캔슬링 지원", price=89000, stock=50, category="electronics", image_emoji="\U0001F3A7"),
        Product(name="기계식 키보드", description="청축 스위치", price=125000, stock=30, category="electronics", image_emoji="\u2328\ufe0f"),
        Product(name="캔버스 백팩", description="15인치 노트북 수납", price=45000, stock=100, category="fashion", image_emoji="\U0001F392"),
        Product(name="스테인리스 텀블러", description="500ml 보온보냉", price=18000, stock=200, category="lifestyle", image_emoji="\U0001F964"),
        Product(name="LED 데스크 램프", description="3단계 밝기 조절", price=32000, stock=80, category="lifestyle", image_emoji="\U0001F4A1"),
        Product(name="블루투스 스피커", description="휴대용 방수 스피커", price=56000, stock=60, category="electronics", image_emoji="\U0001F50A"),
    ]
    db.add_all(products)
    db.commit()

    # Alice 명의로 샘플 주문 하나 생성 (order 접근통제 테스트용 데이터)
    order = Order(user_id=alice.id, status="paid", total_price=89000 + 18000, shipping_address=alice.address)
    db.add(order)
    db.commit()
    db.add_all([
        OrderItem(order_id=order.id, product_id=products[0].id, product_name=products[0].name, quantity=1, price=products[0].price),
        OrderItem(order_id=order.id, product_id=products[3].id, product_name=products[3].name, quantity=1, price=products[3].price),
    ])
    db.commit()
