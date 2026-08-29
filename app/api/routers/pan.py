"""网盘管理接口：容量总览、目录浏览、转存、删除。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import CurrentUser, OperatorUser
from app.schemas.models import (
    PanCookieImportRequest,
    PanLoginCompleteRequest,
    PanLoginStartRequest,
    PanMkdirRequest,
    PanMoveRequest,
    PanRenameRequest,
    PanSaveRequest,
)
from app.services import pan_login as login_service
from app.services import pan_storage as pan_service

router = APIRouter(prefix="/pan", tags=["网盘管理"])


@router.get("", summary="网盘总览（容量与能力）")
async def overview(user: CurrentUser) -> dict[str, Any]:
    """列出所有已启用的网盘存储及其容量。"""
    return {"success": True, **(await pan_service.overview())}


@router.get("/files", summary="浏览网盘目录")
async def list_files(
    user: CurrentUser,
    site_id: int = Query(description="网盘站点 ID"),
    path: str = Query("/", description="目录路径"),
) -> dict[str, Any]:
    """列出指定网盘的目录内容（目录在前）。"""
    result = await pan_service.list_files(site_id, path)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("message", "网盘不存在"))
    return result


@router.get("/pending", summary="待转存的网盘任务")
async def pending(user: CurrentUser, limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    """盘搜命中但尚未转存的任务队列。"""
    from sqlalchemy import select

    from app.db.models import DownloadTask
    from app.db.session import session_scope
    from app.schemas.enums import ResourceKind, TaskStatus

    with session_scope() as session:
        items = [
            {
                "id": task.id,
                "title": task.title,
                "link": task.link,
                "site": task.site,
                "size": task.size,
                "media_type": task.media_type,
                "season": task.season,
                "episodes": task.episodes or [],
                "password": (task.meta or {}).get("password"),
                "pan_type": (task.meta or {}).get("pan_type"),
                "error": task.error,
                "created_at": task.created_at.isoformat() if task.created_at else None,
            }
            for task in session.execute(
                select(DownloadTask)
                .where(
                    DownloadTask.kind == ResourceKind.PAN.value,
                    DownloadTask.status == TaskStatus.PENDING.value,
                )
                .order_by(DownloadTask.created_at.desc())
                .limit(limit)
            ).scalars()
        ]
    return {"success": True, "total": len(items), "items": items}


@router.get("/records", summary="转存记录")
def save_records(user: CurrentUser, limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    """历史转存记录（含失败原因），用于排查"为什么没转存成功"。"""
    items = pan_service.list_save_records(limit=limit)
    return {"success": True, "total": len(items), "items": items}


@router.post("/save", summary="转存分享链接到网盘")
async def save_share(payload: PanSaveRequest, user: OperatorUser) -> dict[str, Any]:
    """把一个网盘分享链接转存进自己的网盘。"""
    result = await pan_service.save_share(
        payload.share_url,
        site_id=payload.site_id,
        password=payload.password,
        target_dir=payload.target_dir,
        task_id=payload.task_id,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "转存失败"))
    return result


@router.post("/transfer", summary="批量转存待处理任务")
async def transfer_pending(
    user: OperatorUser,
    limit: int = Query(20, ge=1, le=200),
    site_id: int | None = None,
) -> dict[str, Any]:
    """把待转存队列里的网盘资源批量转存进网盘。"""
    return {"success": True, **(await pan_service.transfer_pending(limit=limit, site_id=site_id))}


@router.post("/mkdir", summary="创建网盘目录")
async def mkdir(payload: PanMkdirRequest, user: OperatorUser) -> dict[str, Any]:
    result = await pan_service.make_dir(payload.site_id, payload.path)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "创建失败"))
    return result


@router.delete("/files", summary="删除网盘文件或目录")
async def delete_file(
    user: OperatorUser,
    site_id: int = Query(),
    path: str = Query(),
    file_id: str | None = None,
) -> dict[str, Any]:
    result = await pan_service.delete_file(site_id, path, file_id=file_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "删除失败"))
    return result


@router.get("/search", summary="盘内搜索文件")
async def search_files(
    user: CurrentUser,
    site_id: int = Query(description="网盘站点 ID"),
    keyword: str = Query(min_length=1, description="文件名关键词"),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """在网盘内部按文件名搜索（夸克/AList/本地目录支持）。"""
    result = await pan_service.search_files(site_id, keyword, limit=limit)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "搜索失败"))
    return result


@router.post("/rename", summary="重命名网盘文件或目录")
async def rename_file(payload: PanRenameRequest, user: OperatorUser) -> dict[str, Any]:
    result = await pan_service.rename_file(
        payload.site_id, payload.path, payload.new_name, file_id=payload.file_id
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "重命名失败"))
    return result


@router.post("/move", summary="移动或复制网盘文件")
async def move_file(payload: PanMoveRequest, user: OperatorUser) -> dict[str, Any]:
    result = await pan_service.move_file(
        payload.site_id,
        payload.path,
        payload.target_dir,
        file_id=payload.file_id,
        copy=payload.copy_mode,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "操作失败"))
    return result


@router.post("/keep-alive", summary="网盘凭据保活巡检")
async def keep_alive(user: OperatorUser) -> dict[str, Any]:
    """对全部启用网盘做一次登录态探活，及时发现 Cookie 过期。"""
    return {"success": True, **(await pan_service.keep_alive_all())}


@router.get("/download-url", summary="换取临时直链")
async def download_url(
    user: CurrentUser,
    site_id: int = Query(),
    path: str = Query(),
    file_id: str | None = None,
) -> dict[str, Any]:
    """换取可播放/下载的临时直链（可用于 STRM 或投给 aria2）。"""
    result = await pan_service.resolve_download_url(site_id, path, file_id=file_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "换取失败"))
    return result


@router.get("/login/providers", summary="网盘登录能力清单")
def login_providers(user: CurrentUser) -> dict[str, Any]:
    """哪些网盘能扫码、哪些只能导 Cookie。前端据此渲染，不在前端写死。"""
    return {"success": True, "data": login_service.providers()}


@router.post("/login/qrcode", summary="开始扫码登录")
async def login_start(payload: PanLoginStartRequest, user: OperatorUser) -> dict[str, Any]:
    """申请二维码。返回的 token 用于后续轮询。"""
    result = await login_service.start_qrcode(payload.provider)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "无法开始登录"))
    return result


@router.get("/login/qrcode/{token}", summary="轮询扫码状态")
async def login_poll(token: str, user: OperatorUser) -> dict[str, Any]:
    """查询扫码进度。**响应绝不包含 Cookie**，只给状态。"""
    result = await login_service.poll_qrcode(token)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("message", "会话不存在"))
    return result


@router.post("/login/complete", summary="扫码成功后保存凭据")
async def login_complete(
    payload: PanLoginCompleteRequest, user: OperatorUser
) -> dict[str, Any]:
    """把扫到的凭据写入站点（没有对应站点会自动创建并启用）。"""
    result = await login_service.complete_qrcode(
        payload.token, site_id=payload.site_id, site_name=payload.site_name
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "保存失败"))
    return result


@router.post("/login/cookie", summary="导入 Cookie（可选即时校验）")
async def login_cookie(
    payload: PanCookieImportRequest, user: OperatorUser
) -> dict[str, Any]:
    """粘贴 Cookie 并**立刻校验**，无效就不写库——避免半夜任务才发现填错。"""
    result = await login_service.apply_cookie(
        payload.provider,
        payload.cookie,
        site_id=payload.site_id,
        site_name=payload.site_name,
        # 恒为 True：对外接口不开放跳过校验的口子（见 schema 注释与 ADR-40）
        verify=True,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "保存失败"))
    return result


@router.post("/login/verify", summary="只校验 Cookie 不保存")
async def login_verify(
    payload: PanCookieImportRequest, user: OperatorUser
) -> dict[str, Any]:
    """校验但不落库，用于"我这串 Cookie 还有效吗"的自查。"""
    return await login_service.verify_cookie(payload.provider, payload.cookie)


@router.post("/{site_id}/test", summary="网盘连通性测试")
async def test_storage(site_id: int, user: CurrentUser) -> dict[str, Any]:
    return await pan_service.test_storage(site_id)
