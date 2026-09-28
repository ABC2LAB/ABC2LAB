from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..database import get_db
from ..auth import get_current_user
from ..models import User
from ..config import VULN_MODE

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/mypage", response_class=HTMLResponse)
def mypage(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse("mypage.html", {"request": request, "user": user, "error": None})


@router.post("/mypage", response_class=HTMLResponse)
def update_mypage(
    request: Request,
    name: str = Form(...),
    address: str = Form(""),
    db: Session = Depends(get_db),
):
    # 항상 세션의 본인(user.id) 레코드만 수정 - URL/폼에 대상 id를 받지 않음
    # (그래서 이 엔드포인트 자체는 IDOR 표면이 아님 - decoy로 사용)
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    user.name = name
    user.address = address
    db.commit()
    return templates.TemplateResponse("mypage.html", {
        "request": request, "user": user, "error": None, "saved": True,
    })


@router.get("/api/users/{user_id}")
def get_user_profile_api(user_id: int, request: Request, db: Session = Depends(get_db)):
    """마이페이지 화면이 내부적으로 호출하는 프로필 조회 API.
    실제로는 /mypage 렌더링에 세션 사용자 정보를 바로 쓰기 때문에 이 API가
    필수는 아니지만, '화면에는 안 보여도 존재하는 API 엔드포인트'를
    Knowledge Graph가 얼마나 잘 찾아내는지 테스트하기 위해 의도적으로 배치함.
    """
    current = get_current_user(request, db)
    if not current:
        return JSONResponse({"error": "로그인이 필요합니다."}, status_code=401)

    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        return JSONResponse({"error": "사용자를 찾을 수 없습니다."}, status_code=404)

    # [VULN:V2] IDOR - 사용자 프로필 API
    # secure   : 본인이거나 admin일 때만 조회 허용
    # vulnerable: 로그인만 되어 있으면 다른 사용자의 프로필(이름/주소/이메일)을 조회 가능
    is_owner_or_admin = (target.id == current.id) or (current.role == "admin")
    if VULN_MODE != "vulnerable" and not is_owner_or_admin:
        return JSONResponse({"error": "권한이 없습니다."}, status_code=403)

    return JSONResponse({
        "id": target.id, "email": target.email, "name": target.name,
        "address": target.address, "role": target.role,
    })
