"""RSS 追新接口：多站点 RSS 源管理 + 聚合流分流下载。

与 ``/sites`` 里那些 ``provider="rss"`` 的站点分工不同：那些参与**聚合搜索**
（用户搜什么去里面找什么），本接口是**追新流**——不搜索，只定时拉最新，
把新条目按订阅分流后直接投下载。番剧 RSS 普遍不支持关键词查询，
硬塞进搜索链路只会每次白等一轮超时。

设计参考 [Auto_Bangumi](https://github.com/EstrellaXD/Auto_Bangumi) 的 RSS 引擎。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.deps import CurrentUser, OperatorUser
from app.schemas.models import (
    Message,
    RssFeedCreate,
    RssFeedUpdate,
    RssPreviewRequest,
)
from app.services import rss_feeds as service
from app.services import site_catalog

router = APIRouter(prefix="/rss-feeds", tags=["RSS 追新"])


@router.get("", summary="RSS 源列表")
def list_feeds(user: CurrentUser) -> dict[str, Any]:
    items = service.list_feeds()
    return {"success": True, "total": len(items), "stats": service.stats(), "items": items}


@router.post("", summary="新建 RSS 源")
def create_feed(payload: RssFeedCreate, user: OperatorUser) -> dict[str, Any]:
    try:
        data = service.create_feed(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "success": True,
        "data": data,
        "message": "该地址已存在，返回既有记录" if data.get("duplicated") else "已添加",
    }


@router.post("/preview", summary="预览这条 RSS 能解析出什么")
async def preview(payload: RssPreviewRequest, user: OperatorUser) -> dict[str, Any]:
    """先拉一次看看，再决定要不要添加。

    贴进来的地址对不对、是不是聚合流、能否拿到体积与做种数，只有真拉一次
    才知道。不给预览就只能"先存下来，等下一轮定时任务过去了再看有没有动静"。
    """
    return await service.preview(
        payload.url, cookie=payload.cookie, limit=payload.limit
    )


@router.get("/dialects", summary="支持的 RSS 站点方言")
def dialects(user: CurrentUser) -> dict[str, Any]:
    """各站方言及其字段差异说明（界面上作为添加时的提示）。"""
    from app.core.rss_dialects import DIALECT_NOTES, DIALECTS

    return {
        "success": True,
        "total": len(DIALECTS),
        "items": [
            {"key": key, "note": DIALECT_NOTES.get(key, "")} for key in DIALECTS
        ],
    }


@router.post("/check-all", summary="立即巡检全部 RSS 源")
async def check_all(user: OperatorUser, dry_run: bool = False) -> dict[str, Any]:
    return await service.run(dry_run=dry_run, notify=False)


@router.get("/presets", summary="实测可用的 RSS 源清单")
def rss_presets(user: CurrentUser, include_adult: bool = False) -> dict[str, Any]:
    """已实测可拉通的 RSS 追新源，可一键批量添加。

    ``include_adult`` 默认 False：成人向源不该在「一键添加推荐源」时被顺手带进去。
    """
    existing = {str(row.get("url") or "") for row in service.list_feeds()}
    items = []
    for preset in site_catalog.list_rss_presets(include_adult=include_adult):
        row = dict(preset)
        row["installed"] = preset["url"] in existing
        items.append(row)
    return {"success": True, "total": len(items), "items": items}


@router.post("/presets/import", summary="批量添加实测可用的 RSS 源")
def import_rss_presets(
    user: OperatorUser,
    ids: list[str] | None = None,
    enabled: bool = True,
    include_adult: bool = False,
) -> dict[str, Any]:
    """把实测 RSS 源批量落库。``ids`` 为空表示全部（不含成人向，除非显式要求）。

    已存在的地址**跳过而不是报错**：``create_feed`` 本身对重复地址返回既有记录，
    这里把它归到 skipped，好让用户看清"这次真正新增了几条"。
    """
    wanted = site_catalog.list_rss_presets(include_adult=True)
    if ids:
        keep = {str(i) for i in ids}
        wanted = [p for p in wanted if p["id"] in keep]
        missing = keep - {p["id"] for p in wanted}
        if missing:
            raise HTTPException(status_code=404, detail=f"预设不存在：{', '.join(sorted(missing))}")
    else:
        wanted = site_catalog.list_rss_presets(include_adult=include_adult)
    if not wanted:
        raise HTTPException(status_code=400, detail="没有可添加的 RSS 源")

    created: list[str] = []
    skipped: list[str] = []
    for preset in wanted:
        payload = site_catalog.feed_payload(preset, enabled=enabled)
        try:
            data = service.create_feed(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if data.get("duplicated"):
            skipped.append(payload["name"])
        else:
            created.append(payload["name"])
    return {
        "success": True,
        "created": created,
        "skipped": skipped,
        "message": f"新增 {len(created)} 条 RSS 源"
        + (f"，跳过 {len(skipped)} 条已存在" if skipped else ""),
    }


@router.patch("/{feed_id}", summary="更新 RSS 源")
def update_feed(
    feed_id: int, payload: RssFeedUpdate, user: OperatorUser
) -> dict[str, Any]:
    record = service.update_feed(feed_id, payload.model_dump(exclude_unset=True))
    if not record:
        raise HTTPException(status_code=404, detail="RSS 源不存在")
    return {"success": True, "data": record}


@router.delete("/{feed_id}", response_model=Message, summary="删除 RSS 源")
def delete_feed(feed_id: int, user: OperatorUser) -> Message:
    if not service.delete_feed(feed_id):
        raise HTTPException(status_code=404, detail="RSS 源不存在")
    return Message(message="RSS 源已删除")


@router.post("/{feed_id}/check", summary="立即巡检该 RSS 源")
async def check_one(
    feed_id: int, user: OperatorUser, dry_run: bool = False
) -> dict[str, Any]:
    result = await service.check_feed(feed_id, dry_run=dry_run, notify=False)
    if result.get("message") == "RSS 源不存在":
        raise HTTPException(status_code=404, detail="RSS 源不存在")
    return {"success": bool(result.get("success")), **result}
