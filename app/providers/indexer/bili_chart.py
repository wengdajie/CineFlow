"""B 站排行榜（分类发现）。

与 ``indexer/webvideo.py`` 的区别：那个是**按关键词搜**，这里是**不给关键词、
直接要热门榜**，供「热度排行」页的 Bilibili 页签使用。

用两套官方接口，因为 B 站把 UGC 与 PGC 分开了：

* UGC（普通投稿）``/x/web-interface/ranking/v2?rid=<分区>&type=all``
* PGC（番剧/国创/影视，即正版版权内容）``/pgc/season/rank/web/list?season_type=<类型>``

**实测坑（重要）**：``ranking/v2`` 直接裸请求会返回 ``code=-352``（风控拦截），
和 ``indexer/webvideo.py`` 里搜索接口的 412 是同一类问题。解法也一样：
**先 GET 一次 B 站首页拿 buvid3 Cookie**，再调接口。另外 ``rid=13``（番剧）与
``rid=167``（国创）在 ``ranking/v2`` 上返回 ``code=-400``——这两个分区的内容是 PGC，
必须走 ``pgc/season/rank``，这一点接口文档没写明。

所有失败都返回空列表，不抛异常。
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.core.logger import get_logger
from app.utils.http import async_client

logger = get_logger(__name__)

HOME_URL = "https://www.bilibili.com/"
RANKING_URL = "https://api.bilibili.com/x/web-interface/ranking/v2"
PGC_RANK_URL = "https://api.bilibili.com/pgc/season/rank/web/list"

#: 榜单缓存 15 分钟：B 站榜单本身是小时级更新
_CACHE_TTL = 900
_BACKOFF_SECONDS = 300

_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_BACKOFF: float = 0.0

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

#: 分类 → 拉取方式。``kind=ugc`` 用 rid，``kind=pgc`` 用 season_type。
CATEGORIES: dict[str, dict[str, Any]] = {
    "all": {"kind": "ugc", "rid": 0, "label": "全站"},
    "bangumi": {"kind": "pgc", "season_type": 1, "label": "番剧"},
    "guochuang": {"kind": "pgc", "season_type": 4, "label": "国创"},
    "douga": {"kind": "ugc", "rid": 1, "label": "动画"},
    "movie": {"kind": "ugc", "rid": 23, "label": "电影"},
    "teleplay": {"kind": "ugc", "rid": 11, "label": "电视剧"},
    "documentary": {"kind": "ugc", "rid": 177, "label": "纪录片"},
    "ent": {"kind": "ugc", "rid": 5, "label": "娱乐"},
}


def is_rate_limited() -> bool:
    return time.time() < _BACKOFF


def _mark_rate_limited() -> None:
    global _BACKOFF
    _BACKOFF = time.time() + _BACKOFF_SECONDS
    logger.warning("B 站榜单疑似风控，静默 %s 秒", _BACKOFF_SECONDS)


def reset_state() -> None:
    """清空缓存与退避（测试用）。"""
    global _BACKOFF
    _CACHE.clear()
    _BACKOFF = 0.0


def _pick_cover(url: str) -> str | None:
    """B 站封面常返回 http://，统一升到 https 免得页面混合内容被拦。"""
    cover = str(url or "").strip()
    if not cover:
        return None
    if cover.startswith("//"):
        return "https:" + cover
    if cover.startswith("http://"):
        return "https://" + cover[len("http://") :]
    return cover if cover.startswith("https://") else None


def _normalize_ugc(item: dict[str, Any], category: str) -> dict[str, Any] | None:
    title = str(item.get("title") or "").strip()
    bvid = str(item.get("bvid") or "").strip()
    if not title or not bvid:
        return None
    stat = item.get("stat") if isinstance(item.get("stat"), dict) else {}
    owner = item.get("owner") if isinstance(item.get("owner"), dict) else {}
    return {
        "source": "bilibili",
        "category": category,
        "title": title,
        "poster": _pick_cover(item.get("pic") or ""),
        # 播放量投影到 heat，与搜索侧把播放量投影到 seeders 是同一思路（ADR-32）
        "heat": int(stat.get("view") or 0),
        "likes": int(stat.get("like") or 0),
        "uploader": str(owner.get("name") or "").strip() or None,
        "duration": int(item.get("duration") or 0),
        "url": f"https://www.bilibili.com/video/{bvid}",
        "bvid": bvid,
        "media_type": "",
        "rating": None,
        "desc": str(item.get("desc") or "").strip()[:200] or None,
    }


def _normalize_pgc(item: dict[str, Any], category: str) -> dict[str, Any] | None:
    title = str(item.get("title") or "").strip()
    if not title:
        return None
    stat = item.get("stat") if isinstance(item.get("stat"), dict) else {}
    # PGC 的评分是 "9.7分" 这种字符串
    rating = None
    raw_rating = str(item.get("rating") or "").replace("分", "").strip()
    try:
        rating = float(raw_rating) or None
    except (TypeError, ValueError):
        rating = None
    new_ep = item.get("new_ep") if isinstance(item.get("new_ep"), dict) else {}
    return {
        "source": "bilibili",
        "category": category,
        "title": title,
        "poster": _pick_cover(item.get("cover") or ""),
        "heat": int(stat.get("view") or 0),
        "likes": int(stat.get("follow") or 0),
        "uploader": None,
        "duration": 0,
        "url": str(item.get("url") or "").strip() or None,
        "bvid": None,
        # 番剧/国创归到 tv，便于「订阅」时按剧集处理
        "media_type": "tv",
        "rating": rating,
        "episodes_info": str(
            new_ep.get("index_show") or item.get("desc") or ""
        ).strip()
        or None,
        "desc": None,
    }


async def chart(category: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """拉一个分类的 B 站榜单。失败返回空列表。"""
    meta = CATEGORIES.get(category)
    if not meta:
        return []
    limit = max(1, min(int(limit or 20), 100))
    cache_key = f"{category}:{limit}"
    cached = _CACHE.get(cache_key)
    if cached and cached[0] > time.time():
        return cached[1]
    if is_rate_limited():
        return []

    try:
        async with async_client(timeout=15, headers={"User-Agent": UA}) as client:
            # 关键一步：预热首页拿 buvid3，否则 ranking/v2 返回 code=-352
            try:
                await client.get(HOME_URL)
            except httpx.HTTPError:
                # 预热失败不直接放弃，接口有可能仍然可用
                logger.debug("B 站首页预热失败，仍尝试直接请求榜单")

            if meta["kind"] == "pgc":
                url = f"{PGC_RANK_URL}?day=3&season_type={meta['season_type']}"
                referer = "https://www.bilibili.com/v/popular/rank/bangumi"
            else:
                url = f"{RANKING_URL}?rid={meta['rid']}&type=all"
                referer = "https://www.bilibili.com/v/popular/rank/all"

            response = await client.get(url, headers={"Referer": referer})
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("B 站榜单请求失败 category=%s: %s", category, exc)
        return []

    if not isinstance(payload, dict):
        return []
    code = payload.get("code")
    if code != 0:
        # -352 / -412 都是风控，退避避免继续撞墙
        if code in (-352, -412, -509):
            _mark_rate_limited()
        logger.warning("B 站榜单返回 code=%s category=%s", code, category)
        return []

    data = payload.get("data")
    raw_list = (data or {}).get("list") if isinstance(data, dict) else None
    if not isinstance(raw_list, list):
        return []

    normalize = _normalize_pgc if meta["kind"] == "pgc" else _normalize_ugc
    items: list[dict[str, Any]] = []
    for raw in raw_list[:limit]:
        if isinstance(raw, dict):
            row = normalize(raw, category)
            if row:
                items.append(row)
    _CACHE[cache_key] = (time.time() + _CACHE_TTL, items)
    return items


async def health_check() -> tuple[bool, str]:
    """探活：能拉到全站榜就算通。"""
    items = await chart("all", limit=5)
    if items:
        return True, f"B 站榜单可用，全站榜 {len(items)} 条"
    if is_rate_limited():
        return False, "B 站榜单风控中（已自动退避）"
    return False, "B 站榜单无数据"
