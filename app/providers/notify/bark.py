"""Bark（iOS 推送）通知。"""

from __future__ import annotations

from urllib.parse import quote

from app.providers.notify.base import BaseNotifier
from app.providers.registry import register
from app.schemas.enums import NotifyLevel
from app.utils.http import fetch_json


@register
class BarkNotifier(BaseNotifier):
    """Bark。"""

    name = "bark"
    display_name = "Bark（iOS）"

    async def send(
        self,
        title: str,
        body: str = "",
        *,
        level: str = NotifyLevel.INFO.value,
        image: str | None = None,
        link: str | None = None,
    ) -> bool:
        base = str(self.config.get("url") or "https://api.day.app").rstrip("/")
        key = str(self.config.get("api_key") or "")
        if not key:
            return False

        payload = {
            "title": title,
            "body": body or title,
            "group": self.option("group", "CineFlow"),
            "icon": image or self.option("icon", ""),
            "url": link or "",
            "level": "timeSensitive" if level == NotifyLevel.ERROR.value else "active",
        }
        result = await fetch_json(
            f"{base}/{quote(key)}",
            method="POST",
            json_body=payload,
            timeout=self.config.get("timeout"),
        )
        return bool(result and str(result.get("code")) in ("200", "0"))
