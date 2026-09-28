from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..database import get_db
from ..auth import get_current_user
from ..models import Product

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    products = db.query(Product).all()
    return templates.TemplateResponse("index.html", {
        "request": request, "user": user, "products": products,
    })


@router.get("/products/{product_id}", response_class=HTMLResponse)
def product_detail(product_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        return HTMLResponse("<h1>404 Not Found</h1><p>상품이 존재하지 않습니다.</p>", status_code=404)
    return templates.TemplateResponse("product_detail.html", {
        "request": request, "user": user, "product": product,
    })
