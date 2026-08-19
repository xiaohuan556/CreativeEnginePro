import hmac
from datetime import timezone

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .database import get_db
from .models import LoginSession, User
from .security import token_hash, utcnow


def _aware(value):
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def current_session(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    session_token: str | None = Cookie(None, alias="cep_session"),
) -> LoginSession:
    token = request.cookies.get(settings.session_cookie) or session_token
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "请先登录")
    session = db.scalar(select(LoginSession).where(LoginSession.token_hash == token_hash(token)))
    if not session or session.revoked_at or _aware(session.expires_at) <= utcnow() or session.user.status != "active":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录已失效")
    return session


def current_user(session: LoginSession = Depends(current_session)) -> User:
    return session.user


def require_csrf(
    session: LoginSession = Depends(current_session),
    csrf_token: str | None = Header(None, alias="x-csrf-token"),
) -> User:
    if not csrf_token or not hmac.compare_digest(session.csrf_hash, token_hash(csrf_token)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "安全校验失败，请刷新页面后重试")
    return session.user


def require_admin(user: User = Depends(require_csrf)) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "只有管理员可以执行此操作")
    return user
