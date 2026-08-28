"""站点/Provider 配置服务：把数据库配置实例化为可用 Provider。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
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


#: 轮询策略的游标（进程内即可：重启后从头轮询没有副作用）
_ROUND_ROBIN_CURSOR = 0


def _task_counts() -> dict[str, int]:
    """各下载器当前在跑的任务数（用于 least_tasks 策略）。"""
    from app.db.models import DownloadTask
    from app.schemas.enums import TaskStatus

    with session_scope() as session:
        rows = session.execute(
            select(DownloadTask.downloader, func.count(DownloadTask.id))
            .where(
                DownloadTask.status.in_(
                    [TaskStatus.DOWNLOADING.value, TaskStatus.PENDING.value]
                )
            )
            .group_by(DownloadTask.downloader)
        ).all()
    return {str(name): int(count) for name, count in rows if name}


def healthy_downloaders() -> list[BaseDownloader]:
    """按策略排序后的下载器列表，已知不健康的排到最后。

    为什么"排后"而不是"剔除"：健康数据可能过期（比如刚重启下载器还没探测），
    直接剔除会导致"明明能用却不投递"。排到最后既避开坏的，又保留兜底。
    """
    global _ROUND_ROBIN_CURSOR
    items = downloaders()
    if len(items) <= 1:
        return items

    strategy = str(settings.DOWNLOADER_STRATEGY or "priority").strip().lower()
    if strategy == "least_tasks":
        counts = _task_counts()
        items.sort(key=lambda item: (counts.get(item.site_name, 0), item.priority))
    elif strategy == "round_robin":
        _ROUND_ROBIN_CURSOR = (_ROUND_ROBIN_CURSOR + 1) % len(items)
        items = items[_ROUND_ROBIN_CURSOR:] + items[:_ROUND_ROBIN_CURSOR]
    else:  # priority：站点 priority 数字越小越优先（与其它 Provider 一致）
        items.sort(key=lambda item: item.priority)

    try:
        from app.services import site_health

        health = site_health.downloader_health()
    except Exception:  # pragma: no cover - 健康数据不可用时退回原顺序
        return items
    bad = {name for name, status in health.items() if status in ("down", "degraded")}
    if not bad:
        return items
    return [item for item in items if item.site_name not in bad] + [
        item for item in items if item.site_name in bad
    ]


def default_downloader(prefer: str | None = None) -> BaseDownloader | None:
    """取默认下载器（可按名字优先，否则按当前策略挑）。"""
    items = healthy_downloaders()
    if not items:
        return None
    if prefer:
        for item in items:
            if item.site_name == prefer or item.name == prefer:
                return item
    return items[0]


def downloader_candidates(prefer: str | None = None) -> list[BaseDownloader]:
    """投递候选序列：首选在前，其余作为失败自动换源的备选。

    ``CF_DOWNLOADER_FAILOVER=false`` 时只返回首选一个，
    行为与 v1.4.0 完全一致（不擅自把任务投到别的下载器）。
    """
    items = healthy_downloaders()
    if not items:
        return []
    if prefer:
        picked = [
            item for item in items if item.site_name == prefer or item.name == prefer
        ]
        others = [item for item in items if item not in picked]
        items = picked + others if picked else items
    if not settings.DOWNLOADER_FAILOVER:
        return items[:1]
    return items


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
