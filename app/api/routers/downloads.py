"""下载任务接口。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, OperatorUser
from app.db.models import DownloadTask
from app.schemas.enums import TaskStatus
from app.schemas.models import DownloadRequest, DownloadTaskOut, Message
from app.services import download as download_service

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
