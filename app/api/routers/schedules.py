"""定时任务设置接口：查看 / 修改 / 重置 / 立即执行内置调度任务。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.deps import AdminUser, CurrentUser, OperatorUser
from app.schemas.models import Message, ScheduleUpdate
from app.services.scheduler import scheduler_service

router = APIRouter(prefix="/schedules", tags=["定时任务"])


@router.get("", summary="定时任务设置列表")
def list_schedules(user: CurrentUser) -> dict[str, Any]:
    """列出全部内置任务的触发规则与下次执行时间。"""
    return {
        "success": True,
        "running": scheduler_service.running,
        "items": scheduler_service.describe_schedules(),
    }


@router.get("/{key}", summary="单个定时任务设置")
def get_schedule(key: str, user: CurrentUser) -> dict[str, Any]:
    try:
        return {"success": True, "data": scheduler_service.describe_schedule(key)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{key}", summary="修改定时任务触发规则")
def update_schedule(
    key: str, payload: ScheduleUpdate, user: AdminUser
) -> dict[str, Any]:
    """修改后立即改期并持久化，重启后依然生效。"""
    try:
        data = scheduler_service.update_schedule(
            key, payload.model_dump(exclude_unset=True)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "data": data}


@router.post("/{key}/reset", summary="重置为配置默认值")
def reset_schedule(key: str, user: AdminUser) -> dict[str, Any]:
    try:
        data = scheduler_service.reset_schedule(key)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc
    return {"success": True, "data": data}


@router.post("/{key}/run", response_model=Message, summary="立即执行一次")
async def run_schedule(key: str, user: OperatorUser) -> Message:
    try:
        info = scheduler_service.describe_schedule(key)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc
    if not info["scheduled"]:
        raise HTTPException(status_code=400, detail="任务当前未在调度中，请先启用")
    await scheduler_service.run_job_now(info["id"])
    return Message(message=f"{info['name']} 已触发")
