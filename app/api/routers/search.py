"""搜索接口。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, OperatorUser
from app.core.filters import FilterRule
from app.schemas.enums import ResourceKind
from app.schemas.models import SearchRequest
from app.services import search as search_service
from app.services import search_breaker

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
async def do_search(payload: SearchRequest, user: OperatorUser) -> dict[str, Any]:
    results, outcomes = await search_service.search_detailed(
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
        # 带上每个站点的成败原因：只给结果的话，用户无法知道
        # "启用了 4 个站点却只看到 2 个站点的资源" 到底是谁出了问题
        "sites": [outcome.to_dict() for outcome in outcomes],
    }


@router.get("", summary="快速搜索")
async def quick_search(
    user: CurrentUser,
    keyword: str = Query(min_length=1),
    media_type: str | None = None,
    season: int | None = None,
    episode: int | None = None,
) -> dict[str, Any]:
    results, outcomes = await search_service.search_detailed(
        keyword, media_type=media_type, season=season, episode=episode
    )
    return {
        "success": True,
        "total": len(results),
        "items": results,
        "sites": [outcome.to_dict() for outcome in outcomes],
    }


@router.get("/breaker", summary="慢站熔断状态")
def breaker_state(user: CurrentUser) -> dict[str, Any]:
    """哪些站点因反复超时被暂时跳过、还剩多久恢复。

    有了这个接口，「为什么这次搜索少了几个站」才有据可查，
    而不是让结果悄悄变少（ADR-20）。
    """
    return {
        "success": True,
        "enabled": search_breaker.enabled(),
        "items": search_breaker.snapshot(),
    }


@router.post("/breaker/reset", summary="解除慢站熔断")
def breaker_reset(user: OperatorUser, site: str | None = None) -> dict[str, Any]:
    """手动恢复：用户刚改完站点地址/代理，不该还要干等冷却结束。"""
    cleared = search_breaker.reset(site)
    return {"success": True, "cleared": cleared}
