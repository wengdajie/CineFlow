"""网盘分享追更接口：盯住一个持续更新的分享链接做增量转存。

与 ``/subscribes``（按片名去各站搜）的区别：这里的目标链接是已知的，
巡检只做「对比 → 转存新增文件 → 可选重命名」，对标 quark-auto-save。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import CurrentUser, OperatorUser
from app.schemas.models import Message, PanSubscribeCreate, PanSubscribeUpdate
from app.services import pan_subscribe as service

router = APIRouter(prefix="/pan-subscribes", tags=["分享追更"])


@router.get("", summary="分享追更列表")
def list_subscribes(user: CurrentUser) -> dict[str, Any]:
    items = service.list_all()
    return {
        "success": True,
        "total": len(items),
        "invalid": sum(1 for item in items if item.get("invalid")),
        "items": items,
    }


@router.post("", summary="新建分享追更")
def create_subscribe(payload: PanSubscribeCreate, user: OperatorUser) -> dict[str, Any]:
    record = service.create(payload.model_dump())
    return {"success": True, "data": record}


@router.patch("/{subscribe_id}", summary="更新分享追更")
def update_subscribe(
    subscribe_id: int, payload: PanSubscribeUpdate, user: OperatorUser
) -> dict[str, Any]:
    data = payload.model_dump(exclude_unset=True)
    if data.get("status") is not None:
        status_value = data["status"]
        data["status"] = getattr(status_value, "value", status_value)
    record = service.update(subscribe_id, data)
    if not record:
        raise HTTPException(status_code=404, detail="分享追更任务不存在")
    return {"success": True, "data": record}


@router.delete("/{subscribe_id}", response_model=Message, summary="删除分享追更")
def delete_subscribe(subscribe_id: int, user: OperatorUser) -> Message:
    if not service.delete(subscribe_id):
        raise HTTPException(status_code=404, detail="分享追更任务不存在")
    return Message(message="任务已删除")


@router.post("/{subscribe_id}/check", summary="立即巡检该任务")
async def check_one(subscribe_id: int, user: OperatorUser) -> dict[str, Any]:
    result = await service.check_one(subscribe_id, notify=False)
    if result.get("message") == "订阅不存在":
        raise HTTPException(status_code=404, detail="分享追更任务不存在")
    return {"success": bool(result.get("success")), **result}


@router.post("/check-all", summary="立即巡检全部任务")
async def check_all(user: OperatorUser, limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    return {"success": True, **(await service.check_all(limit=limit, notify=False))}
