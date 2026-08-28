"""媒体识别与发现接口（TMDB）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import CurrentUser
from app.core.meta import parse
from app.providers.metadata.tmdb import tmdb
from app.schemas.enums import MediaType

router = APIRouter(prefix="/media", tags=["媒体识别"])


@router.get("/recognize", summary="识别资源名称")
def recognize(user: CurrentUser, name: str = Query(min_length=1)) -> dict[str, Any]:
    """把种子名/文件名解析成结构化元数据（不依赖 TMDB）。"""
    info = parse(name, is_file="." in name.split()[-1] if name else False)
    return {"success": True, "meta": info.to_dict(), "display": info.display_name()}


@router.get("/search", summary="TMDB 搜索")
async def search_media(
    user: CurrentUser,
    keyword: str = Query(min_length=1),
    media_type: MediaType | None = None,
    year: int | None = None,
) -> dict[str, Any]:
    if not tmdb.available:
        # 未配 TMDB 不阻塞主流程：返回空结果并提示
        return {
            "success": True,
            "total": 0,
            "items": [],
            "message": "未配置 TMDB_API_KEY，无法检索元数据（不影响资源搜索与追新）",
        }
    items = await tmdb.search(
        keyword, media_type=media_type.value if media_type else None, year=year
    )
    return {"success": True, "total": len(items), "items": items}


@router.get("/detail/{media_type}/{tmdb_id}", summary="TMDB 详情")
async def media_detail(
    media_type: MediaType, tmdb_id: int, user: CurrentUser
) -> dict[str, Any]:
    if not tmdb.available:
        raise HTTPException(status_code=400, detail="未配置 TMDB_API_KEY")
    detail = await tmdb.detail(tmdb_id, media_type.value)
    if not detail:
        raise HTTPException(status_code=404, detail="未找到该条目")
    return {"success": True, "data": detail}


@router.get("/season/{tmdb_id}/{season}", summary="TMDB 季分集信息")
async def season_detail(tmdb_id: int, season: int, user: CurrentUser) -> dict[str, Any]:
    if not tmdb.available:
        return {
            "success": True,
            "total": 0,
            "items": [],
            "message": "未配置 TMDB_API_KEY",
        }
    episodes = await tmdb.season_episodes(tmdb_id, season)
    return {"success": True, "total": len(episodes), "items": episodes}


@router.get("/trending", summary="热门榜单")
async def trending(
    user: CurrentUser, media_type: str = "all", window: str = "week"
) -> dict[str, Any]:
    if not tmdb.available:
        return {"success": True, "total": 0, "items": [], "message": "未配置 TMDB_API_KEY"}
    items = await tmdb.trending(media_type, window)
    return {"success": True, "total": len(items), "items": items}
