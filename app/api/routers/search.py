"""搜索接口。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser
from app.core.filters import FilterRule
from app.schemas.enums import ResourceKind
from app.schemas.models import SearchRequest
from app.services import search as search_service

router = APIRouter(prefix="/search", tags=["搜索"])


def _build_rule(payload: SearchRequest) -> FilterRule:
    allow: list[str] = []
    if payload.allow_torrent:
        allow += [ResourceKind.TORRENT.value, ResourceKind.MAGNET.value]
    if payload.allow_pan:
        allow += [ResourceKind.PAN.value, ResourceKind.DIRECT.value]
    return FilterRule(
        resolutions=payload.resolutions,
        qualities=payload.qualities,
        include=payload.include,
        exclude=payload.exclude,
        min_seeders=payload.min_seeders,
        allow_kinds=allow,
        sites=payload.sites,
        season=payload.season,
        episodes=[payload.episode] if payload.episode else [],
    )


@router.post("", summary="聚合搜索（BT 站点 + 网盘）")
async def do_search(payload: SearchRequest, user: CurrentUser) -> dict[str, Any]:
    results = await search_service.search(
        payload.keyword,
        media_type=payload.media_type.value if payload.media_type else None,
        season=payload.season,
        episode=payload.episode,
        rule=_build_rule(payload),
    )
    return {
        "success": True,
        "keyword": payload.keyword,
        "total": len(results),
        "items": results,
    }


@router.get("", summary="快速搜索")
async def quick_search(
    user: CurrentUser,
    keyword: str = Query(min_length=1),
    media_type: str | None = None,
    season: int | None = None,
    episode: int | None = None,
) -> dict[str, Any]:
    results = await search_service.search(
        keyword, media_type=media_type, season=season, episode=episode
    )
    return {"success": True, "total": len(results), "items": results}
