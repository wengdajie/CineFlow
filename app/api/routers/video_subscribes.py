"""网页视频订阅接口：UP 主 / 频道 / 播放列表更新自动下载。

与另两种订阅的分工：``/subscribes`` 按片名去各站搜资源，
``/pan-subscribes`` 盯死一个网盘分享链接，
本接口盯的是**一个会持续发新作的创作者页面**。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import CurrentUser, OperatorUser
from app.schemas.models import Message, VideoSubscribeCreate, VideoSubscribeUpdate
from app.services import video_subscribe as service

router = APIRouter(prefix="/video-subscribes", tags=["视频追更"])


@router.get("", summary="视频订阅列表")
def list_subscribes(user: CurrentUser) -> dict[str, Any]:
    items = service.list_all()
    return {
        "success": True,
        "total": len(items),
        "paused": sum(1 for item in items if item.get("status") == "paused"),
        "downloaded": sum(int(item.get("total_downloaded") or 0) for item in items),
        "items": items,
    }


@router.post("", summary="新建视频订阅")
def create_subscribe(payload: VideoSubscribeCreate, user: OperatorUser) -> dict[str, Any]:
    return {"success": True, "data": service.create(payload.model_dump())}


@router.post("/preview", summary="预览该地址能列出哪些投稿")
async def preview(
    user: OperatorUser,
    url: str = Query(min_length=1, description="UP 主空间页 / 频道页 / 播放列表地址"),
    limit: int = Query(10, ge=1, le=50),
) -> dict[str, Any]:
    """先看看能不能列出来，再决定要不要订阅。

    地址填错（比如贴了单个视频页而不是空间页）是最常见的失败，
    先预览一次能立刻发现，不用等定时任务跑完才知道。
    """
    entries, error = await service.list_entries(url, limit=limit)
    return {
        "success": not error,
        "message": error,
        "site": service.guess_site(url),
        "total": len(entries),
        "items": entries,
    }


@router.patch("/{subscribe_id}", summary="更新视频订阅")
def update_subscribe(
    subscribe_id: int, payload: VideoSubscribeUpdate, user: OperatorUser
) -> dict[str, Any]:
    data = payload.model_dump(exclude_unset=True)
    if data.get("status") is not None:
        status_value = data["status"]
        data["status"] = getattr(status_value, "value", status_value)
    record = service.update(subscribe_id, data)
    if not record:
        raise HTTPException(status_code=404, detail="视频订阅不存在")
    return {"success": True, "data": record}


@router.delete("/{subscribe_id}", response_model=Message, summary="删除视频订阅")
def delete_subscribe(subscribe_id: int, user: OperatorUser) -> Message:
    if not service.delete(subscribe_id):
        raise HTTPException(status_code=404, detail="视频订阅不存在")
    return Message(message="订阅已删除")


@router.post("/{subscribe_id}/check", summary="立即巡检该订阅")
async def check_one(subscribe_id: int, user: OperatorUser) -> dict[str, Any]:
    result = await service.check_one(subscribe_id, notify=False)
    if result.get("message") == "订阅不存在":
        raise HTTPException(status_code=404, detail="视频订阅不存在")
    return {"success": bool(result.get("success")), **result}


@router.post("/check-all", summary="立即巡检全部视频订阅")
async def check_all(user: OperatorUser) -> dict[str, Any]:
    return {"success": True, **(await service.check_all())}
