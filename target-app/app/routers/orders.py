from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..database import get_db
from ..auth import get_current_user
from ..models import CartItem, Order, OrderItem
from ..config import VULN_MODE

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/checkout", response_class=HTMLResponse)
def checkout(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    items = db.query(CartItem).filter(CartItem.user_id == user.id).all()
    if not items:
        return RedirectResponse("/cart", status_code=302)

    total = sum(i.price_at_add * i.quantity for i in items)
    return templates.TemplateResponse("checkout.html", {
        "request": request, "user": user, "items": items, "total": total,
    })


@router.post("/orders")
def place_order(
    request: Request,
    shipping_address: str = Form(...),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    items = db.query(CartItem).filter(CartItem.user_id == user.id).all()
    if not items:
        return RedirectResponse("/cart", status_code=302)

    total = sum(i.price_at_add * i.quantity for i in items)
    order = Order(user_id=user.id, total_price=total, shipping_address=shipping_address)
    db.add(order)
    db.commit()
    db.refresh(order)

    for i in items:
        db.add(OrderItem(
            order_id=order.id, product_id=i.product_id,
            product_name=i.product.name, quantity=i.quantity, price=i.price_at_add,
        ))
        db.delete(i)
    db.commit()

    return RedirectResponse(f"/orders/{order.id}", status_code=302)


@router.get("/orders", response_class=HTMLResponse)
def order_list(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    orders = db.query(Order).filter(Order.user_id == user.id).order_by(Order.created_at.desc()).all()
    return templates.TemplateResponse("orders_list.html", {
        "request": request, "user": user, "orders": orders,
    })


@router.get("/orders/{order_id}", response_class=HTMLResponse)
def order_detail(order_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        return HTMLResponse("<h1>404 Not Found</h1>", status_code=404)

    # [VULN:V1] IDOR - 주문 상세 조회
    # secure   : 본인 주문이거나 admin일 때만 조회 허용
    # vulnerable: 로그인만 되어 있으면 소유권과 무관하게 아무 주문이나 조회 가능
    is_owner_or_admin = (order.user_id == user.id) or (user.role == "admin")
    if VULN_MODE != "vulnerable" and not is_owner_or_admin:
        return HTMLResponse("<h1>403 Forbidden</h1><p>본인 주문만 조회할 수 있습니다.</p>", status_code=403)

    return templates.TemplateResponse("order_detail.html", {
        "request": request, "user": user, "order": order,
    })
