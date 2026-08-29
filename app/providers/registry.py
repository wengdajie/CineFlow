"""Provider 注册表。"""

from __future__ import annotations

from typing import Any, TypeVar

from app.core.logger import get_logger
from app.providers.base import BaseProvider

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseProvider)

_REGISTRY: dict[str, type[BaseProvider]] = {}


def register(provider_cls: type[T]) -> type[T]:
    """装饰器：注册 Provider 类。"""
    key = provider_cls.name.lower()
    if key in _REGISTRY and _REGISTRY[key] is not provider_cls:
        logger.warning("Provider %s 被重复注册，后者覆盖前者", key)
    _REGISTRY[key] = provider_cls
    return provider_cls


def get_provider_class(name: str) -> type[BaseProvider] | None:
    """按名字取 Provider 类。"""
    return _REGISTRY.get(str(name or "").lower())


def create_provider(name: str, config: dict[str, Any] | None = None) -> BaseProvider | None:
    """实例化 Provider。"""
    provider_cls = get_provider_class(name)
    if not provider_cls:
        logger.warning("未找到 Provider: %s", name)
        return None
    try:
        return provider_cls(config or {})
    except Exception as exc:
        logger.error("Provider %s 初始化失败: %s", name, exc)
        return None


def list_providers(kind: str | None = None) -> list[dict[str, Any]]:
    """列出已注册 Provider（可按类别过滤）。"""
    items = []
    for key, provider_cls in sorted(_REGISTRY.items()):
        if kind and provider_cls.kind != kind:
            continue
        items.append(
            {
                "name": key,
                "kind": provider_cls.kind,
                "display_name": provider_cls.display_name,
                "doc": (provider_cls.__doc__ or "").strip().splitlines()[0]
                if provider_cls.__doc__
                else "",
            }
        )
    return items


def load_builtin_providers() -> None:
    """导入内置 Provider 模块，触发注册。"""
    from importlib import import_module

    modules = (
        "app.providers.indexer.torznab",
        "app.providers.indexer.rss",
        "app.providers.indexer.nyaa",
        "app.providers.indexer.generic_api",
        "app.providers.indexer.generic_html",
        "app.providers.indexer.mukaku",
        "app.providers.indexer.webvideo",
        "app.providers.indexer.yyets",
        "app.providers.indexer.wp_film",
        "app.providers.pan.pansou",
        "app.providers.pan.generic",
        "app.providers.panstorage.alist",
        "app.providers.panstorage.quark",
        "app.providers.panstorage.local_dir",
        "app.providers.panstorage.webdav",
        "app.providers.downloader.qbittorrent",
        "app.providers.downloader.transmission",
        "app.providers.downloader.aria2",
        "app.providers.downloader.ytdlp",
        "app.providers.mediaserver.emby",
        "app.providers.mediaserver.jellyfin",
        "app.providers.mediaserver.plex",
        "app.providers.notify.webhook",
        "app.providers.notify.telegram",
        "app.providers.notify.wecom",
        "app.providers.notify.bark",
    )
    for module in modules:
        try:
            import_module(module)
        except Exception as exc:  # pragma: no cover
            logger.error("加载内置 Provider 失败 %s: %s", module, exc)
