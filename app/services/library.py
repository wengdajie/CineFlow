"""媒体库服务：完成下载后的整理入库、媒体库扫描与刷新。"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.core.exceptions import ConfigError, NotFoundError
from app.core.logger import get_logger
from app.core.meta import MetaInfo, parse
from app.core.organizer import (
    is_media_file,
    is_subtitle_file,
    iter_media_files,
    transfer_directory,
)
from app.db.models import DownloadTask, LibraryFile, MediaItem, Subscribe, TransferRecord
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
    # skipped 必须单独计数：只在日志里 logger.info 一行的话，返回值是
    # {"tasks": 0, "files": 0, "failed": 0} —— 和"没有待整理任务"一模一样。
    # 实测踩到过：aria2 的 content_path 指向字幕，两个任务被静默跳过，
    # 定时任务与 API 都报"成功"，用户只看到媒体库里什么都没有（ADR-82）。
    stats: dict[str, Any] = {"tasks": 0, "files": 0, "failed": 0, "skipped": 0}
    skipped_detail: list[dict[str, str]] = []
    #: 整理失败的文件明细，全部任务处理完后统一推一条
    failures: list[dict[str, str]] = []
    with session_scope() as session:
        tasks = list(
            session.execute(
                select(DownloadTask).where(
                    DownloadTask.status == TaskStatus.COMPLETED.value
                )
            ).scalars()
        )
        subscriptions = {
            item.id: item
            for item in session.execute(
                select(Subscribe).where(Subscribe.id.in_({t.subscribe_id for t in tasks if t.subscribe_id}))
            ).scalars()
        }
        snapshots = [
            {
                "id": task.id,
                "title": subscriptions[task.subscribe_id].title if task.subscribe_id in subscriptions else task.title,
                "season": subscriptions[task.subscribe_id].season if task.subscribe_id in subscriptions else task.season,
                "media_type": task.media_type,
                "subscribe_id": task.subscribe_id,
                "content_path": (task.meta or {}).get("content_path") or task.save_path,
                "save_path": task.save_path,
                # 洗版上下文：新版本入库成功后据此删除旧文件
                "upgrade_for": (task.meta or {}).get("upgrade_for"),
                "upgrade_target": (task.meta or {}).get("upgrade_target"),
                "score": (task.meta or {}).get("score") or 0,
            }
            for task in tasks
        ]

    for snapshot in snapshots:
        if snapshot.get("upgrade_target"):
            _finish_upgrade_task(snapshot, snapshot["upgrade_target"])
            continue
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
            stats["skipped"] += 1
            skipped_detail.append(
                {
                    "title": snapshot["title"],
                    "reason": f"未找到下载文件（{snapshot['content_path'] or '无路径'}）",
                }
            )
            continue

        info = parse(snapshot["title"])
        subscription = subscriptions.get(snapshot["subscribe_id"])
        identity = MetaInfo(title=subscription.title, year=subscription.year,
                            media_type=subscription.media_type, season=subscription.season) if subscription else None
        season = snapshot["season"] if snapshot["season"] is not None else info.season
        results = transfer_directory(
            source, title=info.title or None, season=season, year=info.year, identity=identity
        )
        if not results and source.is_file():
            # 下载器给的"主文件"可能不是正片。实测 aria2 按 torrent 内文件序返回，
            # `files[0]` 是 140 字节的 .srt，而正片在同一目录里。
            # 单个非媒体文件整理不出东西时，退到它所在目录再试一次 ——
            # 这比让整个任务静默失败合理得多。
            parent = source.parent
            logger.info(
                "任务 #%s 的 %s 不是媒体文件，回退到目录 %s 重试",
                snapshot["id"],
                source.name,
                parent,
            )
            results = transfer_directory(
                parent, title=info.title or None, season=season, year=info.year, identity=identity
            )
        if not results:
            logger.warning(
                "任务 #%s（%s）在 %s 下未发现可整理的媒体文件",
                snapshot["id"],
                snapshot["title"],
                source,
            )
            stats["skipped"] += 1
            skipped_detail.append(
                {
                    "title": snapshot["title"],
                    "reason": f"{source} 下没有可识别的媒体文件",
                }
            )
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
                    failures.append(
                        {
                            "title": snapshot["title"],
                            "source": str(result.source),
                            "message": result.message or "",
                        }
                    )

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
            # 洗版收尾：新版本已确实落地，此时才删旧文件（失败下载不会留空洞）
            if snapshot.get("upgrade_for"):
                new_target = next(
                    (str(r.target) for r in results if r.success and r.target), None
                )
                if new_target:
                    outcome = _finish_upgrade_task(snapshot, new_target)
                    logger.info("洗版替换：%s", outcome["message"])

            await refresh_media_servers(
                str(results[0].target) if results[0].target else None
            )

    # 整理失败必须告知：下载成功但入库失败是最容易被忽略的中间态 ——
    # 「下载任务」页显示已完成，媒体库里却没有，用户往往过很久才发现。
    # 硬链接跨盘（Invalid cross-device link）与权限问题都会走到这里。
    if failures:
        lines = [
            f"· {truncate(item['title'], 40)}"
            + (f"：{truncate(item['message'], 50)}" if item["message"] else "")
            for item in failures[:10]
        ]
        if len(failures) > 10:
            lines.append(f"…另有 {len(failures) - 10} 个")
        await notify_service.send(
            f"入库失败：{len(failures)} 个文件",
            "\n".join(lines),
            level=NotifyLevel.ERROR.value,
            event=EventType.TRANSFER_FAILED.value,
            payload={"failed": len(failures)},
        )

    # 「跳过」和「失败」不一样，但同样不能只写在日志里：
    # 下载完成却一个文件都没入库，是用户最难自己发现的中间态。
    if skipped_detail:
        lines = [
            f"· {truncate(item['title'], 40)}：{truncate(item['reason'], 60)}"
            for item in skipped_detail[:10]
        ]
        if len(skipped_detail) > 10:
            lines.append(f"…另有 {len(skipped_detail) - 10} 个")
        await notify_service.send(
            f"入库跳过：{len(skipped_detail)} 个任务没找到可整理的媒体文件",
            "\n".join(lines)
            + "\n\n下载已完成但没有入库，请检查下载目录里的实际文件"
            "（常见原因：种子里只有样片/字幕，或下载器与本程序的路径映射不一致）。",
            level=NotifyLevel.WARNING.value,
            event=EventType.TRANSFER_FAILED.value,
            payload={"skipped": len(skipped_detail)},
        )
    return stats


def _finish_upgrade_task(snapshot: dict[str, Any], target: str) -> dict[str, Any]:
    from app.services import upgrade

    outcome = upgrade.replace_library_file(
        snapshot["upgrade_for"], target, new_score=float(snapshot.get("score") or 0)
    )
    with session_scope() as session:
        task = session.get(DownloadTask, snapshot["id"])
        if task:
            task.status = TaskStatus.TRANSFERRED.value if outcome["completed"] else TaskStatus.COMPLETED.value
            task.error = None if outcome["completed"] else outcome["message"]
            task.meta = {**(task.meta or {}), "upgrade_target": target}
    return outcome


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
    r"""扫描媒体库目录，重建文件索引。

    返回 ``scanned`` / ``added`` / ``removed`` 三个计数。

    **``removed`` 是 v1.24.0 补的**：在此之前扫描只会往索引里加行，从不删行。
    后果实测到过 —— 把 ``CF_LIBRARY_DIR`` 从 ``D:\...\library`` 改到
    ``K:\media\library`` 之后，旧库那一行永远留在索引里：目录早已不存在，
    而它又落在允许根目录之外，所以连 ``delete_entry`` 都清不掉（会报"超出允许范围"，
    这是对的），索引里就出现一条**任何操作都碰不到的僵尸行**。
    它会污染 `library_stats` 计数与缺集判断（`existing_episodes` 认为某集已有，
    于是订阅不再去补），属于"静默错"而不是"看得见的错"。

    清理口径**只删文件确实不存在的行**，不按根目录过滤 —— 用户可能临时把
    ``LIBRARY_DIR`` 指到别处，不该因此把还在盘上的旧库记录抹掉。
    """
    library_root = Path(root or settings.LIBRARY_DIR)
    stats = {"scanned": 0, "added": 0, "removed": 0}

    files = iter_media_files(library_root) if library_root.exists() else []
    stats["scanned"] = len(files)
    with session_scope() as session:
        rows = list(session.execute(select(LibraryFile)).scalars())
        known = {row.path for row in rows}

        # 先清僵尸行：文件不在了，索引就不该还留着
        for row in rows:
            raw = row.path or ""
            if not raw or Path(raw).exists():
                continue
            session.delete(row)
            known.discard(raw)
            stats["removed"] += 1
            logger.info("清理失效索引：%s（文件已不存在）", raw)

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
    logger.info(
        "媒体库扫描完成：共 %d 个文件，新增 %d，清理失效 %d",
        stats["scanned"], stats["added"], stats["removed"],
    )
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


# ---------------------------------------------------------------------------
# 本地文件操作（重命名 / 移动 / 删除）
# ---------------------------------------------------------------------------
#: 本项目**不做刮削**（v1.24.0 起移除，见 ADR-96）。媒体库页的定位是
#: "看本地下载到了什么 + 手工收拾一下"，因此只提供浏览 / 重命名 / 移动 / 删除。
#:
#: 允许操作的根目录被限制在下面两个：
#:   - ``settings.LIBRARY_DIR``  整理入库后的媒体库
#:   - ``settings.DOWNLOAD_DIR`` 下载器落地目录（用户实测配置为 K:\media）
#: 任何解析后落在这两个根之外的路径都会被拒绝，用来挡 ``../`` 穿越、
#: 绝对路径注入以及指向库外的符号链接（三种都有单测覆盖）。


def library_roots() -> list[Path]:
    """允许被浏览/操作的根目录（去重且保持顺序）。"""
    roots: list[Path] = []
    seen: set[str] = set()
    for raw in (settings.LIBRARY_DIR, settings.DOWNLOAD_DIR):
        try:
            resolved = Path(raw).resolve()
        except OSError:  # pragma: no cover - 盘符掉线等极端情况
            continue
        key = os.path.normcase(str(resolved))
        if key in seen:
            continue
        seen.add(key)
        roots.append(resolved)
    return roots


def _is_within(path: Path, root: Path) -> bool:
    """``path`` 是否等于 ``root`` 或位于其下（大小写不敏感，Windows 必需）。"""
    target = os.path.normcase(str(path))
    base = os.path.normcase(str(root))
    if target == base:
        return True
    return target.startswith(base.rstrip("\\/") + os.sep)


def resolve_library_path(raw: str | Path, *, must_exist: bool = True) -> Path:
    """把用户传入的路径解析为受限根目录内的真实路径。

    解析用 ``Path.resolve()``（会展开 ``..`` 与符号链接），再逐个根做前缀比对。
    不在任何根内 -> ``ConfigError``；要求存在却不存在 -> ``NotFoundError``。
    """
    text = str(raw or "").strip()
    if not text:
        raise ConfigError("路径不能为空")
    try:
        path = Path(text).expanduser().resolve()
    except OSError as exc:
        raise ConfigError(f"路径无法解析：{exc}") from exc
    roots = library_roots()
    if not any(_is_within(path, root) for root in roots):
        allowed = "、".join(str(root) for root in roots)
        raise ConfigError(f"路径超出允许范围：{path}（只允许 {allowed} 之内）")
    if must_exist and not path.exists():
        raise NotFoundError(f"路径不存在：{path}")
    return path


def _entry_kind(path: Path) -> str:
    """给前端一个能直接显示的类型标签。"""
    if path.is_dir():
        return "dir"
    if is_media_file(path):
        return "media"
    if is_subtitle_file(path):
        return "subtitle"
    return "file"


def _dir_size(path: Path) -> tuple[int, int]:
    """目录内文件总大小与文件个数；单文件直接返回自身大小。"""
    if path.is_file():
        try:
            return path.stat().st_size, 1
        except OSError:
            return 0, 1
    total = 0
    count = 0
    for child in path.rglob("*"):
        if not child.is_file():
            continue
        count += 1
        try:
            total += child.stat().st_size
        except OSError:
            continue
    return total, count


def browse_library(path: str | Path | None = None) -> dict[str, Any]:
    """列出某个目录下的条目（用于媒体库页"看本地下载了什么"）。

    ``path`` 为空时列出所有允许的根目录本身，这样前端不用预先知道盘符。
    """
    roots = library_roots()
    if not str(path or "").strip():
        items = []
        for root in roots:
            exists = root.exists()
            size, count = _dir_size(root) if exists else (0, 0)
            items.append(
                {
                    "name": str(root),
                    "path": str(root),
                    "kind": "dir",
                    "size": size,
                    "children": count,
                    "exists": exists,
                    "modified": None,
                }
            )
        return {"path": None, "parent": None, "roots": [str(r) for r in roots], "items": items}

    current = resolve_library_path(path)
    if current.is_file():
        # 直接给文件路径时退回它所在目录，避免前端点了文件就卡死
        current = current.parent

    parent: str | None = None
    if not any(_is_within(current, root) and current == root for root in roots):
        candidate = current.parent
        if any(_is_within(candidate, root) for root in roots):
            parent = str(candidate)

    items = []
    try:
        children = sorted(
            current.iterdir(), key=lambda p: (not p.is_dir(), os.path.normcase(p.name))
        )
    except OSError as exc:
        raise ConfigError(f"目录无法读取：{exc}") from exc
    for child in children:
        kind = _entry_kind(child)
        if kind == "dir":
            size, count = 0, 0
        else:
            try:
                size, count = child.stat().st_size, 0
            except OSError:
                size, count = 0, 0
        try:
            modified = int(child.stat().st_mtime)
        except OSError:
            modified = None
        items.append(
            {
                "name": child.name,
                "path": str(child),
                "kind": kind,
                "size": size,
                "children": count,
                "exists": True,
                "modified": modified,
            }
        )
    return {
        "path": str(current),
        "parent": parent,
        "roots": [str(r) for r in roots],
        "items": items,
    }


#: Windows 文件名禁用字符 + 控制字符。命名校验放在服务层而不是路由层，
#: 因为定时任务/脚本也可能调用这些函数。
_INVALID_NAME_CHARS = set('<>:"/\\|?*')


def _validate_name(name: str) -> str:
    """校验新文件名：不能带路径分隔符，不能是 ``.``/``..``，不能含保留字符。"""
    cleaned = str(name or "").strip()
    if not cleaned:
        raise ConfigError("新名称不能为空")
    if cleaned in {".", ".."}:
        raise ConfigError("新名称不合法")
    bad = sorted(_INVALID_NAME_CHARS & set(cleaned))
    if bad:
        raise ConfigError("新名称含非法字符：" + " ".join(bad))
    if len(cleaned) > 255:
        raise ConfigError("新名称过长（上限 255 字符）")
    return cleaned


def _reindex_paths(old: Path, new: Path) -> int:
    """路径变动后同步 ``library_files`` 索引，返回受影响行数。

    重命名目录会连带影响目录下所有已入库文件，所以这里做前缀替换而不是等值替换。
    """
    old_text = str(old)
    new_text = str(new)
    changed = 0
    with session_scope() as session:
        rows = list(session.execute(select(LibraryFile)).scalars())
        for row in rows:
            current = row.path or ""
            if os.path.normcase(current) == os.path.normcase(old_text):
                row.path = new_text
                changed += 1
            elif os.path.normcase(current).startswith(
                os.path.normcase(old_text.rstrip("\\/") + os.sep)
            ):
                row.path = new_text + current[len(old_text):]
                changed += 1
    return changed


def _forget_paths(target: Path) -> int:
    """删除后清理索引（含目录下所有条目），返回删除行数。"""
    text = str(target)
    removed = 0
    with session_scope() as session:
        rows = list(session.execute(select(LibraryFile)).scalars())
        for row in rows:
            current = row.path or ""
            if os.path.normcase(current) == os.path.normcase(text) or os.path.normcase(
                current
            ).startswith(os.path.normcase(text.rstrip("\\/") + os.sep)):
                session.delete(row)
                removed += 1
    return removed


def rename_entry(path: str | Path, new_name: str) -> dict[str, Any]:
    """重命名媒体库/下载目录里的文件或目录。"""
    source = resolve_library_path(path)
    name = _validate_name(new_name)
    target = source.parent / name
    if os.path.normcase(str(target)) == os.path.normcase(str(source)):
        return {
            "source": str(source),
            "target": str(target),
            "reindexed": 0,
            "message": "名称未变化",
        }
    # 解析一次目标，确保 name 里即使塞了怪东西也跑不出允许范围
    resolve_library_path(target, must_exist=False)
    if target.exists():
        raise ConfigError(f"目标已存在：{target}")
    try:
        source.rename(target)
    except OSError as exc:
        raise ConfigError(f"重命名失败：{exc}") from exc
    reindexed = _reindex_paths(source, target)
    logger.info("重命名 %s -> %s（同步索引 %d 条）", source, target, reindexed)
    return {
        "source": str(source),
        "target": str(target),
        "reindexed": reindexed,
        "message": f"已重命名为 {name}",
    }


def move_entry(
    path: str | Path, dest_dir: str | Path, *, overwrite: bool = False
) -> dict[str, Any]:
    """把文件或目录移动到另一个（同样受限的）目录下。"""
    source = resolve_library_path(path)
    destination = resolve_library_path(dest_dir, must_exist=False)
    if destination.exists() and not destination.is_dir():
        raise ConfigError(f"目标不是目录：{destination}")
    if source.is_dir() and _is_within(destination, source):
        raise ConfigError("不能把目录移动到它自己内部")
    target = destination / source.name
    if os.path.normcase(str(target)) == os.path.normcase(str(source)):
        return {
            "source": str(source),
            "target": str(target),
            "reindexed": 0,
            "message": "已在目标目录，无需移动",
        }
    if target.exists():
        if not overwrite:
            raise ConfigError(f"目标已存在：{target}（勾选覆盖后重试）")
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        except OSError as exc:
            raise ConfigError(f"覆盖目标失败：{exc}") from exc
    destination.mkdir(parents=True, exist_ok=True)
    try:
        # shutil.move 能跨盘（K: -> D:），Path.rename 不能，媒体库和下载盘常常不同盘
        shutil.move(str(source), str(target))
    except OSError as exc:
        raise ConfigError(f"移动失败：{exc}") from exc
    reindexed = _reindex_paths(source, target)
    logger.info("移动 %s -> %s（同步索引 %d 条）", source, target, reindexed)
    return {
        "source": str(source),
        "target": str(target),
        "reindexed": reindexed,
        "message": f"已移动到 {destination}",
    }


def delete_entry(path: str | Path, *, recursive: bool = False) -> dict[str, Any]:
    """删除文件或目录。

    删目录必须显式 ``recursive=True``：否则只允许删空目录。这样"点错一下清空整个
    媒体库"需要两个条件同时成立，而不是一个手滑。
    """
    target = resolve_library_path(path)
    if any(os.path.normcase(str(target)) == os.path.normcase(str(root)) for root in library_roots()):
        raise ConfigError(f"不允许删除根目录：{target}")
    size, count = _dir_size(target)
    if target.is_dir():
        entries = list(target.iterdir())
        if entries and not recursive:
            raise ConfigError(
                f"目录非空（{len(entries)} 个条目），需确认递归删除"
            )
        try:
            shutil.rmtree(target) if entries else target.rmdir()
        except OSError as exc:
            raise ConfigError(f"删除失败：{exc}") from exc
    else:
        try:
            target.unlink()
        except OSError as exc:
            raise ConfigError(f"删除失败：{exc}") from exc
    forgotten = _forget_paths(target)
    logger.info("删除 %s（%d 字节 / %d 文件，清理索引 %d 条）", target, size, count, forgotten)
    return {
        "path": str(target),
        "size": size,
        "files": count,
        "forgotten": forgotten,
        "message": f"已删除 {target.name}",
    }
