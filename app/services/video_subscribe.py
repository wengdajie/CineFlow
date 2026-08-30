"""网页视频订阅：UP 主 / 频道 / 播放列表更新后自动下载。

**补的是哪个洞**（对应路线图 v2.0.0 候选「网页视频订阅」）：
v1.6.0 能用 yt-dlp **下载**单个视频，v1.7.0 能**搜到** B 站/YouTube 视频，
但一直不能"**追**"——关注的 UP 主更新了，用户仍得自己去看、自己贴链接。

## 为什么用 yt-dlp 的扁平提取而不是各站 API

B 站的 ``x/space/wbi/arc/search``（取 UP 主投稿列表）**实测返回 HTTP 412
Precondition Failed**：它要求 wbi 签名（对 query 参数做混淆哈希），
而签名算法会被上游随时更换。自己实现就等于进入 ADR-38 说的军备竞赛。

改用 ``yt_dlp`` 的 ``extract_flat`` 列播放列表：**实测 B 站空间页与
YouTube 频道页都能正常列出**，签名维护成本转移给上游（与 ADR-30 接
yt-dlp 做下载的理由一致）。

## 增量判据必须是「视频 ID」

实测扁平提取对 B 站**不返回 title 也不返回 upload_date**（均为 ``None``），
只有 ``id``（BV 号）稳定可得。所以：

* 去重、"是否新投稿"一律以 **ID** 判定，不用标题也不用日期；
* ``include_regex`` / ``exclude_regex`` 作用在标题上，标题缺失时**放行**
  而不是拦掉——否则 B 站订阅会因为"标题为空匹配不上 include"而永远下不到东西。

## 首次订阅默认不补历史

``skip_existing=True`` 时首检只**记录**当前列表的 ID 而不下载，之后才追新。
不这么做的话，订阅一个十年老 UP 会瞬间投出几十个下载任务把下载器打满。
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from sqlalchemy import select

from app.core.logger import get_logger
from app.db.base import utcnow
from app.db.models import VideoSubscribe
from app.db.session import session_scope
from app.schemas.enums import EventType, NotifyLevel, ResourceKind, SubscribeStatus
from app.utils.strings import truncate

logger = get_logger(__name__)

#: 一次扁平提取最多取多少条。UP 主主页动辄上千投稿，全量拉既慢又容易触发风控。
MAX_CHECK_LIMIT = 50

#: 连续失败多少次后自动暂停这条订阅（地址失效/账号注销时不再无意义重试）
MAX_FAILURES = 5


def guess_site(url: str) -> str:
    """从地址推断来源站点（仅用于展示与分组）。"""
    lowered = str(url or "").lower()
    if "bilibili.com" in lowered or "b23.tv" in lowered:
        return "bilibili"
    if "youtube.com" in lowered or "youtu.be" in lowered:
        return "youtube"
    if "douyin" in lowered:
        return "douyin"
    if "acfun" in lowered:
        return "acfun"
    return "other"


def _compile(pattern: str | None) -> re.Pattern[str] | None:
    """编译用户填的正则；写错了当没填，**不能让一个错正则搞崩整轮巡检**。"""
    if not pattern:
        return None
    try:
        return re.compile(pattern)
    except re.error as exc:
        logger.warning("视频订阅正则无效，已忽略：%s（%s）", pattern, exc)
        return None


def match_entries(
    entries: list[dict[str, Any]],
    *,
    include: str | None = None,
    exclude: str | None = None,
    handled: list[str] | None = None,
) -> list[dict[str, Any]]:
    """挑出「需要下载」的条目：未处理过且符合标题过滤。

    **标题缺失时放行**：B 站扁平提取不给标题（实测），若按"匹配不上 include
    就拦掉"处理，B 站订阅将永远下不到任何东西。宁可多下一个也不能整条失效。
    """
    include_re = _compile(include)
    exclude_re = _compile(exclude)
    seen = set(handled or [])
    picked: list[dict[str, Any]] = []
    for entry in entries:
        video_id = str((entry or {}).get("id") or "").strip()
        if not video_id or video_id in seen:
            continue
        title = str((entry or {}).get("title") or "").strip()
        if title:
            if include_re and not include_re.search(title):
                continue
            if exclude_re and exclude_re.search(title):
                continue
        picked.append(entry)
        seen.add(video_id)
    return picked


async def list_entries(url: str, *, limit: int = 10) -> tuple[list[dict[str, Any]], str]:
    """用 yt-dlp 扁平提取列出最近投稿，返回 ``(条目列表, 错误信息)``。

    扁平提取（``extract_flat``）只读列表页、**不进每个视频详情页**，
    所以一次巡检只有一个请求，既快又不容易触发风控。
    """
    limit = max(1, min(int(limit or 10), MAX_CHECK_LIMIT))
    try:
        import yt_dlp
    except ImportError:
        return [], "未安装 yt-dlp（pip install yt-dlp）"

    from app.providers.downloader.ytdlp import build_headers

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "playlistend": limit,
        "socket_timeout": 25,
        "http_headers": build_headers(url),
    }

    def _extract() -> dict[str, Any]:
        with yt_dlp.YoutubeDL(options) as ydl:
            return ydl.extract_info(url, download=False) or {}

    try:
        info = await asyncio.to_thread(_extract)
    except Exception as exc:
        return [], f"列表提取失败：{exc}"[:300]

    raw_entries = info.get("entries")
    if not isinstance(raw_entries, list):
        return [], "该地址不是可列举的频道/播放列表"

    entries: list[dict[str, Any]] = []
    for raw in raw_entries[:limit]:
        if not isinstance(raw, dict):
            continue
        video_id = str(raw.get("id") or "").strip()
        if not video_id:
            continue
        entries.append(
            {
                "id": video_id,
                "title": str(raw.get("title") or "").strip(),
                "url": str(raw.get("url") or raw.get("webpage_url") or "").strip(),
                "duration": raw.get("duration"),
                "uploader": str(
                    raw.get("uploader") or info.get("uploader") or ""
                ).strip()
                or None,
            }
        )
    return entries, ""


def _snapshot(record: VideoSubscribe) -> dict[str, Any]:
    """把 ORM 记录拷成普通字典，避免出了 session 还碰 ORM 属性。"""
    return {
        "id": record.id,
        "name": record.name,
        "url": record.url,
        "site": record.site,
        "save_path": record.save_path,
        "include_regex": record.include_regex,
        "exclude_regex": record.exclude_regex,
        "check_limit": int(record.check_limit or 10),
        "max_per_run": int(record.max_per_run or 3),
        "max_height": record.max_height,
        "status": record.status,
        "handled_ids": list(record.handled_ids or []),
        "skip_existing": bool(record.skip_existing),
        "failure_count": int(record.failure_count or 0),
        "total_downloaded": int(record.total_downloaded or 0),
    }


async def check_one(subscribe_id: int, *, notify: bool = True) -> dict[str, Any]:
    """巡检一条视频订阅，只下载新增投稿。"""
    with session_scope() as session:
        record = session.get(VideoSubscribe, subscribe_id)
        if not record:
            return {"success": False, "message": "订阅不存在"}
        if record.status != SubscribeStatus.ACTIVE.value:
            return {
                "success": True,
                "skipped": True,
                "downloaded": 0,
                "message": f"状态为 {record.status}，已跳过",
            }
        data = _snapshot(record)

    entries, error = await list_entries(data["url"], limit=data["check_limit"])
    if error:
        return await _record_failure(subscribe_id, error, notify=notify)

    candidates = match_entries(
        entries,
        include=data["include_regex"],
        exclude=data["exclude_regex"],
        handled=data["handled_ids"],
    )

    # 首次订阅：只记账不下载（否则老 UP 会一次投出几十个任务）
    first_run = not data["handled_ids"]
    if first_run and data["skip_existing"] and candidates:
        ids = [str(item["id"]) for item in candidates]
        message = f"首次巡检，已记录 {len(ids)} 个历史投稿（按设置跳过补历史）"
        _apply_success(subscribe_id, ids, downloaded=0, message=message)
        return {
            "success": True,
            "downloaded": 0,
            "skipped_history": len(ids),
            "message": message,
        }

    picked = candidates[: max(1, int(data["max_per_run"] or 3))]
    if not picked:
        message = "暂无新投稿"
        _apply_success(subscribe_id, [], downloaded=0, message=message)
        return {"success": True, "downloaded": 0, "message": message}

    from app.services import download as download_service

    downloaded_ids: list[str] = []
    failures: list[str] = []
    for entry in picked:
        link = entry.get("url") or ""
        if not link:
            continue
        title = entry.get("title") or f"{data['name']} · {entry['id']}"
        task = await download_service.add_download(
            {
                "title": title,
                "link": link,
                "site": data["site"] or guess_site(data["url"]),
                "kind": ResourceKind.WEBVIDEO.value,
                "extra": {"video_subscribe_id": subscribe_id},
            },
            save_path=data["save_path"],
            notify=False,
            video_format=_format_for(data["max_height"]),
        )
        # 无论成败都记下 ID：失败也别在下一轮反复重试同一个视频，
        # 否则一个永久失效的视频会把每轮的配额吃光，新投稿永远排不上。
        downloaded_ids.append(str(entry["id"]))
        if task is None:
            failures.append(str(entry["id"]))

    ok_count = len(downloaded_ids) - len(failures)
    message = f"新增 {ok_count} 个下载任务"
    if failures:
        message += f"，{len(failures)} 个投递失败"
    _apply_success(subscribe_id, downloaded_ids, downloaded=ok_count, message=message)

    if notify and ok_count:
        from app.services import notify as notify_service

        await notify_service.send(
            f"视频订阅更新：{truncate(data['name'], 40)}",
            "\n".join(
                truncate(item.get("title") or item["id"], 60)
                for item in picked
                if str(item["id"]) not in failures
            ),
            level=NotifyLevel.SUCCESS.value,
            event=EventType.DOWNLOAD_ADDED.value,
        )
    return {"success": True, "downloaded": ok_count, "message": message}


def _format_for(max_height: int | None) -> str | None:
    """把「画质上限」翻成 yt-dlp 的 format 选择表达式。"""
    if not max_height:
        return None
    return f"bestvideo[height<={int(max_height)}]+bestaudio/best[height<={int(max_height)}]"


def _apply_success(
    subscribe_id: int, new_ids: list[str], *, downloaded: int, message: str
) -> None:
    """把巡检结果写回记录（成功路径：清零失败计数）。"""
    with session_scope() as session:
        record = session.get(VideoSubscribe, subscribe_id)
        if not record:
            return
        if new_ids:
            merged = list(record.handled_ids or [])
            merged.extend(item for item in new_ids if item not in merged)
            # 只保留最近 500 个：够判增量，又不会让 JSON 字段无限膨胀
            record.handled_ids = merged[-500:]
        record.failure_count = 0
        record.total_downloaded = int(record.total_downloaded or 0) + int(downloaded)
        record.last_message = message[:500]
        record.last_checked_at = utcnow()


async def _record_failure(
    subscribe_id: int, error: str, *, notify: bool = True
) -> dict[str, Any]:
    """记一次失败；连续失败到阈值就自动暂停，不再无意义重试。"""
    paused = False
    name = ""
    with session_scope() as session:
        record = session.get(VideoSubscribe, subscribe_id)
        if not record:
            return {"success": False, "message": "订阅不存在"}
        name = record.name
        record.failure_count = int(record.failure_count or 0) + 1
        record.last_message = error[:500]
        record.last_checked_at = utcnow()
        if record.failure_count >= MAX_FAILURES:
            record.status = SubscribeStatus.PAUSED.value
            paused = True
    if paused:
        logger.warning("视频订阅 #%s 连续失败 %s 次，已自动暂停", subscribe_id, MAX_FAILURES)
        if notify:
            from app.services import notify as notify_service

            await notify_service.send(
                f"视频订阅已暂停：{truncate(name, 40)}",
                f"连续 {MAX_FAILURES} 次巡检失败，最后一次：{error}",
                level=NotifyLevel.WARNING.value,
                event=EventType.SYSTEM_ERROR.value,
            )
    return {"success": False, "downloaded": 0, "message": error, "paused": paused}


async def check_all() -> dict[str, Any]:
    """巡检全部活跃视频订阅（定时任务入口）。"""
    with session_scope() as session:
        ids = list(
            session.execute(
                select(VideoSubscribe.id)
                .where(VideoSubscribe.status == SubscribeStatus.ACTIVE.value)
                .order_by(VideoSubscribe.id.asc())
            ).scalars()
        )
    if not ids:
        return {"success": True, "checked": 0, "downloaded": 0, "items": []}

    items: list[dict[str, Any]] = []
    total = 0
    for subscribe_id in ids:
        try:
            result = await check_one(subscribe_id, notify=True)
        except Exception as exc:  # 单条异常不该中断整轮
            logger.error("视频订阅 #%s 巡检异常：%s", subscribe_id, exc)
            items.append({"id": subscribe_id, "success": False, "message": str(exc)[:200]})
            continue
        total += int(result.get("downloaded") or 0)
        items.append({"id": subscribe_id, **result})
    logger.info("视频订阅巡检完成：%d 条，新增下载 %d 个", len(ids), total)
    return {"success": True, "checked": len(ids), "downloaded": total, "items": items}


def to_dict(record: VideoSubscribe) -> dict[str, Any]:
    """对外输出结构（``handled_ids`` 只给数量，避免响应体里塞几百个 ID）。"""
    return {
        "id": record.id,
        "name": record.name,
        "url": record.url,
        "site": record.site or guess_site(record.url),
        "save_path": record.save_path,
        "include_regex": record.include_regex,
        "exclude_regex": record.exclude_regex,
        "check_limit": int(record.check_limit or 10),
        "max_per_run": int(record.max_per_run or 3),
        "max_height": record.max_height,
        "status": record.status,
        "handled_count": len(record.handled_ids or []),
        "skip_existing": bool(record.skip_existing),
        "failure_count": int(record.failure_count or 0),
        "total_downloaded": int(record.total_downloaded or 0),
        "last_message": record.last_message,
        "last_checked_at": record.last_checked_at.isoformat()
        if record.last_checked_at
        else None,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }


def list_all() -> list[dict[str, Any]]:
    """全部视频订阅。"""
    with session_scope() as session:
        records = list(
            session.execute(
                select(VideoSubscribe).order_by(VideoSubscribe.id.desc())
            ).scalars()
        )
        return [to_dict(record) for record in records]


def create(payload: dict[str, Any]) -> dict[str, Any]:
    """新建一条视频订阅。"""
    url = str(payload.get("url") or "").strip()
    record = VideoSubscribe(
        name=str(payload.get("name") or "").strip()[:255],
        url=url,
        site=str(payload.get("site") or "").strip() or guess_site(url),
        save_path=payload.get("save_path") or None,
        include_regex=payload.get("include_regex") or None,
        exclude_regex=payload.get("exclude_regex") or None,
        check_limit=max(1, min(int(payload.get("check_limit") or 10), MAX_CHECK_LIMIT)),
        max_per_run=max(1, min(int(payload.get("max_per_run") or 3), 20)),
        max_height=payload.get("max_height") or None,
        skip_existing=bool(payload.get("skip_existing", True)),
        status=SubscribeStatus.ACTIVE.value,
    )
    with session_scope() as session:
        session.add(record)
        session.flush()
        return to_dict(record)


def update(subscribe_id: int, payload: dict[str, Any]) -> dict[str, Any] | None:
    """更新视频订阅；``reset_history`` 会清空已处理 ID（下轮重新补历史）。"""
    with session_scope() as session:
        record = session.get(VideoSubscribe, subscribe_id)
        if not record:
            return None
        for field in (
            "name", "url", "site", "save_path", "include_regex",
            "exclude_regex", "max_height", "status", "skip_existing",
        ):
            if field in payload and payload[field] is not None:
                setattr(record, field, payload[field])
        if payload.get("check_limit") is not None:
            record.check_limit = max(
                1, min(int(payload["check_limit"]), MAX_CHECK_LIMIT)
            )
        if payload.get("max_per_run") is not None:
            record.max_per_run = max(1, min(int(payload["max_per_run"]), 20))
        if payload.get("reset_history"):
            record.handled_ids = []
        if payload.get("reset_failures"):
            record.failure_count = 0
            # 被自动暂停的订阅，清失败计数时一并恢复活跃——
            # 否则用户点了「重置」却发现还是不跑，只会以为没生效
            if record.status == SubscribeStatus.PAUSED.value:
                record.status = SubscribeStatus.ACTIVE.value
        if not record.site:
            record.site = guess_site(record.url)
        session.flush()
        return to_dict(record)


def delete(subscribe_id: int) -> bool:
    """删除视频订阅。"""
    with session_scope() as session:
        record = session.get(VideoSubscribe, subscribe_id)
        if not record:
            return False
        session.delete(record)
        return True
