"""API 路由汇总。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routers import (
    ai,
    auth,
    chatops,
    downloaders,
    downloads,
    images,
    library,
    media,
    pan,
    pan_subscribes,
    plugins,
    radar,
    ranking,
    rule_groups,
    schedules,
    search,
    site_health,
    sites,
    strm,
    subscribes,
    system,
    trending,
    users,
    video_subscribes,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(ai.router)
api_router.include_router(search.router)
api_router.include_router(trending.router)
api_router.include_router(images.router)
api_router.include_router(subscribes.router)
api_router.include_router(radar.router)
api_router.include_router(ranking.router)
api_router.include_router(rule_groups.router)
api_router.include_router(downloads.router)
api_router.include_router(downloaders.router)
api_router.include_router(library.router)
api_router.include_router(media.router)
api_router.include_router(sites.router)
api_router.include_router(site_health.router)
api_router.include_router(pan.router)
api_router.include_router(pan_subscribes.router)
api_router.include_router(video_subscribes.router)
api_router.include_router(strm.router)
api_router.include_router(chatops.router)
api_router.include_router(plugins.router)
api_router.include_router(schedules.router)
api_router.include_router(system.router)
api_router.include_router(users.router)
