"""FastAPI 엔트리포인트."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import config
from app.auth import (
    AdminRequired,
    NotAuthenticated,
    PasswordChangeRequired,
    ensure_accounts,
    load_auth_user,
    read_session_cookie,
)
from app.db.session import SessionLocal, init_db
from app.web.routes import router

app = FastAPI(title="모여봐요 발로의 숲 내전 트래커")


@app.middleware("http")
async def resolve_user(request: Request, call_next):
    """요청마다 세션 쿠키 → 사용자 해석 후 request.state.user 에 저장(템플릿/의존성 공용)."""
    user_id = read_session_cookie(request.cookies.get("session"))
    with SessionLocal() as session:
        request.state.user = load_auth_user(session, user_id)
    return await call_next(request)


@app.exception_handler(NotAuthenticated)
async def _not_authenticated(request: Request, exc: NotAuthenticated):
    return RedirectResponse(url="/login", status_code=303)


@app.exception_handler(PasswordChangeRequired)
async def _password_change_required(request: Request, exc: PasswordChangeRequired):
    return RedirectResponse(url="/account/password", status_code=303)


@app.exception_handler(AdminRequired)
async def _admin_required(request: Request, exc: AdminRequired):
    return HTMLResponse("권한이 없습니다. 어드민 전용 기능입니다.", status_code=403)


app.include_router(router)
app.mount(
    "/static",
    StaticFiles(directory=str(config.BASE_DIR / "app" / "web" / "static")),
    name="static",
)


@app.on_event("startup")
def _startup() -> None:
    # Alembic 미실행 로컬 개발 편의: 없으면 스키마 생성 (배포는 alembic upgrade).
    config.SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    config.INBOX_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    # 참가자(디코닉 보유) 계정 자동 발급 — 어드민 부트스트랩 겸용(idempotent).
    with SessionLocal() as session:
        ensure_accounts(session)
