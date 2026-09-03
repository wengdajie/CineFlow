"""聚合搜索服务：并发查询所有 BT 站点与盘搜，去重、过滤、打分。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from typing import Any

from app.core.config import settings
from app.core.filters import FilterRule, filter_and_rank
from app.core.logger import get_logger
from app.db.models import ResourceRecord, SearchHistory
from app.db.session import session_scope
from app.providers.base import Resource, SearchProvider
from app.schemas.enums import MediaType
from app.services import search_breaker
from app.services import sites as site_service
from app.utils.http import FetchError
from app.utils.strings import normalize

logger = get_logger(__name__)

#: 需要随资源一起缓存的「作品级」元数据字段（封面墙 / 榜单展示用）。
#: 白名单而非整份 extra：extra 里还有 provider、detail_id 这类内部字段，
#: 存进库既占空间又会让榜单逻辑依赖实现细节。
_MEDIA_META_KEYS = frozenset({
    "poster",
    "rating",
    "rating_people",
    "year",
    "genres",
    "area",
    "total_episodes",
    "overview",
    "actors",
    "director",
    "status_text",
    "definition",
    "alias",
})


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


@dataclass
class SiteOutcome:
    """单个站点这一轮搜索的结果与原因。

    以前失败被 ``except: continue`` 静默吞掉，前端只看到"启用了站点但结果里没它"，
    根本分不清是「站点挂了」「关键词不匹配」还是「被配额挤掉了」。
    把原因显式带出来，界面才能给出可行动的提示。
    """

    site: str
    #: ok / empty / timeout / error / skipped（skipped = 被熔断器跳过）
    status: str = "ok"
    #: 站点原始返回条数（配额裁剪前）
    raw: int = 0
    #: 实际参与聚合的条数（配额裁剪后）
    kept: int = 0
    #: 命中的关键词
    keyword: str = ""
    message: str = ""
    elapsed_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


#: 单个关键词至少要留这么多秒才值得发请求；余额不足直接放弃，
#: 避免用 0.01s 的超时去捣站点（只会百分之百超时，白白多一次请求）。
_MIN_KEYWORD_TIMEOUT = 1.0


async def _search_one(
    provider: SearchProvider,
    keywords: list[str],
    *,
    media_type: str | None,
    season: int | None,
    episode: int | None,
    semaphore: asyncio.Semaphore,
) -> tuple[list[Resource], SiteOutcome]:
    """在单个站点上按关键词依次尝试，命中即止。

    返回 ``(资源列表, 诊断)``。即使失败也返回诊断，好让调用方汇总展示。
    """
    outcome = SiteOutcome(site=provider.site_name)
    started = time.perf_counter()
    last_error = ""
    #: 是否出现过「服务明确不可用」（502/连不上/DNS 失败）。
    #: 与超时分开记：硬失败要单独进熔断计数，否则已死的站会永远拖慢每次搜索。
    hard_failure = False
    # 熔断中的站点直接跳过：它上几轮都把整个预算吃光且没有任何结果，
    # 继续带着它只会让每次搜索都多等一个 SEARCH_TIMEOUT（实测 25s）。
    # 注意要如实写进诊断，不能静默消失（ADR-20）。
    if search_breaker.is_open(provider.site_name):
        outcome.status = "skipped"
        outcome.message = search_breaker.skip_reason(provider.site_name)
        return [], outcome
    async with semaphore:
        # 预算制：SEARCH_TIMEOUT 是【整个站点】的上限，而不是每个关键词各给一份。
        # 一个卡死的站点以前 = len(keywords) × SEARCH_TIMEOUT（带季集时 3×25=75s），
        # 而 asyncio.gather 要等最慢的一个 → 整个聚合搜索被单一废站拖死。
        budget = float(max(settings.SEARCH_TIMEOUT, 1))
        for keyword in keywords:
            remaining = budget - (time.perf_counter() - started)
            if remaining < _MIN_KEYWORD_TIMEOUT:
                # 预算耗尽：剩下的关键词不再尝试，否则等于继续拖块整体进度。
                # 注意：只有真的花完了时间才会走到这里；
                # 【站点很快返回空、需要试下一个关键词】这个正常路径不受影响。
                logger.warning(
                    "站点 %s 超时预算耗尽，剩余关键词不再尝试（预算 %.0fs）",
                    provider.site_name,
                    budget,
                )
                if outcome.status != "error":
                    outcome.status = "timeout"
                last_error = last_error or f"站点超时预算耗尽（>{settings.SEARCH_TIMEOUT}s）"
                break
            try:
                results = await asyncio.wait_for(
                    provider.search(
                        keyword,
                        media_type=media_type,
                        season=season,
                        episode=episode,
                    ),
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                logger.warning("站点 %s 搜索超时: %s", provider.site_name, keyword)
                outcome.status = "timeout"
                last_error = f"搜索超时（站点预算 >{settings.SEARCH_TIMEOUT}s）"
                continue
            except FetchError as exc:
                # 站点/服务层面的失败（502、超时、TLS、域名失效…）。
                # 这里必须**保留 exc.message**：它已经是"能照着做下一步"的中文，
                # 再包一层 FetchError: 前缀反而把有用信息埋掉。
                logger.warning("站点 %s 请求失败: %s", provider.site_name, exc.message)
                outcome.status = "error"
                last_error = exc.message[:200]
                hard_failure = True
                continue
            except Exception as exc:
                logger.warning("站点 %s 搜索异常: %s", provider.site_name, exc)
                outcome.status = "error"
                last_error = f"{type(exc).__name__}: {exc}"[:200]
                continue
            if results:
                logger.info(
                    "站点 %s 命中 %d 条（关键词: %s）",
                    provider.site_name,
                    len(results),
                    keyword,
                )
                outcome.status = "ok"
                outcome.raw = len(results)
                outcome.keyword = keyword
                outcome.message = f"命中 {len(results)} 条"
                outcome.elapsed_ms = int((time.perf_counter() - started) * 1000)
                search_breaker.record_success(provider.site_name)
                return results, outcome

        # 所有关键词都试过了：区分「真的没有」和「一直在报错」
        if outcome.status == "ok":
            outcome.status = "empty"
            outcome.message = "连通正常，但没有匹配结果"
        else:
            outcome.message = last_error or "搜索失败"
        outcome.elapsed_ms = int((time.perf_counter() - started) * 1000)
        # 熔断计数只认「吃满预算且零结果」这一种情况：
        # 慢但有结果的站（如盘搜）不该被剔掉，快速返回空的站（冷门片）更不该。
        budget_ms = budget * 1000
        if outcome.status == "timeout" and outcome.elapsed_ms >= budget_ms * 0.9:
            if search_breaker.record_timeout(
                provider.site_name, outcome.elapsed_ms, "连续吃满超时预算且无结果"
            ):
                outcome.message += "（已触发熔断，稍后自动重试）"
        elif outcome.status == "error" and hard_failure:
            # 服务明确不可用（如 Jackett 挂掉返回 502）。实测这种站每次搜索
            # 都要先撞 3.4s 才放弃，且永远不会自愈，必须进熔断计数，
            # 否则它会给**每一次**聚合搜索都加上这份固定开销。
            if search_breaker.record_failure(
                provider.site_name, outcome.elapsed_ms, last_error or "服务不可用"
            ):
                outcome.message += "（已触发熔断，稍后自动重试）"
        elif outcome.status == "empty":
            search_breaker.record_success(provider.site_name)
        return [], outcome


def apply_site_quota(
    grouped: list[tuple[list[Resource], SiteOutcome]],
    quota: int,
) -> list[Resource]:
    """轮转交错合并各站点结果，避免单站刷满整个结果集。

    为什么需要：聚合层原先直接 ``extend`` 后做全局截断，于是**返回顺序决定生死**
    ——返回量大的站点会把小站整体挤出结果（实测盘搜 120 条 + 影视站 90 条，
    最终榜上影视站只剩 25 条，用户观感就是"只有盘搜"）。

    关键点：公平性由**轮转交错**保证，而不是靠砍量。交错后每个站点的第 1 条
    都排在最前面，即使后面再被全局上限截断，各站也都有靠前的曝光位。
    这样修复"被挤掉"的同时不会损失结果总数。

    ``quota`` 只是**安全阀**，防某个站点返回上万条把后续过滤/入库拖死；
    <=0 表示不限制。它取值应远大于常规返回量，不参与日常公平性调节。
    """
    buckets: list[list[Resource]] = []
    for resources, outcome in grouped:
        limited = resources[:quota] if quota > 0 else list(resources)
        outcome.kept = len(limited)
        if limited:
            buckets.append(limited)

    # 轮转交错：站点 A 第 1 条、站点 B 第 1 条、站点 A 第 2 条……
    merged: list[Resource] = []
    for index in range(max((len(bucket) for bucket in buckets), default=0)):
        for bucket in buckets:
            if index < len(bucket):
                merged.append(bucket[index])
    return merged


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


def enforce_site_share(ranked: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """全局截断前先保证**每个站点都留有名额**，返回长度不超过 ``limit``。

    **为什么必须有这一步**：``apply_site_quota()`` 的轮转交错只在**排序之前**
    成立，而 :func:`filter_and_rank` 紧接着会按分数做一次**全局重排**——
    交错出来的公平顺序当场被打散，再按 ``limit`` 一刀切下去，
    评分体系天然偏低的站点会被**整站抹掉**。

    实测（复刻线上比例：Mukaku 217 / PanSou 537 / Nyaa 75 / B站 20 / YouTube 20）：
    交错后 5 个站点都在，排序截断后只剩 PanSou 142 + Mukaku 58，
    **Nyaa / Bilibili / YouTube 三个站点一条都不剩** ——
    用户观感就是「明明开了 6 个站，结果只有一两个站的东西」。

    评分低不代表没用：网盘资源没有做种数、网页视频没有分辨率标签，
    它们在通用评分里天然吃亏，但恰恰可能是用户唯一能下到的来源。

    做法：按站点分桶（**桶内保持已排好的分数序**），再轮转取出。
    这样既保住高分资源整体靠前，又让每个站点都有靠前的曝光位。
    ``limit <= 0`` 表示不限制。
    """
    if limit <= 0 or len(ranked) <= limit:
        return ranked

    buckets: dict[str, list[dict[str, Any]]] = {}
    for item in ranked:
        # 站点名可能带 "·分组" 后缀（盘搜按网盘类型分组），按主站名归并，
        # 否则一个站点的多个分组会各自占一份名额，等于变相加权
        site = str(item.get("site") or "")
        buckets.setdefault(site.split("·")[0], []).append(item)

    if len(buckets) <= 1:
        return ranked[:limit]

    merged: list[dict[str, Any]] = []
    ordered = list(buckets.values())
    for index in range(max(len(bucket) for bucket in ordered)):
        for bucket in ordered:
            if index < len(bucket):
                merged.append(bucket[index])
                if len(merged) >= limit:
                    return merged
    return merged


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
    """聚合搜索入口，返回按分数排序的资源字典列表。

    只要结果，用本函数；想同时拿到每个站点的成败原因，用 ``search_detailed``。
    """
    results, _ = await search_detailed(
        title,
        media_type=media_type,
        season=season,
        episode=episode,
        rule=rule,
        rule_group_id=rule_group_id,
        providers=providers,
        extra_keywords=extra_keywords,
        save_history=save_history,
    )
    return results


async def search_detailed(
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
) -> tuple[list[dict[str, Any]], list[SiteOutcome]]:
    """同 ``search``，但额外返回每个站点的诊断结果。"""
    started = time.perf_counter()
    keywords = build_keywords(
        title,
        media_type=media_type,
        season=season,
        episode=episode,
        extra=extra_keywords,
    )
    if not keywords:
        return [], []

    active_providers = providers if providers is not None else site_service.search_providers()
    if not active_providers:
        logger.warning("没有可用的搜索站点，请先在站点管理中添加")
        return [], []

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

    pairs: list[tuple[list[Resource], SiteOutcome]] = []
    for provider, group in zip(active_providers, grouped, strict=False):
        if isinstance(group, Exception):
            # gather 里逃出来的异常也要有名有姓，不能只当作"这个站没结果"
            pairs.append((
                [],
                SiteOutcome(
                    site=provider.site_name,
                    status="error",
                    message=f"{type(group).__name__}: {group}"[:200],
                ),
            ))
            continue
        pairs.append(group)

    collected = apply_site_quota(pairs, settings.SEARCH_MAX_PER_SITE)
    collected = dedupe(collected)[: settings.SEARCH_MAX_RESULTS * 2]
    outcomes = [outcome for _, outcome in pairs]

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
    ranked = filter_and_rank(payload, effective_rule, group)
    ranked = enforce_site_share(ranked, settings.SEARCH_MAX_RESULTS)

    for item in ranked:
        info = item.pop("_meta", None)
        if info is not None:
            item["meta"] = info.to_dict()

    elapsed = int((time.perf_counter() - started) * 1000)
    healthy = sum(1 for outcome in outcomes if outcome.status == "ok")
    logger.info(
        "搜索「%s」完成：%d/%d 站点有结果 / 原始 %d 条 / 命中 %d 条 / %dms",
        title,
        healthy,
        len(active_providers),
        len(collected),
        len(ranked),
        elapsed,
    )
    for outcome in outcomes:
        if outcome.status != "ok":
            logger.info("  站点 %s：%s（%s）", outcome.site, outcome.message, outcome.status)

    if save_history:
        _save_results(title, media_type, ranked, active_providers, elapsed)
    return ranked, outcomes


async def search_stream(
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
) -> AsyncIterator[dict[str, Any]]:
    """流式聚合搜索：**每个站点一返回就立刻产出一批结果**。

    为什么需要它：``search_detailed`` 用 ``asyncio.gather`` 等**所有**站点收齐才
    返回一次。于是整页的等待时间 = 最慢那个站点的耗时（预算上限 25s），
    哪怕最快的站 300ms 就出货了，用户也得干等——观感就是「搜索很慢」。
    实际上开的站越多，越可能有一个慢站，体验反而越差。

    产出的事件（供上层逐条 JSON 编码下发）：

    - ``start``：本次要查哪些站点，让前端先把骨架和进度画出来
    - ``site``：某个站点的结果（已过滤打分并跨站去重），带该站诊断
    - ``done``：全部结束，附完整诊断与耗时

    公平性在流式下是**天然**的：每个站点各自成批下发，不存在
    :func:`enforce_site_share` 要解决的「全局重排后小站被整站抹掉」问题。
    但仍保留两道安全阀，避免个别站点刷屏把浏览器拖死：
    单站最多 ``SEARCH_MAX_PER_SITE``，全局最多 ``SEARCH_MAX_RESULTS * 2``。
    """
    started = time.perf_counter()
    keywords = build_keywords(
        title,
        media_type=media_type,
        season=season,
        episode=episode,
        extra=extra_keywords,
    )
    active_providers = providers if providers is not None else site_service.search_providers()
    if not keywords or not active_providers:
        if not active_providers:
            logger.warning("没有可用的搜索站点，请先在站点管理中添加")
        yield {
            "type": "done",
            "total": 0,
            "items": [],
            "sites": [],
            "elapsed_ms": 0,
            "message": "" if keywords else "关键词为空",
        }
        return

    yield {
        "type": "start",
        "keyword": title,
        "total_sites": len(active_providers),
        "sites": [provider.site_name for provider in active_providers],
    }

    effective_rule = rule or FilterRule()
    if not effective_rule.title_keywords:
        effective_rule.title_keywords = _title_variants(title)

    group = None
    try:
        from app.services import rule_groups as rule_group_service

        group = rule_group_service.load_group(rule_group_id)
    except Exception as exc:  # pragma: no cover - 规则组不可用时退回纯评分
        logger.warning("加载过滤规则组失败，本次仅按评分排序: %s", exc)

    semaphore = asyncio.Semaphore(max(settings.SEARCH_CONCURRENCY, 1))
    tasks = [
        asyncio.ensure_future(
            _search_one(
                provider,
                keywords,
                media_type=media_type,
                season=season,
                episode=episode,
                semaphore=semaphore,
            )
        )
        for provider in active_providers
    ]

    global_cap = max(settings.SEARCH_MAX_RESULTS, 1) * 2
    per_site_cap = settings.SEARCH_MAX_PER_SITE
    seen: set[str] = set()
    ranked_all: list[dict[str, Any]] = []
    outcomes: list[SiteOutcome] = []
    done_count = 0

    try:
        for future in asyncio.as_completed(tasks):
            done_count += 1
            try:
                resources, outcome = await future
            except Exception as exc:
                # as_completed 丢出来的异常没法关联到具体 provider（顺序已被打乱），
                # 但绝不能静默吞掉：给一条无名诊断，总数才对得上（ADR-20）
                logger.warning("站点搜索任务异常: %s", exc)
                outcome = SiteOutcome(
                    site="未知站点",
                    status="error",
                    message=f"{type(exc).__name__}: {exc}"[:200],
                )
                resources = []
            outcomes.append(outcome)

            limited = resources[:per_site_cap] if per_site_cap > 0 else list(resources)
            outcome.kept = len(limited)

            fresh: list[Resource] = []
            for resource in limited:
                key = resource.unique_key
                if key in seen:
                    continue
                seen.add(key)
                fresh.append(resource)

            items: list[dict[str, Any]] = []
            if fresh and len(ranked_all) < global_cap:
                payload = [resource.to_dict() for resource in fresh]
                items = filter_and_rank(payload, effective_rule, group)
                room = global_cap - len(ranked_all)
                if len(items) > room:
                    items = items[:room]
                for item in items:
                    info = item.pop("_meta", None)
                    if info is not None:
                        item["meta"] = info.to_dict()
                ranked_all.extend(items)

            yield {
                "type": "site",
                "site": outcome.to_dict(),
                "items": items,
                "received": done_count,
                "total_sites": len(active_providers),
                "running_total": len(ranked_all),
            }
    finally:
        # 客户端中途断开时，未完成的站点任务必须取消，
        # 否则它们会继续跑满超时预算，白占并发名额和站点配额
        for task in tasks:
            if not task.done():
                task.cancel()

    elapsed = int((time.perf_counter() - started) * 1000)
    healthy = sum(1 for outcome in outcomes if outcome.status == "ok")
    logger.info(
        "流式搜索「%s」完成：%d/%d 站点有结果 / 命中 %d 条 / %dms",
        title,
        healthy,
        len(active_providers),
        len(ranked_all),
        elapsed,
    )
    if save_history:
        _save_results(title, media_type, ranked_all, active_providers, elapsed)

    yield {
        "type": "done",
        "total": len(ranked_all),
        "elapsed_ms": elapsed,
        "sites": [outcome.to_dict() for outcome in outcomes],
    }


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
                row.unique_key: row.id
                for row in session.query(
                    ResourceRecord.unique_key, ResourceRecord.id
                ).all()
            }
            for item in resources[:100]:
                key = item.get("unique_key")
                if not key:
                    continue
                media_meta = {
                    field: value
                    for field, value in (item.get("extra") or {}).items()
                    if field in _MEDIA_META_KEYS
                }
                if key in existing:
                    # 已存在的记录要**回填**作品级元数据：站点后来才补上封面/评分，
                    # 或者本次是从带元数据的站点命中的。只跳过不更新的话，
                    # 老库里的资源永远没有封面，榜单画板就一直是占位图。
                    if media_meta:
                        record = session.get(ResourceRecord, existing[key])
                        if record is not None:
                            merged = dict(record.meta or {})
                            changed = False
                            for field, value in media_meta.items():
                                if merged.get(field) in (None, "", [], {}):
                                    merged[field] = value
                                    changed = True
                            if changed:
                                record.meta = merged
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
                        # extra 里的作品级元数据（封面/评分/年份…）一起存下来：
                        # 榜单要画封面墙，而榜单是读 resources 表算的，不重新联网
                        meta={"password": item.get("password"), **media_meta},
                    )
                )
                existing[key] = 0
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
