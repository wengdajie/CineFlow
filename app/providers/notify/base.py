"""通知渠道抽象。"""

from __future__ import annotations

from abc import abstractmethod

from app.providers.base import BaseProvider
from app.schemas.enums import NotifyLevel, ProviderKind


class BaseNotifier(BaseProvider):
    """通知基类。"""

    kind = ProviderKind.NOTIFIER.value

    @abstractmethod
    async def send(
        self,
        title: str,
        body: str = "",
        *,
        level: str = NotifyLevel.INFO.value,
        image: str | None = None,
        link: str | None = None,
    ) -> bool:
        """发送通知，返回是否成功。"""

    def plain_text(self, title: str, body: str = "") -> str:
        """把标题与正文拼成纯文本。"""
        return f"{title}\n{body}".strip()
