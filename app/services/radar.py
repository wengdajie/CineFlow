"""追新雷达：基于站点「最新资源流」的定时追新。

与 :mod:`app.services.subscribe` 的差异（两者互补）：

- **订阅巡检**：以订阅为主，逐个订阅去各站点*搜索*关键词。适合补全
  历史缺集，但巡检间隔内新发布的资源要等到下一轮才会被发现，且每个
  订阅都要发一次搜索请求。
- **追新雷达**（本模块）：以站点为主，只拉一次各站点的*最新发布流*，
  再把这批新资源与本地订阅做匹配。一次请求即可覆盖全部订阅，
  发现新集的延迟更低、对站点更友好，特别适合日更剧集/新番。

流程::

    各站点 fetch_latest() → 汇总去重 → 解析标题元数据
    → 与活跃订阅匹配（标题 + 季 + 缺集）→ 过滤打分 → 择优下载
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.core.filters import FilterRule, filter_and_rank
from app.core.logger import get_logger
from app.core.meta import parse
from app.db.base import utcnow
from app.db.models import Subscribe
from app.db.session import session_scope
from app.providers.base import Resource, SearchProvider
from app.schemas.enums import (
    EventType,
    MediaType,
    NotifyLevel,
    SubscribeStatus,
)
from app.services import download as download_service
from app.services import notify as notify_service
from app.services import search as search_service
from app.services import sites as site_service
from app.services import subscribe as subscribe_service
from app.utils.strings import normalize, truncate

logger = get_logger(__name__)


def _providers() -> list[SearchProvider]:
    """所有启用的可搜索站点（雷达只用其 fetch_latest）。"""
    return site_service.search_providers()


async def _fetch_one(provider: SearchProvider, limit: int) -> list[Resource]:
    """拉取单个站点的最新资源（失败不影响其他站点）。"""
    try:
        return await asyncio.wait_for(
            provider.fetch_latest(limit=limit), timeout=settings.SEARCH_TIMEOUT
        )
    except asyncio.TimeoutError:
        logger.warning("站点 %s 最新流超时", provider.site_name)
    except Exception as exc:
        logger.warning("站点 %s 最新流异常: %s", provider.site_name, exc)
    return []


async def fetch_feed(limit_per_site: int = 100) -> list[Resource]:
    """并发拉取所有站点的最新资源并去重。"""
    providers = _providers()
    if not providers:
        logger.info("追新雷达：没有启用的站点")
        return []

    groups = await asyncio.gather(
        *(_fetch_one(provider, limit_per_site) for provider in providers),
        return_exceptions=True,
    )
    collected: list[Resource] = []
    for group in groups:
        if isinstance(group, BaseException):
            continue
        collected.extend(group)

    unique = search_service.dedupe(collected)
    logger.info(
        "追新雷达：%d 个站点共获取 %d 条最新资源（去重后 %d 条）",
        len(providers), len(collected), len(unique),
    )
    return unique


def _title_tokens(title: str) -> set[str]:
    """生成用于匹配的标题变体集合（小写、去分隔符）。"""
    base = normalize(title).lower().strip()
    if not base:
        return set()
    tokens = {base, base.replace(" ", "")}
    return {token for token in tokens if len(token) >= 2}


def match_subscribe(
    resource_title: str, subscribes: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """把一条资源标题匹配到某个订阅。

    资源名里的片名可能是中文名、英文名或别名，因此对订阅标题与其
    别名逐一做「包含」判断，取匹配长度最长者（避免《凡人》误配
    《凡人修仙传》）。
    """
    haystack = normalize(resource_title).lower().replace(" ", "")
    if not haystack:
        return None

    best: tuple[int, dict[str, Any]] | None = None
    for item in subscribes:
        for token in item["tokens"]:
            probe = token.replace(" ", "")
            if probe and probe in haystack:
                score = len(probe)
                if best is None or score > best[0]:
                    best = (score, item)
                break
    return best[1] if best else None


def _load_active_subscribes() -> list[dict[str, Any]]:
    """载入活跃订阅及其缺集信息（脱离 Session 的纯数据）。"""
    with session_scope() as session:
        records = list(
            session.execute(
                select(Subscribe).where(
                    Subscribe.status == SubscribeStatus.ACTIVE.value
                )
            ).scalars()
        )
        payload: list[dict[str, Any]] = []
        for record in records:
            missing = subscribe_service.compute_missing(record)
            tokens = _title_tokens(record.title)
            payload.append(
                {
                    "id": record.id,
                    "title": record.title,
                    "season": record.season,
                    "media_type": record.media_type,
                    "missing": missing,
                    "tokens": tokens,
                    "rule": FilterRule.from_subscribe(record),
                    "best_version": bool(record.best_version),
                    "save_path": record.save_path,
                }
            )
        return payload

async def run(
    *, limit_per_site: int = 100, dry_run: bool = False
) -> dict[str, Any]:
    """执行一轮追新雷达。

    Args:
        limit_per_site: 每个站点最多取多少条最新资源。
        dry_run: 只匹配不下载（供前端「预览」使用）。

    Returns:
        本轮统计：资源数、命中订阅数、新增下载等。
    """
    started = time.perf_counter()
    summary: dict[str, Any] = {
        "resources": 0,
        "subscribes": 0,
        "matched": 0,
        "downloads": [],
        "skipped": [],
        "dry_run": dry_run,
    }

    subscribes = _load_active_subscribes()
    summary["subscribes"] = len(subscribes)
    if not subscribes:
        logger.info("追新雷达：没有活跃订阅，跳过")
        return summary

    feed = await fetch_feed(limit_per_site)
    summary["resources"] = len(feed)
    if not feed:
        return summary

    # 按订阅归组候选资源
    grouped: dict[int, list[dict[str, Any]]] = {}
    index = {item["id"]: item for item in subscribes}
    for resource in feed:
        target = match_subscribe(resource.title, subscribes)
        if target is None:
            continue
        payload = resource.to_dict()
        payload["_meta"] = parse(resource.title)
        grouped.setdefault(target["id"], []).append(payload)

    for subscribe_id, candidates in grouped.items():
        item = index[subscribe_id]
        missing = set(item["missing"])
        if not missing:
            continue

        rule: FilterRule = item["rule"]
        rule.episodes = sorted(missing)
        rule.season = item["season"] if item["media_type"] != MediaType.MOVIE.value else None
        ranked = filter_and_rank(candidates, rule)
        if not ranked:
            summary["skipped"].append(
                {"subscribe_id": subscribe_id, "title": item["title"],
                 "reason": "最新资源均未通过过滤规则", "candidates": len(candidates)}
            )
            continue

        summary["matched"] += 1
        picked = _pick(ranked, missing, item)
        for resource in picked:
            info = resource.pop("_meta", None)
            if info is not None:
                resource["meta"] = info.to_dict()
            entry = {
                "subscribe_id": subscribe_id,
                "subscribe": item["title"],
                "title": resource.get("title"),
                "site": resource.get("site"),
                "kind": resource.get("kind"),
                "score": resource.get("score"),
                "episodes": (resource.get("meta") or {}).get("episodes"),
            }
            if dry_run:
                summary["downloads"].append({**entry, "dry_run": True})
                continue
            task = await download_service.add_download(
                resource, subscribe_id=subscribe_id, save_path=item["save_path"]
            )
            if task:
                summary["downloads"].append(entry)

        if picked and not dry_run:
            with session_scope() as session:
                record = session.get(Subscribe, subscribe_id)
                if record:
                    record.last_check_at = utcnow()
                    record.last_matched_at = utcnow()

    elapsed = int((time.perf_counter() - started) * 1000)
    summary["elapsed_ms"] = elapsed
    logger.info(
        "追新雷达完成：%d 条资源 / 命中 %d 个订阅 / 新增 %d 个下载 / %dms",
        summary["resources"], summary["matched"], len(summary["downloads"]), elapsed,
    )

    if summary["downloads"] and not dry_run:
        lines = [
            f"· {truncate(entry['subscribe'], 20)} — {truncate(entry['title'], 48)}"
            for entry in summary["downloads"][:5]
        ]
        await notify_service.send(
            f"追新雷达：新增 {len(summary['downloads'])} 个下载",
            "\n".join(lines),
            level=NotifyLevel.SUCCESS.value,
            event=EventType.RESOURCE_MATCHED.value,
            payload={"source": "radar", "count": len(summary["downloads"])},
        )
    return summary


def _pick(
    ranked: list[dict[str, Any]], missing: set[int], item: dict[str, Any]
) -> list[dict[str, Any]]:
    """从排序后的候选中挑出能补齐缺集的资源。"""
    remaining = set(missing)
    picked: list[dict[str, Any]] = []
    for resource in ranked:
        if not remaining:
            break
        info = resource.get("_meta")
        episodes = set(getattr(info, "episodes", None) or [])
        is_pack = bool(getattr(info, "is_season_pack", False))

        if item["media_type"] == MediaType.MOVIE.value:
            picked.append(resource)
            break
        if episodes:
            useful = episodes & remaining
            if not useful:
                continue
        elif is_pack:
            useful = set(remaining)
        else:
            continue

        picked.append(resource)
        remaining -= useful
        if item["best_version"]:
            break
    return picked
