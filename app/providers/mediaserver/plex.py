"""Plex 媒体服务器。"""

from __future__ import annotations

from app.core.logger import get_logger
from app.providers.base import BaseProvider
from app.providers.registry import register
from app.schemas.enums import ProviderKind
from app.utils.http import fetch_text

logger = get_logger(__name__)


@register
class PlexServer(BaseProvider):
    """Plex。"""

    name = "plex"
    kind = ProviderKind.MEDIASERVER.value
    display_name = "Plex"

    @property
    def base_url(self) -> str:
        return str(self.config.get("url") or "http://127.0.0.1:32400").rstrip("/")

    @property
    def token(self) -> str:
        return str(self.config.get("api_key") or self.config.get("password") or "")

    async def refresh_library(self, path: str | None = None) -> bool:
        """刷新全部library section。"""
        if not self.token:
            logger.warning("Plex 未配置 token，跳过刷新")
            return False
        section = self.option("section")
        target = (
            f"{self.base_url}/library/sections/{section}/refresh"
            if section
            else f"{self.base_url}/library/sections/all/refresh"
        )
        result = await fetch_text(
            target,
            params={"X-Plex-Token": self.token},
            timeout=self.config.get("timeout"),
        )
        return result is not None

    async def health_check(self) -> tuple[bool, str]:
        if not self.token:
            return False, "未配置 token"
        text = await fetch_text(
            f"{self.base_url}/identity",
            params={"X-Plex-Token": self.token},
            timeout=self.config.get("timeout"),
        )
        if text is None:
            return False, "无法连接 Plex"
        return True, "连接正常"
