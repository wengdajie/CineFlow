"""Bangumi 放送日历（追番「今天几点更新」）。

**为什么要单独做这个**：v1.8.0 起榜单页已有豆瓣「动漫」分类与 B 站番剧榜，
但那两个都是**热度**榜——回答的是"现在大家在看什么"。追番真正缺的信息是
**放送日历**：这部番周几更新、更新到第几话、下一话什么时候播。
热度榜永远给不出这个，因为它的排序维度根本不是时间。

数据源 ``https://api.bgm.tv/calendar``：Bangumi（番组计划）官方公开接口，
**免 API Key、无需签名**，一次返回未来一周七天的在播番剧。这是本项目已知
覆盖日本动画放送表最完整的免费来源。

实测得到的三个坑（都已在下面处理）：

1. **weekday.id 是 1~7（周一=1 … 周日=7）**，与 Python 的
   ``date.weekday()``（周一=0 … 周日=6）**错开一位**。直接拿来当下标或做
   "今天"判断会整体偏一天——这是本模块最容易出错的地方，已抽成
   :func:`bgm_weekday_to_python` 并配了回归测试。
2. **封面地址全部是 ``http://``**（实测 113/113 条无一例外）。若原样下发，
   HTTPS 部署的站点会因混合内容被浏览器拦掉，表现为整页封面空白。
   统一升级成 ``https://`` —— 实测 ``lain.bgm.tv`` 支持 https。
3. **约 10% 的条目没有 ``name_cn``**（冷门番/刚建条目），必须回退到日文
   原名 ``name``，否则卡片标题会是空的。
4. **``images.large`` 是未压缩原图**（实测 300 KB ~ 3 MB、单张最慢 21 秒），
   而卡片只有 ~120px 宽。用它会让图片代理超时返回 502、日历随机裂图。
   改取 ``common``（11 KB / 230 ms），见 :data:`COVER_SIZE_PRIORITY`。

反爬自保沿用 ``douban_chart`` 同策略：缓存 + 失败退避 + 任何异常都返回空，
不让日历拉取失败演变成整页 500。
"""

from __future__ import annotations

import time
from datetime import date
from typing import Any

from app.core.logger import get_logger
from app.utils.http import fetch_json

logger = get_logger(__name__)

CALENDAR_URL = "https://api.bgm.tv/calendar"

#: 日历缓存 1 小时。放送表是**按季度**定的，一天内几乎不会变，
#: 1 小时足够新，又能挡住用户反复切页签造成的重复请求。
_CACHE_TTL = 3600
#: 失败后静默多久再试（对上游的基本礼貌）
_BACKOFF_SECONDS = 300

_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_BACKOFF: float = 0.0

#: Bangumi 要求带可辨识的 UA（裸 python-urllib 容易被限），
#: 按其社区惯例带上项目地址。
UA = "CineFlow/1.12 (+https://github.com/wengdajie/CineFlow)"

#: 星期几的中文名，下标即 Python 的 ``weekday()``（0=周一）。
WEEKDAY_NAMES = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def bgm_weekday_to_python(raw: object) -> int | None:
    """把 Bangumi 的 ``weekday.id``（1~7，周一=1）转成 Python 的 0~6（周一=0）。

    **不要删掉这个函数直接用减一**：它还负责挡住脏数据。实测个别条目的
    ``air_weekday`` 会是 ``0`` 或缺失（放送日未定的番），此时返回 ``None``
    表示"归不到具体某天"，调用方应把它放进"未定"桶而不是错算成周日。
    """
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not 1 <= value <= 7:
        return None
    return value - 1


def is_rate_limited() -> bool:
    """当前是否处于失败退避期。"""
    return time.time() < _BACKOFF


def _mark_rate_limited() -> None:
    global _BACKOFF
    _BACKOFF = time.time() + _BACKOFF_SECONDS
    logger.warning("Bangumi 日历接口不可用，静默 %s 秒后重试", _BACKOFF_SECONDS)


def reset_state() -> None:
    """清空缓存与退避状态（测试用）。"""
    global _BACKOFF
    _CACHE.clear()
    _BACKOFF = 0.0


def _headers() -> dict[str, str]:
    return {"User-Agent": UA, "Accept": "application/json"}


def normalize_cover(raw: object) -> str | None:
    """把 Bangumi 封面地址升级为 https。

    实测接口返回的 ``images.*`` **全部是 http://**（113/113），而 ``lain.bgm.tv``
    本身是支持 https 的。不升级的话，用 https 访问 CineFlow 的用户会因
    混合内容（mixed content）被浏览器直接拦掉图片，表现为番剧封面全空白。
    """
    url = str(raw or "").strip()
    if not url:
        return None
    if url.startswith("http://"):
        url = "https://" + url[len("http://") :]
    return url or None


#: 卡片封面的尺寸优先级。**刻意把 large 放在最后**。
#:
#: 实测（本轮，同一张封面的两个尺寸）：
#:
#: ===========  ==========  ========
#: 尺寸          体积        耗时
#: ===========  ==========  ========
#: ``large``    937 KB      13216 ms
#: ``common``    11 KB        600 ms
#: ===========  ==========  ========
#:
#: ``large`` 是**未压缩原图**，实测区间 300 KB ~ 3 MB、单张最慢 **21 秒**。
#: 而榜单卡片里封面的显示宽度只有 ~120px，原图的像素完全用不上。
#:
#: 后果不只是"慢一点"：图片代理的上游超时是 15s，30 张原图并发时会有一部分
#: 直接超时 → 后端返回 **502** → 前端 onerror 退占位，表现为**新番日历随机裂图**
#: （本轮 ui_check 抓到的那个 502 就是它，实测复现：并发拉 18 张原图，1 张 502）。
#: 又因为这些图和 API **同源**，还会占满浏览器连接池（见 ADR-73）。
#:
#: ``common``（11 KB / 230~600 ms）对 120px 的卡片已经足够清晰。
COVER_SIZE_PRIORITY = ("common", "medium", "large", "grid", "small")


def pick_cover(images: object) -> str | None:
    """从 Bangumi 的 ``images`` 里挑一个**适合卡片**的封面尺寸。

    实测 113/113 条目 ``large/common/medium/small/grid`` 五个尺寸**全都有**，
    所以按 :data:`COVER_SIZE_PRIORITY` 取几乎总能拿到 ``common``；
    仍保留逐级回退，是因为接口没有承诺过字段必然存在，
    真缺字段时应当降级到别的尺寸，而不是让卡片变成没有封面。
    """
    if not isinstance(images, dict):
        return None
    for size in COVER_SIZE_PRIORITY:
        url = normalize_cover(images.get(size))
        if url:
            return url
    return None


def _rating(raw: object) -> float | None:
    """取评分；未开分/无人评价时返回 ``None`` 而不是 0。

    0 分和"还没人评"在界面上是两件事，渲染成 0.0 会让用户以为这番很烂。
    """
    if not isinstance(raw, dict):
        return None
    try:
        score = float(raw.get("score") or 0)
    except (TypeError, ValueError):
        return None
    return score or None


def _normalize(item: dict[str, Any], weekday: int | None) -> dict[str, Any] | None:
    """把 Bangumi 条目转成榜单/日历统一结构。"""
    if not isinstance(item, dict):
        return None
    # name_cn 约 10% 为空（冷门番），必须回退日文原名，否则标题是空的
    title = str(item.get("name_cn") or "").strip() or str(item.get("name") or "").strip()
    if not title:
        return None
    poster = pick_cover(item.get("images"))
    subject_id = item.get("id")
    return {
        "source": "bangumi",
        "category": "calendar",
        "title": title,
        # 原名单独给出：搜资源时日文原名往往比中文译名命中率更高
        "original_title": str(item.get("name") or "").strip() or None,
        "poster": poster,
        "rating": _rating(item.get("rating")),
        "media_type": "anime",
        "weekday": weekday,
        "weekday_label": WEEKDAY_NAMES[weekday] if weekday is not None else "未定",
        "air_date": str(item.get("air_date") or "").strip() or None,
        "total_episodes": int(item.get("eps") or 0) or None,
        "bangumi_id": int(subject_id) if isinstance(subject_id, int) else None,
        "bangumi_url": str(item.get("url") or "").strip() or None,
        "rank": int(item.get("rank") or 0) or None,
    }


async def calendar(*, force: bool = False) -> list[dict[str, Any]]:
    """拉取整周放送日历，返回 7 个「天」的列表（周一 → 周日）。

    每天形如 ``{"weekday": 0, "label": "周一", "items": [...]}``。
    任何失败都返回空列表（调用方渲染"暂无"），绝不抛异常。
    """
    cache_key = "calendar"
    if not force:
        cached = _CACHE.get(cache_key)
        if cached and cached[0] > time.time():
            return cached[1]
    if is_rate_limited():
        return []

    payload = await fetch_json(CALENDAR_URL, headers=_headers(), timeout=15)
    if not isinstance(payload, list) or not payload:
        _mark_rate_limited()
        return []

    # 先建满 7 天的空桶，保证「今天没有番」时结构依然完整、前端不用判空
    buckets: dict[int, list[dict[str, Any]]] = {index: [] for index in range(7)}
    undated: list[dict[str, Any]] = []

    for day in payload:
        if not isinstance(day, dict):
            continue
        weekday = bgm_weekday_to_python((day.get("weekday") or {}).get("id"))
        for raw in day.get("items") or []:
            row = _normalize(raw, weekday)
            if not row:
                continue
            if weekday is None:
                undated.append(row)
            else:
                buckets[weekday].append(row)

    days: list[dict[str, Any]] = []
    for index in range(7):
        items = buckets[index]
        # 同一天内按评分高的在前，没评分的垫底——日历里也希望好番更显眼
        items.sort(key=lambda row: (row.get("rating") or 0), reverse=True)
        for order, row in enumerate(items, start=1):
            row["rank"] = order
        days.append(
            {
                "weekday": index,
                "label": WEEKDAY_NAMES[index],
                "items": items,
                "count": len(items),
            }
        )
    if undated:
        days.append(
            {"weekday": None, "label": "未定", "items": undated, "count": len(undated)}
        )

    _CACHE[cache_key] = (time.time() + _CACHE_TTL, days)
    return days


def today_index(today: date | None = None) -> int:
    """今天对应的 Python 星期下标（0=周一）。抽出来便于测试注入固定日期。"""
    return (today or date.today()).weekday()


async def chart(*, limit: int = 30, offset: int = 0) -> list[dict[str, Any]]:
    """把日历摊平成一条榜单，**从今天开始**排（今天 → 明天 → …）。

    榜单页的「新番」页签用这个：用户最关心的是今天和接下来几天更新什么，
    而不是从周一开始念一遍。
    """
    days = await calendar()
    if not days:
        return []
    start = today_index()
    flat: list[dict[str, Any]] = []
    # 只轮转有具体星期的 7 天；"未定"桶始终垫在最后
    dated = [day for day in days if day.get("weekday") is not None]
    undated = [day for day in days if day.get("weekday") is None]
    for step in range(len(dated)):
        day = dated[(start + step) % len(dated)]
        for row in day.get("items") or []:
            item = dict(row)
            item["days_ahead"] = step
            item["is_today"] = step == 0
            flat.append(item)
    for day in undated:
        for row in day.get("items") or []:
            item = dict(row)
            item["days_ahead"] = None
            item["is_today"] = False
            flat.append(item)

    offset = max(0, int(offset or 0))
    limit = max(1, min(int(limit or 30), 100))
    window = flat[offset : offset + limit]
    for order, row in enumerate(window, start=offset + 1):
        row["rank"] = order
    return window


async def health_check() -> tuple[bool, str]:
    """探活：能拉到日历且有条目即视为可用。"""
    days = await calendar()
    total = sum(int(day.get("count") or 0) for day in days)
    if total:
        return True, f"Bangumi 日历可用，本周在播 {total} 部"
    if is_rate_limited():
        return False, "Bangumi 日历接口退避中（稍后自动恢复）"
    return False, "Bangumi 日历无数据"
