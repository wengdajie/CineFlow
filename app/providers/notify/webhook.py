"""通用 Webhook 通知（可对接 Home Assistant、n8n、自建服务等）。"""

from __future__ import annotations

from typing import Any

from app.providers.notify.base import BaseNotifier
from app.providers.registry import register
from app.schemas.enums import NotifyLevel
from app.utils.http import async_client


@register
class WebhookNotifier(BaseNotifier):
    """Webhook。"""

    name = "webhook"
    display_name = "自定义 Webhook"

    async def send(
        self,
        title: str,
        body: str = "",
        *,
        level: str = NotifyLevel.INFO.value,
        image: str | None = None,
        link: str | None = None,
    ) -> bool:
        url = str(self.config.get("url") or "")
        if not url:
            return False

        payload: dict[str, Any] = {
            "title": title,
            "content": body,
            "text": self.plain_text(title, body),
            "level": level,
            "image": image,
            "link": link,
        }
        template = self.option("template")
        if isinstance(template, dict):
            # 支持自定义 body 模板，占位符 {title} / {content} / {level}
            payload = {
                key: (
                    value.format(title=title, content=body, level=level)
                    if isinstance(value, str)
                    else value
                )
                for key, value in template.items()
            }

        headers = dict(self.option("headers", {}) or {})
        method = str(self.option("method", "POST")).upper()
        try:
            async with async_client(
                timeout=self.config.get("timeout"), headers=headers
            ) as client:
                response = await client.request(method, url, json=payload)
                return response.status_code < 300
        except Exception:
            return False
