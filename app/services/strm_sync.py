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

import posixpath
import secrets
import time
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.core.logger import get_logger
from app.db.base import utcnow
from app.db.models import SiteConfig, StrmRecord
from app.db.session import session_scope
from app.providers.panstorage.base import BasePanStorage, IncompleteDirectory, PanFile
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


def _output_path(source: str, base: str, root: Path) -> Path | None:
    """远端路径只能映射到本轮输出目录内。"""
    try:
        relative = PurePosixPath("/" + source.lstrip("/")).relative_to(
            PurePosixPath("/" + base.lstrip("/"))
        )
    except ValueError:
        return None
    target = root / str(relative)
    return target if target.resolve().is_relative_to(root.resolve()) else None


def _link_for(record_id: int, direct: str | None, mode: str, token: str) -> str:
    """算出写进 .strm 文件的那一行内容。"""
    if mode == "direct" and direct:
        return direct
    base = str(settings.STRM_BASE_URL or "").rstrip("/")
    return f"{base}/api/v1/strm/play/{record_id}?token={token}"


#: 一次遍历的墙钟预算（秒）与目录请求上限。
#:
#: **为什么必须有**（v1.24.0 实测）：``_walk`` 原来只限递归深度（12 层），
#: 而网盘每个目录要一次 HTTP。实测一个真实百度网盘账号：
#:
#: | 范围 | 目录请求数 | 耗时 |
#: |---|---|---|
#: | 只下钻 2 层 | **127 次** | **94.6s**（平均 0.74s/次） |
#: | 默认 12 层 | 组合爆炸 | 实测 **600s 仍未返回** |
#:
#: 而 ``POST /strm/sync`` 是**同步**端点：用户点一下「全盘同步」，
#: 请求就永远不返回，浏览器超时后他不知道到底同步了没有。
#: 深度限制防的是"循环软链无限下钻"，防不住"盘子本来就很大"——
#: 这是两个不同的问题，需要各自的闸门。
#:
#: 取 90s：低于常见反代 120s 网关超时，留出写文件与响应的余量。
WALK_BUDGET_SECONDS = 90.0
#: 目录请求数上限，防止预算内被极小目录刷满请求
WALK_MAX_DIRS = 400

#: **全盘同步**（``sync_all``）的总墙钟预算。
#:
#: 光有 ``WALK_BUDGET_SECONDS`` 是不够的 —— 那是**每个盘**的预算，而
#: ``sync_all`` 要循环所有启用的网盘，最坏情况是 ``盘数 × 90s``。
#: v1.24.0 实测：加完单盘闸门后 ``POST /strm/sync`` 仍然 **180s 超时**，
#: 因为实机有 3 个启用的网盘。
#: "给每一步设上限"不等于"给整件事设上限"，循环里的上限必须是**共享**的。
SYNC_ALL_BUDGET_SECONDS = 100.0

#: 低于这个剩余预算就不再开始扫下一个盘。
#: 硬塞进去只会得到"扫了 0 个目录 → 没有视频"这种**看着像结论其实不是**的输出。
MIN_WALK_BUDGET_SECONDS = 5.0

#: 随行元数据（字幕/NFO/封面）下载的预算与条数上限。
#:
#: **为什么要单独设**（v1.24.0 实测）：给遍历加完闸门后，全盘同步实测**仍然 617s**。
#: 分阶段计时才看清同步有三个阶段，而当时只有第一个被限住：
#:
#: | 阶段 | 实测（一个真实百度网盘账号） |
#: |---|---|
#: | 遍历目录 | 90.4s —— **已有闸门**，如实截断 |
#: | 写 STRM 记录 | 782 条 × 0.6 ms ≈ 0.5s —— 可以忽略 |
#: | 下载随行元数据 | **255 个文件 × 1.4s/个 ≈ 359s** —— 完全没有闸门 |
#:
#: 教训：给"最像瓶颈的那一步"加限制不等于给整件事加限制。
#: 元数据是**可选增强**（字幕缺了照样能播），所以它最该被砍，而不是拖垮整个请求。
METADATA_BUDGET_SECONDS = 45.0
METADATA_MAX_FILES = 200

#: 单独同步一个网盘时，**全部阶段合计**的墙钟预算。
#: 压在常见 120s 网关超时之下：遍历最多 90s，剩下的留给写记录与元数据。
SYNC_STORAGE_BUDGET_SECONDS = 110.0


async def _walk(
    storage: BasePanStorage,
    path: str,
    *,
    depth: int = 0,
    max_depth: int = 12,
    budget: float = WALK_BUDGET_SECONDS,
    max_dirs: int = WALK_MAX_DIRS,
) -> tuple[list[tuple[str, PanFile]], bool]:
    """递归遍历网盘目录。

    返回 ``(条目列表, 是否被截断)``。**截断标记必须回传**：
    "只同步了一部分"和"这个盘就这么点东西"是两件事，
    不区分的话用户会以为同步完了（ADR-82 的口径，
    也是 ADR-96 里"降级要能和按预期执行区分"的同一条）。

    三道闸门：递归深度（防循环软链）、墙钟预算、目录请求数上限。
    """
    deadline = time.monotonic() + budget
    budget_state = {"dirs": 0, "truncated": False}

    async def walk(current: str, current_depth: int) -> list[tuple[str, PanFile]]:
        if current_depth > max_depth:
            budget_state["truncated"] = True
            return []
        if time.monotonic() > deadline or budget_state["dirs"] >= max_dirs:
            budget_state["truncated"] = True
            return []
        budget_state["dirs"] += 1
        collected: list[tuple[str, PanFile]] = []
        try:
            entries = await storage.list_dir_for_sync(current)
        except IncompleteDirectory as exc:
            budget_state["truncated"] = True
            entries = exc.files
        except Exception as exc:
            logger.warning("STRM 遍历目录失败 %s: %s", current, exc)
            budget_state["truncated"] = True
            return []
        for entry in entries:
            if entry.is_dir:
                collected.extend(await walk(entry.path, current_depth + 1))
            else:
                collected.append((current, entry))
        return collected

    found = await walk(path, depth)
    if budget_state["truncated"]:
        logger.warning(
            "STRM 遍历达到上限提前收工：已扫 %d 个目录 / 预算 %.0fs（盘子太大或网盘响应慢）",
            budget_state["dirs"],
            budget,
        )
    return found, budget_state["truncated"]


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
    budget: float | None = None,
) -> dict[str, Any]:
    """把某个网盘目录同步成 STRM 文件树。

    返回统计：``created`` / ``updated`` / ``removed`` / ``metadata`` / ``skipped``。

    ``budget`` 是**这次调用全部阶段**（遍历 + 写记录 + 下元数据）共享的墙钟预算。
    由 ``sync_all`` 传入它的**剩余**时间，这样多个盘共用一份墙钟而不是各自重新计时
    （见 ``SYNC_ALL_BUDGET_SECONDS``）。单独同步一个盘时用默认值
    ``SYNC_STORAGE_BUDGET_SECONDS``。
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
        # 调用方（sync_all / API）总会读这个键，因此必须在任何提前 return 之前就存在，
        # 否则"网盘不存在"这种早退路径会让调用方拿到 KeyError 而不是一个明确结论。
        "truncated": False,
        "message": "",
    }
    if not storage:
        stats["message"] = "网盘不存在或未启用"
        return stats

    mode = link_mode or settings.STRM_LINK_MODE
    if clean is None:
        clean = settings.STRM_CLEAN_INVALID

    # 一个 deadline 管住**全部**阶段。只给遍历设限是不够的 ——
    # 实测元数据阶段（359s）比遍历（90s）还慢，加完遍历闸门后全盘同步仍要 617s。
    total_budget = SYNC_STORAGE_BUDGET_SECONDS if budget is None else budget
    deadline = time.monotonic() + total_budget

    files, truncated = await _walk(
        storage, pan_path, budget=min(WALK_BUDGET_SECONDS, total_budget)
    )
    stats["truncated"] = truncated
    videos = [(parent, item) for parent, item in files if _is_video(item.name)]
    root = _strm_root().resolve()
    if strm_subdir:
        root = (root / strm_subdir).resolve()
    if not root.is_relative_to(_strm_root().resolve()):
        raise ValueError("STRM 子目录超出输出目录")
    base = storage.normalize_path(pan_path)
    if not videos:
        # 截断时"没找到视频"极可能只是还没扫到，不能说成"这里没有视频"
        stats["message"] = (
            f"{storage.site_name} 的 {pan_path} 下暂未扫到视频文件"
            f"（目录读取失败、过深、分页未读全或超出预算，只覆盖一部分目录，可指定 pan_path 重试）"
            if truncated
            else f"{storage.site_name} 的 {pan_path} 下没有找到视频文件"
        )
        if clean and not dry_run and not truncated:
            stats["removed"] = _clean_invalid(site_id, set(), base=base, root=root)
        return stats

    # STRM 目录结构镜像网盘结构；可选再套一层子目录便于多盘并存
    seen_sources: set[str] = set()

    for _parent, item in videos:
        seen_sources.add(item.path)
        # 网盘路径相对 pan_path 的部分，用来在本地重建同样的层级
        target = _output_path(item.path, base, root)
        if target is None:
            truncated = stats["truncated"] = True
            stats["skipped"] += 1
            continue
        target = target.with_suffix(".strm")
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
            record.play_token = record.play_token or secrets.token_urlsafe(32)
            play_token = record.play_token
            record.last_synced_at = utcnow()
            record_id = record.id

        direct = None
        if mode == "direct":
            try:
                direct = await storage.download_url(item.path, file_id=item.file_id)
            except Exception as exc:
                logger.debug("换取直链失败 %s: %s", item.path, exc)
        content = _link_for(record_id, direct, mode, play_token)

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

    # 随行元数据（字幕/NFO/图片）直接下载到 STRM 目录旁边。
    # 用**剩余**预算而不是固定值：整个 sync_storage 要有一个总上限，
    # 否则"遍历 90s + 元数据 45s"叠起来又会超出调用方的耐心。
    if settings.STRM_SYNC_METADATA and not dry_run:
        meta_budget = min(
            METADATA_BUDGET_SECONDS, max(0.0, deadline - time.monotonic())
        )
        stats["metadata"], meta_truncated = await _sync_metadata(
            storage, files, base, root, budget=meta_budget
        )
        if meta_truncated:
            # 元数据被砍也是截断：用户看到"字幕只下了一部分"要能知道原因
            stats["truncated"] = True
            truncated = True

    # ⚠️ 截断时**绝对不能清理**。清理的判据是"这次没见到 = 源文件已消失"，
    # 而截断意味着"很多文件只是还没扫到"。实测后果很严重：
    # 一次截断的全盘同步 `removed: 636` —— 把上一轮刚生成的 636 个 STRM 全删了，
    # 于是每跑一次就在"新增 636"和"清理 636"之间来回翻，媒体库反复丢片重扫。
    # 这是 ADR-82 的直接推论：把"没看到"当成"不存在"就会误删。
    if clean and not dry_run and not truncated:
        stats["removed"] = _clean_invalid(site_id, seen_sources, base=base, root=root)
    elif clean and truncated:
        logger.warning(
            "%s 本轮遍历被截断，跳过失效清理（避免把没扫到的文件误判成已删除）",
            storage.site_name,
        )

    stats["message"] = (
        f"{storage.site_name}：新增 {stats['created']}、更新 {stats['updated']}、"
        f"未变 {stats['skipped']}、清理 {stats['removed']}，"
        f"共 {format_size(stats['total_size'])}"
    )
    if truncated:
        stats["message"] += (
            "；⚠️ 本轮只扫了一部分（目录读取失败、过深、分页未读全或超出预算），"
            "已跳过失效清理，建议改用「指定目录同步」"
        )
    logger.info("STRM 同步完成 %s", stats["message"])
    return stats


async def _sync_metadata(
    storage: BasePanStorage,
    files: list[tuple[str, PanFile]],
    base: str,
    root: Path,
    *,
    budget: float = METADATA_BUDGET_SECONDS,
    max_files: int = METADATA_MAX_FILES,
) -> tuple[int, bool]:
    """把网盘上的字幕/NFO/图片下载到 STRM 目录（供媒体服务器直接读取）。

    返回 ``(下载成功数, 是否因预算提前收工)``。

    **有预算**：实测每个文件要 1.4s（换直链 + 下载两次网络往返），
    一个盘 255 个元数据文件就是 359s，比遍历还慢。字幕缺了片子照样能播，
    所以超预算时**砍掉这一步**而不是拖垮整个同步请求。
    """
    from app.utils.http import async_client

    deadline = time.monotonic() + budget
    count = 0
    attempted = 0
    truncated = False
    for _parent, item in files:
        if not _is_metadata(item.name):
            continue
        if time.monotonic() > deadline or attempted >= max_files:
            truncated = True
            break
        attempted += 1
        target = _output_path(item.path, base, root)
        if target is None:
            truncated = True
            continue
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
    if truncated:
        logger.warning(
            "随行元数据下载达到上限提前收工：已试 %d 个 / 成功 %d 个（预算 %.0fs）",
            attempted,
            count,
            budget,
        )
    return count, truncated


def _clean_invalid(site_id: int, seen_sources: set[str], *, base: str, root: Path) -> int:
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
            source = posixpath.normpath("/" + record.source_path.lstrip("/"))
            scope = posixpath.normpath("/" + base.lstrip("/"))
            path = Path(record.strm_path)
            if not (source == scope or source.startswith(scope.rstrip("/") + "/")):
                continue
            if not path.resolve().is_relative_to(root.resolve()):
                continue
            if record.source_path in seen_sources:
                continue
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

    # 总预算在循环外算一次，各盘**共享**剩余时间。
    # 每盘各自 90s 的话，3 个盘就是 270s —— 端点照样超时（实测 180s TIMEOUT）。
    deadline = time.monotonic() + SYNC_ALL_BUDGET_SECONDS
    skipped_for_budget: list[str] = []

    # 只挑出真正要同步的盘，用来算每盘的公平配额。
    targets: list[tuple[int, BasePanStorage]] = []
    for storage in storages:
        site_id = storage.config.get("id") or site_ids.get(storage.site_name)
        if site_id:
            targets.append((int(site_id), storage))

    for index, (site_id, storage) in enumerate(targets):
        remaining = deadline - time.monotonic()
        if remaining <= MIN_WALK_BUDGET_SECONDS:
            # 剩余时间已不足以扫出任何有意义的结果，如实记下"没轮到它"，
            # 而不是给它 1 秒钟然后报告"这个盘没有视频"（那是假结论）。
            skipped_for_budget.append(storage.site_name)
            summary["details"].append(
                {
                    "site_id": site_id,
                    "site_name": storage.site_name,
                    "created": 0,
                    "updated": 0,
                    "removed": 0,
                    "metadata": 0,
                    "skipped": 0,
                    "total_size": 0,
                    # 「没轮到扫」不是「只扫了一部分」：前者一个目录都没看过。
                    # 混成一个标记会让顶层消息把同一个盘算两遍（实测出现过）。
                    "truncated": False,
                    "not_scanned": True,
                    "message": f"{storage.site_name}：本轮总预算已用尽，未开始扫描",
                }
            )
            continue
        # **公平分配**而不是"先到先得"：按剩余盘数均分剩余时间。
        # 实测教训：一个慢盘（百度，90s）会把总预算吃干，后面那个 0.0s 就能扫完的
        # 本地目录永远轮不到 —— 排在后面的盘等于永久不同步。
        # 快盘用不完的配额会自然回到 remaining 里给后面的盘，不会浪费。
        share = remaining / max(1, len(targets) - index)
        result = await sync_storage(
            site_id,
            pan_path=storage.root_path,
            strm_subdir=storage.site_name,
            budget=min(SYNC_STORAGE_BUDGET_SECONDS, max(share, MIN_WALK_BUDGET_SECONDS)),
        )
        result.setdefault("site_name", storage.site_name)
        summary["created"] += result["created"]
        summary["removed"] += result["removed"]
        summary["details"].append(result)

    summary["skipped_storages"] = skipped_for_budget

    # 截断必须冒泡到顶层消息：只有 details 里带标记的话，
    # 用户在界面上看到的仍是一句听着像"干完了"的话。
    # 用站点**名字**而不是 ID：消息是给人看的，"站点 31, 11" 没法据此行动。
    truncated_names = [
        str(item.get("site_name") or item.get("site_id"))
        for item in summary["details"]
        if item.get("truncated")
    ]
    summary["truncated"] = bool(truncated_names) or bool(skipped_for_budget)
    summary["message"] = (
        f"共 {summary['storages']} 个网盘，新增 STRM {summary['created']}、"
        f"清理 {summary['removed']}"
    )
    if truncated_names:
        summary["message"] += (
            f"；⚠️ 其中 {len(truncated_names)} 个盘因目录太多/网盘太慢**只扫了一部分**"
            f"（{', '.join(truncated_names)}），"
            "建议改用「指定目录同步」而不是全盘同步"
        )
    if skipped_for_budget:
        summary["message"] += (
            f"；其中 {len(skipped_for_budget)} 个盘（{', '.join(skipped_for_budget)}）"
            "本轮完全没轮到，请单独同步"
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


def valid_play_token(record_id: int, token: str) -> bool:
    """播放凭据与用户登录无关，稳定到显式轮换为止。"""
    with session_scope() as session:
        record = session.get(StrmRecord, record_id)
        return bool(
            record
            and record.play_token
            and secrets.compare_digest(record.play_token.encode(), token.encode())
        )


def rewrite_play_links(*, rotate: bool = False) -> dict[str, int]:
    """无需访问网盘，重写已有 proxy STRM；可同时吊销旧凭据。"""
    stats = {"rewritten": 0, "failed": 0}
    with session_scope() as session:
        records = session.scalars(select(StrmRecord).where(StrmRecord.link_mode == "proxy")).all()
        for record in records:
            path = Path(record.strm_path)
            if not path.resolve().is_relative_to(_strm_root().resolve()):
                stats["failed"] += 1
                continue
            token = (
                secrets.token_urlsafe(32) if rotate or not record.play_token else record.play_token
            )
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(_link_for(record.id, None, "proxy", token), encoding="utf-8")
            except OSError:
                stats["failed"] += 1
                continue
            record.play_token = token
            stats["rewritten"] += 1
    return stats


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
