"""API 依赖：认证与数据库会话。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import decode_access_token
from app.core.version import API_PREFIX
from app.db.models import User
from app.db.session import get_db

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{API_PREFIX}/auth/login", auto_error=False
)

DbSession = Annotated[Session, Depends(get_db)]


def current_user(
    token: Annotated[str | None, Depends(oauth2_scheme)] = None,
    session: DbSession = None,  # type: ignore[assignment]
    x_api_token: Annotated[str | None, Header(alias="X-API-Token")] = None,
) -> User:
    """解析当前用户。

    支持两种方式：JWT（Web 前端）与 ``X-API-Token``（脚本/插件调用）。
    """
    if x_api_token and settings.API_TOKEN and x_api_token == settings.API_TOKEN:
        user = session.query(User).filter(User.is_superuser.is_(True)).first()
        if user:
            return user

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未认证",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(token)
    if not payload or not payload.get("sub"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="令牌无效或已过期"
        )

    user = session.query(User).filter(User.username == payload["sub"]).one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已禁用")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_superuser(user: CurrentUser) -> User:
    """要求管理员权限。"""
    if not user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user


SuperUser = Annotated[User, Depends(require_superuser)]
