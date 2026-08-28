"""系统信息、日志、调度、通知接口。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser
from app.core.config import settings
from app.core.logger import recent_logs
from app.core.version import APP_TITLE, APP_VERSION
from app.db.models import (
    DownloadTask,
    LibraryFile,
    NotificationRecord,
    Subscribe,
)
from app.db.session import session_scope
from app.providers.metadata.tmdb import tmdb
from app.schemas.enums import SubscribeStatus, TaskStatus
from app.schemas.models import Message
from app.services import library as library_service
from app.services import notify as notify_service
from app.services.scheduler import scheduler_service

router = APIRouter(prefix="/system", tags=["系统"])


@router.get("/info", summary="系统信息")
def info(user: CurrentUser) -> dict[str, Any]:
    return {
        "success": True,
        "name": APP_TITLE,
        "version": APP_VERSION,
        "timezone": settings.TIMEZONE,
        "transfer_mode": settings.TRANSFER_MODE,
        "tmdb_enabled": tmdb.available,
        "scheduler_running": scheduler_service.running,
        "directories": {
            "data": str(settings.DATA_DIR),
            "downloads": str(settings.DOWNLOAD_DIR),
            "library": str(settings.LIBRARY_DIR),
            "strm": str(settings.STRM_DIR),
            "plugins": str(settings.PLUGIN_DIR),
        },
        "intervals": {
            "subscribe_minutes": settings.SUBSCRIBE_INTERVAL_MINUTES,
            "download_minutes": settings.DOWNLOAD_CHECK_INTERVAL_MINUTES,
            "library_cron": settings.LIBRARY_SCAN_CRON,
        },
    }


@router.get("/dashboard", summary="仪表盘统计")
def dashboard(user: CurrentUser) -> dict[str, Any]:
    with session_scope() as session:
        active = (
            session.execute(
                select(func.count(Subscribe.id)).where(
                    Subscribe.status == SubscribeStatus.ACTIVE.value
                )
            ).scalar()
            or 0
        )
        completed = (
            session.execute(
                select(func.count(Subscribe.id)).where(
                    Subscribe.status == SubscribeStatus.COMPLETED.value
                )
            ).scalar()
            or 0
        )
        downloading = (
            session.execute(
                select(func.count(DownloadTask.id)).where(
                    DownloadTask.status.in_(
                        [TaskStatus.DOWNLOADING.value, TaskStatus.PENDING.value]
                    )
                )
            ).scalar()
            or 0
        )
        finished = (
            session.execute(
                select(func.count(DownloadTask.id)).where(
                    DownloadTask.status.in_(
                        [TaskStatus.COMPLETED.value, TaskStatus.TRANSFERRED.value]
                    )
                )
            ).scalar()
            or 0
        )
        recent_files = list(
            session.execute(
                select(LibraryFile).order_by(LibraryFile.created_at.desc()).limit(12)
            ).scalars()
        )
        recent = [
            {
                "title": item.title,
                "season": item.season,
                "episode": item.episode,
                "media_type": item.media_type,
                "resolution": item.resolution,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in recent_files
        ]

    return {
        "success": True,
        "subscribes": {"active": active, "completed": completed},
        "downloads": {"running": downloading, "finished": finished},
        "library": library_service.library_stats(),
        "recent": recent,
    }


@router.get("/logs", summary="最近日志")
def logs(
    user: CurrentUser, limit: int = Query(200, le=1000), level: str | None = None
) -> dict[str, Any]:
    items = recent_logs(limit, level)
    return {"success": True, "total": len(items), "items": items}


@router.get("/jobs", summary="定时任务列表")
def jobs(user: CurrentUser) -> dict[str, Any]:
    items = scheduler_service.list_jobs()
    return {"success": True, "running": scheduler_service.running, "items": items}


@router.post("/jobs/{job_id}/run", response_model=Message, summary="立即执行任务")
async def run_job(job_id: str, user: CurrentUser) -> Message:
    if not await scheduler_service.run_job_now(job_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    return Message(message="任务已触发")


@router.get("/notifications", summary="通知记录")
def notifications(
    user: CurrentUser, limit: int = Query(100, le=1000)
) -> dict[str, Any]:
    with session_scope() as session:
        records = list(
            session.execute(
                select(NotificationRecord)
                .order_by(NotificationRecord.created_at.desc())
                .limit(limit)
            ).scalars()
        )
        items = [
            {
                "id": item.id,
                "title": item.title,
                "body": item.body,
                "level": item.level,
                "event": item.event,
                "channel": item.channel,
                "success": item.success,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in records
        ]
    return {"success": True, "total": len(items), "items": items}


@router.post("/notify/test", response_model=Message, summary="测试通知渠道")
async def test_notify(user: CurrentUser) -> Message:
    count = await notify_service.send(
        "CineFlow 测试通知", "如果你收到这条消息，说明通知渠道配置正确。"
    )
    return Message(success=count > 0, message=f"已推送 {count} 个渠道")
