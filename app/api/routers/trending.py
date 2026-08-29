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
async def resources(
    user: CurrentUser,
    days: int = Query(14, ge=1, le=180),
    limit: int = Query(20, ge=1, le=100),
    media_type: MediaType | None = None,
    kind: ResourceKind | None = None,
    with_poster: bool = Query(
        True, description="是否用豆瓣补全缺失封面（画板模式需要）"
    ),
) -> dict[str, Any]:
    """按「作品 + 季」聚合搜索缓存，热度 = 做种 + 站点覆盖 + 新鲜度 + 画质。

    ``with_poster=true`` 时会对缺封面的条目走豆瓣补图（有缓存与限流退避）。
    """
    data = trending_service.resource_ranking(
        days=days,
        limit=limit,
        media_type=media_type.value if media_type else None,
        kind=kind.value if kind else None,
    )
    if with_poster:
        data["items"] = await trending_service.enrich_posters(data.get("items") or [])
    return {"success": True, "data": data}


@router.get("/live", summary="实时热榜（联网拉取各站点最新流）")
async def live(
    user: CurrentUser,
    limit: int = Query(20, ge=1, le=100),
    limit_per_site: int = Query(40, ge=1, le=200),
    media_type: MediaType | None = None,
    with_poster: bool = Query(True, description="是否用豆瓣补全缺失封面"),
) -> dict[str, Any]:
    """不依赖历史数据，直接聚合站点最新流；未启用站点时返回空榜。"""
    data = await trending_service.live_ranking(
        limit=limit,
        limit_per_site=limit_per_site,
        media_type=media_type.value if media_type else None,
    )
    if with_poster:
        data["items"] = await trending_service.enrich_posters(data.get("items") or [])
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


@router.get("/douban", summary="豆瓣条目搜索（封面与元数据）")
async def douban_suggest(
    user: CurrentUser,
    keyword: str = Query(min_length=1, description="片名关键词"),
    limit: int = Query(10, ge=1, le=20),
) -> dict[str, Any]:
    """直接查豆瓣公开 suggest 接口，用于手动挑封面或校正元数据。"""
    from app.providers.metadata import douban

    items = await douban.suggest(keyword, limit=limit)
    return {
        "success": True,
        "total": len(items),
        "rate_limited": douban.is_rate_limited(),
        "items": items,
    }
