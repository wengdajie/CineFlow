"""订阅服务：自动化追新的核心。

单次巡检流程：

1. 取出所有 active 订阅
2. 结合 TMDB 总集数与媒体库已有集数，算出缺失集
3. 聚合搜索（BT + 网盘），过滤打分
4. 择优投递下载；季包一次补齐多集
5. 全部集齐后标记订阅完成并通知
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.core.logger import get_logger
from app.db.base import utcnow
from app.db.models import MediaItem, Subscribe
from app.db.session import session_scope
from app.providers.metadata.tmdb import tmdb
from app.schemas.enums import (
    EventType,
    MediaType,
    NotifyLevel,
    SubscribeStatus,
)
from app.services import download as download_service
from app.services import library as library_service
from app.services import notify as notify_service
from app.services import search as search_service
from app.utils.strings import truncate

logger = get_logger(__name__)


async def create_subscribe(payload: dict[str, Any]) -> Subscribe:
    """新增订阅（自动补全 TMDB 元数据与总集数）。"""
    title = str(payload.get("title") or "").strip()
    if not title:
        raise ValueError("订阅标题不能为空")

    media_type = str(payload.get("media_type") or MediaType.TV.value)
    season = int(payload.get("season") or 1)
    year = payload.get("year")
    tmdb_id = payload.get("tmdb_id")
    total_episodes = int(payload.get("total_episodes") or 0)
    media_payload: dict[str, Any] | None = None

    if tmdb.available:
        detail = None
        if tmdb_id:
            detail = await tmdb.detail(int(tmdb_id), media_type)
        else:
            recognized = await tmdb.recognize(
                title, media_type=media_type, year=int(year) if year else None
            )
            if recognized:
                detail = await tmdb.detail(
                    int(recognized["tmdb_id"]), recognized["media_type"]
                )
                media_type = recognized["media_type"] or media_type
        if detail:
            media_payload = detail
            tmdb_id = detail.get("tmdb_id")
            year = detail.get("year") or year
            title = detail.get("title") or title
            if media_type != MediaType.MOVIE.value and not total_episodes:
                episodes = await tmdb.season_episodes(int(tmdb_id), season)
                total_episodes = len(episodes)

    with session_scope() as session:
        existing = session.execute(
            select(Subscribe).where(
                Subscribe.title == title,
                Subscribe.season == season,
                Subscribe.media_type == media_type,
            )
        ).scalar_one_or_none()
        if existing:
            raise ValueError(f"订阅已存在：{title} 第{season}季")

        media_id = None
        if media_payload and media_payload.get("tmdb_id"):
            media = session.execute(
                select(MediaItem).where(
                    MediaItem.tmdb_id == media_payload["tmdb_id"],
                    MediaItem.media_type == media_type,
                )
            ).scalar_one_or_none()
            if not media:
                media = MediaItem(
                    title=media_payload.get("title") or title,
                    original_title=media_payload.get("original_title"),
                    year=media_payload.get("year"),
                    media_type=media_type,
                    tmdb_id=media_payload.get("tmdb_id"),
                    imdb_id=media_payload.get("imdb_id"),
                    overview=media_payload.get("overview"),
                    poster=media_payload.get("poster"),
                    backdrop=media_payload.get("backdrop"),
                    vote_average=media_payload.get("vote_average"),
                    genres=media_payload.get("genres") or [],
                    total_seasons=media_payload.get("total_seasons"),
                )
                session.add(media)
                session.flush()
            media_id = media.id

        subscribe = Subscribe(
            media_id=media_id,
            title=title,
            year=int(year) if year else None,
            media_type=media_type,
            tmdb_id=int(tmdb_id) if tmdb_id else None,
            season=season,
            total_episodes=total_episodes,
            start_episode=int(payload.get("start_episode") or 1),
            downloaded_episodes=[],
            lack_episodes=total_episodes,
            status=SubscribeStatus.ACTIVE.value,
            quality=payload.get("quality"),
            resolution=payload.get("resolution"),
            effect=payload.get("effect"),
            include=payload.get("include"),
            exclude=payload.get("exclude"),
            min_seeders=int(payload.get("min_seeders") or 0),
            sources=payload.get("sources") or [],
            allow_pan=bool(payload.get("allow_pan", True)),
            allow_torrent=bool(payload.get("allow_torrent", True)),
            best_version=bool(payload.get("best_version", False)),
            rule_group_id=int(payload["rule_group_id"]) if payload.get("rule_group_id") else None,
            save_path=payload.get("save_path"),
            note=payload.get("note"),
        )
        session.add(subscribe)
        session.flush()
        session.refresh(subscribe)
        session.expunge(subscribe)

    logger.info("新增订阅：%s 第%s季（共%s集）", title, season, total_episodes or "未知")
    await notify_service.send(
        f"新增订阅：{title}",
        f"第 {season} 季"
        + (f" · 共 {total_episodes} 集" if total_episodes else ""),
        level=NotifyLevel.INFO.value,
        event=EventType.SUBSCRIBE_ADDED.value,
        image=media_payload.get("poster") if media_payload else None,
        payload={"subscribe_id": subscribe.id},
    )
    return subscribe


def compute_missing(subscribe: Subscribe) -> list[int]:
    """计算缺失集数（合并已下载记录与媒体库实际文件）。"""
    if subscribe.media_type == MediaType.MOVIE.value:
        # 电影：库中已有文件则视为完成
        done = library_service.has_library_file(
            subscribe.title, MediaType.MOVIE.value
        )
        return [] if done else [1]

    total = subscribe.total_episodes or 0
    downloaded = set(subscribe.downloaded_episodes or [])
    downloaded |= library_service.existing_episodes(subscribe.title, subscribe.season)

    start = max(subscribe.start_episode or 1, 1)
    if total:
        return [ep for ep in range(start, total + 1) if ep not in downloaded]

    # 总集数未知（未配 TMDB 或新番未定）：探测下一集
    next_episode = (max(downloaded) + 1) if downloaded else start
    return [next_episode]


def mark_episodes_done(subscribe_id: int, episodes: list[int]) -> None:
    """标记集数已完成，必要时把订阅置为完成。"""
    if not episodes:
        return
    with session_scope() as session:
        subscribe = session.get(Subscribe, subscribe_id)
        if not subscribe:
            return
        merged = sorted(set(subscribe.downloaded_episodes or []) | set(episodes))
        subscribe.downloaded_episodes = merged
        subscribe.last_matched_at = utcnow()
        if subscribe.total_episodes:
            lack = max(subscribe.total_episodes - len(merged), 0)
            subscribe.lack_episodes = lack
            if lack == 0:
                subscribe.status = SubscribeStatus.COMPLETED.value
        logger.info(
            "订阅 #%s 更新已下载集数：%s", subscribe_id, merged[-5:]
        )


async def refresh_total_episodes(subscribe_id: int) -> int:
    """从 TMDB 刷新总集数（追新剧集会持续更新）。"""
    if not tmdb.available:
        return 0
    with session_scope() as session:
        subscribe = session.get(Subscribe, subscribe_id)
        if not subscribe or not subscribe.tmdb_id:
            return 0
        tmdb_id, season, media_type = subscribe.tmdb_id, subscribe.season, subscribe.media_type

    if media_type == MediaType.MOVIE.value:
        return 0
    episodes = await tmdb.season_episodes(int(tmdb_id), int(season))
    # 只统计已播出的集
    today = utcnow().date().isoformat()
    aired = [
        item
        for item in episodes
        if not item.get("air_date") or str(item["air_date"]) <= today
    ]
    total = len(aired)
    if total:
        with session_scope() as session:
            subscribe = session.get(Subscribe, subscribe_id)
            if subscribe and total != subscribe.total_episodes:
                subscribe.total_episodes = total
                logger.info("订阅 #%s 总集数更新为 %s", subscribe_id, total)
    return total


async def process_subscribe(subscribe_id: int) -> dict[str, Any]:
    """处理单个订阅：搜索 -> 择优 -> 下载。"""
    await refresh_total_episodes(subscribe_id)

    with session_scope() as session:
        subscribe = session.get(Subscribe, subscribe_id)
        if not subscribe or subscribe.status != SubscribeStatus.ACTIVE.value:
            return {"subscribe_id": subscribe_id, "skipped": True}
        session.expunge(subscribe)

    missing = compute_missing(subscribe)
    result: dict[str, Any] = {
        "subscribe_id": subscribe_id,
        "title": subscribe.title,
        "missing": missing,
        "matched": 0,
        "downloads": [],
    }

    if not missing:
        with session_scope() as session:
            record = session.get(Subscribe, subscribe_id)
            if record:
                record.last_check_at = utcnow()
                if record.total_episodes:
                    record.status = SubscribeStatus.COMPLETED.value
                    record.lack_episodes = 0
        await notify_service.send(
            f"订阅完成：{subscribe.title}",
            f"第 {subscribe.season} 季已全部入库",
            level=NotifyLevel.SUCCESS.value,
            event=EventType.SUBSCRIBE_COMPLETED.value,
            payload={"subscribe_id": subscribe_id},
        )
        return result

    logger.info(
        "订阅巡检 #%s《%s》缺失 %d 集: %s",
        subscribe_id,
        subscribe.title,
        len(missing),
        missing[:10],
    )

    resources = await search_service.search_for_subscribe(subscribe, missing)
    result["matched"] = len(resources)

    if not resources:
        with session_scope() as session:
            record = session.get(Subscribe, subscribe_id)
            if record:
                record.last_check_at = utcnow()
        return result

    # 择优下载：优先季包，其次逐集补齐
    remaining = set(missing)
    picked: list[dict[str, Any]] = []
    for resource in resources:
        if not remaining:
            break
        info = resource.get("meta") or {}
        episodes = set(info.get("episodes") or [])
        is_pack = bool(info.get("is_season_pack"))

        if episodes:
            useful = episodes & remaining
            if not useful:
                continue
        elif is_pack:
            useful = set(remaining)
        elif subscribe.media_type == MediaType.MOVIE.value:
            useful = {1}
        else:
            continue

        picked.append(resource)
        remaining -= useful
        if subscribe.best_version or subscribe.media_type == MediaType.MOVIE.value:
            break

    for resource in picked:
        task = await download_service.add_download(
            resource,
            subscribe_id=subscribe_id,
            save_path=subscribe.save_path,
        )
        if task:
            result["downloads"].append(
                {
                    "title": resource.get("title"),
                    "site": resource.get("site"),
                    "score": resource.get("score"),
                }
            )

    if picked:
        await notify_service.send(
            f"订阅命中：{subscribe.title}",
            f"第 {subscribe.season} 季，新增 {len(picked)} 个下载任务\n"
            + "\n".join(truncate(item.get("title"), 60) for item in picked[:3]),
            level=NotifyLevel.SUCCESS.value,
            event=EventType.RESOURCE_MATCHED.value,
            payload={"subscribe_id": subscribe_id, "count": len(picked)},
        )

    with session_scope() as session:
        record = session.get(Subscribe, subscribe_id)
        if record:
            record.last_check_at = utcnow()
            if picked:
                record.last_matched_at = utcnow()
            record.lack_episodes = len(remaining)
            record.error = None
    return result


async def run_all(limit: int | None = None) -> dict[str, Any]:
    """巡检所有活跃订阅。"""
    with session_scope() as session:
        stmt = select(Subscribe.id).where(
            Subscribe.status == SubscribeStatus.ACTIVE.value
        ).order_by(Subscribe.updated_at.asc())
        if limit:
            stmt = stmt.limit(limit)
        ids = [row[0] for row in session.execute(stmt).all()]

    logger.info("开始订阅巡检，共 %d 个活跃订阅", len(ids))
    summary = {"total": len(ids), "downloads": 0, "results": []}
    for subscribe_id in ids:
        try:
            outcome = await process_subscribe(subscribe_id)
        except Exception as exc:
            logger.error("订阅 #%s 处理失败: %s", subscribe_id, exc)
            with session_scope() as session:
                record = session.get(Subscribe, subscribe_id)
                if record:
                    record.error = str(exc)[:500]
            continue
        summary["downloads"] += len(outcome.get("downloads", []))
        summary["results"].append(outcome)
    logger.info(
        "订阅巡检结束：%d 个订阅，新增 %d 个下载任务",
        summary["total"],
        summary["downloads"],
    )
    return summary
