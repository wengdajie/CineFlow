"""网盘 STRM 同步：把网盘目录映射成本地 .strm 文件供媒体服务器扫描。

**为什么做**：这是当前 NAS 影视自动化的事实标准做法（docs/09 差距矩阵 #7）。
媒体服务器扫描 KB 级文本文件而不是 TB 级视频，入库从几小时变成几分钟，
播放时再按需换取网盘直链。

**两种链接形式**（``CF_STRM_LINK_MODE``）：

- ``direct``：把网盘临时直链写进 STRM。播放器直连网盘，NAS 零流量，
  但直链**会过期**，过期后需重新同步。
- ``proxy``（默认推荐）：写 CineFlow 自己的 302 端点
  ``/api/v1/strm/play/{记录ID}``。链接永不过期，播放时才实时换直链，
  代价是每次播放要多一跳（但只是 302 跳转，不代理流量）。

**增量与清理**：``strm_records`` 表记录每个 STRM 对应网盘哪个文件。
同步时对比网盘现状：新增文件 → 生成；源文件消失 → 标记失效并删除 STRM
（``CF_STRM_CLEAN_INVALID``），顺带清理空目录。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.core.logger import get_logger
from app.db.base import utcnow
from app.db.models import SiteConfig, StrmRecord
from app.db.session import session_scope
from app.providers.panstorage.base import BasePanStorage, PanFile
from app.schemas.enums import EventType, NotifyLevel, ProviderKind
from app.utils.strings import format_size

logger = get_logger(__name__)


def _is_video(name: str) -> bool:
    """是否是要生成 STRM 的视频文件。"""
    return Path(name).suffix.lower() in settings.MEDIA_EXTENSIONS


def _is_metadata(name: str) -> bool:
    """字幕/NFO/图片等随行元数据。"""
    suffix = Path(name).suffix.lower()
    return suffix in settings.SUBTITLE_EXTENSIONS or suffix in (
        ".nfo",
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
    )


def _strm_root() -> Path:
    return Path(settings.STRM_DIR)


def _link_for(record_id: int, direct: str | None, mode: str) -> str:
    """算出写进 .strm 文件的那一行内容。"""
    if mode == "direct" and direct:
        return direct
    base = str(settings.STRM_BASE_URL or "").rstrip("/")
    return f"{base}/api/v1/strm/play/{record_id}"


async def _walk(
    storage: BasePanStorage, path: str, *, depth: int = 0, max_depth: int = 12
) -> list[tuple[str, PanFile]]:
    """递归遍历网盘目录，返回 ``(所在目录, 文件)`` 列表。

    限制递归深度，避免网盘上出现循环软链时无限下钻。
    """
    if depth > max_depth:
        return []
    collected: list[tuple[str, PanFile]] = []
    try:
        entries = await storage.list_dir(path)
    except Exception as exc:
        logger.warning("STRM 遍历目录失败 %s: %s", path, exc)
        return []
    for entry in entries:
        if entry.is_dir:
            collected.extend(
                await _walk(storage, entry.path, depth=depth + 1, max_depth=max_depth)
            )
        else:
            collected.append((path, entry))
    return collected


def _storages() -> list[BasePanStorage]:
    from app.services import pan_storage

    return pan_storage.storages()


def _get_storage(site_id: int) -> BasePanStorage | None:
    from app.services import pan_storage

    return pan_storage.get_storage(site_id)


async def sync_storage(
    site_id: int,
    *,
    pan_path: str = "/",
    strm_subdir: str | None = None,
    clean: bool | None = None,
    link_mode: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """把某个网盘目录同步成 STRM 文件树。

    返回统计：``created`` / ``updated`` / ``removed`` / ``metadata`` / ``skipped``。
    """
    storage = _get_storage(site_id)
    stats: dict[str, Any] = {
        "site_id": site_id,
        "created": 0,
        "updated": 0,
        "removed": 0,
        "metadata": 0,
        "skipped": 0,
        "total_size": 0,
        "message": "",
    }
    if not storage:
        stats["message"] = "网盘不存在或未启用"
        return stats

    mode = link_mode or settings.STRM_LINK_MODE
    if clean is None:
        clean = settings.STRM_CLEAN_INVALID

    files = await _walk(storage, pan_path)
    videos = [(parent, item) for parent, item in files if _is_video(item.name)]
    if not videos:
        stats["message"] = f"{storage.site_name} 的 {pan_path} 下没有找到视频文件"
        return stats

    # STRM 目录结构镜像网盘结构；可选再套一层子目录便于多盘并存
    root = _strm_root()
    if strm_subdir:
        root = root / strm_subdir

    base = storage.normalize_path(pan_path)
    seen_sources: set[str] = set()

    for _parent, item in videos:
        seen_sources.add(item.path)
        # 网盘路径相对 pan_path 的部分，用来在本地重建同样的层级
        relative = item.path[len(base) :].lstrip("/") if base != "/" else item.path.lstrip("/")
        target = (root / relative).with_suffix(".strm")
        stats["total_size"] += item.size

        if dry_run:
            stats["created"] += 1
            continue

        with session_scope() as session:
            record = session.execute(
                select(StrmRecord).where(
                    StrmRecord.site_id == site_id, StrmRecord.source_path == item.path
                )
            ).scalar_one_or_none()
            is_new = record is None
            if record is None:
                record = StrmRecord(
                    strm_path=str(target),
                    site_id=site_id,
                    source_path=item.path,
                    file_id=item.file_id,
                    size=item.size,
                    link_mode=mode,
                )
                session.add(record)
                session.flush()  # 需要自增 ID 来拼 proxy 链接
            else:
                record.strm_path = str(target)
                record.file_id = item.file_id or record.file_id
                record.size = item.size
                record.link_mode = mode
            record.alive = True
            record.last_synced_at = utcnow()
            record_id = record.id

        direct = None
        if mode == "direct":
            try:
                direct = await storage.download_url(item.path, file_id=item.file_id)
            except Exception as exc:
                logger.debug("换取直链失败 %s: %s", item.path, exc)
        content = _link_for(record_id, direct, mode)

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            previous = target.read_text(encoding="utf-8") if target.exists() else None
            if previous == content:
                stats["skipped"] += 1
            else:
                target.write_text(content, encoding="utf-8")
                stats["created" if is_new else "updated"] += 1
        except OSError as exc:
            logger.warning("写入 STRM 失败 %s: %s", target, exc)
            continue

    # 随行元数据（字幕/NFO/图片）直接下载到 STRM 目录旁边
    if settings.STRM_SYNC_METADATA and not dry_run:
        stats["metadata"] = await _sync_metadata(storage, files, base, root)

    if clean and not dry_run:
        stats["removed"] = _clean_invalid(site_id, seen_sources)

    stats["message"] = (
        f"{storage.site_name}：新增 {stats['created']}、更新 {stats['updated']}、"
        f"未变 {stats['skipped']}、清理 {stats['removed']}，"
        f"共 {format_size(stats['total_size'])}"
    )
    logger.info("STRM 同步完成 %s", stats["message"])
    return stats


async def _sync_metadata(
    storage: BasePanStorage,
    files: list[tuple[str, PanFile]],
    base: str,
    root: Path,
) -> int:
    """把网盘上的字幕/NFO/图片下载到 STRM 目录（供媒体服务器直接读取）。"""
    from app.utils.http import async_client

    count = 0
    for _parent, item in files:
        if not _is_metadata(item.name):
            continue
        relative = item.path[len(base) :].lstrip("/") if base != "/" else item.path.lstrip("/")
        target = root / relative
        if target.exists() and target.stat().st_size > 0:
            continue
        try:
            url = await storage.download_url(item.path, file_id=item.file_id)
        except Exception:
            url = None
        if not url:
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            headers = storage.auth_header() if hasattr(storage, "auth_header") else {}
            async with async_client(timeout=30, headers=headers or None) as client:
                response = await client.get(url)
                if response.status_code == 200 and response.content:
                    target.write_bytes(response.content)
                    count += 1
        except Exception as exc:
            logger.debug("元数据下载失败 %s: %s", item.path, exc)
    return count


def _clean_invalid(site_id: int, seen_sources: set[str]) -> int:
    """删除源文件已消失的 STRM，并清理由此产生的空目录。"""
    removed = 0
    stale_dirs: set[Path] = set()
    with session_scope() as session:
        records = list(
            session.execute(
                select(StrmRecord).where(StrmRecord.site_id == site_id)
            ).scalars()
        )
        for record in records:
            if record.source_path in seen_sources:
                continue
            path = Path(record.strm_path)
            try:
                if path.exists():
                    path.unlink()
                    stale_dirs.add(path.parent)
            except OSError as exc:
                logger.warning("删除失效 STRM 失败 %s: %s", path, exc)
                continue
            session.delete(record)
            removed += 1

    # 自底向上删空目录：STRM 目录树很深，留一堆空壳会让媒体库出现空剧集
    root = _strm_root().resolve()
    for directory in sorted(stale_dirs, key=lambda p: len(p.parts), reverse=True):
        current = directory
        while current.exists() and current.resolve() != root:
            try:
                if any(current.iterdir()):
                    break
                current.rmdir()
                current = current.parent
            except OSError:
                break
    return removed


async def sync_all(*, notify: bool = True) -> dict[str, Any]:
    """遍历所有启用的网盘做一次 STRM 同步（供定时任务调用）。"""
    storages = _storages()
    summary: dict[str, Any] = {"storages": len(storages), "created": 0, "removed": 0, "details": []}
    if not storages:
        summary["message"] = "没有可用的网盘存储，请先在站点管理中添加并启用"
        return summary

    with session_scope() as session:
        site_ids = {
            site.name: site.id
            for site in session.execute(
                select(SiteConfig).where(
                    SiteConfig.kind == ProviderKind.PANSTORAGE.value,
                    SiteConfig.enabled.is_(True),
                )
            ).scalars()
        }

    for storage in storages:
        site_id = storage.config.get("id") or site_ids.get(storage.site_name)
        if not site_id:
            continue
        result = await sync_storage(
            int(site_id), pan_path=storage.root_path, strm_subdir=storage.site_name
        )
        summary["created"] += result["created"]
        summary["removed"] += result["removed"]
        summary["details"].append(result)

    summary["message"] = (
        f"共 {summary['storages']} 个网盘，新增 STRM {summary['created']}、"
        f"清理 {summary['removed']}"
    )

    if notify and (summary["created"] or summary["removed"]):
        from app.services import notify as notify_service

        await notify_service.send(
            "STRM 同步完成",
            summary["message"],
            level=NotifyLevel.SUCCESS.value,
            event=EventType.LIBRARY_REFRESHED.value,
        )
    return summary


async def resolve_play_url(record_id: int) -> tuple[str | None, str]:
    """给 302 播放端点用：把记录 ID 换成当前有效的网盘直链。"""
    with session_scope() as session:
        record = session.get(StrmRecord, record_id)
        if not record:
            return None, "STRM 记录不存在"
        site_id = record.site_id
        source_path = record.source_path
        file_id = record.file_id

    storage = _get_storage(int(site_id)) if site_id else None
    if not storage:
        return None, "对应网盘未启用"
    try:
        url = await storage.download_url(source_path, file_id=file_id)
    except Exception as exc:
        return None, f"换取直链失败：{exc}"
    if not url:
        return None, "该网盘不支持换取直链"
    return url, "ok"


def list_records(
    *, site_id: int | None = None, alive_only: bool = False, limit: int = 200
) -> list[dict[str, Any]]:
    """列出已生成的 STRM 记录。"""
    with session_scope() as session:
        stmt = select(StrmRecord).order_by(StrmRecord.updated_at.desc()).limit(limit)
        if site_id:
            stmt = stmt.where(StrmRecord.site_id == site_id)
        if alive_only:
            stmt = stmt.where(StrmRecord.alive.is_(True))
        return [
            {
                "id": row.id,
                "strm_path": row.strm_path,
                "site_id": row.site_id,
                "source_path": row.source_path,
                "size": row.size,
                "size_text": format_size(row.size),
                "link_mode": row.link_mode,
                "alive": row.alive,
                "last_synced_at": row.last_synced_at.isoformat() if row.last_synced_at else None,
            }
            for row in session.execute(stmt).scalars()
        ]


def stats() -> dict[str, Any]:
    """STRM 概览（供前端卡片展示）。"""
    with session_scope() as session:
        records = list(session.execute(select(StrmRecord)).scalars())
    total_size = sum(record.size for record in records)
    return {
        "total": len(records),
        "alive": sum(1 for record in records if record.alive),
        "invalid": sum(1 for record in records if not record.alive),
        "total_size": total_size,
        "total_size_text": format_size(total_size),
        "strm_dir": str(_strm_root()),
        "link_mode": settings.STRM_LINK_MODE,
    }
