"""站点健康接口（v1.5.0）：把「站点静默失效」变成可见状态。

判定与告警逻辑全在 ``app/services/site_health.py``，这里只做 HTTP 暴露。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import CurrentUser, OperatorUser
from app.services import site_health as service

router = APIRouter(prefix="/site-health", tags=["站点健康"])


@router.get("", summary="健康总览（每站最新状态）")
def overview(user: CurrentUser) -> dict[str, Any]:
    return service.overview()


@router.get("/records", summary="历史探测记录")
def records(
    user: CurrentUser,
    site: str | None = Query(default=None, description="只看某个站点"),
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    items = service.list_records(site_name=site, limit=limit)
    return {"success": True, "total": len(items), "items": items}


@router.post("/check", summary="立即巡检全部站点")
async def check_all(user: OperatorUser) -> dict[str, Any]:
    # 探测会真的去搜索一次，属于"重活"，所以要操作员权限
    return await service.check_all(notify=False)


@router.post("/check/{site_id}", summary="立即探测单个站点")
async def check_one(site_id: int, user: OperatorUser) -> dict[str, Any]:
    result = await service.check_site(site_id, notify=False)
    if not result.get("success") and result.get("message") == "站点不存在":
        raise HTTPException(status_code=404, detail="站点不存在")
    return result
