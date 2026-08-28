"""媒体库服务：完成下载后的整理入库、媒体库扫描与刷新。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.core.logger import get_logger
from app.core.meta import parse
from app.core.organizer import iter_media_files, transfer_directory
from app.db.models import DownloadTask, LibraryFile, MediaItem, TransferRecord
from app.db.session import session_scope
from app.schemas.enums import EventType, MediaType, NotifyLevel, TaskStatus
from app.services import notify as notify_service
from app.services import sites as site_service
from app.utils.strings import truncate

logger = get_logger(__name__)


def _task_content_path(task: DownloadTask) -> Path | None:
    """推断下载完成后的内容路径。"""
    meta = task.meta or {}
    candidate = meta.get("content_path") or task.save_path
    if not candidate:
        return None
    path = Path(candidate)
    if path.exists():
        return path
    # 下载器与本程序路径映射不同时，尝试在下载目录中按标题匹配
    fallback = Path(settings.DOWNLOAD_DIR)
    if fallback.exists():
        for child in fallback.rglob("*"):
            if child.name == path.name:
                return child
    return None


async def transfer_completed_tasks() -> dict[str, int]:
    """把所有已完成但未整理的任务转移进媒体库。"""
    stats = {"tasks": 0, "files": 0, "failed": 0}
    with session_scope() as session:
        tasks = list(
            session.execute(
                select(DownloadTask).where(
                    DownloadTask.status == TaskStatus.COMPLETED.value
                )
            ).scalars()
        )
        snapshots = [
            {
                "id": task.id,
                "title": task.title,
                "season": task.season,
                "media_type": task.media_type,
                "subscribe_id": task.subscribe_id,
                "content_path": (task.meta or {}).get("content_path") or task.save_path,
                "save_path": task.save_path,
                # 洗版上下文：新版本入库成功后据此删除旧文件
                "upgrade_for": (task.meta or {}).get("upgrade_for"),
                "score": (task.meta or {}).get("score") or 0,
            }
            for task in tasks
        ]

    for snapshot in snapshots:
        source = None
        candidate = snapshot["content_path"]
        if candidate and Path(candidate).exists():
            source = Path(candidate)
        else:
            with session_scope() as session:
                task = session.get(DownloadTask, snapshot["id"])
                source = _task_content_path(task) if task else None

        if not source:
            logger.warning("任务 #%s 未找到下载文件，跳过整理", snapshot["id"])
            continue

        info = parse(snapshot["title"])
        results = transfer_directory(
            source,
            title=info.title or None,
            season=snapshot["season"] if snapshot["season"] is not None else info.season,
        )
        if not results:
            logger.info("任务 #%s 未发现可整理的媒体文件", snapshot["id"])
            continue

        stats["tasks"] += 1
        success_files = 0
        with session_scope() as session:
            for result in results:
                session.add(
                    TransferRecord(
                        task_id=snapshot["id"],
                        source_path=str(result.source),
                        target_path=str(result.target) if result.target else None,
                        mode=result.mode,
                        success=result.success,
                        message=result.message,
                        media_title=result.meta.title if result.meta else None,
                        media_type=result.meta.media_type if result.meta else None,
                        season=result.meta.season if result.meta else None,
                        episode=result.meta.episode_start if result.meta else None,
                        size=result.size,
                    )
                )
                if result.success and result.target:
                    success_files += 1
                    _register_library_file(
                        session,
                        result.target,
                        result.meta,
                        result.size,
                        quality_score=float(snapshot.get("score") or 0),
                    )
                elif not result.success:
                    stats["failed"] += 1

            task = session.get(DownloadTask, snapshot["id"])
            if task and success_files:
                task.status = TaskStatus.TRANSFERRED.value

        stats["files"] += success_files

        if success_files:
            episodes = sorted(
                {
                    result.meta.episode_start
                    for result in results
                    if result.success and result.meta and result.meta.episode_start
                }
            )
            if snapshot["subscribe_id"]:
                from app.services import subscribe as subscribe_service

                subscribe_service.mark_episodes_done(
                    snapshot["subscribe_id"], episodes
                )

            await notify_service.send(
                f"入库完成：{truncate(snapshot['title'], 60)}",
                f"整理 {success_files} 个文件"
                + (f"，集数 {episodes}" if episodes else ""),
                level=NotifyLevel.SUCCESS.value,
                event=EventType.TRANSFER_COMPLETED.value,
                payload={"task_id": snapshot["id"], "files": success_files},
            )
            # 刮削 NFO 与图片。**best-effort**：失败只记日志，绝不影响入库结果
            if settings.SCRAPE_ENABLED:
                from app.services import scraper

                for result in results:
                    if not (result.success and result.target):
                        continue
                    try:
                        await scraper.scrape_file(
                            result.target,
                            result.meta,
                            overwrite=settings.SCRAPE_OVERWRITE,
                        )
                    except Exception as exc:
                        logger.warning("刮削失败 %s: %s", result.target, exc)

            # 洗版收尾：新版本已确实落地，此时才删旧文件（失败下载不会留空洞）
            if snapshot.get("upgrade_for"):
                from app.services import upgrade as upgrade_service

                new_target = next(
                    (str(r.target) for r in results if r.success and r.target), None
                )
                if new_target:
                    outcome = upgrade_service.replace_library_file(
                        snapshot["upgrade_for"],
                        new_target,
                        new_score=float(snapshot.get("score") or 0),
                    )
                    logger.info("洗版替换：%s", outcome["message"])

            await refresh_media_servers(
                str(results[0].target) if results[0].target else None
            )
    return stats


def _register_library_file(
    session: Any, target: Path, meta: Any, size: int, *, quality_score: float = 0.0
) -> None:
    """把入库文件写入索引（用于缺集计算与去重）。"""
    existing = session.execute(
        select(LibraryFile).where(LibraryFile.path == str(target))
    ).scalar_one_or_none()
    if existing:
        return

    media_id = None
    if meta and meta.title:
        media = session.execute(
            select(MediaItem).where(
                MediaItem.title == meta.title,
                MediaItem.media_type == meta.media_type,
            )
        ).scalar_one_or_none()
        if not media:
            media = MediaItem(
                title=meta.title,
                year=meta.year,
                media_type=meta.media_type or MediaType.UNKNOWN.value,
            )
            session.add(media)
            session.flush()
        media_id = media.id

    session.add(
        LibraryFile(
            media_id=media_id,
            path=str(target),
            title=(meta.title if meta else target.stem) or target.stem,
            year=meta.year if meta else None,
            media_type=(meta.media_type if meta else MediaType.UNKNOWN.value),
            season=meta.season if meta else None,
            episode=meta.episode_start if meta else None,
            resolution=meta.resolution if meta else None,
            size=size,
            # 存下入库时的质量评分，洗版时用它和新资源比较
            quality_score=quality_score,
        )
    )


async def refresh_media_servers(path: str | None = None) -> int:
    """通知所有媒体服务器刷新媒体库。"""
    servers = site_service.media_servers()
    refreshed = 0
    for server in servers:
        refresh = getattr(server, "refresh_library", None)
        if not refresh:
            continue
        try:
            if await refresh(path):
                refreshed += 1
        except Exception as exc:
            logger.warning("媒体服务器 %s 刷新失败: %s", server.site_name, exc)
    if refreshed:
        await notify_service.emit(
            EventType.LIBRARY_REFRESHED.value, {"path": path, "servers": refreshed}
        )
    return refreshed


def scan_library(root: str | Path | None = None) -> dict[str, int]:
    """扫描媒体库目录，重建文件索引。"""
    library_root = Path(root or settings.LIBRARY_DIR)
    stats = {"scanned": 0, "added": 0}
    if not library_root.exists():
        return stats

    files = iter_media_files(library_root)
    stats["scanned"] = len(files)
    with session_scope() as session:
        known = {
            row[0]
            for row in session.execute(select(LibraryFile.path)).all()
        }
        for path in files:
            if str(path) in known:
                continue
            info = parse(path.name, is_file=True)
            if not info.title or len(info.title) < 2:
                info = parse(f"{path.parent.name} {path.name}", is_file=True)
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            _register_library_file(session, path, info, size)
            stats["added"] += 1
    logger.info("媒体库扫描完成：共 %d 个文件，新增 %d", stats["scanned"], stats["added"])
    return stats


def existing_episodes(
    title: str,
    season: int | None,
    media_type: str | None = None,
) -> set[int]:
    """查询媒体库中已有的集数。"""
    with session_scope() as session:
        stmt = select(LibraryFile.episode).where(LibraryFile.title == title)
        if season is not None:
            stmt = stmt.where(LibraryFile.season == season)
        if media_type is not None:
            stmt = stmt.where(LibraryFile.media_type == media_type)
        return {
            row[0]
            for row in session.execute(stmt).all()
            if row[0] is not None
        }


def has_library_file(title: str, media_type: str | None = None) -> bool:
    """判断媒体库是否已存在指定标题的文件（电影入库判断用）。"""
    with session_scope() as session:
        stmt = select(LibraryFile.id).where(LibraryFile.title == title)
        if media_type is not None:
            stmt = stmt.where(LibraryFile.media_type == media_type)
        return session.execute(stmt.limit(1)).first() is not None


def library_stats() -> dict[str, Any]:
    """媒体库统计。"""
    from sqlalchemy import func

    with session_scope() as session:
        total_files = session.execute(select(func.count(LibraryFile.id))).scalar() or 0
        total_size = session.execute(select(func.sum(LibraryFile.size))).scalar() or 0
        movies = (
            session.execute(
                select(func.count(LibraryFile.id)).where(
                    LibraryFile.media_type == MediaType.MOVIE.value
                )
            ).scalar()
            or 0
        )
        episodes = (
            session.execute(
                select(func.count(LibraryFile.id)).where(
                    LibraryFile.media_type.in_(
                        [MediaType.TV.value, MediaType.ANIME.value]
                    )
                )
            ).scalar()
            or 0
        )
        series = (
            session.execute(
                select(func.count(func.distinct(LibraryFile.title))).where(
                    LibraryFile.media_type.in_(
                        [MediaType.TV.value, MediaType.ANIME.value]
                    )
                )
            ).scalar()
            or 0
        )
    return {
        "files": total_files,
        "size": int(total_size),
        "movies": movies,
        "episodes": episodes,
        "series": series,
    }
