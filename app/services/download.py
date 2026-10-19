"""下载服务：把候选资源投递到下载器，并跟踪进度。

网盘类资源不进入 BT 下载器：

- 默认读取对应平台的分享原文件，或用户选中的盘内文件，交给 aria2。
- 只有明确选择 ``save_then_download`` 才转存；失败保留原操作意图供重试。
"""

from __future__ import annotations

import asyncio
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.exceptions import DownloadRejected, ProviderError
from app.core.logger import get_logger
from app.core.meta import parse
from app.core.pan_links import PAN_LABELS, pan_family
from app.db.base import utcnow
from app.db.models import DownloadTask, Subscribe
from app.db.session import session_scope
from app.providers.base import Resource
from app.schemas.enums import EventType, NotifyLevel, ResourceKind, TaskStatus
from app.services import download_routing, pan_download
from app.services import notify as notify_service
from app.services import sites as site_service
from app.utils.strings import format_size, safe_filename, truncate

logger = get_logger(__name__)


def _existing_task(link: str, subscribe_id: int | None) -> int | None:
    """同一订阅下同一链接已有「在办」任务就返回其 ID，否则 None。

    **只服务于自动链路**（见调用点）：订阅巡检每 30 分钟跑一轮，
    命中的往往还是上一轮那个资源。没有这个检查，任务表会按巡检频率
    无限膨胀，网盘分享还会被反复提交转存（百度那边表现为
    落地目录里堆出一串 ``xxx(1).mp4``）。

    「在办」= 非 failed / canceled。失败与取消的要放行，
    因为失败常常只是临时的（站点抽风、下载器没起来），
    连重试都不给才是真的卡死。
    """
    if not link:
        return None
    with session_scope() as session:
        existing = session.execute(
            select(DownloadTask.id).where(
                DownloadTask.link == link,
                DownloadTask.subscribe_id == subscribe_id,
                DownloadTask.status.notin_([
                    TaskStatus.FAILED.value,
                    TaskStatus.CANCELED.value,
                ]),
            )
        ).scalars().first()
        return existing


def _resolve_save_path(
    media_type: str, title: str, season: int | None, custom: str | None
) -> str:
    """计算下载保存目录。"""
    if custom:
        return str(custom)
    root = Path(settings.DOWNLOAD_DIR)
    from app.schemas.enums import MediaType

    if media_type in (MediaType.TV.value, MediaType.ANIME.value):
        folder = root / "tv"
    elif media_type == MediaType.MOVIE.value:
        folder = root / "movies"
    else:
        folder = root / "others"
    return str(folder)


async def add_download(
    resource: dict[str, Any],
    *,
    subscribe_id: int | None = None,
    downloader_name: str | None = None,
    save_path: str | None = None,
    notify: bool = True,
    video_format: str | None = None,
) -> DownloadTask | None:
    """先持久化自动任务占位，再执行外部投递；中断状态保留供人工核对。"""
    link = str(resource.get("link") or "")
    if not link:
        return None
    if subscribe_id is not None and _existing_task(link, subscribe_id):
        return None
    kind = str(resource.get("kind") or ResourceKind.TORRENT.value)
    key = sha256(
        f"{subscribe_id}:{Resource(title='', link=link, kind=kind).unique_key}".encode()
    ).hexdigest() if subscribe_id is not None else None
    try:
        with session_scope() as session:
            if key is not None:
                session.execute(
                    update(DownloadTask)
                    .where(
                        DownloadTask.dispatch_key == key,
                        DownloadTask.status.in_([TaskStatus.FAILED.value, TaskStatus.CANCELED.value]),
                    )
                    .values(dispatch_key=None)
                )
            claim = DownloadTask(
                title=str(resource.get("title") or "")[:500],
                link=link,
                kind=kind,
                subscribe_id=subscribe_id,
                dispatch_key=key,
                status=TaskStatus.SUBMITTING.value,
                error="正在投递；如进程中断，请核对下载器后移除此占位再重试",
            )
            session.add(claim)
            session.flush()
            reserved_id = claim.id
            reserved_at = claim.created_at
    except IntegrityError:
        return None
    try:
        return await _dispatch_download(
            resource,
            subscribe_id=subscribe_id,
            downloader_name=downloader_name,
            save_path=save_path,
            notify=notify,
            video_format=video_format,
            reserved_id=reserved_id,
        )
    except DownloadRejected:
        with session_scope() as session:
            claim = session.get(DownloadTask, reserved_id)
            if claim and claim.status == TaskStatus.SUBMITTING.value and not claim.external_id and claim.created_at.replace(tzinfo=None) == reserved_at.replace(tzinfo=None):
                session.delete(claim)
        raise
    except asyncio.CancelledError:
        raise  # 保留占位：外部服务可能已接受，自动重发可能重复下载
    except Exception as exc:
        with session_scope() as session:
            claim = session.get(DownloadTask, reserved_id)
            if claim:
                claim.error = f"投递结果未知，请核对下载器后移除此占位再重试：{type(exc).__name__}"
        raise


async def _dispatch_download(
    resource: dict[str, Any],
    *,
    subscribe_id: int | None = None,
    downloader_name: str | None = None,
    save_path: str | None = None,
    notify: bool = True,
    video_format: str | None = None,
    reserved_id: int | None = None,
) -> DownloadTask | None:
    """添加下载任务。

    ``video_format`` 只对 ``kind=webvideo`` 有意义：界面上用户选定的画质
    （yt-dlp 的 format_id）。其它 kind 会忽略它——BT/网盘没有"选画质"这回事。
    """
    title = str(resource.get("title") or "")
    link = str(resource.get("link") or "")
    if not link:
        logger.warning("资源缺少下载链接：%s", title)
        return None

    meta = resource.get("meta") or parse(title).to_dict()
    kind = str(resource.get("kind") or ResourceKind.TORRENT.value)
    media_type = meta.get("media_type") or "unknown"
    target_path = _resolve_save_path(
        media_type, meta.get("title") or title, meta.get("season"), save_path
    )
    if subscribe_id:
        with session_scope() as session:
            subscription = session.get(Subscribe, subscribe_id)
            if subscription:
                media_type = subscription.media_type
                meta = {**meta, "media_type": media_type, "season": subscription.season}
                custom = save_path or subscription.save_path
                target = Path(_resolve_save_path(media_type, subscription.title, subscription.season, custom))
                if not custom:
                    target /= safe_filename(subscription.title)
                    if media_type in ("tv", "anime"):
                        target /= f"Season {subscription.season:02d}"
                target_path = str(target)

    external_id: str | None = None
    downloader = None
    status = TaskStatus.PENDING.value
    error: str | None = None
    pan_saved: dict[str, Any] | None = None
    pan_landed: dict[str, Any] = {}

    if kind in (ResourceKind.TORRENT.value, ResourceKind.MAGNET.value):
        # 多下载器时按策略排序，投递失败自动换下一个（CF_DOWNLOADER_FAILOVER）。
        # 用 routing 而不是 site_service 直接取：必须先筛掉**收不了这种资源**的
        # 下载器，否则只装了 yt-dlp 的用户点磁力会被投给 yt-dlp 并标成"正在下载"
        candidates = download_routing.candidates_for(kind, downloader_name)
        if not candidates:
            error = download_routing.hint_of(kind)
            status = TaskStatus.FAILED.value
            logger.error("添加下载失败：%s（%s）", error, truncate(title, 80))
        else:
            attempts: list[str] = []
            for candidate in candidates:
                try:
                    external_id = await candidate.add(
                        link,
                        save_path=target_path,
                        cookie=resource.get("cookie"),
                    )
                except Exception as exc:  # 下载器抛错也算这一个失败，继续换源
                    external_id = None
                    attempts.append(f"{candidate.site_name}: {exc}"[:120])
                else:
                    if not external_id:
                        attempts.append(f"{candidate.site_name}: 拒绝或超时")
                if external_id:
                    downloader = candidate
                    status = TaskStatus.DOWNLOADING.value
                    if attempts:
                        logger.info(
                            "已自动换源投递到 %s（前序失败：%s）",
                            candidate.site_name,
                            "；".join(attempts),
                        )
                    break
            if not external_id:
                downloader = candidates[0]
                status = TaskStatus.FAILED.value
                error = ("下载器投递失败 → " + "；".join(attempts))[:500]
    elif kind == ResourceKind.WEBVIDEO.value:
        # 视频网页（B 站/YouTube 等公开视频）：必须交给 yt-dlp，
        # 其他下载器拿到网页地址只会下到一个 HTML 文件
        picked = download_routing.candidates_for(kind, downloader_name or "ytdlp")
        downloader = picked[0] if picked else None
        if downloader:
            external_id = await downloader.add(
                link, save_path=target_path, video_format=video_format
            )
            if external_id:
                status = TaskStatus.DOWNLOADING.value
            else:
                status = TaskStatus.FAILED.value
                # add() 返回 None 的两种原因都要让用户看到，否则无从下手
                error = "解析失败或该地址属于付费内容（详见运行日志）"
        else:
            error = download_routing.hint_of(kind)
            status = TaskStatus.FAILED.value
    elif kind == ResourceKind.DIRECT.value:
        picked = download_routing.candidates_for(kind, downloader_name or "aria2")
        downloader = picked[0] if picked else None
        if downloader:
            external_id = await downloader.add(link, save_path=target_path)
            status = (
                TaskStatus.DOWNLOADING.value if external_id else TaskStatus.FAILED.value
            )
            if not external_id:
                error = f"{downloader.site_name} 拒绝或超时（详见运行日志）"
        else:
            error = download_routing.hint_of(kind)
            status = TaskStatus.FAILED.value
    else:
        # 默认读取分享原文件；只有明确选择 save_then_download 才写入网盘。
        status = TaskStatus.PENDING.value
        error = None
        target_path = str(Path(target_path) / ("pan-" + sha256(link.encode()).hexdigest()[:12]))
        from app.services import pan_storage as pan_service

        if resource.get("pan_saved"):
            pan_result = resource["pan_saved"]
        elif resource.get("pan_mode") == "save_then_download":
            pan_result = await _try_auto_save_pan(link, resource)
        else:
            pan_result = await pan_service.prepare_share_download(link, resource.get("password"))
        if pan_result is not None:
            if pan_result.get("success"):
                pan_saved = pan_result
                # 「转存成功」只是把文件挪进了自己的云端，用户要的是**本地文件**。
                # 这一段以前根本不存在：_ROUTES 的注释、pan_pending_hint() 都在讲
                # 「转存 → 换直链 → 投 aria2」这条链，但代码里只有前一半，
                # 任务停在 transferred 就再也不动了（实测用户报「网盘资源下载」无效）。
                landed = await _land_pan_to_local(
                    pan_result, target_path, downloader_name
                )
                pan_landed = landed
                if landed.get("skipped"):
                    error = "等待可用的 aria2 下载器下载到本地"
                elif landed.get("success"):
                    downloader = landed.get("downloader")
                    external_id = landed.get("external_id")
                    status = TaskStatus.DOWNLOADING.value
                elif landed.get("uncertain"):
                    downloader = landed["downloader"]
                    external_id = landed["external_ids"][0]
                    status = TaskStatus.SUBMITTING.value
                    error = landed["message"]
                else:
                    # 云端转存不等于本地下载成功，保留结果供核对并明确报告失败。
                    error = str(landed.get("message") or "")[:500] or None
            else:
                error = str(pan_result.get("message") or "")[:500] or None
                status = TaskStatus.PENDING.value if pan_result.get("needs_config") else TaskStatus.FAILED.value
        elif not download_routing.has_pan_account():
            # 既没网盘账号也没 aria2，这条任务永远不会自己走完。
            # 这里必须把【下一步该做什么】写进 error，否则任务就这么
            # pending 着，界面上一片空白，用户根本不知道在等什么。
            error = download_routing.pan_pending_hint() or None

    if kind == ResourceKind.PAN.value and not external_id:
        raise DownloadRejected(error or "网盘资源尚未具备下载条件，请检查账号和 aria2 后重试")

    with session_scope() as session:
        values = {
            "subscribe_id": subscribe_id,
            "title": title[:500],
            "kind": kind,
            "site": str(resource.get("site") or "")[:128] or None,
            "link": link,
            "downloader": downloader.site_name if downloader else None,
            "external_id": external_id,
            "save_path": target_path,
            "status": status,
            "size": int(resource.get("size") or 0),
            "media_type": media_type,
            "season": meta.get("season"),
            "episodes": meta.get("episodes") or [],
            "error": error,
            "meta": {
                "password": resource.get("password"),
                "page_url": resource.get("page_url"),
                "score": resource.get("score"),
                "resolution": meta.get("resolution"),
                "quality": meta.get("quality"),
                "pan_type": PAN_LABELS.get(pan_family(link), resource.get("pan_type") or "待识别网盘") if kind == ResourceKind.PAN.value else None,
                "pan_origin": "share" if kind == ResourceKind.PAN.value and not resource.get("pan_saved") and resource.get("pan_mode") != "save_then_download" else "storage",
                "video_format": video_format or None,
                **(
                    {
                        "pan_storage": pan_saved.get("storage"),
                        "saved_path": pan_saved.get("saved_path"),
                        "pan_storage_id": pan_saved.get("site_id"),
                        "saved_files": pan_saved.get("saved_files") or [],
                        "pan_download_ids": pan_landed.get("external_ids") or [],
                        "pan_local_dir": target_path,
                    }
                    if pan_saved
                    else {}
                ),
            },
        }
        task = DownloadTask() if reserved_id is None else session.get(DownloadTask, reserved_id)
        if task is not None:
            for key, value in values.items():
                setattr(task, key, value)
            session.add(task)
            session.flush()
            task_id = task.id
            session.expunge(task)

    if task is None:
        await _withdraw_submitted(downloader, pan_landed.get("external_ids") or [external_id])
        return None

    logger.info(
        "已登记下载任务 #%s [%s] %s", task_id, status, truncate(title, 80)
    )

    if notify and status in (
        TaskStatus.DOWNLOADING.value,
        TaskStatus.PENDING.value,
        TaskStatus.TRANSFERRED.value,
    ):
        body = f"{format_size(resource.get('size'))} · {resource.get('site')}"
        if kind == ResourceKind.PAN.value:
            if pan_saved and pan_saved.get("origin") == "share":
                body += "\n分享原文件正在下载到本地，未执行转存"
            elif pan_saved:
                body += f"\n已自动转存到 {pan_saved.get('storage')}"
                if pan_saved.get("saved_path"):
                    body += f"：{pan_saved['saved_path']}"
            else:
                body += "\n网盘下载尚未启动，请查看任务中的账号或下载权限提示"
        await notify_service.send(
            f"开始下载：{truncate(title, 60)}",
            body,
            level=NotifyLevel.INFO.value,
            event=EventType.DOWNLOAD_ADDED.value,
            payload={"task_id": task_id, "kind": kind},
        )
    return task


async def _land_pan_to_local(
    pan_result: dict[str, Any],
    save_path: str,
    downloader_name: str | None,
) -> dict[str, Any]:
    """展开分享或盘内选中的原文件，连同平台请求头交给 aria2。

    全部文件成功投递才返回 success；中途失败撤回已排队文件并保留源数据。
    缺下载器返回 skipped，调用方保留待处理状态。
    """
    saved_path = str(pan_result.get("saved_path") or "").strip()
    if not saved_path:
        # 转存报成功却没给路径，就没法换直链。说清楚是哪一步缺东西，
        # 而不是笼统报"下载失败"。
        return {"success": False, "message": "网盘未返回落地路径，无法换取直链"}

    candidates = download_routing.candidates_for(ResourceKind.PAN.value, downloader_name)
    if not candidates:
        logger.info("网盘任务缺少可用的 aria2，保留待处理状态")
        return {"skipped": True}

    site_id = pan_result.get("site_id") or pan_result.get("storage_id")
    if not site_id:
        return {"success": False, "message": "缺少网盘站点 ID，无法换取直链"}

    reachable = []
    reasons = []
    for candidate in candidates:
        health = getattr(candidate, "health_check", None)
        if health:
            try:
                ok, message = await health()
            except Exception:
                ok, message = False, "连接异常"
            if not ok:
                reasons.append(f"{candidate.site_name}：{message}")
                continue
        reachable.append(candidate)
    if not reachable:
        return {"success": False, "message": "aria2 当前不可用，请启动或检查 RPC 配置：" + "；".join(reasons)}
    candidates = reachable

    try:
        files = await pan_download.resolve_files(pan_result)
    except (ValueError, OSError) as exc:
        return {"success": False, "message": str(exc)}
    except Exception as exc:
        logger.warning("网盘文件解析失败：%s", type(exc).__name__)
        return {"success": False, "message": "网盘文件解析失败，请检查连接或重新登录后重试"}
    attempts: list[str] = []
    for candidate in candidates:
        ids: list[str] = []
        for item in files:
            relative = Path(item["relative"])
            options = {"save_path": str(Path(save_path) / relative.parent), "user_agent": item.get("user_agent") or None}
            if getattr(candidate, "name", None) == "aria2":
                options["filename"] = Path(item["path"]).name
                options["headers"] = item.get("headers") or {}
                options["original_file"] = True
            try:
                external_id = await candidate.add(item["url"], **options)
            except Exception:
                external_id = None
            if not external_id:
                attempts.append(f"{candidate.site_name}: 文件投递被拒绝或超时")
                break
            ids.append(external_id)
        if len(ids) == len(files):
            return {
                "success": True,
                "downloader": candidate,
                "external_id": ids[0],
                "external_ids": ids,
            }
        withdrawn = []
        for gid in ids:
            try:
                withdrawn.append(await candidate.remove(gid, delete_files=False))
            except Exception:
                withdrawn.append(False)
        if not all(withdrawn):
            return {"success": False, "uncertain": True, "downloader": candidate, "external_ids": ids,
                    "message": "部分文件已提交，但下载器未确认撤回；请核对 aria2，暂勿重复下载"}
    return {
        "success": False,
        "message": "直链投递失败 → " + "；".join(attempts),
    }


async def _withdraw_submitted(downloader: Any, ids: list[str]) -> bool:
    if downloader is None:
        return True
    results = await asyncio.gather(*(downloader.remove(gid, delete_files=False) for gid in ids if gid), return_exceptions=True)
    ok = all(not isinstance(result, BaseException) and bool(result) for result in results)
    if not ok:
        logger.error("任务已移除，但下载器未确认撤回，请人工核对")
    return ok


async def queue_saved_pan_task(task_id: int, result: dict[str, Any]) -> dict[str, Any]:
    """为已转存的待下载任务补投直链，重试时不再次转存分享。"""
    with session_scope() as session:
        task = session.get(DownloadTask, task_id)
        if not task or task.external_id:
            return {"success": False, "message": "任务不存在或已提交下载器"}
        save_path = (task.meta or {}).get("pan_local_dir") or str(Path(task.save_path or settings.DOWNLOAD_DIR) / f"pan-task-{task_id}")
        claimed = session.execute(update(DownloadTask).where(
            DownloadTask.id == task_id, DownloadTask.status == TaskStatus.PENDING.value,
            DownloadTask.external_id.is_(None),
        ).values(status=TaskStatus.SUBMITTING.value, error="正在提交本地下载")).rowcount
        if not claimed:
            return {"success": False, "message": "任务已在提交，请勿重复操作"}
    landed = await _land_pan_to_local(result, save_path, "aria2")
    with session_scope() as session:
        task = session.get(DownloadTask, task_id)
        if task is not None:
            task.save_path = save_path
            task.status = TaskStatus.DOWNLOADING.value if landed.get("success") else TaskStatus.PENDING.value
            task.progress = 0.0
            task.completed_at = None
            task.error = None if landed.get("success") else landed.get("message") or "等待可用的 aria2 下载器"
            task.external_id = landed.get("external_id")
            task.downloader = landed["downloader"].site_name if landed.get("success") else None
            task.meta = {**(task.meta or {}), "pan_storage": result.get("storage"),
                         "pan_origin": "share" if result.get("origin") == "share" else "storage",
                         "pan_storage_id": result.get("site_id"), "saved_path": result.get("saved_path"),
                         "saved_files": result.get("saved_files") or [],
                         "pan_local_dir": save_path,
                         "pan_download_ids": landed.get("external_ids") or []}
    if task is None:
        ok = await _withdraw_submitted(landed.get("downloader"), landed.get("external_ids") or [landed.get("external_id")])
        return {"success": False, "message": "任务已移除，已撤回本次下载" if ok else "下载器未确认撤回，请人工核对"}
    return landed


async def _try_auto_save_pan(
    link: str, resource: dict[str, Any]
) -> dict[str, Any] | None:
    """网盘资源自动转存。

    返回 ``None`` 表示**没有配置网盘存储**（保持原有 pending 行为）；
    返回 dict 表示尝试过转存，``success`` 指示结果。
    转存失败不影响任务登记，只把原因写进 ``error`` 供界面显示。
    """
    from app.services import pan_storage as pan_service

    if resource.get("pan_mode") != "save_then_download" and not bool(settings.PAN_AUTO_SAVE):
        return None
    try:
        if not pan_service.storages():
            return None
        return await pan_service.save_share(
            link, password=resource.get("password") or None
        )
    except Exception as exc:  # pragma: no cover - 转存异常不应打断下载登记
        logger.warning("网盘自动转存异常: %s", exc)
        return {"success": False, "message": f"自动转存异常: {exc}"}


async def sync_tasks() -> dict[str, int]:
    """同步下载器中的任务状态，返回各状态计数。"""
    from app.services import library as library_service

    stats = {"checked": 0, "completed": 0, "failed": 0}
    #: 本轮新翻转为失败的任务，循环结束后统一推一条通知
    failures: list[dict[str, Any]] = []
    #: 本轮新完成的任务，循环结束后广播 download.completed 给插件
    completed: list[dict[str, Any]] = []
    downloaders = {item.site_name: item for item in site_service.downloaders()}

    with session_scope() as session:
        pending = list(
            session.execute(
                select(DownloadTask).where(
                    DownloadTask.status.in_(
                        [
                            TaskStatus.PENDING.value,
                            TaskStatus.DOWNLOADING.value,
                            TaskStatus.PAUSED.value,
                        ]
                    )
                )
            ).scalars()
        )
        snapshots = [
            {
                "id": task.id,
                "external_id": task.external_id,
                "downloader": task.downloader,
                "title": task.title,
                "kind": task.kind,
                "pan_download_ids": (task.meta or {}).get("pan_download_ids") or [],
                "save_path": task.save_path,
            }
            for task in pending
            if task.external_id
        ]

    for snapshot in snapshots:
        downloader = downloaders.get(snapshot["downloader"] or "")
        if not downloader:
            continue

        state = (
            await pan_download.combined_state(downloader, snapshot["pan_download_ids"], snapshot["save_path"])
            if snapshot["pan_download_ids"] else await downloader.get(snapshot["external_id"])
        )
        stats["checked"] += 1
        if not state:
            continue

        with session_scope() as session:
            task = session.get(DownloadTask, snapshot["id"])
            if not task:
                continue
            if task.status in (TaskStatus.TRANSFERRED.value, TaskStatus.CANCELED.value) or task.external_id != snapshot["external_id"]:
                continue
            task.progress = round(state.progress, 4)
            task.speed = state.speed
            task.eta = state.eta
            if state.size:
                task.size = state.size
            if state.error:
                task.error = state.error

            if state.finished and task.status != TaskStatus.TRANSFERRED.value:
                # 同样只在**状态翻转**时记事件，避免每轮巡检重复广播同一个任务
                if task.status != TaskStatus.COMPLETED.value:
                    completed.append(
                        {
                            "task_id": task.id,
                            "title": task.title,
                            "kind": task.kind,
                            "size": state.size or task.size or 0,
                            "content_path": state.content_path or state.save_path,
                            "subscribe_id": task.subscribe_id,
                        }
                    )
                task.status = TaskStatus.COMPLETED.value
                task.completed_at = utcnow()
                content_path = state.content_path or state.save_path
                task.meta = {**(task.meta or {}), "content_path": content_path}
                stats["completed"] += 1
            elif state.status == TaskStatus.FAILED.value:
                # 只在**状态翻转**时收集，否则每 5 分钟一轮会把同一个死种反复推送
                if task.status != TaskStatus.FAILED.value:
                    failures.append(
                        {
                            "id": task.id,
                            "title": task.title,
                            "error": state.error or task.error or "",
                        }
                    )
                task.status = TaskStatus.FAILED.value
                stats["failed"] += 1
            else:
                task.status = state.status

    # 下载失败必须主动告知：用户不会去盯「下载任务」页，
    # 而失败恰恰是最需要人介入的状态（换资源 / 清磁盘 / 修下载器）。
    # 合成一条而不是每个任务一条，避免一次批量失败刷屏。
    if failures:
        lines = [
            f"· {truncate(item['title'], 50)}"
            + (f"（{truncate(item['error'], 40)}）" if item["error"] else "")
            for item in failures[:10]
        ]
        if len(failures) > 10:
            lines.append(f"…另有 {len(failures) - 10} 个")
        await notify_service.send(
            f"下载失败：{len(failures)} 个任务",
            "\n".join(lines),
            level=NotifyLevel.ERROR.value,
            event=EventType.DOWNLOAD_FAILED.value,
            payload={"failed": len(failures),
                     "task_ids": [item["id"] for item in failures]},
        )

    # 广播 download.completed。**只走事件总线、不发用户通知**：
    # 紧接着的入库完成已经会推一条，两条挨着发就是刷屏。
    # 但插件侧必须能收到 —— 开发指南把它列为可订阅事件，
    # 之前却没有任何触发点，插件订阅后永远收不到回调。
    for item in completed:
        await notify_service.emit(EventType.DOWNLOAD_COMPLETED.value, item)

    # 对已完成任务执行整理
    await library_service.transfer_completed_tasks()
    return stats


async def remove_task(task_id: int, *, delete_files: bool = False) -> bool:
    """删除任务（同时从下载器移除）。"""
    with session_scope() as session:
        task = session.get(DownloadTask, task_id)
        if not task:
            return False
        external_id = task.external_id
        downloader_name = task.downloader
        external_ids = (task.meta or {}).get("pan_download_ids") or [external_id]

    if external_id:
        downloader = site_service.default_downloader(downloader_name) if downloader_name else None
        if not downloader:
            raise ProviderError("任务原下载器不存在或未启用，已保留任务记录，请恢复原下载器后重试")
        results = [await downloader.remove(gid, delete_files=delete_files) for gid in external_ids]
        if not all(results):
            raise ProviderError("下载器未确认移除全部文件，已保留任务记录，请稍后重试")

    with session_scope() as session:
        task = session.get(DownloadTask, task_id)
        if task:
            session.delete(task)
    return True


async def control_task(task_id: int, action: str) -> bool:
    """暂停/恢复任务。"""
    with session_scope() as session:
        task = session.get(DownloadTask, task_id)
        if not task or not task.external_id or not task.downloader:
            return False
        external_id, downloader_name = task.external_id, task.downloader
        external_ids = (task.meta or {}).get("pan_download_ids") or [external_id]

    downloader = site_service.default_downloader(downloader_name)
    if not downloader:
        return False

    method = downloader.pause if action == "pause" else downloader.resume
    ok = all([await method(gid) for gid in external_ids])
    if ok:
        with session_scope() as session:
            task = session.get(DownloadTask, task_id)
            if task:
                task.status = (
                    TaskStatus.PAUSED.value
                    if action == "pause"
                    else TaskStatus.DOWNLOADING.value
                )
    return ok
