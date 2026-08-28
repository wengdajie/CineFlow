"""过滤规则组接口（v1.5.0）：有序偏好的 CRUD 与试算。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.deps import AdminUser, CurrentUser
from app.schemas.models import (
    Message,
    RuleGroupCreate,
    RuleGroupPreviewRequest,
    RuleGroupUpdate,
)
from app.services import rule_groups as service

router = APIRouter(prefix="/rule-groups", tags=["规则组"])


@router.get("", summary="规则组列表")
def list_groups(user: CurrentUser) -> dict[str, Any]:
    items = service.list_groups()
    return {
        "success": True,
        "total": len(items),
        "default": next((item["name"] for item in items if item["is_default"]), None),
        "items": items,
    }


@router.get("/{group_id}", summary="规则组详情")
def get_group(group_id: int, user: CurrentUser) -> dict[str, Any]:
    record = service.get_group(group_id)
    if not record:
        raise HTTPException(status_code=404, detail="规则组不存在")
    return {"success": True, "data": record}


@router.post("", summary="新增规则组")
def create_group(payload: RuleGroupCreate, user: AdminUser) -> dict[str, Any]:
    data = payload.model_dump()
    try:
        return {"success": True, "data": service.create(data)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/{group_id}", summary="更新规则组")
def update_group(group_id: int, payload: RuleGroupUpdate, user: AdminUser) -> dict[str, Any]:
    data = payload.model_dump(exclude_unset=True)
    try:
        record = service.update(group_id, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not record:
        raise HTTPException(status_code=404, detail="规则组不存在")
    return {"success": True, "data": record}


@router.delete("/{group_id}", response_model=Message, summary="删除规则组")
def delete_group(group_id: int, user: AdminUser) -> Message:
    if not service.delete(group_id):
        raise HTTPException(status_code=404, detail="规则组不存在")
    return Message(message="规则组已删除（引用它的订阅已解绑）")


@router.post("/{group_id}/preview", summary="试算规则组效果")
def preview_group(
    group_id: int, payload: RuleGroupPreviewRequest, user: CurrentUser
) -> dict[str, Any]:
    result = service.preview(group_id, payload.resources)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("message") or "规则组不存在")
    return result
