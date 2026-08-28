"""Telegram Bot 通知。"""

from __future__ import annotations

from app.providers.notify.base import BaseNotifier
from app.providers.registry import register
from app.schemas.enums import NotifyLevel
from app.utils.http import fetch_json

LEVEL_ICONS = {
    NotifyLevel.INFO.value: "ℹ️",
    NotifyLevel.SUCCESS.value: "✅",
    NotifyLevel.WARNING.value: "⚠️",
    NotifyLevel.ERROR.value: "❌",
}


@register
class TelegramNotifier(BaseNotifier):
    """Telegram。"""

    name = "telegram"
    display_name = "Telegram Bot"

    async def send(
        self,
        title: str,
        body: str = "",
        *,
        level: str = NotifyLevel.INFO.value,
        image: str | None = None,
        link: str | None = None,
    ) -> bool:
        token = str(self.config.get("api_key") or self.option("token") or "")
        chat_id = str(self.option("chat_id") or self.config.get("username") or "")
        if not token or not chat_id:
            return False

        icon = LEVEL_ICONS.get(level, "")
        text = f"{icon} *{title}*\n{body}".strip()
        if link:
            text += f"\n{link}"

        api_base = str(self.config.get("url") or "https://api.telegram.org").rstrip("/")
        payload = await fetch_json(
            f"{api_base}/bot{token}/sendMessage",
            method="POST",
            json_body={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False,
            },
            timeout=self.config.get("timeout"),
        )
        return bool(payload and payload.get("ok"))

    async def health_check(self) -> tuple[bool, str]:
        token = str(self.config.get("api_key") or self.option("token") or "")
        if not token:
            return False, "未配置 Bot Token"
        api_base = str(self.config.get("url") or "https://api.telegram.org").rstrip("/")
        payload = await fetch_json(f"{api_base}/bot{token}/getMe", timeout=10)
        if not payload or not payload.get("ok"):
            return False, "Token 无效或网络不可达"
        return True, f"连接正常：{payload['result'].get('username')}"
