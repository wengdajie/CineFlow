"""用户与权限管理接口（v1.5.0）。

三档角色（admin/operator/viewer，见 ``UserRole``），只有 admin 能进这里。

两条**必须**的自我保护规则，否则用户会把自己锁在外面：
1. 不能删除/停用自己；
2. 不能把**最后一个管理员**降级或停用。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from app.api.deps import ROLE_LABELS, AdminUser, DbSession, role_of
from app.core.security import hash_password
from app.db.models import User
from app.schemas.enums import UserRole
from app.schemas.models import Message, UserCreate, UserUpdate

router = APIRouter(prefix="/users", tags=["用户"])


def _to_dict(user: User) -> dict[str, Any]:
    role = role_of(user)
    return {
        "id": user.id,
        "username": user.username,
        "role": role.value,
        "role_label": ROLE_LABELS[role.value],
        "rank": role.rank,
        "note": user.note,
        "is_active": bool(user.is_active),
        "is_superuser": bool(user.is_superuser),
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


def _admin_count(session: Any, *, exclude_id: int | None = None) -> int:
    """当前**启用中**的管理员数量。"""
    stmt = select(func.count(User.id)).where(
        User.role == UserRole.ADMIN.value, User.is_active.is_(True)
    )
    if exclude_id is not None:
        stmt = stmt.where(User.id != exclude_id)
    return int(session.execute(stmt).scalar() or 0)


@router.get("", summary="用户列表")
def list_users(user: AdminUser, session: DbSession) -> dict[str, Any]:
    rows = list(session.execute(select(User).order_by(User.id.asc())).scalars())
    return {
        "success": True,
        "total": len(rows),
        "roles": [
            {"value": item.value, "label": ROLE_LABELS[item.value], "rank": item.rank}
            for item in UserRole
        ],
        "items": [_to_dict(item) for item in rows],
    }


@router.post("", summary="新增用户")
def create_user(payload: UserCreate, user: AdminUser, session: DbSession) -> dict[str, Any]:
    username = payload.username.strip()
    exists = session.execute(
        select(User).where(User.username == username)
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=400, detail=f"用户名已存在：{username}")

    role = UserRole(payload.role)
    record = User(
        username=username,
        password_hash=hash_password(payload.password),
        role=role.value,
        # is_superuser 由 role 推导，保持两者永远一致（老代码与 JWT 仍在读它）
        is_superuser=role is UserRole.ADMIN,
        is_active=bool(payload.is_active),
        note=payload.note,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return {"success": True, "data": _to_dict(record)}


@router.patch("/{user_id}", summary="更新用户")
def update_user(
    user_id: int, payload: UserUpdate, user: AdminUser, session: DbSession
) -> dict[str, Any]:
    record = session.get(User, user_id)
    if not record:
        raise HTTPException(status_code=404, detail="用户不存在")

    data = payload.model_dump(exclude_unset=True)
    new_role = UserRole(data["role"]) if data.get("role") else None
    disabling = data.get("is_active") is False

    # 最后一个管理员不能被降级或停用，否则没人能再进后台
    losing_admin = role_of(record) is UserRole.ADMIN and (
        (new_role is not None and new_role is not UserRole.ADMIN) or disabling
    )
    if losing_admin and _admin_count(session, exclude_id=user_id) == 0:
        raise HTTPException(
            status_code=400, detail="这是最后一个启用中的管理员，不能降级或停用"
        )
    if record.id == user.id and disabling:
        raise HTTPException(status_code=400, detail="不能停用自己的账号")

    if data.get("password"):
        record.password_hash = hash_password(data["password"])
    if new_role is not None:
        record.role = new_role.value
        record.is_superuser = new_role is UserRole.ADMIN
    if "note" in data:
        record.note = data["note"]
    if "is_active" in data:
        record.is_active = bool(data["is_active"])
    session.commit()
    session.refresh(record)
    return {"success": True, "data": _to_dict(record)}


@router.delete("/{user_id}", response_model=Message, summary="删除用户")
def delete_user(user_id: int, user: AdminUser, session: DbSession) -> Message:
    record = session.get(User, user_id)
    if not record:
        raise HTTPException(status_code=404, detail="用户不存在")
    if record.id == user.id:
        raise HTTPException(status_code=400, detail="不能删除自己的账号")
    if role_of(record) is UserRole.ADMIN and _admin_count(session, exclude_id=user_id) == 0:
        raise HTTPException(status_code=400, detail="这是最后一个管理员，不能删除")
    session.delete(record)
    session.commit()
    return Message(message=f"用户 {record.username} 已删除")
