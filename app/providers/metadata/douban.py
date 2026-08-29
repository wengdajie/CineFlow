"""豆瓣元数据（封面/年份/集数）。

**为什么需要豆瓣**：TMDB 需要用户自备 API Key，且国产剧集的中文标题命中率
一般；豆瓣的 ``subject_suggest`` 是**公开接口、无需 Key**，对中文片名的命中
率极高，非常适合做封面兜底。

**降级策略**：豆瓣有反爬限流（连续请求会短暂 403/空响应）。因此这里做了
三层保护——内存缓存、失败退避（限流期间直接跳过不再打请求）、以及所有异常
都返回空结果。任何情况下都不会让搜索/热榜接口 500。

**防盗链**：豆瓣图片带 Referer 校验，前端必须用 ``referrerpolicy="no-referrer"``
才能正常显示（``web/assets`` 里的 ``posterBox()`` 已处理）。
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote

from app.core.logger import get_logger
from app.utils.http import fetch_json

logger = get_logger(__name__)

SUGGEST_URL = "https://movie.douban.com/j/subject_suggest"

#: 缓存 6 小时：封面几乎不变，没必要反复请求（也是对豆瓣的基本礼貌）
_CACHE_TTL = 6 * 3600
#: 被限流后静默这么久，避免雪上加霜
_BACKOFF_SECONDS = 300

_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
#: 触发限流的时间戳；为 0 表示正常
_rate_limited_until: float = 0.0


def _cache_get(key: str) -> list[dict[str, Any]] | None:
    item = _CACHE.get(key)
    if not item:
        return None
    expire_at, value = item
    if expire_at < time.time():
        _CACHE.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: list[dict[str, Any]]) -> None:
    _CACHE[key] = (time.time() + _CACHE_TTL, value)


def is_rate_limited() -> bool:
    """当前是否处于限流退避期。"""
    return time.time() < _rate_limited_until


def _mark_rate_limited() -> None:
    global _rate_limited_until
    _rate_limited_until = time.time() + _BACKOFF_SECONDS
    logger.warning("豆瓣接口疑似限流，静默 %s 秒后重试", _BACKOFF_SECONDS)


def reset_state() -> None:
    """清空缓存与退避状态（测试用）。"""
    global _rate_limited_until
    _CACHE.clear()
    _rate_limited_until = 0.0


def _headers() -> dict[str, str]:
    # 豆瓣对无 Referer / 非浏览器 UA 的请求更容易限流
    return {
        "Referer": "https://movie.douban.com/",
        "Accept": "application/json, text/plain, */*",
    }


def _normalize(item: dict[str, Any]) -> dict[str, Any] | None:
    """把豆瓣 suggest 条目转成项目内部统一的元数据字典。"""
    if not isinstance(item, dict):
        return None
    title = str(item.get("title") or "").strip()
    if not title:
        return None
    # episode 只有剧集才有值，用它区分电影/电视剧比 type 字段更可靠
    episode_raw = str(item.get("episode") or "").strip()
    episodes = int(episode_raw) if episode_raw.isdigit() else None
    year_raw = str(item.get("year") or "").strip()
    return {
        "title": title,
        "sub_title": str(item.get("sub_title") or "").strip() or None,
        "year": int(year_raw) if year_raw.isdigit() else None,
        "episodes": episodes,
        "media_type": "tv" if episodes else "movie",
        "poster": str(item.get("img") or "").strip() or None,
        "douban_id": str(item.get("id") or "").strip() or None,
        "douban_url": str(item.get("url") or "").strip() or None,
        "source": "douban",
    }


async def suggest(keyword: str, *, limit: int = 10) -> list[dict[str, Any]]:
    """按关键词搜索豆瓣条目，返回归一化后的元数据列表。

    失败（网络异常/限流/无结果）统一返回空列表，调用方据此回退到下一层封面源。
    """
    word = str(keyword or "").strip()
    if not word:
        return []

    cache_key = word.lower()
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached[:limit]

    if is_rate_limited():
        return []

    payload = await fetch_json(
        f"{SUGGEST_URL}?q={quote(word)}",
        headers=_headers(),
        timeout=8,
    )
    if payload is None:
        # fetch_json 失败返回 None：可能是限流也可能是网络问题，一律退避
        _mark_rate_limited()
        return []
    if not isinstance(payload, list):
        return []

    items = [n for n in (_normalize(i) for i in payload) if n]
    _cache_set(cache_key, items)
    return items[:limit]


def _score(item: dict[str, Any], title: str, year: int | None) -> int:
    """给候选条目打匹配分，挑最贴合的那个。"""
    score = 0
    name = str(item.get("title") or "")
    sub = str(item.get("sub_title") or "")
    if name == title:
        score += 100
    elif title and (title in name or name in title):
        score += 50
    if sub and title and title in sub:
        score += 20
    if year and item.get("year") == year:
        score += 30
    elif year and item.get("year") and abs(int(item["year"]) - year) <= 1:
        # 上映年份跨年很常见（12 月上映、次年引进），差 1 年也算靠谱
        score += 12
    if item.get("poster"):
        score += 5
    return score


async def match(
    title: str, *, year: int | None = None, media_type: str | None = None
) -> dict[str, Any] | None:
    """为一部作品找到最匹配的豆瓣条目（用于补封面）。"""
    name = str(title or "").strip()
    if not name:
        return None
    candidates = await suggest(name, limit=10)
    if not candidates:
        return None
    if media_type in ("movie", "tv"):
        # 类型明确时优先同类型，但没有同类型也不至于放弃（好过没封面）
        same = [c for c in candidates if c.get("media_type") == media_type]
        candidates = same or candidates
    return max(candidates, key=lambda c: _score(c, name, year))


async def poster(
    title: str, *, year: int | None = None, media_type: str | None = None
) -> str | None:
    """只取封面地址的便捷方法。"""
    found = await match(title, year=year, media_type=media_type)
    return found.get("poster") if found else None


async def health_check() -> tuple[bool, str]:
    """探活：豆瓣公开接口无需配置，能搜到结果即视为可用。"""
    if is_rate_limited():
        return False, "豆瓣接口限流中，稍后自动恢复"
    items = await suggest("流浪地球", limit=1)
    if items:
        return True, "豆瓣接口正常"
    return False, "豆瓣接口无响应（可能被限流或网络不通）"
