"""聚合搜索服务：并发查询所有 BT 站点与盘搜，去重、过滤、打分。"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.core.config import settings
from app.core.filters import FilterRule, filter_and_rank
from app.core.logger import get_logger
from app.db.models import ResourceRecord, SearchHistory
from app.db.session import session_scope
from app.providers.base import Resource, SearchProvider
from app.schemas.enums import MediaType
from app.services import sites as site_service
from app.utils.strings import normalize

logger = get_logger(__name__)


def build_keywords(
    title: str,
    *,
    media_type: str | None = None,
    season: int | None = None,
    episode: int | None = None,
    extra: list[str] | None = None,
) -> list[str]:
    """生成多组搜索关键词，提升命中率。

    例如追剧《庆余年》第 2 季第 5 集会生成：
    ``庆余年 S02E05`` / ``庆余年 第二季`` / ``庆余年``
    """
    base = normalize(title).strip()
    if not base:
        return []

    keywords: list[str] = []
    is_tv = media_type in (MediaType.TV.value, MediaType.ANIME.value)

    if is_tv and season is not None and episode is not None:
        keywords.append(f"{base} S{season:02d}E{episode:02d}")
    if is_tv and season is not None:
        keywords.append(f"{base} S{season:02d}")
        if season > 1:
            keywords.append(f"{base} 第{season}季")
    keywords.append(base)

    for item in extra or []:
        cleaned = normalize(item).strip()
        if cleaned:
            keywords.append(cleaned)

    # 去重且保持顺序
    seen: set[str] = set()
    unique = []
    for keyword in keywords:
        lowered = keyword.lower()
        if lowered not in seen:
            seen.add(lowered)
            unique.append(keyword)
    return unique


async def _search_one(
    provider: SearchProvider,
    keywords: list[str],
    *,
    media_type: str | None,
    season: int | None,
    episode: int | None,
    semaphore: asyncio.Semaphore,
) -> list[Resource]:
    """在单个站点上按关键词依次尝试，命中即止。"""
    async with semaphore:
        for keyword in keywords:
            try:
                results = await asyncio.wait_for(
                    provider.search(
                        keyword,
                        media_type=media_type,
                        season=season,
                        episode=episode,
                    ),
                    timeout=settings.SEARCH_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.warning("站点 %s 搜索超时: %s", provider.site_name, keyword)
                continue
            except Exception as exc:
                logger.warning("站点 %s 搜索异常: %s", provider.site_name, exc)
                continue
            if results:
                logger.info(
                    "站点 %s 命中 %d 条（关键词: %s）",
                    provider.site_name,
                    len(results),
                    keyword,
                )
                return results
        return []


def dedupe(resources: list[Resource]) -> list[Resource]:
    """跨站去重（按 unique_key）。"""
    seen: set[str] = set()
    unique: list[Resource] = []
    for resource in resources:
        key = resource.unique_key
        if key in seen:
            continue
        seen.add(key)
        unique.append(resource)
    return unique


async def search(
    title: str,
    *,
    media_type: str | None = None,
    season: int | None = None,
    episode: int | None = None,
    rule: FilterRule | None = None,
    rule_group_id: int | None = None,
    providers: list[SearchProvider] | None = None,
    extra_keywords: list[str] | None = None,
    save_history: bool = True,
) -> list[dict[str, Any]]:
    """聚合搜索入口，返回按分数排序的资源字典列表。"""
    started = time.perf_counter()
    keywords = build_keywords(
        title,
        media_type=media_type,
        season=season,
        episode=episode,
        extra=extra_keywords,
    )
    if not keywords:
        return []

    active_providers = providers if providers is not None else site_service.search_providers()
    if not active_providers:
        logger.warning("没有可用的搜索站点，请先在站点管理中添加")
        return []

    semaphore = asyncio.Semaphore(max(settings.SEARCH_CONCURRENCY, 1))
    tasks = [
        _search_one(
            provider,
            keywords,
            media_type=media_type,
            season=season,
            episode=episode,
            semaphore=semaphore,
        )
        for provider in active_providers
    ]
    grouped = await asyncio.gather(*tasks, return_exceptions=True)

    collected: list[Resource] = []
    for group in grouped:
        if isinstance(group, Exception):
            continue
        collected.extend(group)

    collected = dedupe(collected)[: settings.SEARCH_MAX_RESULTS * 2]

    # 标题相关性过滤：避免站点返回大量无关结果
    effective_rule = rule or FilterRule()
    if not effective_rule.title_keywords:
        effective_rule.title_keywords = _title_variants(title)

    payload = [resource.to_dict() for resource in collected]
    # 规则组（有序偏好）在硬过滤与评分之后生效，只影响排序/兜底剔除
    group = None
    try:
        from app.services import rule_groups as rule_group_service

        group = rule_group_service.load_group(rule_group_id)
    except Exception as exc:  # pragma: no cover - 规则组不可用时退回纯评分
        logger.warning("加载过滤规则组失败，本次仅按评分排序: %s", exc)
    ranked = filter_and_rank(payload, effective_rule, group)[: settings.SEARCH_MAX_RESULTS]

    for item in ranked:
        info = item.pop("_meta", None)
        if info is not None:
            item["meta"] = info.to_dict()

    elapsed = int((time.perf_counter() - started) * 1000)
    logger.info(
        "搜索「%s」完成：%d 站点 / 原始 %d 条 / 命中 %d 条 / %dms",
        title,
        len(active_providers),
        len(collected),
        len(ranked),
        elapsed,
    )

    if save_history:
        _save_results(title, media_type, ranked, active_providers, elapsed)
    return ranked


def _title_variants(title: str) -> list[str]:
    """生成标题匹配变体（用于相关性过滤）。"""
    import re

    base = normalize(title).strip()
    if not base:
        return []
    variants = {base}
    # 中英混合标题：中英文各自成为一个变体
    chinese = "".join(re.findall(r"[\u4e00-\u9fff0-9]+", base))
    if len(chinese) >= 2:
        variants.add(chinese)
    english = " ".join(re.findall(r"[A-Za-z]+", base))
    if len(english) >= 3:
        variants.add(english)
        variants.add(english.replace(" ", "."))
        variants.add(english.replace(" ", ""))
    return [re.escape(item) for item in variants if item]


def _save_results(
    keyword: str,
    media_type: str | None,
    resources: list[dict[str, Any]],
    providers: list[SearchProvider],
    elapsed_ms: int,
) -> None:
    """缓存搜索结果与历史，便于前端二次操作与统计。"""
    try:
        with session_scope() as session:
            session.add(
                SearchHistory(
                    keyword=keyword,
                    media_type=media_type,
                    result_count=len(resources),
                    sites=[provider.site_name for provider in providers],
                    elapsed_ms=elapsed_ms,
                )
            )
            existing = {
                row.unique_key
                for row in session.query(ResourceRecord.unique_key).all()
            }
            for item in resources[:100]:
                key = item.get("unique_key")
                if not key or key in existing:
                    continue
                info = item.get("meta") or {}
                session.add(
                    ResourceRecord(
                        unique_key=key,
                        title=item.get("title", "")[:500],
                        kind=item.get("kind", ""),
                        site=item.get("site", "")[:128],
                        link=item.get("link", ""),
                        page_url=item.get("page_url"),
                        size=int(item.get("size") or 0),
                        seeders=int(item.get("seeders") or 0),
                        leechers=int(item.get("leechers") or 0),
                        media_type=info.get("media_type") or MediaType.UNKNOWN.value,
                        season=info.get("season"),
                        episodes=info.get("episodes") or [],
                        resolution=info.get("resolution"),
                        quality=info.get("quality"),
                        video_codec=info.get("video_codec"),
                        audio_codec=info.get("audio_codec"),
                        release_group=info.get("release_group"),
                        score=float(item.get("score") or 0),
                        meta={"password": item.get("password")},
                    )
                )
                existing.add(key)
    except Exception as exc:  # pragma: no cover - 缓存失败不影响搜索
        logger.warning("保存搜索结果失败: %s", exc)


async def search_for_subscribe(subscribe: Any, missing: list[int] | None = None) -> list[dict[str, Any]]:
    """针对订阅生成搜索请求（自动带上缺集与过滤规则）。"""
    rule = FilterRule.from_subscribe(subscribe)
    rule.title_keywords = _title_variants(subscribe.title)
    if missing:
        rule.episodes = missing

    episode = missing[0] if missing and len(missing) == 1 else None
    return await search(
        subscribe.title,
        media_type=subscribe.media_type,
        season=subscribe.season,
        episode=episode,
        rule=rule,
        # 订阅可以绑定自己的规则组；没绑就由 load_group 退回全局默认组
        rule_group_id=getattr(subscribe, "rule_group_id", None),
        save_history=False,
    )
