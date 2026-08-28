"""热度排行接口：资源榜 / 实时榜 / 搜索热词 / 站点贡献。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser
from app.schemas.enums import MediaType, ResourceKind
from app.services import trending as trending_service

router = APIRouter(prefix="/trending", tags=["热度排行"])


@router.get("", summary="排行总览（资源榜 + 热词 + 站点）")
def overview(
    user: CurrentUser,
    days: int = Query(14, ge=1, le=180, description="统计窗口天数"),
    limit: int = Query(10, ge=1, le=100),
) -> dict[str, Any]:
    """一次取回三张榜单，供仪表盘与搜索页使用。"""
    return {"success": True, "data": trending_service.overview(days=days, limit=limit)}


@router.get("/resources", summary="资源热度榜（基于本地搜索缓存）")
def resources(
    user: CurrentUser,
    days: int = Query(14, ge=1, le=180),
    limit: int = Query(20, ge=1, le=100),
    media_type: MediaType | None = None,
    kind: ResourceKind | None = None,
) -> dict[str, Any]:
    """按「作品 + 季」聚合搜索缓存，热度 = 做种 + 站点覆盖 + 新鲜度 + 画质。"""
    data = trending_service.resource_ranking(
        days=days,
        limit=limit,
        media_type=media_type.value if media_type else None,
        kind=kind.value if kind else None,
    )
    return {"success": True, "data": data}


@router.get("/live", summary="实时热榜（联网拉取各站点最新流）")
async def live(
    user: CurrentUser,
    limit: int = Query(20, ge=1, le=100),
    limit_per_site: int = Query(40, ge=1, le=200),
    media_type: MediaType | None = None,
) -> dict[str, Any]:
    """不依赖历史数据，直接聚合站点最新流；未启用站点时返回空榜。"""
    data = await trending_service.live_ranking(
        limit=limit,
        limit_per_site=limit_per_site,
        media_type=media_type.value if media_type else None,
    )
    return {"success": True, "data": data}


@router.get("/keywords", summary="搜索热词榜")
def keywords(
    user: CurrentUser,
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(12, ge=1, le=100),
) -> dict[str, Any]:
    return {"success": True, "data": trending_service.hot_keywords(days=days, limit=limit)}


@router.get("/sites", summary="站点贡献榜")
def sites(
    user: CurrentUser,
    days: int = Query(14, ge=1, le=180),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    return {"success": True, "data": trending_service.site_activity(days=days, limit=limit)}
