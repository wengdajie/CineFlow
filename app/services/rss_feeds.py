"""RSS 追新引擎：多站点 RSS 源 + 聚合流分流下载。

设计参考 [Auto_Bangumi](https://github.com/EstrellaXD/Auto_Bangumi) 的 RSS
引擎，但落到本项目已有的订阅/下载链路上，不另起一套番剧模型。

与既有两条追新路径的分工（三者互补，不是替代）：

| 路径 | 数据来源 | 适合什么 |
| --- | --- | --- |
| 订阅巡检 | 逐个订阅去各站**搜索** | 补历史缺集 |
| 追新雷达 | 各站 ``fetch_latest()`` | 已配站点的最新流 |
| **RSS 追新**（本模块） | 用户自己贴的 RSS 地址 | 番剧站/PT 个人订阅流，**不支持搜索**的源 |

第三条之所以必须单独存在：番剧 RSS（Mikan「我的番组」、dmhy 分类流）
**根本不支持关键词查询**，塞进搜索链路只会每次白等一轮超时；
但它们恰恰是追新时效性最好的源（发布即出现在流里）。

**核心是聚合流分流**。一条聚合 RSS 里混着几十部作品，处理方式必须是
"先识别每条是什么，再看它是否命中订阅"，而不是全量下载：

    拉取 RSS → 方言解析 → 去掉处理过的 guid → 正则过滤
    → aggregate ? 按标题匹配订阅（未命中则跳过） : 直接归到绑定订阅
    → 解析季集 → 只保留缺集 → 过滤打分 → 择优投递

⚠️ **增量判据用 guid 而不是"发布时间晚于上次检查"**：RSS 的 pubDate
不可靠（不少站点重新发布/修种子时会刷新时间，也有站点整条流共用一个时间），
按时间判断会重复下载或漏下。guid 是站点给的稳定标识（Mikan 用种子 hash、
Nyaa 用 view 链接），比时间可靠得多。
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select

from app.core.filters import FilterRule, filter_and_rank
from app.core.logger import get_logger
from app.core.meta import parse
from app.core.rss_dialects import RssEntry, detect_dialect, parse_feed
from app.db.base import utcnow
from app.db.models import RssFeed
from app.db.session import session_scope
from app.providers.base import Resource
from app.schemas.enums import (
    EventType,
    MediaType,
    NotifyLevel,
    ResourceKind,
)
from app.utils.http import fetch_text
from app.utils.strings import truncate

logger = get_logger(__name__)

#: ``handled_guids`` 最多保留多少条。RSS 通常只暴露最近几十条，
#: 留 500 条足够覆盖"翻页翻不到的旧条目"，又不会让这一列无限膨胀。
MAX_HANDLED = 500

#: 连续失败多少次后自动停用（与视频追更一致的口径）
MAX_FAILURES = 5

#: 同一站点的多条 feed 之间的间隔（秒）。Auto_Bangumi 因同站并发被 429
#: 才加了这个延迟，本项目沿用：追新是分钟级任务，慢一点无所谓，被封才致命。
PER_HOST_DELAY = 2.0


def _per_host_delay() -> float:
    """同站间隔（可在设置页调，读不到配置就用模块默认值）。"""
    from app.core.config import settings

    try:
        return max(0.0, float(getattr(settings, "RSS_PER_HOST_DELAY", PER_HOST_DELAY)))
    except (TypeError, ValueError):
        return PER_HOST_DELAY


def _compile(pattern: str | None) -> re.Pattern[str] | None:
    """编译用户填的正则；写错了就当作没填而不是让整轮挂掉。"""
    text = str(pattern or "").strip()
    if not text:
        return None
    try:
        return re.compile(text, re.IGNORECASE)
    except re.error as exc:
        logger.warning("RSS 过滤正则无效（已忽略）：%s（%s）", text, exc)
        return None


def title_allowed(
    title: str, include: str | None = None, exclude: str | None = None
) -> bool:
    """标题是否通过包含/排除过滤。

    **排除优先于包含**：两者同时命中时判为不要。用户写排除词的意图
    通常比包含词更明确（"什么都行，但绝对不要生肉"）。
    """
    text = str(title or "")
    exclude_re = _compile(exclude)
    if exclude_re is not None and exclude_re.search(text):
        return False
    include_re = _compile(include)
    return include_re is None or bool(include_re.search(text))


def _to_resource(entry: RssEntry, feed_name: str) -> dict[str, Any]:
    """RSS 条目 → 资源字典（复用既有过滤打分与下载链路）。"""
    resource = Resource(
        title=entry.title,
        link=entry.link,
        site=feed_name,
        kind=(
            ResourceKind.MAGNET.value if entry.is_magnet else ResourceKind.TORRENT.value
        ),
        page_url=entry.homepage,
        description=entry.description,
        size=entry.size,
        seeders=entry.seeders,
        leechers=entry.leechers,
        grabs=entry.grabs,
        publish_at=entry.publish_at,
        extra={**entry.extra, "rss_guid": entry.guid},
    )
    return resource.to_dict()


async def fetch_entries(
    url: str, *, cookie: str | None = None, timeout: float | None = None
) -> tuple[str, str, list[RssEntry]]:
    """拉取并解析一条 RSS，返回 ``(feed 标题, 方言, 条目)``。"""
    text = await fetch_text(
        url, headers={"Cookie": cookie or ""}, timeout=timeout
    )
    if not text:
        return "", "generic", []
    return parse_feed(text, url=url)


async def preview(
    url: str, *, cookie: str | None = None, limit: int = 20
) -> dict[str, Any]:
    """试拉一条 RSS 看看能解析出什么（**落库前必须先能看**）。

    加这个入口的理由和 AI 建站点一样：用户贴进来的地址对不对、
    是不是聚合流、能不能拿到体积与做种数，只有真拉一次才知道。
    不给预览就只能"先存下来，等下一轮定时任务过去了再看有没有动静"。
    """
    feed_title, dialect, entries = await fetch_entries(url, cookie=cookie)
    if not entries:
        return {
            "success": False,
            "message": (
                "没有解析出任何条目：请确认这是 RSS/Atom 地址（不是网页地址），"
                "需要登录的源要填 Cookie"
            ),
            "title": feed_title,
            "dialect": dialect,
            "items": [],
        }

    # 一条流里出现多个不同作品名 → 判定为聚合流，作为 aggregate 的默认建议
    titles = {parse(entry.title).title for entry in entries[:30]}
    titles.discard("")
    suggest_aggregate = len(titles) > 1

    items = []
    for entry in entries[:limit]:
        info = parse(entry.title)
        items.append(
            {
                "title": entry.title,
                "parsed_title": info.title,
                "season": info.season,
                "episodes": info.episodes,
                "resolution": info.resolution,
                "size": entry.size,
                "seeders": entry.seeders,
                "publish_at": entry.publish_at.isoformat() if entry.publish_at else None,
                "kind": "magnet" if entry.is_magnet else "torrent",
                "guid": entry.guid,
            }
        )
    return {
        "success": True,
        "message": f"解析到 {len(entries)} 条，识别出 {len(titles)} 部作品",
        "title": feed_title or detect_dialect(url=url),
        "dialect": dialect,
        "suggest_aggregate": suggest_aggregate,
        "distinct_titles": sorted(titles)[:20],
        "total": len(entries),
        "items": items,
    }


def _active_subscribes() -> list[dict[str, Any]]:
    """活跃订阅及其缺集（脱离 Session 的纯数据，供标题匹配用）。"""
    from app.services import radar as radar_service

    return radar_service._load_active_subscribes()


def match_subscribe(
    title: str, subscribes: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """把一条 RSS 标题匹配到订阅（复用雷达的匹配口径，避免两套规则漂移）。"""
    from app.services import radar as radar_service

    return radar_service.match_subscribe(title, subscribes)
def _snapshot(record: RssFeed) -> dict[str, Any]:
    """把记录拍成纯数据（后续网络请求期间不持有 Session）。"""
    return {
        "id": record.id,
        "name": record.name,
        "url": record.url,
        "dialect": record.dialect or "generic",
        "aggregate": bool(record.aggregate),
        "cookie": record.cookie,
        "include_regex": record.include_regex,
        "exclude_regex": record.exclude_regex,
        "save_path": record.save_path,
        "subscribe_id": record.subscribe_id,
        "max_per_run": max(1, int(record.max_per_run or 5)),
        "handled_guids": list(record.handled_guids or []),
        "skip_existing": bool(record.skip_existing),
    }


def _apply_success(
    feed_id: int,
    new_guids: list[str],
    *,
    dialect: str,
    downloaded: int,
    message: str,
) -> None:
    """写回成功结果：合并 guid、清零失败计数、更新方言。"""
    with session_scope() as session:
        record = session.get(RssFeed, feed_id)
        if not record:
            return
        if new_guids:
            merged = list(record.handled_guids or [])
            merged.extend(item for item in new_guids if item not in merged)
            record.handled_guids = merged[-MAX_HANDLED:]
        if dialect and dialect != record.dialect:
            # 方言由 feed 自述判定，第一次真拉过才知道，落库省掉后续重复判断
            record.dialect = dialect
        record.failure_count = 0
        record.total_downloaded = int(record.total_downloaded or 0) + int(downloaded)
        record.last_message = message[:500]
        record.last_checked_at = utcnow()


async def _record_failure(feed_id: int, error: str, *, notify: bool = True) -> dict[str, Any]:
    """记一次失败；连续失败到阈值就自动停用，不再无意义重试。"""
    disabled = False
    name = ""
    with session_scope() as session:
        record = session.get(RssFeed, feed_id)
        if not record:
            return {"success": False, "message": "RSS 源不存在"}
        name = record.name
        record.failure_count = int(record.failure_count or 0) + 1
        record.last_message = error[:500]
        record.last_checked_at = utcnow()
        if record.failure_count >= MAX_FAILURES:
            record.enabled = False
            disabled = True
    if disabled:
        logger.warning("RSS 源 #%s 连续失败 %s 次，已自动停用", feed_id, MAX_FAILURES)
        if notify:
            from app.services import notify as notify_service

            await notify_service.send(
                f"RSS 源已停用：{truncate(name, 40)}",
                f"连续 {MAX_FAILURES} 次拉取失败，最后一次：{error}",
                level=NotifyLevel.WARNING.value,
                event=EventType.SYSTEM_ERROR.value,
            )
    return {
        "success": False,
        "downloaded": 0,
        "message": error,
        "disabled": disabled,
        "new": 0,
    }


async def check_feed(
    feed_id: int, *, dry_run: bool = False, notify: bool = True
) -> dict[str, Any]:
    """巡检一条 RSS 源：取新条目 → 分流 → 过滤打分 → 投递下载。

    ``dry_run`` 只算不下、也**不写回 guid**，供界面「试运行」用；否则试跑
    一次就把条目标记成已处理，真正巡检时反而什么都不下了。
    """
    with session_scope() as session:
        record = session.get(RssFeed, feed_id)
        if not record:
            return {"success": False, "message": "RSS 源不存在"}
        feed = _snapshot(record)

    try:
        feed_title, dialect, entries = await fetch_entries(
            feed["url"], cookie=feed["cookie"]
        )
    except Exception as exc:
        return await _record_failure(feed_id, f"拉取失败：{exc}"[:200], notify=notify)

    if not entries:
        return await _record_failure(
            feed_id, "没有解析出任何条目（地址是否为 RSS？需要登录的源要填 Cookie）",
            notify=notify,
        )

    handled = set(feed["handled_guids"])
    fresh = [entry for entry in entries if (entry.guid or entry.link) not in handled]
    all_guids = [entry.guid or entry.link for entry in entries if entry.guid or entry.link]

    # 首次拉取默认只记账不下载：老 RSS 里躺着几十条历史条目，
    # 不这样做的话「新加一个源」等于立刻投出几十个下载任务。
    if feed["skip_existing"] and not handled:
        if not dry_run:
            _apply_success(
                feed_id, all_guids, dialect=dialect, downloaded=0,
                message=f"首次拉取：记录 {len(all_guids)} 条历史条目，下轮起只下新增",
            )
        return {
            "success": True,
            "downloaded": 0,
            "new": 0,
            "total": len(entries),
            "dialect": dialect,
            "feed_title": feed_title,
            "first_run": True,
            "message": f"首次拉取已记录 {len(all_guids)} 条历史条目，下一轮起只处理新增",
            "items": [],
        }

    allowed = [
        entry
        for entry in fresh
        if title_allowed(entry.title, feed["include_regex"], feed["exclude_regex"])
    ]
    if not allowed:
        message = (
            f"共 {len(entries)} 条，其中 {len(fresh)} 条是新条目但全部被过滤规则排除"
            if fresh
            else f"没有新条目（共 {len(entries)} 条均已处理过）"
        )
        if not dry_run:
            _apply_success(
                feed_id, [entry.guid or entry.link for entry in fresh],
                dialect=dialect, downloaded=0, message=message,
            )
        return {
            "success": True, "downloaded": 0, "new": len(fresh),
            "total": len(entries), "dialect": dialect, "feed_title": feed_title,
            "message": message, "items": [],
        }

    from app.services import download as download_service
    from app.services import radar as radar_service

    subscribes = _active_subscribes() if feed["aggregate"] else []
    index = {item["id"]: item for item in subscribes}

    picked: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    if feed["aggregate"]:
        # 聚合流：一条 RSS 混着多部作品，逐条识别后只下命中订阅的
        grouped: dict[int, list[dict[str, Any]]] = {}
        for entry in allowed:
            target = match_subscribe(entry.title, subscribes)
            if target is None:
                skipped.append({"title": entry.title, "reason": "未命中任何活跃订阅"})
                continue
            payload = _to_resource(entry, feed["name"])
            payload["_meta"] = parse(entry.title)
            grouped.setdefault(target["id"], []).append(payload)

        for subscribe_id, candidates in grouped.items():
            item = index[subscribe_id]
            missing = set(item["missing"])
            if not missing:
                skipped.append(
                    {"title": item["title"], "reason": "该订阅已无缺集"}
                )
                continue
            rule: FilterRule = item["rule"]
            rule.episodes = sorted(missing)
            rule.season = (
                item["season"]
                if item["media_type"] != MediaType.MOVIE.value
                else None
            )
            # RSS 条目本身没有做种数（多数番剧站不给），这里放宽以免整流被滤空
            rule.min_seeders = 0
            ranked = filter_and_rank(candidates, rule)
            if not ranked:
                skipped.append(
                    {"title": item["title"], "reason": "新条目均未通过订阅过滤规则"}
                )
                continue
            for resource in radar_service._pick(ranked, missing, item):
                resource["_subscribe_id"] = subscribe_id
                resource["_subscribe"] = item["title"]
                resource["_save_path"] = feed["save_path"] or item["save_path"]
                picked.append(resource)
    else:
        # 单番流：整条 RSS 都是同一部作品，直接按顺序取
        bound = feed["subscribe_id"]
        for entry in allowed:
            payload = _to_resource(entry, feed["name"])
            payload["_meta"] = parse(entry.title)
            payload["_subscribe_id"] = bound
            payload["_subscribe"] = feed["name"]
            payload["_save_path"] = feed["save_path"]
            picked.append(payload)

    picked = picked[: feed["max_per_run"]]

    downloads: list[dict[str, Any]] = []
    for resource in picked:
        subscribe_id = resource.pop("_subscribe_id", None)
        subscribe_name = resource.pop("_subscribe", "")
        save_path = resource.pop("_save_path", None)
        info = resource.pop("_meta", None)
        if info is not None:
            resource["meta"] = info.to_dict()
        entry_info = {
            "title": resource.get("title"),
            "subscribe": subscribe_name,
            "subscribe_id": subscribe_id,
            "kind": resource.get("kind"),
            "size": resource.get("size"),
            "episodes": (resource.get("meta") or {}).get("episodes"),
        }
        if dry_run:
            downloads.append({**entry_info, "dry_run": True})
            continue
        task = await download_service.add_download(
            resource, subscribe_id=subscribe_id, save_path=save_path, notify=False
        )
        if task:
            downloads.append(entry_info)
        else:
            skipped.append(
                {"title": resource.get("title"), "reason": "下载器未接受（重复或无可用下载器）"}
            )

    real = [item for item in downloads if not item.get("dry_run")]
    message = (
        f"新增 {len(real)} 个下载（新条目 {len(fresh)} 条 / 通过过滤 {len(allowed)} 条）"
        if real
        else f"新条目 {len(fresh)} 条，未产生下载"
    )
    if not dry_run:
        _apply_success(
            feed_id,
            [entry.guid or entry.link for entry in fresh],
            dialect=dialect,
            downloaded=len(real),
            message=message,
        )
        if real and notify:
            from app.services import notify as notify_service

            lines = [
                "· " + truncate(str(item.get("title") or ""), 56) for item in real[:5]
            ]
            label = truncate(str(feed["name"]), 24)
            await notify_service.send(
                f"RSS 追新：{label} 新增 {len(real)} 个下载",
                chr(10).join(lines),
                level=NotifyLevel.SUCCESS.value,
                event=EventType.RESOURCE_MATCHED.value,
                payload={"source": "rss", "feed_id": feed_id, "count": len(real)},
            )

    return {
        "success": True,
        "downloaded": len(real),
        "new": len(fresh),
        "total": len(entries),
        "dialect": dialect,
        "feed_title": feed_title,
        "aggregate": feed["aggregate"],
        "message": message,
        "items": downloads,
        "skipped": skipped[:20],
        "dry_run": dry_run,
    }
async def run(
    *, limit: int = 0, dry_run: bool = False, notify: bool = True
) -> dict[str, Any]:
    """巡检全部启用的 RSS 源（定时任务入口）。

    多条 feed 之间**串行**并在同站之间留间隔：Auto_Bangumi 踩过的坑是同一站点
    的多条 RSS 一次性并发请求会被 429（它加了 ``RSS_PER_HOST_DELAY``）。
    追新是分钟级任务，串行慢一点无所谓，被封站才是真问题。
    """
    started = time.perf_counter()
    with session_scope() as session:
        rows = list(
            session.execute(
                select(RssFeed.id, RssFeed.url)
                .where(RssFeed.enabled.is_(True))
                .order_by(RssFeed.id.asc())
            )
        )
    if limit and limit > 0:
        rows = rows[:limit]
    if not rows:
        return {
            "success": True, "checked": 0, "downloaded": 0,
            "message": "没有启用的 RSS 源", "items": [],
        }

    items: list[dict[str, Any]] = []
    total = 0
    last_host = ""
    for feed_id, url in rows:
        host = urlparse(str(url or "")).netloc.lower()
        if host and host == last_host:
            await asyncio.sleep(_per_host_delay())
        last_host = host
        try:
            result = await check_feed(feed_id, dry_run=dry_run, notify=notify)
        except Exception as exc:  # 单条异常不该中断整轮
            logger.error("RSS 源 #%s 巡检异常：%s", feed_id, exc)
            items.append({"id": feed_id, "success": False, "message": str(exc)[:200]})
            continue
        total += int(result.get("downloaded") or 0)
        items.append({"id": feed_id, **result})

    elapsed = int((time.perf_counter() - started) * 1000)
    logger.info(
        "RSS 追新完成：%d 个源 / 新增 %d 个下载 / %dms", len(rows), total, elapsed
    )
    return {
        "success": True,
        "checked": len(rows),
        "downloaded": total,
        "elapsed_ms": elapsed,
        "dry_run": dry_run,
        "items": items,
    }


def to_dict(record: RssFeed) -> dict[str, Any]:
    """对外输出结构（``handled_guids`` 只给数量，不塞几百个 ID 进响应体）。"""
    return {
        "id": record.id,
        "name": record.name,
        "url": record.url,
        "dialect": record.dialect or "generic",
        "aggregate": bool(record.aggregate),
        "enabled": bool(record.enabled),
        "has_cookie": bool(record.cookie),
        "include_regex": record.include_regex,
        "exclude_regex": record.exclude_regex,
        "save_path": record.save_path,
        "subscribe_id": record.subscribe_id,
        "max_per_run": int(record.max_per_run or 5),
        "handled_count": len(record.handled_guids or []),
        "skip_existing": bool(record.skip_existing),
        "failure_count": int(record.failure_count or 0),
        "total_downloaded": int(record.total_downloaded or 0),
        "last_message": record.last_message,
        "last_checked_at": record.last_checked_at.isoformat()
        if record.last_checked_at
        else None,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }


def list_feeds() -> list[dict[str, Any]]:
    """全部 RSS 源。"""
    with session_scope() as session:
        records = list(
            session.execute(select(RssFeed).order_by(RssFeed.id.desc())).scalars()
        )
        return [to_dict(record) for record in records]


def create_feed(payload: dict[str, Any]) -> dict[str, Any]:
    """新建 RSS 源；URL 已存在则直接返回既有记录（避免重复添加报 500）。"""
    url = str(payload.get("url") or "").strip()
    if not url:
        raise ValueError("RSS 地址不能为空")
    with session_scope() as session:
        exists = session.execute(
            select(RssFeed).where(RssFeed.url == url)
        ).scalar_one_or_none()
        if exists is not None:
            return {**to_dict(exists), "duplicated": True}
        record = RssFeed(
            name=str(payload.get("name") or "").strip()[:255] or "未命名 RSS",
            url=url,
            dialect=str(payload.get("dialect") or "").strip()
            or detect_dialect(url=url),
            aggregate=bool(payload.get("aggregate", True)),
            enabled=bool(payload.get("enabled", True)),
            cookie=payload.get("cookie") or None,
            include_regex=payload.get("include_regex") or None,
            exclude_regex=payload.get("exclude_regex") or None,
            save_path=payload.get("save_path") or None,
            subscribe_id=payload.get("subscribe_id") or None,
            max_per_run=max(1, min(int(payload.get("max_per_run") or 5), 50)),
            skip_existing=bool(payload.get("skip_existing", True)),
            handled_guids=[],
        )
        session.add(record)
        session.flush()
        return to_dict(record)


def update_feed(feed_id: int, payload: dict[str, Any]) -> dict[str, Any] | None:
    """更新 RSS 源；``reset_history`` 清空已处理 guid（下轮把流里全部当新的）。"""
    with session_scope() as session:
        record = session.get(RssFeed, feed_id)
        if not record:
            return None
        for name in (
            "name", "url", "dialect", "cookie", "include_regex",
            "exclude_regex", "save_path", "subscribe_id",
        ):
            if name in payload and payload[name] is not None:
                setattr(record, name, payload[name])
        for flag in ("aggregate", "enabled", "skip_existing"):
            if payload.get(flag) is not None:
                setattr(record, flag, bool(payload[flag]))
        if payload.get("max_per_run") is not None:
            record.max_per_run = max(1, min(int(payload["max_per_run"]), 50))
        if payload.get("reset_history"):
            record.handled_guids = []
        if payload.get("reset_failures"):
            record.failure_count = 0
            # 被自动停用的源，清失败计数时一并恢复启用——
            # 否则用户点了「重置」却发现还是不跑，只会以为没生效
            record.enabled = True
        session.flush()
        return to_dict(record)


def delete_feed(feed_id: int) -> bool:
    """删除 RSS 源。"""
    with session_scope() as session:
        record = session.get(RssFeed, feed_id)
        if not record:
            return False
        session.delete(record)
        return True


def stats() -> dict[str, Any]:
    """RSS 追新总览（给仪表盘/设置页用）。"""
    feeds = list_feeds()
    return {
        "total": len(feeds),
        "enabled": sum(1 for item in feeds if item["enabled"]),
        "aggregate": sum(1 for item in feeds if item["aggregate"]),
        "downloaded": sum(item["total_downloaded"] for item in feeds),
        "failing": sum(1 for item in feeds if item["failure_count"] > 0),
        "dialects": sorted({item["dialect"] for item in feeds}),
    }
