"""发现榜单（豆瓣 / B 站 / 本地可用站点）。

**为什么与 ``trending.py`` 分开**：``trending.py`` 算的是「**你自己站点**搜出来的
资源有多热」，数据源是本地 ``resources`` 表，是**回顾性**的；本模块回答的是
「**现在外面**有什么值得看」，数据源是豆瓣与 B 站的公开榜单，是**发现性**的。
两者口径不同，混在一个函数里会让「热度」这个字段含义不清。

四个分类榜（电影 / 电视剧 / 动漫 / 综艺）以**豆瓣为主源**，
并用本地已有资源做「可下载」标注——这是本项目相对纯榜单站的价值：
榜单上能直接看出**哪几部你的站点已经有片源了**。

Bilibili 榜单单独一个页签，因为它是视频站榜单（UGC 投稿 + PGC 番剧），
条目形态与影视作品不同（有 UP 主、播放量，没有"季"的概念）。
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select

from app.core.logger import get_logger
from app.core.meta import parse
from app.db.models import ResourceRecord
from app.db.session import session_scope
from app.providers.indexer import bili_chart, yt_chart
from app.providers.metadata import bangumi, douban_chart
from app.services.trending import _canonical_title

logger = get_logger(__name__)

#: 四个影视分类榜 + B 站 + YouTube，前端页签就是照这个顺序渲染。
#:
#: ``kind`` 决定这一类的**交互形态**，前端据此决定显示什么按钮：
#: ``media`` 是影视作品（要先搜资源再下载），``video`` 是单个视频
#: （已经有确切播放地址，直接交给 yt-dlp 下载即可，不需要搜资源）。
#: 把这个判断放后端下发，前端就不用写死"哪些分类是视频站"。
CATEGORIES: dict[str, dict[str, Any]] = {
    "movie": {"label": "电影", "source": "douban", "douban": "movie", "kind": "media"},
    "tv": {"label": "电视剧", "source": "douban", "douban": "tv", "kind": "media"},
    "anime": {"label": "动漫", "source": "douban", "douban": "anime", "kind": "media"},
    "show": {"label": "综艺", "source": "douban", "douban": "show", "kind": "media"},
    "bilibili": {
        "label": "Bilibili", "source": "bilibili", "bili": "all", "kind": "video",
    },
    "youtube": {
        "label": "YouTube", "source": "youtube", "region": "US", "kind": "video",
    },
    # 新番放送日历：与「动漫」页签**口径不同**——动漫榜是豆瓣热度
    # （现在大家在看什么），这里是 Bangumi 放送表（这周哪天更新第几话）。
    # 追番真正需要的是后者，热度榜给不出"周几更新"。
    "bangumi": {
        "label": "新番", "source": "bangumi", "kind": "media",
    },
}


def _local_titles() -> dict[str, dict[str, Any]]:
    """本地资源标题索引，用于给榜单条目标注「已有片源」。

    用 ``_canonical_title`` 归一化（去掉年份/清晰度/字幕组等噪声），
    否则「凡人修仙传」和「凡人修仙传.S01.1080p」会被当成两部作品。
    """
    index: dict[str, dict[str, Any]] = {}
    try:
        with session_scope() as session:
            rows = session.execute(
                select(ResourceRecord.title, ResourceRecord.site).limit(5000)
            ).all()
    except Exception as exc:  # 数据库异常不该让榜单整页失败
        logger.warning("读取本地资源标题失败：%s", exc)
        return index

    for title, site in rows:
        raw = str(title or "")
        # 同时索引「原始标题」与「识别出的作品名」两种归一化结果：
        # 站点标题常带发布组前缀，只归一化一种会漏匹配。
        candidates = {_canonical_title(raw)}
        parsed_title = parse(raw).title
        if parsed_title:
            candidates.add(_canonical_title(parsed_title))
        for key in candidates:
            if not key:
                continue
            entry = index.setdefault(key, {"count": 0, "sites": set()})
            entry["count"] += 1
            if site:
                entry["sites"].add(str(site))
    return index


def _annotate_local(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """给榜单条目补 ``local_count`` / ``local_sites``（有没有现成片源）。"""
    index = _local_titles()
    if not index:
        for item in items:
            item["local_count"] = 0
            item["local_sites"] = []
        return items
    for item in items:
        key = _canonical_title(str(item.get("title") or ""))
        entry = index.get(key)
        item["local_count"] = int(entry["count"]) if entry else 0
        item["local_sites"] = sorted(entry["sites"])[:5] if entry else []
    return items


async def chart(
    category: str, *, limit: int = 24, offset: int = 0
) -> dict[str, Any]:
    """取一个分类榜。任何来源失败都返回空 items + 可读 message，不抛异常。

    ``offset`` 供前端下拉加载更多。返回体带 ``has_more``，让前端知道
    还能不能继续翻——两个来源的可翻上限不同（豆瓣约 300 条，B 站一次给全量），
    统一用「这一页是否被取满」来判断，避免把上限写死在前端。
    """
    meta = CATEGORIES.get(category)
    if not meta:
        return {
            "category": category,
            "label": category,
            "source": "",
            "kind": "media",
            "items": [],
            "count": 0,
            "offset": 0,
            "has_more": False,
            "message": f"未知分类：{category}",
        }

    offset = max(0, int(offset or 0))
    items: list[dict[str, Any]] = []
    message = ""
    if meta["source"] == "douban":
        items = await douban_chart.chart(
            str(meta["douban"]), limit=limit, offset=offset
        )
        if not items and offset == 0:
            message = (
                "豆瓣限流中，已自动退避，请稍后重试"
                if douban_chart.is_rate_limited()
                else "豆瓣榜单暂无数据（可能是网络不通）"
            )
    elif meta["source"] == "bangumi":
        items = await bangumi.chart(limit=limit, offset=offset)
        if not items and offset == 0:
            message = (
                "Bangumi 日历接口退避中，请稍后重试"
                if bangumi.is_rate_limited()
                else "Bangumi 放送日历暂无数据（可能是网络不通）"
            )
    elif meta["source"] == "youtube":
        items = await yt_chart.chart(str(meta["region"]), limit=limit, offset=offset)
        if not items and offset == 0:
            message = (
                "YouTube 公开榜单实例暂不可用，已自动退避"
                if yt_chart.is_rate_limited()
                else "YouTube 榜单暂无数据（需要能访问外网）"
            )
    else:
        items = await bili_chart.chart(str(meta["bili"]), limit=limit, offset=offset)
        if not items and offset == 0:
            message = (
                "B 站风控中，已自动退避，请稍后重试"
                if bili_chart.is_rate_limited()
                else "B 站榜单暂无数据（可能是网络不通）"
            )

    items = _annotate_local(list(items))
    # 名次要接着上一页continue，不能每页都从 1 开始
    for index, item in enumerate(items, start=offset + 1):
        item["rank"] = index
    return {
        "category": category,
        "label": str(meta["label"]),
        "source": str(meta["source"]),
        # media=影视作品（要搜资源）/ video=单个视频（可直接下载）
        "kind": str(meta.get("kind") or "media"),
        "items": items,
        "count": len(items),
        "offset": offset,
        # 取满这一页就认为还有下一页；取不满说明已到底
        "has_more": len(items) >= limit,
        "message": message,
    }


async def overview(*, limit: int = 12) -> dict[str, Any]:
    """一次并发拉全部分类，用于首屏与「全部」视图。"""
    names = list(CATEGORIES)
    results = await asyncio.gather(
        *(chart(name, limit=limit) for name in names), return_exceptions=True
    )
    charts: list[dict[str, Any]] = []
    for name, result in zip(names, results, strict=True):
        if isinstance(result, BaseException):
            logger.warning("发现榜单 %s 失败：%s", name, result)
            charts.append(
                {
                    "category": name,
                    "label": str(CATEGORIES[name]["label"]),
                    "source": str(CATEGORIES[name]["source"]),
                    "kind": str(CATEGORIES[name].get("kind") or "media"),
                    "items": [],
                    "count": 0,
                    "message": "拉取失败",
                }
            )
        else:
            charts.append(result)
    return {"charts": charts, "categories": categories()}


async def bangumi_calendar() -> dict[str, Any]:
    """整周放送日历（周一→周日七个桶），并标注今天是哪一天。

    与 ``chart("bangumi")`` 的区别：那个把日历**摊平成一条榜单**（从今天起排），
    适合复用现有榜单卡片；这个保留**按天分组**的结构，前端才能画出
    "周一 | 周二 | …"的日历视图。两者共用同一份缓存，不会多打请求。
    """
    days = await bangumi.calendar()
    today = bangumi.today_index()
    for day in days:
        day["items"] = _annotate_local(list(day.get("items") or []))
        day["is_today"] = day.get("weekday") == today
    total = sum(int(day.get("count") or 0) for day in days)
    return {
        "source": "bangumi",
        "today": today,
        "today_label": bangumi.WEEKDAY_NAMES[today],
        "days": days,
        "total": total,
        "message": ""
        if total
        else (
            "Bangumi 日历接口退避中，请稍后重试"
            if bangumi.is_rate_limited()
            else "Bangumi 放送日历暂无数据（可能是网络不通）"
        ),
    }


def categories() -> list[dict[str, str]]:
    """页签元数据，前端据此渲染，新增分类无需改前端。

    带上 ``kind`` 让前端知道该分类的卡片要显示「搜资源」还是「下载」——
    这个判断属于数据源的性质，写在后端才不会两边漂移。
    """
    return [
        {
            "key": key,
            "label": str(meta["label"]),
            "source": str(meta["source"]),
            "kind": str(meta.get("kind") or "media"),
        }
        for key, meta in CATEGORIES.items()
    ]


async def bili_categories_chart(
    category: str, *, limit: int = 24, offset: int = 0
) -> dict[str, Any]:
    """B 站细分分区榜（番剧/国创/电影…），供 Bilibili 页签内二级切换。"""
    if category not in bili_chart.CATEGORIES:
        return {
            "category": category,
            "label": category,
            "source": "bilibili",
            "kind": "video",
            "items": [],
            "count": 0,
            "offset": 0,
            "has_more": False,
            "message": "未知分区",
        }
    offset = max(0, int(offset or 0))
    items = await bili_chart.chart(category, limit=limit, offset=offset)
    items = _annotate_local(list(items))
    for index, item in enumerate(items, start=offset + 1):
        item["rank"] = index
    return {
        "category": category,
        "label": str(bili_chart.CATEGORIES[category]["label"]),
        "source": "bilibili",
        # B 站条目是单个视频，可直接下载——前端据此显示「下载」而不是「搜资源」
        "kind": "video",
        "items": items,
        "count": len(items),
        "offset": offset,
        "has_more": len(items) >= limit,
        "message": ""
        if items or offset
        else "该分区暂无数据或正在风控退避",
    }


def bili_partitions() -> list[dict[str, str]]:
    """B 站可用分区清单。"""
    return [
        {"key": key, "label": str(meta["label"])}
        for key, meta in bili_chart.CATEGORIES.items()
    ]


async def yt_region_chart(
    region: str, *, limit: int = 30, offset: int = 0
) -> dict[str, Any]:
    """YouTube 地区榜（美国/日本/韩国…），供 YouTube 页签内二级切换。

    结构与 ``bili_categories_chart`` 保持一致，前端两个视频页签可共用渲染。
    """
    if region not in yt_chart.CATEGORIES:
        return {
            "category": region,
            "label": region,
            "source": "youtube",
            "kind": "video",
            "items": [],
            "count": 0,
            "offset": 0,
            "has_more": False,
            "message": "未知地区",
        }
    offset = max(0, int(offset or 0))
    items = await yt_chart.chart(region, limit=limit, offset=offset)
    items = _annotate_local(list(items))
    for index, item in enumerate(items, start=offset + 1):
        item["rank"] = index
    return {
        "category": region,
        "label": str(yt_chart.CATEGORIES[region]["label"]),
        "source": "youtube",
        "kind": "video",
        "items": items,
        "count": len(items),
        "offset": offset,
        "has_more": len(items) >= limit,
        "message": ""
        if items or offset
        else (
            "YouTube 公开榜单实例暂不可用，已自动退避"
            if yt_chart.is_rate_limited()
            else "该地区暂无数据（需要能访问外网）"
        ),
    }


def yt_regions() -> list[dict[str, str]]:
    """YouTube 可用地区清单。"""
    return yt_chart.partitions()
