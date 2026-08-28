"""站点与 Provider 管理接口。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, SuperUser
from app.db.base import utcnow
from app.db.models import SiteConfig
from app.providers.registry import list_providers
from app.schemas.enums import ProviderKind
from app.schemas.models import Message, SiteCreate, SiteOut, SiteUpdate
from app.services import sites as site_service

router = APIRouter(prefix="/sites", tags=["站点管理"])


def _to_out(site: SiteConfig) -> SiteOut:
    data = SiteOut.model_validate(site)
    data.has_credentials = bool(site.api_key or site.password or site.cookie)
    return data


@router.get("/providers", summary="可用 Provider 列表")
def providers(user: CurrentUser, kind: ProviderKind | None = None) -> list[dict[str, Any]]:
    return list_providers(kind.value if kind else None)


@router.get("", response_model=list[SiteOut], summary="站点列表")
def list_sites(
    session: DbSession,
    user: CurrentUser,
    kind: ProviderKind | None = None,
    enabled_only: bool = False,
) -> list[SiteOut]:
    records = site_service.list_sites(
        session, kind=kind.value if kind else None, enabled_only=enabled_only
    )
    return [_to_out(item) for item in records]


@router.post("", response_model=SiteOut, summary="新增站点")
def create_site(payload: SiteCreate, session: DbSession, user: SuperUser) -> SiteOut:
    exists = session.execute(
        select(SiteConfig).where(SiteConfig.name == payload.name)
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail="站点名称已存在")

    from app.providers.registry import get_provider_class

    if not get_provider_class(payload.provider):
        raise HTTPException(
            status_code=400, detail=f"未知 provider：{payload.provider}"
        )

    site = SiteConfig(**payload.model_dump(exclude={"kind"}), kind=payload.kind.value)
    session.add(site)
    session.commit()
    session.refresh(site)
    return _to_out(site)


@router.patch("/{site_id}", response_model=SiteOut, summary="更新站点")
def update_site(
    site_id: int, payload: SiteUpdate, session: DbSession, user: SuperUser
) -> SiteOut:
    site = session.get(SiteConfig, site_id)
    if not site:
        raise HTTPException(status_code=404, detail="站点不存在")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(site, key, value)
    session.commit()
    session.refresh(site)
    return _to_out(site)


@router.delete("/{site_id}", response_model=Message, summary="删除站点")
def delete_site(site_id: int, session: DbSession, user: SuperUser) -> Message:
    site = session.get(SiteConfig, site_id)
    if not site:
        raise HTTPException(status_code=404, detail="站点不存在")
    session.delete(site)
    session.commit()
    return Message(message="站点已删除")


@router.post("/{site_id}/test", summary="测试站点连通性")
async def test_site(site_id: int, session: DbSession, user: CurrentUser) -> dict[str, Any]:
    site = session.get(SiteConfig, site_id)
    if not site:
        raise HTTPException(status_code=404, detail="站点不存在")

    provider = site_service.get_provider_by_site_id(site_id)
    if not provider:
        raise HTTPException(status_code=400, detail="Provider 初始化失败")

    ok, message = await provider.health_check()
    site.last_status = f"{'正常' if ok else '异常'}: {message}"[:255]
    site.last_check_at = utcnow()
    session.commit()
    return {"success": ok, "message": message}
