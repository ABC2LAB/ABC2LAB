from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import Optional

from ..database import get_db
from ..auth import get_current_user
from ..models import CartItem, Product
from ..config import VULN_MODE

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/cart", response_class=HTMLResponse)
def view_cart(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    items = db.query(CartItem).filter(CartItem.user_id == user.id).all()
    total = sum(i.price_at_add * i.quantity for i in items)
    return templates.TemplateResponse("cart.html", {
        "request": request, "user": user, "items": items, "total": total,
    })


@router.post("/cart/add")
def add_to_cart(
    request: Request,
    product_id: int = Form(...),
    quantity: int = Form(1),
    price: Optional[float] = Form(None),  # 클라이언트가 보낼 수 있는(보내면 안 되는) 필드
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "로그인이 필요합니다."}, status_code=401)

    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        return JSONResponse({"error": "상품을 찾을 수 없습니다."}, status_code=404)

    # [VULN:V5] 가격 변조 (Business Logic / Improper Client-Side Trust)
    # secure   : 항상 서버(DB)의 상품 가격을 사용 - 클라이언트가 보낸 price 무시
    # vulnerable: 클라이언트가 price를 보내면 그 값을 그대로 신뢰해 장바구니에 저장
    if VULN_MODE == "vulnerable" and price is not None:
        effective_price = price
    else:
        effective_price = product.price

    existing = db.query(CartItem).filter(
        CartItem.user_id == user.id, CartItem.product_id == product_id
    ).first()
    if existing:
        existing.quantity += quantity
        existing.price_at_add = effective_price
    else:
        db.add(CartItem(
            user_id=user.id, product_id=product_id,
            quantity=quantity, price_at_add=effective_price,
        ))
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/cart/update")
def update_cart(
    request: Request,
    item_id: int = Form(...),
    quantity: int = Form(...),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "로그인이 필요합니다."}, status_code=401)

    item = db.query(CartItem).filter(CartItem.id == item_id).first()
    if not item or item.user_id != user.id:
        # 소유권 체크는 모드와 무관하게 항상 강제 (오탐 측정용 decoy)
        return JSONResponse({"error": "권한이 없습니다."}, status_code=403)

    item.quantity = max(1, quantity)
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/cart/remove")
def remove_from_cart(
    request: Request,
    item_id: int = Form(...),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "로그인이 필요합니다."}, status_code=401)

    item = db.query(CartItem).filter(CartItem.id == item_id).first()
    if not item or item.user_id != user.id:
        return JSONResponse({"error": "권한이 없습니다."}, status_code=403)

    db.delete(item)
    db.commit()
    return JSONResponse({"ok": True})
