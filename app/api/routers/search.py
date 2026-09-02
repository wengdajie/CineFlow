"""搜索接口。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, OperatorUser
from app.core.filters import FilterRule
from app.core.logger import get_logger
from app.schemas.enums import ResourceKind
from app.schemas.models import SearchRequest
from app.services import search as search_service
from app.services import search_breaker

logger = get_logger(__name__)

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


@router.post("/stream", summary="流式聚合搜索（结果逐站返回）")
async def do_search_stream(payload: SearchRequest, user: OperatorUser) -> StreamingResponse:
    """按站点逐批下发结果，用 NDJSON（每行一个 JSON 对象）。

    为什么不用 SSE：``EventSource`` **不能自定义请求头**，也只支持 GET，
    没法带 ``Authorization: Bearer``。而本接口要复用完整的搜索条件（POST body）
    与既有鉴权，所以选 NDJSON —— 前端用 ``fetch`` + ``ReadableStream`` 读，
    实现成本比 SSE 还低，且不需要额外的 token 传递方式（避免把 token 放进 URL
    被日志/Referer 带走）。
    """

    async def emit() -> AsyncIterator[bytes]:
        try:
            async for event in search_service.search_stream(
                payload.keyword,
                media_type=payload.media_type.value if payload.media_type else None,
                season=payload.season,
                episode=payload.episode,
                rule=_build_rule(payload),
            ):
                yield (json.dumps(event, ensure_ascii=False, default=str) + "\n").encode()
        except asyncio.CancelledError:  # pragma: no cover - 客户端主动断开
            raise
        except Exception as exc:  # 流已经开始就没法再改状态码，只能把错误也写进流里
            logger.exception("流式搜索失败: %s", exc)
            error = {"type": "error", "message": f"{type(exc).__name__}: {exc}"[:200]}
            yield (json.dumps(error, ensure_ascii=False) + "\n").encode()

    return StreamingResponse(
        emit(),
        media_type="application/x-ndjson",
        headers={
            # 关掉 Nginx 一类反代的缓冲，否则它会攒够一整块才转发，
            # 流式在用户那边看起来又变成了「一次性出结果」（部署面最常见的坑）
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache, no-transform",
        },
    )


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
