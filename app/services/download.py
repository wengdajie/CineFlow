"""下载服务：把候选资源投递到下载器，并跟踪进度。

网盘类资源不进入 BT 下载器：若配置了 aria2 且拿到直链则交给 aria2，
否则登记为待人工/插件处理的任务（保留分享链接与提取码）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.core.logger import get_logger
from app.core.meta import parse
from app.db.base import utcnow
from app.db.models import DownloadTask
from app.db.session import session_scope
from app.schemas.enums import EventType, NotifyLevel, ResourceKind, TaskStatus
from app.services import notify as notify_service
from app.services import sites as site_service
from app.utils.strings import format_size, truncate

logger = get_logger(__name__)


def _resolve_save_path(
    media_type: str, title: str, season: int | None, custom: str | None
) -> str:
    """计算下载保存目录。"""
    if custom:
        return str(custom)
    root = Path(settings.DOWNLOAD_DIR)
    from app.schemas.enums import MediaType

    if media_type in (MediaType.TV.value, MediaType.ANIME.value):
        folder = root / "tv"
    elif media_type == MediaType.MOVIE.value:
        folder = root / "movies"
    else:
        folder = root / "others"
    return str(folder)


async def add_download(
    resource: dict[str, Any],
    *,
    subscribe_id: int | None = None,
    downloader_name: str | None = None,
    save_path: str | None = None,
    notify: bool = True,
) -> DownloadTask | None:
    """添加下载任务。"""
    title = str(resource.get("title") or "")
    link = str(resource.get("link") or "")
    if not link:
        logger.warning("资源缺少下载链接：%s", title)
        return None

    meta = resource.get("meta") or parse(title).to_dict()
    kind = str(resource.get("kind") or ResourceKind.TORRENT.value)
    media_type = meta.get("media_type") or "unknown"
    target_path = _resolve_save_path(
        media_type, meta.get("title") or title, meta.get("season"), save_path
    )

    external_id: str | None = None
    downloader = None
    status = TaskStatus.PENDING.value
    error: str | None = None

    if kind in (ResourceKind.TORRENT.value, ResourceKind.MAGNET.value):
        downloader = site_service.default_downloader(downloader_name)
        if not downloader:
            error = "未配置下载器"
            status = TaskStatus.FAILED.value
            logger.error("添加下载失败：%s（%s）", error, truncate(title, 80))
        else:
            external_id = await downloader.add(
                link,
                save_path=target_path,
                cookie=resource.get("cookie"),
            )
            if external_id:
                status = TaskStatus.DOWNLOADING.value
            else:
                status = TaskStatus.FAILED.value
                error = "下载器拒绝或超时"
    elif kind == ResourceKind.DIRECT.value:
        downloader = site_service.default_downloader(downloader_name or "aria2")
        if downloader and downloader.name == "aria2":
            external_id = await downloader.add(link, save_path=target_path)
            status = (
                TaskStatus.DOWNLOADING.value if external_id else TaskStatus.FAILED.value
            )
        else:
            error = "直链需要 aria2 下载器"
    else:
        # 网盘分享：登记任务，由网盘插件或用户转存
        status = TaskStatus.PENDING.value
        error = None

    with session_scope() as session:
        task = DownloadTask(
            subscribe_id=subscribe_id,
            title=title[:500],
            kind=kind,
            site=str(resource.get("site") or "")[:128] or None,
            link=link,
            downloader=downloader.site_name if downloader else None,
            external_id=external_id,
            save_path=target_path,
            status=status,
            size=int(resource.get("size") or 0),
            media_type=media_type,
            season=meta.get("season"),
            episodes=meta.get("episodes") or [],
            error=error,
            meta={
                "password": resource.get("password"),
                "page_url": resource.get("page_url"),
                "score": resource.get("score"),
                "resolution": meta.get("resolution"),
                "quality": meta.get("quality"),
                "pan_type": (resource.get("extra") or {}).get("pan_type"),
            },
        )
        session.add(task)
        session.flush()
        task_id = task.id
        session.expunge(task)

    logger.info(
        "已登记下载任务 #%s [%s] %s", task_id, status, truncate(title, 80)
    )

    if notify and status in (TaskStatus.DOWNLOADING.value, TaskStatus.PENDING.value):
        body = f"{format_size(resource.get('size'))} · {resource.get('site')}"
        if kind == ResourceKind.PAN.value:
            body += "\n网盘资源，请在任务列表中转存"
        await notify_service.send(
            f"开始下载：{truncate(title, 60)}",
            body,
            level=NotifyLevel.INFO.value,
            event=EventType.DOWNLOAD_ADDED.value,
            payload={"task_id": task_id, "kind": kind},
        )
    return task


async def sync_tasks() -> dict[str, int]:
    """同步下载器中的任务状态，返回各状态计数。"""
    from app.services import library as library_service

    stats = {"checked": 0, "completed": 0, "failed": 0}
    downloaders = {item.site_name: item for item in site_service.downloaders()}
    if not downloaders:
        return stats

    with session_scope() as session:
        pending = list(
            session.execute(
                select(DownloadTask).where(
                    DownloadTask.status.in_(
                        [
                            TaskStatus.PENDING.value,
                            TaskStatus.DOWNLOADING.value,
                            TaskStatus.PAUSED.value,
                        ]
                    )
                )
            ).scalars()
        )
        snapshots = [
            {
                "id": task.id,
                "external_id": task.external_id,
                "downloader": task.downloader,
                "title": task.title,
                "kind": task.kind,
            }
            for task in pending
            if task.external_id
        ]

    for snapshot in snapshots:
        downloader = downloaders.get(snapshot["downloader"] or "")
        if not downloader:
            downloader = next(iter(downloaders.values()), None)
        if not downloader:
            continue

        state = await downloader.get(snapshot["external_id"])
        stats["checked"] += 1
        if not state:
            continue

        with session_scope() as session:
            task = session.get(DownloadTask, snapshot["id"])
            if not task:
                continue
            task.progress = round(state.progress, 4)
            task.speed = state.speed
            task.eta = state.eta
            if state.size:
                task.size = state.size
            if state.error:
                task.error = state.error

            if state.finished and task.status != TaskStatus.TRANSFERRED.value:
                task.status = TaskStatus.COMPLETED.value
                task.completed_at = utcnow()
                content_path = state.content_path or state.save_path
                task.meta = {**(task.meta or {}), "content_path": content_path}
                stats["completed"] += 1
            elif state.status == TaskStatus.FAILED.value:
                task.status = TaskStatus.FAILED.value
                stats["failed"] += 1
            else:
                task.status = state.status

    # 对已完成任务执行整理
    if stats["completed"]:
        await library_service.transfer_completed_tasks()
    return stats


async def remove_task(task_id: int, *, delete_files: bool = False) -> bool:
    """删除任务（同时从下载器移除）。"""
    with session_scope() as session:
        task = session.get(DownloadTask, task_id)
        if not task:
            return False
        external_id = task.external_id
        downloader_name = task.downloader

    if external_id:
        downloader = site_service.default_downloader(downloader_name)
        if downloader:
            await downloader.remove(external_id, delete_files=delete_files)

    with session_scope() as session:
        task = session.get(DownloadTask, task_id)
        if task:
            session.delete(task)
    return True


async def control_task(task_id: int, action: str) -> bool:
    """暂停/恢复任务。"""
    with session_scope() as session:
        task = session.get(DownloadTask, task_id)
        if not task or not task.external_id:
            return False
        external_id, downloader_name = task.external_id, task.downloader

    downloader = site_service.default_downloader(downloader_name)
    if not downloader:
        return False

    ok = (
        await downloader.pause(external_id)
        if action == "pause"
        else await downloader.resume(external_id)
    )
    if ok:
        with session_scope() as session:
            task = session.get(DownloadTask, task_id)
            if task:
                task.status = (
                    TaskStatus.PAUSED.value
                    if action == "pause"
                    else TaskStatus.DOWNLOADING.value
                )
    return ok
