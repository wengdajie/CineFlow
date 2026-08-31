"""下载任务接口。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, OperatorUser
from app.db.models import DownloadTask
from app.providers.downloader.ytdlp import guess_site
from app.schemas.enums import ResourceKind, TaskStatus
from app.schemas.models import DownloadRequest, DownloadTaskOut, Message
from app.services import download as download_service
from app.services import download_routing

router = APIRouter(prefix="/downloads", tags=["下载"])


@router.get("", response_model=list[DownloadTaskOut], summary="任务列表")
def list_tasks(
    session: DbSession,
    user: CurrentUser,
    status: TaskStatus | None = None,
    subscribe_id: int | None = None,
    limit: int = Query(200, le=1000),
) -> list[DownloadTask]:
    stmt = select(DownloadTask)
    if status:
        stmt = stmt.where(DownloadTask.status == status.value)
    if subscribe_id:
        stmt = stmt.where(DownloadTask.subscribe_id == subscribe_id)
    stmt = stmt.order_by(DownloadTask.created_at.desc()).limit(limit)
    return list(session.execute(stmt).scalars())


@router.post("", summary="添加下载")
async def add_download(payload: DownloadRequest, user: OperatorUser) -> dict[str, Any]:
    resource = {
        "title": payload.title,
        "link": payload.link,
        "kind": payload.kind,
        "site": payload.site,
        "size": payload.size,
        "password": payload.password,
        "page_url": payload.page_url,
        "meta": payload.meta or None,
    }
    task = await download_service.add_download(
        resource,
        subscribe_id=payload.subscribe_id,
        downloader_name=payload.downloader,
        save_path=payload.save_path,
    )
    if not task:
        raise HTTPException(status_code=400, detail="添加下载失败，请检查下载器配置")
    # ⚠️ 任务建出来了 ≠ 投递成功。下载器拒绝/超时时 add_download 会把任务落库为
    # failed 并写明原因，如果这里仍然回 success=True，界面就会弹绿色的
    # 「已加入下载队列」，而任务列表里其实是一条红色失败 —— 用户完全被误导
    # （实测：qBittorrent 没起来时，磁力/种子都是这个表现）。
    # 所以把「真实结局」如实带出去，让前端能按 ok / failed 分别提示。
    ok = task.status != TaskStatus.FAILED.value
    payload: dict[str, Any] = {
        "success": ok,
        "task_id": task.id,
        "status": task.status,
        "downloader": task.downloader,
    }
    if task.error:
        # 前端统一读 message 展示；detail 保持与 HTTPException 一致的字段名
        payload["message"] = task.error
        payload["detail"] = task.error
    elif task.status == TaskStatus.PENDING.value:
        # pending 不是失败，但也没真正开始下（典型：网盘资源缺网盘账号/aria2）。
        # 不给提示的话用户会以为在下载，其实永远不会动。
        hint = download_routing.pan_pending_hint() if task.kind == ResourceKind.PAN.value else ""
        if hint:
            payload["message"] = hint
    return payload


@router.get("/routing", summary="各资源类型当前能否下载")
def routing(user: CurrentUser, downloader: str | None = None) -> dict[str, Any]:
    """告诉前端【哪些类型现在下不了、缺什么】。

    不同资源要不同的下载方式：磁力/种子靠 BT 下载器，
    网盘靠转存或 aria2，视频网页只能靠 yt-dlp。有了这个接口，
    界面能在用户点下载【之前】就把缺什么说清楚，
    而不是点了才弹一个失败。
    """
    from app.services import download_routing

    return {"success": True, **download_routing.describe(downloader)}


@router.post("/webvideo/probe", summary="解析视频网页（不下载）")
async def probe_webvideo(
    user: OperatorUser, url: str = Query(min_length=4, description="视频页面地址")
) -> dict[str, Any]:
    """先看清楚再决定下不下：返回标题、作者、时长与可用画质。

    只支持**公开可访问**的内容。长视频平台的正片播放页会被直接拒绝，
    因为那类内容需要会员，抓取等于绕过付费墙。
    """
    from app.providers.registry import create_provider

    provider = create_provider("ytdlp", {"name": "yt-dlp"})
    if provider is None:
        raise HTTPException(status_code=500, detail="yt-dlp Provider 未注册")
    result = await provider.probe(url)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message") or "解析失败")
    return {"success": True, "data": result}


@router.post("/webvideo", summary="下载视频网页（B 站/YouTube/抖音等公开视频）")
async def add_webvideo(
    user: OperatorUser,
    url: str = Query(min_length=4),
    title: str | None = None,
    save_path: str | None = None,
    video_format: str | None = Query(
        None,
        description="画质 format_id（取自 /webvideo/probe 的 formats），留空自动选最佳",
    ),
) -> dict[str, Any]:
    """把一个公开视频页面加入下载队列（由 yt-dlp 解析）。

    ``video_format`` 让界面「先解析看画质、再挑一档下载」这条链路真正生效，
    而不是给一个选了没用的下拉框。
    """
    task = await download_service.add_download(
        {
            "title": title or url,
            "link": url,
            "kind": ResourceKind.WEBVIDEO.value,
            "site": guess_site(url),
            "page_url": url,
        },
        save_path=save_path,
        video_format=video_format,
    )
    if not task:
        raise HTTPException(status_code=400, detail="添加失败，请检查 yt-dlp 下载器是否启用")
    if task.status == TaskStatus.FAILED.value:
        # 任务已落库便于追溯，但要如实告诉用户这次没成功
        raise HTTPException(status_code=400, detail=task.error or "下载失败")
    return {"success": True, "task_id": task.id, "status": task.status}


@router.post("/sync", summary="同步下载状态并整理已完成任务")
async def sync_tasks(user: OperatorUser) -> dict[str, Any]:
    return {"success": True, **(await download_service.sync_tasks())}


@router.post("/{task_id}/{action}", response_model=Message, summary="暂停/恢复任务")
async def control(task_id: int, action: str, user: OperatorUser) -> Message:
    if action not in ("pause", "resume"):
        raise HTTPException(status_code=400, detail="action 只能是 pause 或 resume")
    ok = await download_service.control_task(task_id, action)
    if not ok:
        raise HTTPException(status_code=400, detail="操作失败")
    return Message(message="操作成功")


@router.delete("/{task_id}", response_model=Message, summary="删除任务")
async def remove(
    task_id: int, user: OperatorUser, delete_files: bool = False
) -> Message:
    ok = await download_service.remove_task(task_id, delete_files=delete_files)
    if not ok:
        raise HTTPException(status_code=404, detail="任务不存在")
    return Message(message="任务已删除")
