"""追新雷达接口：手动触发、预览、查看最新流。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, OperatorUser
from app.services import radar as radar_service
from app.services.scheduler import scheduler_service

router = APIRouter(prefix="/radar", tags=["追新雷达"])


@router.post("/run", summary="手动触发一轮追新雷达")
async def run_radar(
    user: OperatorUser,
    dry_run: bool = Query(False, description="仅预览匹配结果，不发起下载"),
) -> dict[str, Any]:
    """立即执行一轮追新雷达。"""
    result = await radar_service.run(dry_run=dry_run)
    return {"success": True, "data": result}


@router.get("/feed", summary="最新资源流预览")
async def feed_preview(
    user: OperatorUser,
    limit_per_site: int = Query(20, ge=1, le=200),
) -> dict[str, Any]:
    """拉取各站点最新资源列表（不订阅匹配，仅预览）。"""
    feed = await radar_service.fetch_feed(limit_per_site=limit_per_site)
    return {
        "success": True,
        "data": {
            "total": len(feed),
            "items": [item.to_dict() for item in feed],
        },
    }


@router.get("/jobs", summary="雷达调度任务状态")
def radar_jobs(user: CurrentUser) -> dict[str, Any]:
    """查看雷达调度任务信息。"""
    jobs = scheduler_service.list_jobs()
    radar = [j for j in jobs if "radar" in j["id"].lower()]
    return {"success": True, "data": {"jobs": radar, "radar_enabled": bool(radar)}}
