"""订阅接口。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.db.models import Subscribe
from app.schemas.enums import SubscribeStatus
from app.schemas.models import (
    Message,
    SubscribeCreate,
    SubscribeOut,
    SubscribeUpdate,
    UpgradeRequest,
)
from app.services import subscribe as subscribe_service

router = APIRouter(prefix="/subscribes", tags=["订阅追新"])


@router.get("", response_model=list[SubscribeOut], summary="订阅列表")
def list_subscribes(
    session: DbSession,
    user: CurrentUser,
    status: SubscribeStatus | None = None,
    keyword: str | None = None,
    limit: int = Query(200, le=1000),
) -> list[Subscribe]:
    stmt = select(Subscribe)
    if status:
        stmt = stmt.where(Subscribe.status == status.value)
    if keyword:
        stmt = stmt.where(Subscribe.title.like(f"%{keyword}%"))
    stmt = stmt.order_by(Subscribe.created_at.desc()).limit(limit)
    return list(session.execute(stmt).scalars())


@router.post("", response_model=SubscribeOut, summary="新增订阅")
async def create_subscribe(payload: SubscribeCreate, user: CurrentUser) -> Subscribe:
    try:
        return await subscribe_service.create_subscribe(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{subscribe_id}", response_model=SubscribeOut, summary="订阅详情")
def get_subscribe(subscribe_id: int, session: DbSession, user: CurrentUser) -> Subscribe:
    record = session.get(Subscribe, subscribe_id)
    if not record:
        raise HTTPException(status_code=404, detail="订阅不存在")
    return record


@router.patch("/{subscribe_id}", response_model=SubscribeOut, summary="更新订阅")
def update_subscribe(
    subscribe_id: int,
    payload: SubscribeUpdate,
    session: DbSession,
    user: CurrentUser,
) -> Subscribe:
    record = session.get(Subscribe, subscribe_id)
    if not record:
        raise HTTPException(status_code=404, detail="订阅不存在")

    data = payload.model_dump(exclude_unset=True)
    if data.get("status"):
        data["status"] = data["status"].value if hasattr(data["status"], "value") else data["status"]
    for key, value in data.items():
        setattr(record, key, value)
    session.commit()
    session.refresh(record)
    return record


@router.delete("/{subscribe_id}", response_model=Message, summary="删除订阅")
def delete_subscribe(subscribe_id: int, session: DbSession, user: CurrentUser) -> Message:
    record = session.get(Subscribe, subscribe_id)
    if not record:
        raise HTTPException(status_code=404, detail="订阅不存在")
    session.delete(record)
    session.commit()
    return Message(message="订阅已删除")


@router.post("/{subscribe_id}/run", summary="立即搜索该订阅")
async def run_subscribe(subscribe_id: int, user: CurrentUser) -> dict[str, Any]:
    result = await subscribe_service.process_subscribe(subscribe_id)
    return {"success": True, **result}


@router.get("/{subscribe_id}/missing", summary="查看缺失集数")
def missing_episodes(
    subscribe_id: int, session: DbSession, user: CurrentUser
) -> dict[str, Any]:
    record = session.get(Subscribe, subscribe_id)
    if not record:
        raise HTTPException(status_code=404, detail="订阅不存在")
    missing = subscribe_service.compute_missing(record)
    return {
        "subscribe_id": subscribe_id,
        "title": record.title,
        "season": record.season,
        "total_episodes": record.total_episodes,
        "downloaded": record.downloaded_episodes,
        "missing": missing,
    }


@router.post("/{subscribe_id}/upgrade", summary="洗版：寻找更优版本")
async def upgrade_subscribe(
    subscribe_id: int, payload: UpgradeRequest, user: CurrentUser
) -> dict[str, Any]:
    """为订阅已入库的剧集寻找评分更高的版本。

    默认 ``dry_run=true`` 只试算不提交下载——洗版会替换已有文件，
    先让用户看清「哪一集会被什么资源替换」再决定。
    """
    from app.services import upgrade as upgrade_service

    result = await upgrade_service.check_subscribe(subscribe_id, dry_run=payload.dry_run)
    if result.get("message") == "订阅不存在":
        raise HTTPException(status_code=404, detail="订阅不存在")
    return {"success": True, "dry_run": payload.dry_run, **result}


@router.post("/upgrade-all", summary="洗版：巡检所有最优版本订阅")
async def upgrade_all(payload: UpgradeRequest, user: CurrentUser) -> dict[str, Any]:
    """批量巡检所有开启了「最优版本」的订阅。"""
    from app.services import upgrade as upgrade_service

    result = await upgrade_service.run(dry_run=payload.dry_run, notify=False)
    return {"success": True, "dry_run": payload.dry_run, **result}


@router.post("/run-all", summary="立即巡检所有订阅")
async def run_all(user: CurrentUser, limit: int | None = None) -> dict[str, Any]:
    return await subscribe_service.run_all(limit)
