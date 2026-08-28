"""榜单自动订阅接口（v1.5.0）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.deps import AdminUser, CurrentUser, OperatorUser
from app.schemas.models import Message, RankingRuleCreate, RankingRuleUpdate
from app.services import ranking as service

router = APIRouter(prefix="/ranking-rules", tags=["榜单订阅"])


@router.get("", summary="榜单规则列表")
def list_rules(user: CurrentUser) -> dict[str, Any]:
    items = service.list_rules()
    return {
        "success": True,
        "total": len(items),
        "sources": [{"value": key, "label": label} for key, label in service.SOURCES.items()],
        "items": items,
    }


@router.post("", summary="新增榜单规则")
def create_rule(payload: RankingRuleCreate, user: AdminUser) -> dict[str, Any]:
    data = payload.model_dump()
    data["media_type"] = getattr(data["media_type"], "value", data["media_type"])
    try:
        return {"success": True, "data": service.create(data)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/{rule_id}", summary="更新榜单规则")
def update_rule(rule_id: int, payload: RankingRuleUpdate, user: AdminUser) -> dict[str, Any]:
    data = payload.model_dump(exclude_unset=True)
    if data.get("media_type") is not None:
        data["media_type"] = getattr(data["media_type"], "value", data["media_type"])
    try:
        record = service.update(rule_id, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not record:
        raise HTTPException(status_code=404, detail="榜单规则不存在")
    return {"success": True, "data": record}


@router.delete("/{rule_id}", response_model=Message, summary="删除榜单规则")
def delete_rule(rule_id: int, user: AdminUser) -> Message:
    if not service.delete(rule_id):
        raise HTTPException(status_code=404, detail="榜单规则不存在")
    return Message(message="规则已删除")


@router.post("/{rule_id}/preview", summary="试算（只看会订阅哪些，不落库）")
async def preview_rule(rule_id: int, user: CurrentUser) -> dict[str, Any]:
    result = await service.run_rule(rule_id, dry_run=True)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("message") or "规则不存在")
    return result


@router.post("/{rule_id}/run", summary="立即执行该规则")
async def run_rule(rule_id: int, user: OperatorUser) -> dict[str, Any]:
    result = await service.run_rule(rule_id)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("message") or "规则不存在")
    return result


@router.post("/run-all", summary="立即执行所有启用规则")
async def run_all(user: OperatorUser) -> dict[str, Any]:
    return await service.run()
