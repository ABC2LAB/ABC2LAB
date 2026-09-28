from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .config import SECRET_KEY, VULN_MODE
from .database import Base, engine, SessionLocal
from .seed import seed
from .routers import pages, auth_routes, cart, orders, mypage, admin

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Test Shop (씨투랩 AI 웹취약점진단 - 테스트 웹앱)",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(pages.router)
app.include_router(auth_routes.router)
app.include_router(cart.router)
app.include_router(orders.router)
app.include_router(mypage.router)
app.include_router(admin.router)


@app.on_event("startup")
def on_startup():
    db = SessionLocal()
    try:
        seed(db)
    finally:
        db.close()
    print(f"[testapp] VULN_MODE = {VULN_MODE}")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
