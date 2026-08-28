"""Emby 媒体服务器：入库后触发扫描。"""

from __future__ import annotations

from app.core.logger import get_logger
from app.providers.base import BaseProvider
from app.providers.registry import register
from app.schemas.enums import ProviderKind
from app.utils.http import async_client, fetch_json

logger = get_logger(__name__)


@register
class EmbyServer(BaseProvider):
    """Emby。"""

    name = "emby"
    kind = ProviderKind.MEDIASERVER.value
    display_name = "Emby"

    @property
    def base_url(self) -> str:
        return str(self.config.get("url") or "http://127.0.0.1:8096").rstrip("/")

    @property
    def token(self) -> str:
        return str(self.config.get("api_key") or "")

    def _params(self) -> dict[str, str]:
        return {"api_key": self.token}

    async def refresh_library(self, path: str | None = None) -> bool:
        """触发媒体库扫描；``path`` 存在时尝试增量刷新。"""
        if not self.token:
            logger.warning("Emby 未配置 api_key，跳过刷新")
            return False
        try:
            async with async_client(timeout=self.config.get("timeout")) as client:
                if path:
                    response = await client.post(
                        f"{self.base_url}/Library/Media/Updated",
                        params=self._params(),
                        json={"Updates": [{"Path": str(path), "UpdateType": "Created"}]},
                    )
                    if response.status_code < 300:
                        return True
                response = await client.post(
                    f"{self.base_url}/Library/Refresh", params=self._params()
                )
                return response.status_code < 300
        except Exception as exc:
            logger.warning("Emby 刷新失败: %s", exc)
            return False

    async def health_check(self) -> tuple[bool, str]:
        if not self.token:
            return False, "未配置 api_key"
        payload = await fetch_json(
            f"{self.base_url}/System/Info",
            params=self._params(),
            timeout=self.config.get("timeout"),
        )
        if not payload:
            return False, "无法连接 Emby"
        return True, f"连接正常，版本 {payload.get('Version', '未知')}"
