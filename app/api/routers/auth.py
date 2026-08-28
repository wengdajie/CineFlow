"""认证接口。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from app.api.deps import CurrentUser, DbSession
from app.core.security import create_access_token, hash_password, verify_password
from app.db.base import utcnow
from app.db.models import User
from app.schemas.models import Message, TokenResponse

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/login", response_model=TokenResponse, summary="登录获取令牌")
def login(
    session: DbSession,
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
) -> TokenResponse:
    user = session.query(User).filter(User.username == form.username).one_or_none()
    if not user or not verify_password(form.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误"
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="用户已被禁用")

    user.last_login_at = utcnow()
    session.commit()

    return TokenResponse(
        access_token=create_access_token(
            user.username, {"is_superuser": user.is_superuser}
        ),
        username=user.username,
        is_superuser=user.is_superuser,
    )


@router.get("/me", summary="当前用户信息")
def me(user: CurrentUser) -> dict:
    return {
        "username": user.username,
        "is_superuser": user.is_superuser,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


@router.post("/password", response_model=Message, summary="修改密码")
def change_password(
    user: CurrentUser,
    session: DbSession,
    old_password: str,
    new_password: str,
) -> Message:
    if not verify_password(old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="原密码不正确")
    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="新密码至少 6 位")

    record = session.get(User, user.id)
    record.password_hash = hash_password(new_password)
    session.commit()
    return Message(message="密码已更新")
