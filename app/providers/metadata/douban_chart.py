"""豆瓣榜单（分类发现）。

与 ``metadata/douban.py`` 的区别：那个是**按标题找一部作品**的元数据补全
（``subject_suggest``），这里是**不给关键词、直接要一批热门作品**，用于「热度排行」
页的电影 / 电视剧 / 动漫 / 综艺四个分类榜。

用的是豆瓣「选片」页背后的公开接口::

    GET https://movie.douban.com/j/search_subjects?type=movie&tag=热门&page_limit=20&page_start=0

它免 API Key、返回带封面与评分的 JSON，是本项目已知覆盖中文影视最好的免费来源。
注意 ``type`` 只有 ``movie`` / ``tv`` 两种，**动漫与综艺是靠 tv 的 tag 区分的**
（``tag=日本动画`` / ``tag=综艺``），这一点接口文档里没有，是实测得出的。

反爬自保与 ``metadata/douban.py`` 同策略：缓存 + 命中限流后退避，
任何失败都返回空列表而不抛异常（榜单为空好过整页 500）。
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote

from app.core.logger import get_logger
from app.utils.http import fetch_json

logger = get_logger(__name__)

SUBJECTS_URL = "https://movie.douban.com/j/search_subjects"

#: 榜单缓存 30 分钟。豆瓣热门榜本身按天级变化，30 分钟足够新，
#: 又能挡住用户反复切页签带来的请求。
_CACHE_TTL = 1800
_BACKOFF_SECONDS = 300

_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_BACKOFF: float = 0.0

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

#: 分类 → (豆瓣 type, 豆瓣 tag, 我们的 media_type)
#: tag 用实测可用的值；``tv/动画`` 实测返回 0 条，所以动漫用 ``日本动画``。
CATEGORIES: dict[str, dict[str, str]] = {
    "movie": {"type": "movie", "tag": "热门", "media_type": "movie", "label": "电影"},
    "tv": {"type": "tv", "tag": "国产剧", "media_type": "tv", "label": "电视剧"},
    "anime": {"type": "tv", "tag": "日本动画", "media_type": "tv", "label": "动漫"},
    "show": {"type": "tv", "tag": "综艺", "media_type": "tv", "label": "综艺"},
}


def is_rate_limited() -> bool:
    return time.time() < _BACKOFF


def _mark_rate_limited() -> None:
    global _BACKOFF
    _BACKOFF = time.time() + _BACKOFF_SECONDS
    logger.warning("豆瓣榜单疑似限流，静默 %s 秒", _BACKOFF_SECONDS)


def reset_state() -> None:
    """清空缓存与退避（测试用）。"""
    global _BACKOFF
    _CACHE.clear()
    _BACKOFF = 0.0


def _headers() -> dict[str, str]:
    # 必须带 Referer：豆瓣对该接口校验来源，裸请求会被拒
    return {"User-Agent": UA, "Referer": "https://movie.douban.com/explore"}


def _normalize_cover(cover: object) -> str | None:
    """把豆瓣封面地址上的坏镜像换成可用镜像。

    豆瓣把同一张图随机分发到 img1/img2/img3/img9.doubanio.com，实测 **img9
    已长期损坏**：即便带正确 Referer 也返回 200 + text/html 反爬页而非图片。
    约 1/4 的封面会落在该镜像上，若原样存库，前端就会大面积裂图。
    各镜像共享同一套路径，因此只需把主机名换成好镜像即可。

    图片代理侧（``app/api/routers/images.py``）也有一层镜像轮换兜底，
    这里做的是「不要一开始就把坏地址写进库」，两层互补。
    """
    url = str(cover or "").strip()
    if not url:
        return None
    return url.replace("//img9.doubanio.com/", "//img3.doubanio.com/")


def _normalize(item: dict[str, Any], category: str) -> dict[str, Any] | None:
    """把豆瓣条目转成榜单统一结构。"""
    title = str(item.get("title") or "").strip()
    if not title:
        return None
    meta = CATEGORIES.get(category) or {}
    # 评分可能是空串（未上映/无人评分）——不要渲染成 0 分
    try:
        rating = float(item.get("rate") or 0) or None
    except (TypeError, ValueError):
        rating = None
    return {
        "source": "douban",
        "category": category,
        "title": title,
        "poster": _normalize_cover(item.get("cover")),
        "rating": rating,
        "media_type": meta.get("media_type") or "",
        "douban_id": str(item.get("id") or "").strip() or None,
        "douban_url": str(item.get("url") or "").strip() or None,
        # 「更新至10集」这类信息只有剧集才有，对追剧很有用
        "episodes_info": str(item.get("episodes_info") or "").strip() or None,
        "playable": bool(item.get("playable")),
        "is_new": bool(item.get("is_new")),
    }


async def chart(category: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """拉一个分类的豆瓣榜单。失败返回空列表。"""
    meta = CATEGORIES.get(category)
    if not meta:
        return []
    limit = max(1, min(int(limit or 20), 50))
    cache_key = f"{category}:{limit}"
    cached = _CACHE.get(cache_key)
    if cached and cached[0] > time.time():
        return cached[1]
    if is_rate_limited():
        return []

    url = (
        f"{SUBJECTS_URL}?type={meta['type']}&tag={quote(meta['tag'])}"
        f"&page_limit={limit}&page_start=0"
    )
    payload = await fetch_json(url, headers=_headers(), timeout=15)
    if not isinstance(payload, dict):
        # fetch_json 失败返回 None；无法区分限流与网络故障时按限流自保
        _mark_rate_limited()
        return []
    subjects = payload.get("subjects")
    if not isinstance(subjects, list):
        return []

    items: list[dict[str, Any]] = []
    for raw in subjects:
        if isinstance(raw, dict):
            row = _normalize(raw, category)
            if row:
                items.append(row)
    _CACHE[cache_key] = (time.time() + _CACHE_TTL, items)
    return items


async def health_check() -> tuple[bool, str]:
    """探活：能拉到电影榜就算通。"""
    items = await chart("movie", limit=5)
    if items:
        return True, f"豆瓣榜单可用，电影榜 {len(items)} 条"
    if is_rate_limited():
        return False, "豆瓣榜单限流中（已自动退避）"
    return False, "豆瓣榜单无数据"
