"""热度排行：把「搜索缓存 + 搜索历史 + 站点最新流」聚合成榜单。

三份数据源各有侧重，互相补充：

- ``ResourceRecord``（搜索缓存）：站内已见过的资源，带做种数与体积，
  用于算「资源热度榜」——同一部片子被多站收录、做种越多越热。
- ``SearchHistory``（搜索历史）：用户/订阅实际搜过什么，用于算
  「搜索热词榜」，反映本机关注度。
- 站点最新流（``radar.fetch_feed``）：实时拉取，用于算「实时热榜」，
  反映站点当下在更新什么。

热度分数刻意做成**可解释**的加权求和，而不是玄学模型：

    heat = 做种数增益 + 站点覆盖度 + 资源条目数 + 新鲜度 + 画质加成

最终再线性归一化到 0~100 便于前端画条形图。
"""

from __future__ import annotations

import asyncio
import math
import re
from collections import defaultdict
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select

from app.core.logger import get_logger
from app.core.meta import parse
from app.db.base import utcnow
from app.db.models import ResourceRecord, SearchHistory
from app.db.session import session_scope
from app.schemas.enums import MediaType, ResourceKind
from app.utils.strings import normalize

logger = get_logger(__name__)

#: 画质加成（越高的规格通常代表越受关注的正式发布）
_RESOLUTION_BONUS = {"2160p": 12, "1080p": 8, "720p": 4}


def _freshness(hours: float) -> float:
    """新鲜度：24 小时内满分，之后按半衰期衰减。"""
    if hours <= 24:
        return 20.0
    return 20.0 * math.exp(-(hours - 24) / 96.0)


#: 发布站常见的「同一部剧不同封装」标记。这些只描述同一集内容的不同规格
#: （码率/帧率/HDR 方案/音轨），**不是不同作品**，做榜单归并时必须剥掉，
#: 否则《师兄太稳健》会被拆成「高码版 / 60帧率版本 / 杜比视界版本」等 6~7 条。
_VARIANT_TAGS = (
    "杜比视界版本",
    "杜比视界",
    "60帧率版本",
    "60帧版本",
    "60帧",
    "高码率版本",
    "高码版本",
    "高码率",
    "高码版",
    "hdr版本",
    "hdr10",
    "国语配音",
    "国语音轨",
    "国语中字",
    "中文字幕",
    "内嵌简中",
    "全集",
    "更新中",
)

#: 标题里的「集数标记」，如 ``第09集``、``第01,02,03集``、``第12-13集``、``全36集``、
#: ``更15集``。部分站点会把集号拼在片名**前面**甚至中间，导致解析出的 title 里混进集号，
#: 同一部剧就会按集号被拆成十几条榜单项。这里在任意位置做剥离。
_EPISODE_MARK = re.compile(
    r"(?:第\s*[\d,\-、~至\s]+\s*集"      # 第09集 / 第01,02集 / 第12-13集
    r"|全\s*\d+\s*集"                     # 全36集
    r"|更新?至?\s*第?\s*\d+\s*集"          # 更15集 / 更新至第15集
    r"|ep?\s*\d+(?:\s*[\-~]\s*\d+)?"     # EP05 / E05-08
    r")",
    re.I,
)

#: 季标记：``第二季`` / ``第2季`` / ``S02``。归并键里季号单独存放，
#: 因此片名内部的季标记要剥掉，避免 ``庆余年第二季`` 与 ``庆余年`` 分家。
_SEASON_MARK = re.compile(
    r"(?:第\s*[0-9一二三四五六七八九十]+\s*季|\bs\d{1,2}\b|season\s*\d{1,2})",
    re.I,
)


def _canonical_title(raw: str) -> str:
    """把资源标题收敛成「作品名」，供榜单归并使用。

    只在热度归并时使用，不改动 :mod:`app.core.meta` 的解析结果，
    避免影响下载命名与缺集计算。
    """
    lowered = normalize(raw or "").lower()
    lowered = _EPISODE_MARK.sub(" ", lowered)
    lowered = _SEASON_MARK.sub(" ", lowered)
    for tag in _VARIANT_TAGS:
        lowered = lowered.replace(tag, " ")
    # 去掉分隔符、装饰符号与空白，得到稳定的归并键
    # （部分站点会给标题加 ✅ ★ 「」 等装饰，不剥掉会另起一条榜单项）
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", lowered)


def _group_key(title: str, media_type: str | None, season: int | None) -> str:
    """把资源标题归并成「作品 + 季」维度的键。"""
    info = parse(title)
    name = _canonical_title(info.title or title)
    if not name:
        name = _canonical_title(title)
    kind = media_type or info.media_type or MediaType.UNKNOWN.value
    number = season if season is not None else info.season
    if kind == MediaType.MOVIE.value:
        return f"movie:{name}"
    # 剧集季号缺失时按第 1 季归并：站点常把单季剧的季号省略，
    # 若不归并会出现「同名剧 S1」与「同名剧 S?」两条并列榜单项。
    if number is None:
        number = 1
    return f"{kind}:{name}:s{number}"


def _display_title(raw: str, parsed: str | None) -> str:
    """榜单展示用的片名：剥掉集数标记与版本标记后的可读文本。"""
    text = _EPISODE_MARK.sub(" ", normalize(parsed or raw or ""))
    for tag in _VARIANT_TAGS:
        for variant in (f"[{tag}]", f"【{tag}】", tag):
            text = text.replace(variant, " ")
    text = re.sub(r"\s{2,}", " ", text).strip(" .-_[]()（）【】·、,，")
    # 去掉首尾装饰符号（✅ ★ 「」 等），保留中英文与数字开头
    text = re.sub(r"^[^0-9A-Za-z\u4e00-\u9fff]+", "", text)
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff\s)）】」]+$", "", text).strip()
    if not text:
        text = re.sub(r"\s{2,}", " ", normalize(raw or "")).strip()
    return text


class _Bucket:
    """一个作品（或作品+季）的热度累加器。"""

    __slots__ = (
        "count",
        "episodes",
        "kinds",
        "latest",
        "media",
        "media_type",
        "resolutions",
        "samples",
        "season",
        "seeders",
        "sites",
        "size",
        "title",
    )

    def __init__(self) -> None:
        self.title = ""
        self.media_type = MediaType.UNKNOWN.value
        self.season: int | None = None
        self.sites: set[str] = set()
        self.count = 0
        self.seeders = 0
        self.size = 0
        self.episodes: set[int] = set()
        self.kinds: set[str] = set()
        self.resolutions: set[str] = set()
        self.latest: Any = None
        self.samples: list[dict[str, Any]] = []
        #: 作品级元数据（封面/评分/年份…），来自站点搜索接口
        self.media: dict[str, Any] = {}

    def absorb_media(self, meta: dict[str, Any] | None) -> None:
        """合并作品级元数据：先到先得，不覆盖已有值。

        同一部作品会有几十条资源，每条都带一份站点元数据。先到先得即可，
        但要逐字段合并——有的站点给了封面没给评分，另一个站点相反，
        逐字段补齐才能拿到最完整的展示信息。
        """
        for key, value in (meta or {}).items():
            if value in (None, "", [], {}):
                continue
            if key not in self.media:
                self.media[key] = value

    def heat(self) -> float:
        """可解释的热度分。"""
        score = min(self.seeders, 5000) ** 0.5 * 3.0          # 做种数（开方抑制头部）
        score += len(self.sites) * 14                          # 多站收录
        score += min(self.count, 40) * 1.6                     # 资源条目数
        score += min(len(self.episodes), 60) * 0.8             # 覆盖集数
        if self.latest is not None:
            hours = max((utcnow() - self.latest).total_seconds() / 3600.0, 0.0)
            score += _freshness(hours)
        for resolution in self.resolutions:
            score += _RESOLUTION_BONUS.get(resolution, 0)
        if ResourceKind.PAN.value in self.kinds:
            score += 6                                          # 有网盘资源，观看门槛更低
        return round(score, 2)

    def absorb(self, other: _Bucket) -> None:
        """把另一个桶并进来（用于把 media_type 未知的组折叠到已知组）。"""
        self.count += other.count
        self.seeders += other.seeders
        self.size = max(self.size, other.size)
        self.sites |= other.sites
        self.kinds |= other.kinds
        self.resolutions |= other.resolutions
        self.episodes |= other.episodes
        if other.latest is not None and (self.latest is None or other.latest > self.latest):
            self.latest = other.latest
        for sample in other.samples:
            if len(self.samples) >= 3:
                break
            self.samples.append(sample)
        self.absorb_media(other.media)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "media_type": self.media_type,
            "season": self.season,
            "sites": sorted(self.sites),
            "site_count": len(self.sites),
            "resource_count": self.count,
            "seeders": self.seeders,
            "size": self.size,
            "episodes": sorted(self.episodes)[:80],
            "episode_count": len(self.episodes),
            "latest_episode": max(self.episodes) if self.episodes else None,
            "kinds": sorted(self.kinds),
            "resolutions": sorted(self.resolutions),
            "latest_at": self.latest.isoformat() if self.latest else None,
            "heat": self.heat(),
            "samples": self.samples[:3],
            # 画板模式需要的展示信息；站点没提供时为空，前端做占位降级
            "poster": self.media.get("poster"),
            "rating": self.media.get("rating"),
            "rating_people": self.media.get("rating_people"),
            "year": self.media.get("year"),
            "genres": self.media.get("genres") or [],
            "actors": self.media.get("actors") or [],
            "area": self.media.get("area"),
            "overview": self.media.get("overview"),
            "director": self.media.get("director"),
            "status_text": self.media.get("status_text"),
            "total_episodes": self.media.get("total_episodes"),
        }


def _collapse_unknown(buckets: dict[str, _Bucket]) -> dict[str, _Bucket]:
    """把 ``media_type`` 未识别的分组折叠到同名的已知分组上。

    盘搜结果常常缺少足够信息判断电影/剧集（标题里没有 SxxExx），
    于是同一部作品会出现 ``tv:片名:s1`` 与 ``unknown:片名:s1`` 两条。
    这里在**同名**前提下把 unknown 并入已知类型，优先 tv 再 movie。
    """
    merged = dict(buckets)
    for key in list(merged):
        if not key.startswith(f"{MediaType.UNKNOWN.value}:"):
            continue
        _, name, season = key.split(":", 2)
        for target in (f"{MediaType.TV.value}:{name}:{season}", f"movie:{name}"):
            if target in merged and target != key:
                merged[target].absorb(merged.pop(key))
                break
    return merged


def _finalize(buckets: dict[str, _Bucket], limit: int) -> list[dict[str, Any]]:
    """折叠同名未知类型 + 排序 + 归一化 + 截断。"""
    buckets = _collapse_unknown(buckets)
    items = sorted(
        (bucket.to_dict() for bucket in buckets.values()),
        key=lambda item: item["heat"],
        reverse=True,
    )[:limit]
    top = items[0]["heat"] if items else 0
    for index, item in enumerate(items, start=1):
        item["rank"] = index
        item["heat_percent"] = round(item["heat"] / top * 100, 1) if top else 0.0
    return items


def resource_ranking(
    *,
    limit: int = 20,
    days: int = 14,
    media_type: str | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    """资源热度榜（来自本地搜索缓存 ``resources`` 表）。"""
    since = utcnow() - timedelta(days=max(days, 1))
    buckets: dict[str, _Bucket] = defaultdict(_Bucket)
    scanned = 0

    with session_scope() as session:
        stmt = select(ResourceRecord).where(ResourceRecord.created_at >= since)
        if media_type:
            stmt = stmt.where(ResourceRecord.media_type == media_type)
        if kind:
            stmt = stmt.where(ResourceRecord.kind == kind)
        rows = list(session.execute(stmt.limit(5000)).scalars())
        scanned = len(rows)

        for row in rows:
            key = _group_key(row.title, row.media_type, row.season)
            bucket = buckets[key]
            if not bucket.title:
                info = parse(row.title)
                bucket.title = _display_title(row.title, info.title)
                bucket.media_type = row.media_type or info.media_type
                bucket.season = row.season if row.season is not None else info.season
            bucket.count += 1
            bucket.seeders += int(row.seeders or 0)
            bucket.size = max(bucket.size, int(row.size or 0))
            bucket.sites.add(row.site or "未知站点")
            bucket.kinds.add(row.kind or ResourceKind.TORRENT.value)
            if row.resolution:
                bucket.resolutions.add(row.resolution)
            bucket.episodes.update(row.episodes or [])
            bucket.absorb_media(row.meta or {})
            moment = row.publish_at or row.created_at
            if moment and (bucket.latest is None or moment > bucket.latest):
                bucket.latest = moment
            if len(bucket.samples) < 3:
                bucket.samples.append(
                    {
                        "title": row.title,
                        "site": row.site,
                        "kind": row.kind,
                        "size": int(row.size or 0),
                        "seeders": int(row.seeders or 0),
                        "link": row.link,
                        "page_url": row.page_url,
                        "score": float(row.score or 0),
                    }
                )

    items = _finalize(buckets, limit)
    return {
        "source": "resources",
        "window_days": days,
        "scanned": scanned,
        "total": len(items),
        "items": items,
    }


async def live_ranking(
    *, limit: int = 20, limit_per_site: int = 40, media_type: str | None = None
) -> dict[str, Any]:
    """实时热榜：直接拉取各站点最新流后聚合（联网，无缓存依赖）。"""
    from app.services import radar as radar_service

    feed = await radar_service.fetch_feed(limit_per_site=limit_per_site)
    buckets: dict[str, _Bucket] = defaultdict(_Bucket)

    for resource in feed:
        info = parse(resource.title)
        if media_type and (info.media_type or MediaType.UNKNOWN.value) != media_type:
            continue
        key = _group_key(resource.title, info.media_type, info.season)
        bucket = buckets[key]
        if not bucket.title:
            bucket.title = _display_title(resource.title, info.title)
            bucket.media_type = info.media_type or MediaType.UNKNOWN.value
            bucket.season = info.season
        bucket.count += 1
        bucket.seeders += int(resource.seeders or 0)
        bucket.size = max(bucket.size, int(resource.size or 0))
        bucket.sites.add(resource.site or "未知站点")
        bucket.kinds.add(resource.kind or ResourceKind.TORRENT.value)
        if info.resolution:
            bucket.resolutions.add(info.resolution)
        bucket.episodes.update(info.episodes or [])
        bucket.absorb_media(resource.extra or {})
        moment = resource.publish_at
        if moment and (bucket.latest is None or moment > bucket.latest):
            bucket.latest = moment
        if len(bucket.samples) < 3:
            bucket.samples.append(
                {
                    "title": resource.title,
                    "site": resource.site,
                    "kind": resource.kind,
                    "size": int(resource.size or 0),
                    "seeders": int(resource.seeders or 0),
                    "link": resource.link,
                    "page_url": resource.page_url,
                    "score": 0.0,
                }
            )

    items = _finalize(buckets, limit)
    return {
        "source": "live",
        "feed_total": len(feed),
        "total": len(items),
        "items": items,
    }


async def enrich_posters(
    items: list[dict[str, Any]], *, limit: int = 24
) -> list[dict[str, Any]]:
    """给榜单条目补全封面（画板模式的关键）。

    **封面回退链**：站点自带 → 豆瓣 → TMDB → 前端占位。
    站点自带的最准（就是那条资源的封面），豆瓣对中文片名命中率最高且免 Key，
    TMDB 需要用户配 Key 所以排最后。

    只给**前 ``limit`` 条**补图：画板首屏就这么多，再往后补属于浪费外部配额；
    用户滚动/翻页时会带着新的 offset 再来一次。
    """
    from app.providers.metadata import douban

    targets = [i for i in items[:limit] if not i.get("poster")]
    if not targets:
        return items

    # 并发但有上限：外部接口都怕突发，8 并发足够快又不至于触发限流
    semaphore = asyncio.Semaphore(8)

    async def fill(item: dict[str, Any]) -> None:
        title = str(item.get("title") or "").strip()
        if not title:
            return
        media_type = item.get("media_type")
        kind = "tv" if media_type == MediaType.TV.value else (
            "movie" if media_type == MediaType.MOVIE.value else None
        )
        year = item.get("year") if isinstance(item.get("year"), int) else None
        async with semaphore:
            try:
                found = await douban.match(title, year=year, media_type=kind)
            except Exception as exc:  # 补图失败绝不能影响榜单本身
                logger.debug("豆瓣补图失败 %s: %s", title, exc)
                return
        if not found:
            return
        item["poster"] = found.get("poster")
        item["poster_source"] = "douban"
        # 把匹配置信度带给前端：medium 的封面可能是同名作品，界面可加提示
        if found.get("match_confidence"):
            item["poster_confidence"] = found["match_confidence"]
        item.setdefault("douban_url", found.get("douban_url"))
        # 顺手补齐空字段：年份/集数对画板卡片的信息密度很有用
        if not item.get("year") and found.get("year"):
            item["year"] = found["year"]
        if not item.get("total_episodes") and found.get("episodes"):
            item["total_episodes"] = found["episodes"]

    await asyncio.gather(*(fill(i) for i in targets))
    return items


def hot_keywords(*, limit: int = 12, days: int = 30) -> dict[str, Any]:
    """搜索热词榜（来自 ``search_history``）。"""
    since = utcnow() - timedelta(days=max(days, 1))
    with session_scope() as session:
        rows = list(
            session.execute(
                select(
                    SearchHistory.keyword,
                    func.count(SearchHistory.id),
                    func.sum(SearchHistory.result_count),
                    func.max(SearchHistory.created_at),
                )
                .where(SearchHistory.created_at >= since)
                .group_by(SearchHistory.keyword)
                .order_by(func.count(SearchHistory.id).desc())
                .limit(limit)
            ).all()
        )

    items = [
        {
            "rank": index,
            "keyword": keyword,
            "times": int(times or 0),
            "results": int(results or 0),
            "last_at": last.isoformat() if last else None,
        }
        for index, (keyword, times, results, last) in enumerate(rows, start=1)
    ]
    top = items[0]["times"] if items else 0
    for item in items:
        item["heat_percent"] = round(item["times"] / top * 100, 1) if top else 0.0
    return {"source": "search_history", "window_days": days, "total": len(items), "items": items}


def site_activity(*, days: int = 14, limit: int = 20) -> dict[str, Any]:
    """站点贡献榜：哪些站点提供了最多可用资源。"""
    since = utcnow() - timedelta(days=max(days, 1))
    with session_scope() as session:
        rows = list(
            session.execute(
                select(
                    ResourceRecord.site,
                    func.count(ResourceRecord.id),
                    func.sum(ResourceRecord.seeders),
                    func.avg(ResourceRecord.score),
                    func.max(ResourceRecord.created_at),
                )
                .where(ResourceRecord.created_at >= since)
                .group_by(ResourceRecord.site)
                .order_by(func.count(ResourceRecord.id).desc())
                .limit(limit)
            ).all()
        )

    items = [
        {
            "rank": index,
            "site": site or "未知站点",
            "resources": int(count or 0),
            "seeders": int(seeders or 0),
            "avg_score": round(float(avg_score or 0), 1),
            "last_at": last.isoformat() if last else None,
        }
        for index, (site, count, seeders, avg_score, last) in enumerate(rows, start=1)
    ]
    top = items[0]["resources"] if items else 0
    for item in items:
        item["heat_percent"] = round(item["resources"] / top * 100, 1) if top else 0.0
    return {"window_days": days, "total": len(items), "items": items}


def overview(*, days: int = 14, limit: int = 10) -> dict[str, Any]:
    """排行总览：一次拿到资源榜 + 热词榜 + 站点榜（供仪表盘/搜索页使用）。"""
    return {
        "resources": resource_ranking(limit=limit, days=days),
        "keywords": hot_keywords(limit=limit, days=days),
        "sites": site_activity(limit=limit, days=days),
    }
