"""洗版：发现明显更优的版本时替换已入库文件。

**场景**：追剧时经常先下到 1080p WEB-DL 应急，几天后才出 2160p 蓝光。
手动替换要「删旧文件 → 重下 → 重命名 → 刷新媒体库」，很烦。

**为什么默认关闭**（``CF_UPGRADE_ENABLED=False``）：洗版会**删除**已入库文件，
是本项目里唯一会主动删用户数据的功能。必须用户明确知情后才打开。

**防止无限横跳**（这是洗版最容易做错的地方）：
1. 新资源评分必须超出已有版本 ``CF_UPGRADE_SCORE_DELTA`` 分（默认 15）才动，
   避免两个评分接近的版本反复互相替换；
2. 每个文件最多洗 ``CF_UPGRADE_MAX_TIMES`` 次（默认 2），用尽即锁定；
3. 只有 ``subscribes.best_version=True`` 的订阅才参与洗版。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.core.filters import FilterRule, filter_and_rank, score_resource
from app.core.logger import get_logger
from app.core.meta import parse
from app.db.models import LibraryFile, Subscribe
from app.db.session import session_scope
from app.schemas.enums import EventType, NotifyLevel, SubscribeStatus
from app.utils.strings import truncate

logger = get_logger(__name__)


def evaluate(
    current_score: float,
    candidate_score: float,
    *,
    upgrade_count: int = 0,
    delta: float | None = None,
    max_times: int | None = None,
) -> tuple[bool, str]:
    """判断是否值得洗版。纯函数，便于穷举测试各种边界。"""
    threshold = settings.UPGRADE_SCORE_DELTA if delta is None else delta
    limit = settings.UPGRADE_MAX_TIMES if max_times is None else max_times

    if upgrade_count >= max(limit, 0):
        return False, f"已洗版 {upgrade_count} 次，达到上限 {limit}"
    gain = candidate_score - current_score
    if gain < threshold:
        return False, f"提升 {gain:.1f} 分，未达阈值 {threshold:.1f}"
    return True, f"提升 {gain:.1f} 分（阈值 {threshold:.1f}）"


def _library_candidates(subscribe: Subscribe) -> list[dict[str, Any]]:
    """列出该订阅已入库的文件（洗版的比较基准）。"""
    with session_scope() as session:
        rows = session.execute(
            select(LibraryFile).where(LibraryFile.title == subscribe.title)
        ).scalars()
        items = []
        for row in rows:
            if subscribe.season and row.season and row.season != subscribe.season:
                continue
            items.append(
                {
                    "id": row.id,
                    "path": row.path,
                    "season": row.season,
                    "episode": row.episode,
                    "resolution": row.resolution,
                    "size": row.size,
                    "quality_score": row.quality_score or 0.0,
                    "upgrade_count": row.upgrade_count or 0,
                }
            )
        return items


def _score_existing(item: dict[str, Any]) -> float:
    """给已入库文件算一个可比较的分数。

    历史入库的文件没存过评分（``quality_score=0``），这时按文件名重新算，
    保证老库也能参与洗版判断。
    """
    if item["quality_score"] > 0:
        return item["quality_score"]
    name = Path(item["path"]).name
    return score_resource(
        {"title": name, "size": item["size"], "seeders": 0, "kind": "torrent"}
    )


async def check_subscribe(subscribe_id: int, *, dry_run: bool = False) -> dict[str, Any]:
    """为单个订阅寻找更优版本。

    返回 ``candidates``（每集的洗版决策），``upgraded`` 为实际提交下载的数量。
    """
    from app.services import download as download_service
    from app.services import search as search_service

    result: dict[str, Any] = {
        "subscribe_id": subscribe_id,
        "upgraded": 0,
        "skipped": 0,
        "candidates": [],
        "message": "",
    }

    with session_scope() as session:
        subscribe = session.get(Subscribe, subscribe_id)
        if not subscribe:
            result["message"] = "订阅不存在"
            return result
        if not subscribe.best_version:
            result["message"] = "该订阅未开启「最优版本」，跳过洗版"
            return result
        snapshot = {
            "id": subscribe.id,
            "title": subscribe.title,
            "year": subscribe.year,
            "season": subscribe.season,
            "media_type": subscribe.media_type,
            "save_path": subscribe.save_path,
        }
        rule = FilterRule.from_subscribe(subscribe)
        existing = _library_candidates(subscribe)

    if not existing:
        result["message"] = "媒体库中还没有该剧集的文件，无需洗版"
        return result

    resources = await search_service.search(
        snapshot["title"],
        media_type=snapshot["media_type"],
        season=snapshot["season"],
        rule=rule,
    )
    ranked = filter_and_rank(resources, rule)
    if not ranked:
        result["message"] = "没有搜到可用资源"
        return result

    # 按集号归组，逐集比较（整季包按季比较）
    for item in existing:
        best = None
        for resource in ranked:
            info = parse(str(resource.get("title") or ""))
            # 集号不匹配就跳过；季包例外（季包能覆盖任意单集）
            mismatch = (
                item["episode"]
                and info.episodes
                and item["episode"] not in info.episodes
            )
            if mismatch and not info.is_season_pack:
                continue
            best = resource
            break
        if not best:
            continue

        current = _score_existing(item)
        candidate = float(best.get("score") or score_resource(best, rule))
        should, reason = evaluate(
            current, candidate, upgrade_count=item["upgrade_count"]
        )
        decision = {
            "library_file": item["path"],
            "episode": item["episode"],
            "current_score": round(current, 2),
            "candidate": truncate(str(best.get("title") or ""), 90),
            "candidate_score": round(candidate, 2),
            "upgrade": should,
            "reason": reason,
        }
        result["candidates"].append(decision)

        if not should:
            result["skipped"] += 1
            continue
        if dry_run:
            result["upgraded"] += 1
            continue

        # 提交下载。**注意不在这里删旧文件**——要等新文件真正入库后再删，
        # 否则下载失败就会留下一个空洞。删除动作在 library 转移完成时执行。
        # 把洗版上下文塞进 resource["extra"]，下载完成整理时据此删旧文件
        extra = dict(best.get("extra") or {})
        extra.update(
            {
                "upgrade_for": item["path"],
                "upgrade_from_score": round(current, 2),
                "library_file_id": item["id"],
            }
        )
        best["extra"] = extra
        task = await download_service.add_download(
            best,
            subscribe_id=snapshot["id"],
            save_path=snapshot["save_path"],
        )
        if task is not None:
            result["upgraded"] += 1
            logger.info(
                "洗版已提交：%s → %s（%s）",
                Path(item["path"]).name,
                decision["candidate"],
                reason,
            )
        else:
            result["skipped"] += 1

    result["message"] = (
        f"{snapshot['title']}：检查 {len(existing)} 个已入库文件，"
        f"提交洗版 {result['upgraded']} 个，跳过 {result['skipped']} 个"
    )
    return result


def replace_library_file(
    old_path: str, new_path: str, *, new_score: float = 0.0, delete_old: bool = True
) -> dict[str, Any]:
    """新版本入库成功后，替换掉旧文件记录并删除旧文件。

    这一步刻意与「提交下载」分离：只有新文件确实落地了才删旧的，
    避免下载失败留下空洞。
    """
    outcome: dict[str, Any] = {"deleted": False, "message": ""}
    old = Path(old_path)
    with session_scope() as session:
        record = session.execute(
            select(LibraryFile).where(LibraryFile.path == str(old_path))
        ).scalar_one_or_none()
        if record:
            record.path = str(new_path)
            record.quality_score = new_score
            record.upgrade_count = (record.upgrade_count or 0) + 1

    if delete_old and old.exists() and str(old) != str(new_path):
        try:
            old.unlink()
            outcome["deleted"] = True
            outcome["message"] = f"已删除旧版本 {old.name}"
        except OSError as exc:
            outcome["message"] = f"删除旧版本失败：{exc}"
            logger.warning("洗版删除旧文件失败 %s: %s", old, exc)
    else:
        outcome["message"] = "旧文件不存在或与新文件相同，未删除"
    return outcome


async def run(*, limit: int = 20, dry_run: bool = False, notify: bool = True) -> dict[str, Any]:
    """巡检所有开启了「最优版本」的订阅（供定时任务调用）。"""
    stats: dict[str, Any] = {"checked": 0, "upgraded": 0, "details": []}
    if not settings.UPGRADE_ENABLED and not dry_run:
        stats["message"] = "洗版未启用（CF_UPGRADE_ENABLED=false）"
        return stats

    with session_scope() as session:
        ids = [
            row.id
            for row in session.execute(
                select(Subscribe)
                .where(
                    Subscribe.best_version.is_(True),
                    Subscribe.status == SubscribeStatus.ACTIVE.value,
                )
                .limit(max(limit, 1))
            ).scalars()
        ]

    for subscribe_id in ids:
        outcome = await check_subscribe(subscribe_id, dry_run=dry_run)
        stats["checked"] += 1
        stats["upgraded"] += outcome["upgraded"]
        stats["details"].append(outcome)

    stats["message"] = (
        f"洗版巡检：检查 {stats['checked']} 个订阅，提交 {stats['upgraded']} 个更优版本"
    )
    logger.info(stats["message"])

    if notify and stats["upgraded"]:
        from app.services import notify as notify_service

        await notify_service.send(
            f"发现 {stats['upgraded']} 个更优版本，已提交洗版",
            stats["message"],
            level=NotifyLevel.INFO.value,
            event=EventType.RESOURCE_MATCHED.value,
        )
    return stats
