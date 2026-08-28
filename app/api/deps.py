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
from app.schemas.enums import UserRole

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


#: 角色显示名（403 提示里用，避免用户看到英文标识一头雾水）
ROLE_LABELS = {
    UserRole.ADMIN.value: "管理员",
    UserRole.OPERATOR.value: "操作员",
    UserRole.VIEWER.value: "访客",
}


def role_of(user: User) -> UserRole:
    """取用户角色，非法/缺失时按 ``is_superuser`` 兜底。

    老库补列默认是 ``admin``，但仍可能出现历史脏值；这里做最后一道保险，
    保证鉴权永远有确定结果（既不放行也不 500）。
    """
    try:
        return UserRole(user.role)
    except (ValueError, TypeError):
        return UserRole.ADMIN if user.is_superuser else UserRole.VIEWER


def require_role(minimum: UserRole):
    """生成"至少需要某个角色"的依赖。

    只比较 ``UserRole.rank``（viewer 1 < operator 2 < admin 3），
    不做细粒度 ACL —— 家用场景真实需求就三档（ADR-19）。
    """

    def dependency(user: CurrentUser) -> User:
        if role_of(user).rank < minimum.rank:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"需要{ROLE_LABELS[minimum.value]}及以上权限"
                f"（当前身份：{ROLE_LABELS[role_of(user).value]}）",
            )
        return user

    return dependency


#: 可执行「搜索/订阅/下载/整理」等写操作，但改不了系统配置与用户
OperatorUser = Annotated[User, Depends(require_role(UserRole.OPERATOR))]
#: 可改系统配置、站点、用户
AdminUser = Annotated[User, Depends(require_role(UserRole.ADMIN))]
