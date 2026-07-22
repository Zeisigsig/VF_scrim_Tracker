"""인증: 비밀번호 해싱, 서명 세션 쿠키, 계정 발급, FastAPI 의존성.

새 의존성 없이 stdlib 만으로 구현(과투자 회피):
- 비밀번호는 pbkdf2_hmac(sha256) + per-user salt
- 세션은 user_id 를 HMAC-SHA256 으로 서명한 쿠키 (서버 상태 없음)
어드민 권한은 저장하지 않고 config.admin_usernames() 로 매 요청 판정한다.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.db.models import Player, User
from app.db.session import get_session

COOKIE_NAME = "session"


# --- 비밀번호 ----------------------------------------------------------

def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, config.PBKDF2_ITERATIONS)
    return "$".join([
        "pbkdf2_sha256", str(config.PBKDF2_ITERATIONS),
        base64.b64encode(salt).decode(), base64.b64encode(dk).decode(),
    ])


def verify_password(pw: str, stored: str) -> bool:
    try:
        algo, iters, salt_b64, hash_b64 = stored.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    salt = base64.b64decode(salt_b64)
    expected = base64.b64decode(hash_b64)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, int(iters))
    return hmac.compare_digest(dk, expected)


# --- 세션 쿠키 (HMAC 서명) ---------------------------------------------

def _sign(value: str) -> str:
    sig = hmac.new(config.SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()
    return f"{value}.{sig}"


def make_session_cookie(user_id: int) -> str:
    return _sign(str(user_id))


def read_session_cookie(cookie: str | None) -> int | None:
    if not cookie or "." not in cookie:
        return None
    value, _, sig = cookie.rpartition(".")
    expected = hmac.new(config.SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected) or not value.isdigit():
        return None
    return int(value)


# --- 요청 컨텍스트 사용자 ----------------------------------------------

@dataclass
class AuthUser:
    id: int
    username: str
    player_id: int
    is_admin: bool
    must_change_password: bool


def load_auth_user(session: Session, user_id: int | None) -> AuthUser | None:
    if user_id is None:
        return None
    u = session.get(User, user_id)
    if u is None:
        return None
    return AuthUser(
        id=u.id, username=u.username, player_id=u.player_id,
        is_admin=u.username in config.admin_usernames(),
        must_change_password=u.must_change_password,
    )


# --- 의존성 / 예외 -----------------------------------------------------

class NotAuthenticated(Exception):
    """로그인 필요 → /login 리다이렉트."""


class PasswordChangeRequired(Exception):
    """첫 로그인 비번 변경 강제 → /account/password 리다이렉트."""


class AdminRequired(Exception):
    """어드민 전용 → 403."""


# 강제 비번 변경 중에도 접근 허용할 경로 접두사.
_PW_ALLOWED = ("/account/password", "/logout", "/static")


def current_user(request: Request) -> AuthUser | None:
    """미들웨어가 request.state.user 에 미리 심어둔 사용자(없으면 None)."""
    return getattr(request.state, "user", None)


def require_user(request: Request) -> AuthUser:
    user = current_user(request)
    if user is None:
        raise NotAuthenticated()
    if user.must_change_password and not request.url.path.startswith(_PW_ALLOWED):
        raise PasswordChangeRequired()
    return user


def require_admin(user: AuthUser = Depends(require_user)) -> AuthUser:
    if not user.is_admin:
        raise AdminRequired()
    return user


# --- 계정 발급 / 비번 초기화 -------------------------------------------

def eligible_players(session: Session) -> list[Player]:
    """계정 발급 대상: 디스코드 닉이 있고 이탈하지 않은 선수."""
    return list(session.scalars(
        select(Player).where(
            Player.discord_name.is_not(None), Player.departed.is_(False)
        ).order_by(Player.discord_name)
    ).all())


def ensure_accounts(session: Session) -> int:
    """대상 선수 중 계정이 없는 사람에게 기본 비번으로 계정을 발급. 생성 수 반환.

    username 은 디스코드 닉(고정). 이미 같은 username 이 있으면 건너뛴다.
    """
    existing_pids = set(session.scalars(select(User.player_id)).all())
    existing_names = set(session.scalars(select(User.username)).all())
    created = 0
    for p in eligible_players(session):
        if p.id in existing_pids or p.discord_name in existing_names:
            continue
        session.add(User(
            username=p.discord_name, player_id=p.id,
            password_hash=hash_password(config.DEFAULT_PASSWORD),
            must_change_password=True,
        ))
        existing_names.add(p.discord_name)
        created += 1
    if created:
        session.commit()
    return created


def reset_password(session: Session, user_id: int) -> bool:
    """어드민이 계정 비번을 기본값으로 초기화하고 강제 변경 플래그를 세운다."""
    u = session.get(User, user_id)
    if u is None:
        return False
    u.password_hash = hash_password(config.DEFAULT_PASSWORD)
    u.must_change_password = True
    session.commit()
    return True
