"""下载服务：把候选资源投递到下载器，并跟踪进度。

网盘类资源不进入 BT 下载器：

- 若已配置**网盘存储**（AList / 夸克），则**自动转存**进自己的网盘；
- 否则登记为 ``pending`` 任务（保留分享链接与提取码），
  可稍后在「网盘管理」页一键转存，或交给 ``pan_transfer`` 插件投递外部服务。
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
    pan_saved: dict[str, Any] | None = None

    if kind in (ResourceKind.TORRENT.value, ResourceKind.MAGNET.value):
        # 多下载器时按策略排序，投递失败自动换下一个（CF_DOWNLOADER_FAILOVER）
        candidates = site_service.downloader_candidates(downloader_name)
        if not candidates:
            error = "未配置下载器"
            status = TaskStatus.FAILED.value
            logger.error("添加下载失败：%s（%s）", error, truncate(title, 80))
        else:
            attempts: list[str] = []
            for candidate in candidates:
                try:
                    external_id = await candidate.add(
                        link,
                        save_path=target_path,
                        cookie=resource.get("cookie"),
                    )
                except Exception as exc:  # 下载器抛错也算这一个失败，继续换源
                    external_id = None
                    attempts.append(f"{candidate.site_name}: {exc}"[:120])
                else:
                    if not external_id:
                        attempts.append(f"{candidate.site_name}: 拒绝或超时")
                if external_id:
                    downloader = candidate
                    status = TaskStatus.DOWNLOADING.value
                    if attempts:
                        logger.info(
                            "已自动换源投递到 %s（前序失败：%s）",
                            candidate.site_name,
                            "；".join(attempts),
                        )
                    break
            if not external_id:
                downloader = candidates[0]
                status = TaskStatus.FAILED.value
                error = ("下载器投递失败 → " + "；".join(attempts))[:500]
    elif kind == ResourceKind.WEBVIDEO.value:
        # 视频网页（B 站/YouTube 等公开视频）：必须交给 yt-dlp，
        # 其他下载器拿到网页地址只会下到一个 HTML 文件
        downloader = site_service.default_downloader(downloader_name or "ytdlp")
        if downloader and downloader.name == "ytdlp":
            external_id = await downloader.add(link, save_path=target_path)
            if external_id:
                status = TaskStatus.DOWNLOADING.value
            else:
                status = TaskStatus.FAILED.value
                # add() 返回 None 的两种原因都要让用户看到，否则无从下手
                error = "解析失败或该地址属于付费内容（详见运行日志）"
        else:
            error = "视频网页需要 yt-dlp 下载器，请到站点管理启用"
            status = TaskStatus.FAILED.value
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
        # 网盘分享：优先自动转存进自己的网盘，失败/未配置则留待人工或插件处理
        status = TaskStatus.PENDING.value
        error = None
        pan_result = await _try_auto_save_pan(link, resource)
        if pan_result is not None:
            if pan_result.get("success"):
                status = TaskStatus.TRANSFERRED.value
                pan_saved = pan_result
            else:
                error = str(pan_result.get("message") or "")[:500] or None

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
                **(
                    {
                        "pan_storage": pan_saved.get("storage"),
                        "saved_path": pan_saved.get("saved_path"),
                    }
                    if pan_saved
                    else {}
                ),
            },
        )
        session.add(task)
        session.flush()
        task_id = task.id
        session.expunge(task)

    logger.info(
        "已登记下载任务 #%s [%s] %s", task_id, status, truncate(title, 80)
    )

    if notify and status in (
        TaskStatus.DOWNLOADING.value,
        TaskStatus.PENDING.value,
        TaskStatus.TRANSFERRED.value,
    ):
        body = f"{format_size(resource.get('size'))} · {resource.get('site')}"
        if kind == ResourceKind.PAN.value:
            if pan_saved:
                body += f"\n已自动转存到 {pan_saved.get('storage')}"
                if pan_saved.get("saved_path"):
                    body += f"：{pan_saved['saved_path']}"
            else:
                body += "\n网盘资源，请在「网盘管理」页转存"
        await notify_service.send(
            f"开始下载：{truncate(title, 60)}",
            body,
            level=NotifyLevel.INFO.value,
            event=EventType.DOWNLOAD_ADDED.value,
            payload={"task_id": task_id, "kind": kind},
        )
    return task


async def _try_auto_save_pan(
    link: str, resource: dict[str, Any]
) -> dict[str, Any] | None:
    """网盘资源自动转存。

    返回 ``None`` 表示**没有配置网盘存储**（保持原有 pending 行为）；
    返回 dict 表示尝试过转存，``success`` 指示结果。
    转存失败不影响任务登记，只把原因写进 ``error`` 供界面显示。
    """
    from app.services import pan_storage as pan_service

    if not bool(settings.PAN_AUTO_SAVE):
        return None
    try:
        if not pan_service.storages():
            return None
        return await pan_service.save_share(
            link, password=resource.get("password") or None
        )
    except Exception as exc:  # pragma: no cover - 转存异常不应打断下载登记
        logger.warning("网盘自动转存异常: %s", exc)
        return {"success": False, "message": f"自动转存异常: {exc}"}


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
