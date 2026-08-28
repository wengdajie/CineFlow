"""企业微信机器人通知。"""

from __future__ import annotations

from app.providers.notify.base import BaseNotifier
from app.providers.registry import register
from app.schemas.enums import NotifyLevel
from app.utils.http import fetch_json


@register
class WecomNotifier(BaseNotifier):
    """企业微信群机器人。"""

    name = "wecom"
    display_name = "企业微信机器人"

    async def send(
        self,
        title: str,
        body: str = "",
        *,
        level: str = NotifyLevel.INFO.value,
        image: str | None = None,
        link: str | None = None,
    ) -> bool:
        webhook = str(self.config.get("url") or "")
        if not webhook:
            return False

        content = f"**{title}**\n{body}".strip()
        if link:
            content += f"\n[查看详情]({link})"
        payload = {"msgtype": "markdown", "markdown": {"content": content}}
        result = await fetch_json(
            webhook,
            method="POST",
            json_body=payload,
            timeout=self.config.get("timeout"),
        )
        return bool(result and result.get("errcode") == 0)

    async def health_check(self) -> tuple[bool, str]:
        if not self.config.get("url"):
            return False, "未配置 webhook 地址"
        return True, "已配置（发送时校验）"
