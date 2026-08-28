"""STRM 管理接口：概览、记录、手动同步与 302 播放跳转。

**为什么 ``/strm/play/{id}`` 必须匿名**：这个地址会被写进 .strm 文件，
由 Emby/Jellyfin/Plex 或播放器直接请求，它们带不了 JWT。
所以这里不挂认证依赖，安全性靠「ID 不可枚举猜测 + 只返回 302 跳转」兜底，
与 ADR-03（Webhook 匿名端点）同一思路。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

from app.api.deps import CurrentUser, OperatorUser
from app.schemas.models import StrmSyncRequest
from app.services import strm_sync as strm_service

router = APIRouter(prefix="/strm", tags=["STRM 同步"])


@router.get("", summary="STRM 概览")
def overview(user: CurrentUser) -> dict[str, Any]:
    """STRM 总量/失效数/占用空间/当前链接模式，供前端卡片展示。"""
    return {"success": True, "data": strm_service.stats()}


@router.get("/records", summary="STRM 记录列表")
def records(
    user: CurrentUser,
    site_id: int | None = None,
    alive_only: bool = False,
    limit: int = Query(200, ge=1, le=2000),
) -> dict[str, Any]:
    """已生成的 STRM 文件与其网盘源文件的对应关系。"""
    items = strm_service.list_records(site_id=site_id, alive_only=alive_only, limit=limit)
    return {"success": True, "total": len(items), "items": items}


@router.post("/sync", summary="手动同步 STRM")
async def sync(payload: StrmSyncRequest, user: OperatorUser) -> dict[str, Any]:
    """同步单个网盘目录或遍历全部网盘生成 STRM。"""
    if payload.site_id:
        result = await strm_service.sync_storage(
            payload.site_id,
            pan_path=payload.pan_path,
            strm_subdir=payload.strm_subdir,
            clean=payload.clean,
            link_mode=payload.link_mode,
        )
        return {"success": True, **result}
    return {"success": True, **(await strm_service.sync_all(notify=False))}


@router.get("/play/{record_id}", summary="播放跳转（匿名 302）", include_in_schema=True)
async def play(record_id: int) -> RedirectResponse:
    """把 STRM 记录换成当前有效的网盘直链并 302 跳转。

    刻意不代理流量：只回一个 Location 头，视频数据由播放器直连网盘。
    """
    url, message = await strm_service.resolve_play_url(record_id)
    if not url:
        raise HTTPException(status_code=404, detail=message)
    return RedirectResponse(url, status_code=302)
