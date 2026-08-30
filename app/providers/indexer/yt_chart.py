"""YouTube 热门榜（Piped 开源 API）。

**为什么不用 yt-dlp 抓官方 Trending**：YouTube 已在 2025 年下线
``/feed/trending`` 页面，实测 yt-dlp 请求该地址会被 302 回首页并报
"channel/playlist does not exist"；社区流传的 "Popular Right Now" 播放列表
仍能抓到，但里面是 2024 年的陈旧条目，拿来当"当前最热"是假数据。

**为什么不用 YouTube Data API v3**：官方接口的 ``chart=mostPopular`` 确实
可用，但需要用户自行申请 API Key 并有每日配额。本项目其余榜单（豆瓣/B 站）
都是开箱即用的，为一个分类引入"必须先配 Key"的门槛不划算。

**所以选 Piped**：开源 YouTube 前端（https://github.com/TeamPiped/Piped）
的公开 API 实例，``GET /trending?region=XX`` 直接返回带封面、播放量、
UP 主的 JSON，免 Key。代价是**公开实例稳定性差**——实测 8 个候选实例只有
1 个可用（其余 DNS 失败/502/403）。因此这里的核心设计是**多实例故障转移**：
按顺序试，谁先返回可用数据就用谁，并把成功的实例记下来优先复用。

拉不到数据时统一返回空列表 + 可读 message，绝不抛异常——榜单空着好过整页 500。
"""

from __future__ import annotations

import time
from typing import Any

from app.core.logger import get_logger
from app.utils.http import fetch_json

logger = get_logger(__name__)

#: Piped 公开实例（按实测可用性排序）。单个实例随时可能挂，所以必须有多个。
#: 用户可通过站点 options 里的 ``instances`` 覆盖这份清单。
DEFAULT_INSTANCES: tuple[str, ...] = (
    "https://api.piped.private.coffee",
    "https://pipedapi.reallyaweso.me",
    "https://pipedapi.drgns.space",
    "https://piped-api.codespace.cz",
    "https://pipedapi.ducks.party",
    "https://api.piped.yt",
)

#: 榜单缓存 30 分钟，与豆瓣同口径：热门榜按天级变化，30 分钟足够新，
#: 又能挡住用户反复切页签打爆本就脆弱的公开实例。
_CACHE_TTL = 1800
_BACKOFF_SECONDS = 300

_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_BACKOFF: float = 0.0
#: 上次成功的实例。下次优先用它，省掉重复试错的等待。
_PREFERRED: str = ""

#: 可选地区。YouTube 热门榜是分地区的，中文用户通常关心港台日韩。
CATEGORIES: dict[str, dict[str, Any]] = {
    "US": {"label": "美国", "region": "US"},
    "JP": {"label": "日本", "region": "JP"},
    "KR": {"label": "韩国", "region": "KR"},
    "HK": {"label": "香港", "region": "HK"},
    "TW": {"label": "台湾", "region": "TW"},
    "GB": {"label": "英国", "region": "GB"},
}


def is_rate_limited() -> bool:
    return time.time() < _BACKOFF


def _mark_rate_limited() -> None:
    global _BACKOFF
    _BACKOFF = time.time() + _BACKOFF_SECONDS
    logger.warning("YouTube 榜单全部实例不可用，静默 %s 秒", _BACKOFF_SECONDS)


def reset_state() -> None:
    """清空缓存、退避与实例偏好（测试用）。"""
    global _BACKOFF, _PREFERRED
    _CACHE.clear()
    _BACKOFF = 0.0
    _PREFERRED = ""


def _instance_order(instances: tuple[str, ...]) -> list[str]:
    """把上次成功的实例排到最前，避免每次都从头试一遍。"""
    ordered = list(instances)
    if _PREFERRED and _PREFERRED in ordered:
        ordered.remove(_PREFERRED)
        ordered.insert(0, _PREFERRED)
    return ordered


def _thumb(url: str) -> str | None:
    """封面地址规范化。

    Piped 默认把缩略图代理到自己域名（``proxy.<instance>/vi/...?host=i.ytimg.com``），
    那个代理常年不稳定。这里还原成 YouTube 官方图床直链——实测浏览器能直连
    ``i.ytimg.com``，少一跳更快也更稳。
    """
    raw = str(url or "").strip()
    if not raw:
        return None
    if "/vi/" in raw and "host=i.ytimg.com" in raw:
        tail = raw.split("/vi/", 1)[1].split("?", 1)[0]
        return f"https://i.ytimg.com/vi/{tail}"
    if raw.startswith("//"):
        return "https:" + raw
    return raw if raw.startswith("https://") else None


def _normalize(item: dict[str, Any], region: str) -> dict[str, Any] | None:
    """把 Piped 条目转成与豆瓣/B 站一致的榜单结构。"""
    title = str(item.get("title") or "").strip()
    path = str(item.get("url") or "").strip()
    if not title or not path:
        return None
    # Piped 给的是站内相对路径 /watch?v=xxx，要还原成真实 YouTube 地址，
    # 否则前端「下载」按钮把相对路径丢给 yt-dlp 会直接失败。
    video_id = ""
    if "v=" in path:
        video_id = path.split("v=", 1)[1].split("&", 1)[0]
    if not video_id:
        return None
    duration = int(item.get("duration") or 0)
    return {
        "source": "youtube",
        "category": region,
        "title": title,
        "poster": _thumb(item.get("thumbnail") or ""),
        # 播放量投影到 heat，与 B 站同口径（ADR-32）
        "heat": max(0, int(item.get("views") or 0)),
        "likes": 0,
        "uploader": str(item.get("uploaderName") or "").strip() or None,
        # 直播中 Piped 给 duration=-1，负数会让前端时长显示成乱码
        "duration": duration if duration > 0 else 0,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "video_id": video_id,
        "media_type": "",
        "rating": None,
        "desc": str(item.get("shortDescription") or "").strip()[:200] or None,
        "is_live": duration < 0,
    }


async def chart(
    region: str,
    *,
    limit: int = 30,
    offset: int = 0,
    instances: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """拉一个地区的 YouTube 热门榜。任何失败都返回空列表。

    Piped 的 ``/trending`` **不支持分页**（一次固定返回约 15~50 条），
    所以这里在本地做切片：``offset`` 超出可用条数时自然返回空，
    前端据此判定"已到底"。
    """
    global _PREFERRED
    meta = CATEGORIES.get(region)
    if not meta:
        return []
    limit = max(1, min(int(limit or 30), 100))
    offset = max(0, int(offset or 0))

    # 整份地区榜只缓存一次，分页在缓存之上切片，避免同一地区反复外网请求
    cache_key = f"yt:{region}"
    cached = _CACHE.get(cache_key)
    if cached and cached[0] > time.time():
        return cached[1][offset : offset + limit]
    if is_rate_limited():
        return []

    pool = instances or DEFAULT_INSTANCES
    rows: list[dict[str, Any]] = []
    for base in _instance_order(pool):
        url = f"{base.rstrip('/')}/trending?region={meta['region']}"
        payload = await fetch_json(url, timeout=15)
        if not isinstance(payload, list) or not payload:
            logger.debug("Piped 实例 %s 无数据，换下一个", base)
            continue
        for raw in payload:
            if isinstance(raw, dict):
                row = _normalize(raw, region)
                if row:
                    rows.append(row)
        if rows:
            _PREFERRED = base
            logger.info("YouTube 榜单来自实例 %s，%s 条", base, len(rows))
            break

    if not rows:
        _mark_rate_limited()
        return []
    _CACHE[cache_key] = (time.time() + _CACHE_TTL, rows)
    return rows[offset : offset + limit]


async def health_check() -> tuple[bool, str]:
    """探活：能拉到美国热门榜就算通。"""
    items = await chart("US", limit=5)
    if items:
        return True, f"YouTube 榜单可用（实例 {_PREFERRED or '未知'}），{len(items)} 条"
    if is_rate_limited():
        return False, "YouTube 榜单全部公开实例不可用（已自动退避）"
    return False, "YouTube 榜单无数据"


def partitions() -> list[dict[str, str]]:
    """可用地区清单，供前端渲染二级切换。"""
    return [{"key": key, "label": str(meta["label"])} for key, meta in CATEGORIES.items()]
