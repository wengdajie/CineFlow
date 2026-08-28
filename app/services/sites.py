"""站点/Provider 配置服务：把数据库配置实例化为可用 Provider。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logger import get_logger
from app.db.models import SiteConfig
from app.db.session import session_scope
from app.providers.base import BaseProvider, SearchProvider
from app.providers.downloader.base import BaseDownloader
from app.providers.notify.base import BaseNotifier
from app.providers.registry import create_provider
from app.schemas.enums import ProviderKind

logger = get_logger(__name__)


def site_to_config(site: SiteConfig) -> dict[str, Any]:
    """把 ORM 记录转成 Provider 配置字典。"""
    return {
        "id": site.id,
        "name": site.name,
        "provider": site.provider,
        "url": site.url,
        "api_key": site.api_key,
        "username": site.username,
        "password": site.password,
        "cookie": site.cookie,
        "enabled": site.enabled,
        "priority": site.priority,
        "timeout": site.timeout,
        "options": site.options or {},
    }


def list_sites(
    session: Session, *, kind: str | None = None, enabled_only: bool = False
) -> list[SiteConfig]:
    """查询站点配置。"""
    stmt = select(SiteConfig)
    if kind:
        stmt = stmt.where(SiteConfig.kind == kind)
    if enabled_only:
        stmt = stmt.where(SiteConfig.enabled.is_(True))
    stmt = stmt.order_by(SiteConfig.priority.asc(), SiteConfig.id.asc())
    return list(session.execute(stmt).scalars())


def build_providers(kind: str, *, enabled_only: bool = True) -> list[BaseProvider]:
    """按类别构建 Provider 实例列表。"""
    providers: list[BaseProvider] = []
    with session_scope() as session:
        configs = [
            site_to_config(site)
            for site in list_sites(session, kind=kind, enabled_only=enabled_only)
        ]
    for config in configs:
        provider = create_provider(config["provider"], config)
        if provider:
            providers.append(provider)
        else:
            logger.warning(
                "站点 %s 使用了未知 provider: %s", config["name"], config["provider"]
            )
    return providers


def search_providers() -> list[SearchProvider]:
    """所有可搜索 Provider（BT 索引器 + 网盘搜索）。"""
    providers: list[SearchProvider] = []
    for kind in (ProviderKind.INDEXER.value, ProviderKind.PAN.value):
        for provider in build_providers(kind):
            if isinstance(provider, SearchProvider):
                providers.append(provider)
    return providers


def downloaders() -> list[BaseDownloader]:
    """所有下载器。"""
    return [
        provider
        for provider in build_providers(ProviderKind.DOWNLOADER.value)
        if isinstance(provider, BaseDownloader)
    ]


def default_downloader(prefer: str | None = None) -> BaseDownloader | None:
    """取默认下载器（可按名字优先）。"""
    items = downloaders()
    if not items:
        return None
    if prefer:
        for item in items:
            if item.site_name == prefer or item.name == prefer:
                return item
    return items[0]


def notifiers() -> list[BaseNotifier]:
    """所有通知渠道。"""
    return [
        provider
        for provider in build_providers(ProviderKind.NOTIFIER.value)
        if isinstance(provider, BaseNotifier)
    ]


def media_servers() -> list[BaseProvider]:
    """所有媒体服务器。"""
    return build_providers(ProviderKind.MEDIASERVER.value)


def get_provider_by_site_id(site_id: int) -> BaseProvider | None:
    """按站点 ID 构建 Provider。"""
    with session_scope() as session:
        site = session.get(SiteConfig, site_id)
        if not site:
            return None
        config = site_to_config(site)
    return create_provider(config["provider"], config)
