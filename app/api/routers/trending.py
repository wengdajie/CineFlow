"""热度排行接口：资源榜 / 实时榜 / 搜索热词 / 站点贡献。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser
from app.schemas.enums import MediaType, ResourceKind
from app.services import discover as discover_service
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


@router.get("/discover", summary="发现榜总览（豆瓣四分类 + B 站，一次并发拉全部）")
async def discover_overview(
    user: CurrentUser,
    limit: int = Query(12, ge=1, le=50, description="每个分类取几条"),
) -> dict[str, Any]:
    """首屏用：一次拿到全部分类榜，避免前端串行请求 5 次。"""
    return {"success": True, "data": await discover_service.overview(limit=limit)}


@router.get("/discover/categories", summary="发现榜分类清单（含 B 站分区）")
def discover_categories(user: CurrentUser) -> dict[str, Any]:
    """前端据此渲染页签，新增分类无需改前端。"""
    return {
        "success": True,
        "data": {
            "categories": discover_service.categories(),
            "bili_partitions": discover_service.bili_partitions(),
            "yt_regions": discover_service.yt_regions(),
        },
    }


@router.get("/discover/{category}", summary="单个分类榜（电影/电视剧/动漫/综艺/Bilibili）")
async def discover_chart(
    category: str,
    user: CurrentUser,
    limit: int = Query(30, ge=1, le=100, description="每页条数，默认 30"),
    offset: int = Query(0, ge=0, le=500, description="偏移量，供下拉加载更多"),
) -> dict[str, Any]:
    """切页签时只拉一个分类，比 overview 快。

    未知分类返回 success=True + 空 items + 可读 message，
    而不是 404 —— 榜单页拿不到数据时应当显示"暂无"而非整页报错。
    """
    return {
        "success": True,
        "data": await discover_service.chart(category, limit=limit, offset=offset),
    }


@router.get("/youtube/{region}", summary="YouTube 地区热门榜（美国/日本/韩国…）")
async def yt_region_chart(
    region: str,
    user: CurrentUser,
    limit: int = Query(30, ge=1, le=100, description="每页条数，默认 30"),
    offset: int = Query(0, ge=0, le=500, description="偏移量，供下拉加载更多"),
) -> dict[str, Any]:
    """YouTube 页签内的二级地区切换。

    数据来自 Piped 开源实例（免 API Key），实例不可用时返回空 items +
    可读 message，不抛 5xx。
    """
    return {
        "success": True,
        "data": await discover_service.yt_region_chart(
            region, limit=limit, offset=offset
        ),
    }


@router.get("/bilibili/{partition}", summary="B 站分区榜（番剧/国创/电影/电视剧…）")
async def bili_partition_chart(
    partition: str,
    user: CurrentUser,
    limit: int = Query(30, ge=1, le=100, description="每页条数，默认 30"),
    offset: int = Query(0, ge=0, le=500, description="偏移量，供下拉加载更多"),
) -> dict[str, Any]:
    """Bilibili 页签内的二级分区切换。"""
    return {
        "success": True,
        "data": await discover_service.bili_categories_chart(
            partition, limit=limit, offset=offset
        ),
    }
