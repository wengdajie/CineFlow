"""Jellyfin 媒体服务器。"""

from __future__ import annotations

from app.providers.mediaserver.emby import EmbyServer
from app.providers.registry import register
from app.schemas.enums import ProviderKind


@register
class JellyfinServer(EmbyServer):
    """Jellyfin（API 与 Emby 基本兼容）。"""

    name = "jellyfin"
    kind = ProviderKind.MEDIASERVER.value
    display_name = "Jellyfin"

    @property
    def base_url(self) -> str:
        return str(self.config.get("url") or "http://127.0.0.1:8096").rstrip("/")
