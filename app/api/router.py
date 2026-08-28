"""API 路由汇总。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routers import (
    auth,
    downloads,
    library,
    media,
    plugins,
    radar,
    search,
    sites,
    subscribes,
    system,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(search.router)
api_router.include_router(subscribes.router)
api_router.include_router(radar.router)
api_router.include_router(downloads.router)
api_router.include_router(library.router)
api_router.include_router(media.router)
api_router.include_router(sites.router)
api_router.include_router(plugins.router)
api_router.include_router(system.router)
