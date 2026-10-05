import csv
import io

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..database import get_db
from ..auth import get_current_user
from ..models import User, Product, Order
from ..config import VULN_MODE

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="app/templates")


def _require_admin_page(request: Request, db: Session):
    """페이지(HTML) 라우트용 - 이 체크는 모드와 무관하게 항상 강제됨 (decoy)."""
    user = get_current_user(request, db)
    if not user:
        return None, RedirectResponse("/login", status_code=302)
    if user.role != "admin":
        return None, HTMLResponse("<h1>403 Forbidden</h1><p>관리자만 접근할 수 있습니다.</p>", status_code=403)
    return user, None


@router.get("", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    user, err = _require_admin_page(request, db)
    if err:
        return err
    stats = {
        "user_count": db.query(User).count(),
        "product_count": db.query(Product).count(),
        "order_count": db.query(Order).count(),
        "revenue": sum(o.total_price for o in db.query(Order).all()),
    }
    return templates.TemplateResponse("admin/dashboard.html", {"request": request, "user": user, "stats": stats})


@router.get("/users", response_class=HTMLResponse)
def admin_users_page(request: Request, db: Session = Depends(get_db)):
    user, err = _require_admin_page(request, db)
    if err:
        return err
    users = db.query(User).all()
    return templates.TemplateResponse("admin/users.html", {"request": request, "user": user, "users": users})


@router.get("/api/users")
def admin_users_api(request: Request, db: Session = Depends(get_db)):
    """admin/users.html 화면이 표를 그릴 때 호출하는 JSON API."""
    current = get_current_user(request, db)
    if not current:
        return JSONResponse({"error": "로그인이 필요합니다."}, status_code=401)

    # [VULN:V3] Broken Function Level Access Control
    # secure   : role == "admin" 인지 확인
    # vulnerable: 로그인 여부만 확인 -> 일반 user 권한으로도 전체 사용자 목록 조회 가능
    if VULN_MODE == "vulnerable":
        is_allowed = True  # 로그인만 되어 있으면 통과 (역할 체크 누락)
    else:
        is_allowed = current.role == "admin"

    if not is_allowed:
        return JSONResponse({"error": "권한이 없습니다."}, status_code=403)

    users = db.query(User).all()
    return JSONResponse([
        {"id": u.id, "email": u.email, "name": u.name, "role": u.role}
        for u in users
    ])


@router.get("/products", response_class=HTMLResponse)
def admin_products(request: Request, db: Session = Depends(get_db)):
    user, err = _require_admin_page(request, db)
    if err:
        return err
    products = db.query(Product).all()
    return templates.TemplateResponse("admin/products.html", {"request": request, "user": user, "products": products})


@router.get("/products/new", response_class=HTMLResponse)
def new_product_form(request: Request, db: Session = Depends(get_db)):
    user, err = _require_admin_page(request, db)
    if err:
        return err
    return templates.TemplateResponse("admin/product_form.html", {"request": request, "user": user, "product": None})


@router.post("/products")
def create_product(
    request: Request,
    name: str = Form(...), description: str = Form(""), price: float = Form(...),
    stock: int = Form(0), category: str = Form("general"), image_emoji: str = Form("\U0001F4E6"),
    db: Session = Depends(get_db),
):
    user, err = _require_admin_page(request, db)
    if err:
        return err
    db.add(Product(name=name, description=description, price=price, stock=stock, category=category, image_emoji=image_emoji))
    db.commit()
    return RedirectResponse("/admin/products", status_code=302)


@router.get("/products/{product_id}/edit", response_class=HTMLResponse)
def edit_product_form(product_id: int, request: Request, db: Session = Depends(get_db)):
    user, err = _require_admin_page(request, db)
    if err:
        return err
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        return HTMLResponse("<h1>404 Not Found</h1>", status_code=404)
    return templates.TemplateResponse("admin/product_form.html", {"request": request, "user": user, "product": product})


@router.post("/products/{product_id}")
def update_product(
    product_id: int, request: Request,
    name: str = Form(...), description: str = Form(""), price: float = Form(...),
    stock: int = Form(0), category: str = Form("general"), image_emoji: str = Form("\U0001F4E6"),
    db: Session = Depends(get_db),
):
    user, err = _require_admin_page(request, db)
    if err:
        return err
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        return HTMLResponse("<h1>404 Not Found</h1>", status_code=404)
    product.name, product.description, product.price = name, description, price
    product.stock, product.category, product.image_emoji = stock, category, image_emoji
    db.commit()
    return RedirectResponse("/admin/products", status_code=302)


@router.post("/products/{product_id}/delete")
def delete_product(product_id: int, request: Request, db: Session = Depends(get_db)):
    user, err = _require_admin_page(request, db)
    if err:
        return err
    product = db.query(Product).filter(Product.id == product_id).first()
    if product:
        db.delete(product)
        db.commit()
    return RedirectResponse("/admin/products", status_code=302)


@router.get("/orders", response_class=HTMLResponse)
def admin_orders(request: Request, db: Session = Depends(get_db)):
    user, err = _require_admin_page(request, db)
    if err:
        return err
    orders = db.query(Order).order_by(Order.created_at.desc()).all()
    return templates.TemplateResponse("admin/orders.html", {"request": request, "user": user, "orders": orders})


@router.get("/export/orders")
def export_orders_csv(request: Request, db: Session = Depends(get_db)):
    """어떤 화면에서도 링크로 연결되어 있지 않은 '숨겨진' 엔드포인트.
    URL을 알고 있으면 바로 호출 가능 - Forced Browsing / 크롤러의
    링크 기반 탐색만으로는 발견되지 않는 엔드포인트를 테스트하기 위함.
    (katana의 -kf all, JS 파일 스캔 등 별도 탐색 기법이 있어야 발견 가능)
    """
    current = get_current_user(request, db)

    # [VULN:V4] Missing Function Level Access Control (완전 누락)
    # secure   : admin만 접근 가능
    # vulnerable: 인증 체크 자체가 없음 - 비로그인 상태(Guest)에서도 전체 주문 데이터 CSV 다운로드 가능
    if VULN_MODE != "vulnerable":
        if not current or current.role != "admin":
            return JSONResponse({"error": "권한이 없습니다."}, status_code=403)

    orders = db.query(Order).all()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["order_id", "user_id", "user_email", "total_price", "shipping_address", "created_at"])
    for o in orders:
        writer.writerow([o.id, o.user_id, o.user.email, o.total_price, o.shipping_address, o.created_at])
    buf.seek(0)
    return StreamingResponse(buf, media_type="text/csv", headers={
        "Content-Disposition": "attachment; filename=orders_export.csv"
    })
