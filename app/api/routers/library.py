"""媒体库与整理接口。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.organizer import transfer_directory
from app.db.models import LibraryFile, TransferRecord
from app.schemas.models import TransferRequest, TransferResultOut
from app.services import library as library_service

router = APIRouter(prefix="/library", tags=["媒体库"])


@router.get("/stats", summary="媒体库统计")
def stats(user: CurrentUser) -> dict[str, Any]:
    return {"success": True, "data": library_service.library_stats()}


@router.get("/files", summary="已入库文件")
def files(
    session: DbSession,
    user: CurrentUser,
    keyword: str | None = None,
    media_type: str | None = None,
    limit: int = Query(200, le=2000),
) -> dict[str, Any]:
    stmt = select(LibraryFile)
    if keyword:
        stmt = stmt.where(LibraryFile.title.like(f"%{keyword}%"))
    if media_type:
        stmt = stmt.where(LibraryFile.media_type == media_type)
    stmt = stmt.order_by(LibraryFile.created_at.desc()).limit(limit)
    records = list(session.execute(stmt).scalars())
    return {
        "success": True,
        "total": len(records),
        "items": [
            {
                "id": item.id,
                "title": item.title,
                "year": item.year,
                "media_type": item.media_type,
                "season": item.season,
                "episode": item.episode,
                "resolution": item.resolution,
                "size": item.size,
                "path": item.path,
            }
            for item in records
        ],
    }


@router.post("/scan", summary="扫描媒体库")
def scan(user: CurrentUser, path: str | None = None) -> dict[str, Any]:
    return {"success": True, **library_service.scan_library(path)}


@router.post("/transfer", summary="手动整理目录/文件")
def transfer(payload: TransferRequest, user: CurrentUser) -> dict[str, Any]:
    results = transfer_directory(
        payload.source,
        library_dir=payload.library_dir,
        mode=payload.mode,
        title=payload.title,
        season=payload.season,
        overwrite=payload.overwrite,
        dry_run=payload.dry_run,
    )
    items = [
        TransferResultOut(
            success=item.success,
            source=str(item.source),
            target=str(item.target) if item.target else None,
            mode=item.mode,
            message=item.message,
            size=item.size,
            meta=item.meta.to_dict() if item.meta else None,
        )
        for item in results
    ]
    return {
        "success": True,
        "total": len(items),
        "succeeded": sum(1 for item in items if item.success),
        "items": items,
    }


@router.post("/refresh", summary="刷新媒体服务器")
async def refresh(user: CurrentUser, path: str | None = None) -> dict[str, Any]:
    count = await library_service.refresh_media_servers(path)
    return {"success": True, "refreshed": count}


@router.get("/transfers", summary="整理记录")
def transfers(
    session: DbSession, user: CurrentUser, limit: int = Query(100, le=1000)
) -> dict[str, Any]:
    records = list(
        session.execute(
            select(TransferRecord).order_by(TransferRecord.created_at.desc()).limit(limit)
        ).scalars()
    )
    return {
        "success": True,
        "total": len(records),
        "items": [
            {
                "id": item.id,
                "source_path": item.source_path,
                "target_path": item.target_path,
                "mode": item.mode,
                "success": item.success,
                "message": item.message,
                "media_title": item.media_title,
                "season": item.season,
                "episode": item.episode,
                "size": item.size,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in records
        ],
    }
