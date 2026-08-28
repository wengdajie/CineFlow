"""通知服务与事件总线。

事件总线让插件可以订阅系统事件（下载完成、订阅完成等）而无需改动核心代码。
"""

from __future__ import annotations

import asyncio
import inspect
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from app.core.logger import get_logger
from app.db.models import NotificationRecord
from app.db.session import session_scope
from app.schemas.enums import NotifyLevel
from app.services import sites as site_service

logger = get_logger(__name__)

Handler = Callable[..., Any]
_HANDLERS: dict[str, list[Handler]] = defaultdict(list)


def subscribe_event(event: str, handler: Handler) -> None:
    """注册事件处理器。"""
    if handler not in _HANDLERS[event]:
        _HANDLERS[event].append(handler)


def unsubscribe_event(event: str, handler: Handler) -> None:
    """注销事件处理器。"""
    if handler in _HANDLERS[event]:
        _HANDLERS[event].remove(handler)


def clear_handlers(event: str | None = None) -> None:
    """清理事件处理器（插件重载时使用）。"""
    if event:
        _HANDLERS.pop(event, None)
    else:
        _HANDLERS.clear()


async def emit(event: str, payload: dict[str, Any] | None = None) -> None:
    """广播事件（异常隔离，单个处理器失败不影响其他）。"""
    data = payload or {}
    for handler in list(_HANDLERS.get(event, [])):
        try:
            result = handler(data)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            logger.error("事件 %s 的处理器执行失败: %s", event, exc)


async def send(
    title: str,
    body: str = "",
    *,
    level: str = NotifyLevel.INFO.value,
    event: str | None = None,
    image: str | None = None,
    link: str | None = None,
    payload: dict[str, Any] | None = None,
) -> int:
    """向所有已启用渠道推送通知，返回成功渠道数。"""
    channels = site_service.notifiers()
    success = 0
    results: list[tuple[str, bool]] = []

    if channels:
        outcomes = await asyncio.gather(
            *(
                channel.send(title, body, level=level, image=image, link=link)
                for channel in channels
            ),
            return_exceptions=True,
        )
        for channel, outcome in zip(channels, outcomes, strict=False):
            ok = outcome is True
            if isinstance(outcome, Exception):
                logger.warning("通知渠道 %s 异常: %s", channel.site_name, outcome)
            results.append((channel.site_name, ok))
            success += int(ok)

    try:
        with session_scope() as session:
            session.add(
                NotificationRecord(
                    title=title,
                    body=body,
                    level=level,
                    event=event,
                    channel=",".join(name for name, _ in results) or None,
                    success=success > 0 or not channels,
                    payload=payload or {},
                )
            )
    except Exception as exc:  # pragma: no cover
        logger.warning("通知记录写入失败: %s", exc)

    if event:
        await emit(event, {"title": title, "body": body, **(payload or {})})
    return success
