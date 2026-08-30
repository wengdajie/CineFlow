"""下载器管理接口（设置页专用）。

**为什么单独一个路由**：下载器原先混在 ``/sites`` 里，用通用站点表单配置，
专有参数只能手写 ``options`` JSON。v1.10.0 起把下载器从「站点管理」页移到
「设置」页，并按 ``downloader_specs`` 渲染真实表单，需要两个额外能力：

1. ``GET /downloaders/schema`` —— 下发字段清单（有哪些参数、什么类型、合法值）；
2. ``POST/PATCH`` 时按字段清单把值分流到 ``SiteConfig`` 的列或 ``options`` JSON。

存储仍然复用 ``SiteConfig`` 表（kind=downloader），所以已有配置**无需迁移**，
``site_service.downloaders()`` 那套构建逻辑也完全不动。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import AdminUser, CurrentUser, DbSession
from app.db.base import utcnow
from app.db.models import SiteConfig
from app.providers.registry import get_provider_class
from app.schemas.enums import ProviderKind
from app.schemas.models import Message
from app.services import downloader_specs
from app.services import sites as site_service

router = APIRouter(prefix="/downloaders", tags=["下载器"])

#: 敏感字段回显时一律脱敏，避免界面泄漏密码
_SECRET_KEYS = ("password", "api_key")


class DownloaderPayload(BaseModel):
    """新增/更新下载器。``values`` 的键取自 schema 下发的字段清单。"""

    name: str = Field(min_length=1, max_length=128, description="显示名")
    provider: str = Field(min_length=1, description="qbittorrent / transmission / aria2 / ytdlp")
    enabled: bool = False
    values: dict[str, Any] = Field(default_factory=dict)


def _split_values(provider: str, values: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """把前端提交的扁平字典按字段清单拆成「表列」与「options JSON」两份。

    未登记的键会被**丢弃**而不是塞进 options：否则前端一有笔误就会往
    options 里堆垃圾键，日后排查"这个键哪来的"极其痛苦。
    """
    fields = {item["key"]: item for item in downloader_specs.fields_for(provider)}
    columns: dict[str, Any] = {}
    options: dict[str, Any] = {}
    for key, raw in values.items():
        spec = fields.get(key)
        if spec is None:
            continue
        value = _coerce(spec, raw)
        if value is None:
            continue
        if spec["target"] == "column":
            columns[key] = value
        else:
            options[key] = value
    return columns, options


def _coerce(spec: dict[str, Any], raw: Any) -> Any:
    """按字段类型转换并校验。返回 None 表示「这一项不写入」。"""
    kind = spec.get("type") or "str"
    label = spec.get("label") or spec["key"]

    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off"):
            return False
        raise HTTPException(status_code=400, detail=f"{label} 需要布尔值")

    if kind in ("int", "float"):
        text = str(raw).strip()
        if not text:
            return None
        try:
            number: Any = float(text)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"{label} 需要数字") from exc
        if kind == "int":
            if number != int(number):
                raise HTTPException(status_code=400, detail=f"{label} 需要整数")
            number = int(number)
        low, high = spec.get("minimum"), spec.get("maximum")
        if low is not None and number < low:
            raise HTTPException(status_code=400, detail=f"{label} 不能小于 {low:g}")
        if high is not None and number > high:
            raise HTTPException(status_code=400, detail=f"{label} 不能大于 {high:g}")
        return number

    if kind == "list":
        if isinstance(raw, list):
            items = [str(item).strip() for item in raw]
        else:
            items = [item.strip() for item in str(raw).replace("、", ",").split(",")]
        cleaned = [item for item in items if item]
        return cleaned or None

    if kind == "choice":
        text = str(raw).strip()
        if not text:
            return None
        choices = [str(item) for item in (spec.get("choices") or [])]
        if choices and text not in choices:
            raise HTTPException(
                status_code=400, detail=f"{label} 只能是 {'/'.join(choices)}"
            )
        return text

    text = str(raw).strip()
    # 密码留空表示「不修改」，交给上层跳过，而不是把已存的密码清成空串
    return text or None


def _to_out(site: SiteConfig) -> dict[str, Any]:
    """回显一个下载器配置（密码脱敏）。"""
    options = dict(site.options or {})
    values: dict[str, Any] = {
        "url": site.url or "",
        "username": site.username or "",
        "priority": site.priority,
        "timeout": site.timeout,
    }
    for key, value in options.items():
        values[key] = value
    # 密码类字段只回「有没有设」，不回真值
    for key in _SECRET_KEYS:
        stored = getattr(site, key, None) if key != "password" else site.password
        if key in values:
            values.pop(key, None)
        values[key + "_set"] = bool(stored)
    return {
        "id": site.id,
        "name": site.name,
        "provider": site.provider,
        "enabled": site.enabled,
        "priority": site.priority,
        "last_status": site.last_status,
        "last_check_at": site.last_check_at,
        "values": values,
    }


@router.get("/schema", summary="下载器字段清单（前端据此渲染表单）")
def schema(user: CurrentUser) -> dict[str, Any]:
    """有哪些下载器、每个下载器能配哪些参数。"""
    return {
        "success": True,
        "items": downloader_specs.schema(),
        "strategies": [
            {"value": "priority", "label": "按优先级"},
            {"value": "least_tasks", "label": "任务最少优先"},
            {"value": "round_robin", "label": "轮询"},
        ],
    }


@router.get("", summary="下载器列表")
def list_downloaders(session: DbSession, user: CurrentUser) -> dict[str, Any]:
    rows = site_service.list_sites(session, kind=ProviderKind.DOWNLOADER.value)
    return {"success": True, "items": [_to_out(row) for row in rows]}


@router.post("", summary="新增下载器")
def create_downloader(
    payload: DownloaderPayload, session: DbSession, user: AdminUser
) -> dict[str, Any]:
    provider_cls = get_provider_class(payload.provider)
    if not provider_cls or provider_cls.kind != ProviderKind.DOWNLOADER.value:
        raise HTTPException(status_code=400, detail=f"不是下载器：{payload.provider}")
    if session.execute(
        select(SiteConfig).where(SiteConfig.name == payload.name)
    ).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="名称已存在")

    columns, options = _split_values(payload.provider, payload.values)
    site = SiteConfig(
        name=payload.name,
        kind=ProviderKind.DOWNLOADER.value,
        provider=payload.provider,
        enabled=payload.enabled,
        options=options,
        **columns,
    )
    session.add(site)
    session.commit()
    session.refresh(site)
    return {"success": True, "data": _to_out(site)}


@router.patch("/{site_id}", summary="更新下载器")
def update_downloader(
    site_id: int, payload: DownloaderPayload, session: DbSession, user: AdminUser
) -> dict[str, Any]:
    site = session.get(SiteConfig, site_id)
    if not site or site.kind != ProviderKind.DOWNLOADER.value:
        raise HTTPException(status_code=404, detail="下载器不存在")

    columns, options = _split_values(payload.provider or site.provider, payload.values)
    site.name = payload.name
    site.enabled = payload.enabled
    for key, value in columns.items():
        setattr(site, key, value)
    # options 做合并而非整体替换：前端只提交了部分字段时不该把其余的清掉
    merged = dict(site.options or {})
    merged.update(options)
    site.options = merged
    session.commit()
    session.refresh(site)
    return {"success": True, "data": _to_out(site)}


@router.delete("/{site_id}", response_model=Message, summary="删除下载器")
def delete_downloader(site_id: int, session: DbSession, user: AdminUser) -> Message:
    site = session.get(SiteConfig, site_id)
    if not site or site.kind != ProviderKind.DOWNLOADER.value:
        raise HTTPException(status_code=404, detail="下载器不存在")
    session.delete(site)
    session.commit()
    return Message(message="下载器已删除")


@router.post("/{site_id}/test", summary="测试下载器连通性")
async def test_downloader(
    site_id: int, session: DbSession, user: CurrentUser
) -> dict[str, Any]:
    site = session.get(SiteConfig, site_id)
    if not site or site.kind != ProviderKind.DOWNLOADER.value:
        raise HTTPException(status_code=404, detail="下载器不存在")
    provider = site_service.get_provider_by_site_id(site_id)
    if not provider:
        raise HTTPException(status_code=400, detail="Provider 初始化失败")
    ok, message = await provider.health_check()
    site.last_status = f"{'正常' if ok else '异常'}: {message}"[:255]
    site.last_check_at = utcnow()
    session.commit()
    return {"success": ok, "message": message}
