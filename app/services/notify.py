"""通知服务与事件总线。

事件总线让插件可以订阅系统事件（下载完成、订阅完成等）而无需改动核心代码。

## 按事件类型分渠道（v1.12.0）

原先所有事件都推给所有渠道，实际用起来很吵：「开始下载」这种流水信息
和「站点掉线」这种需要立刻处理的告警混在一个群里，重要的那条反而被刷掉。

现在每个通知渠道可以在站点 ``options`` 里声明自己关心哪些事件：

* ``{"events": ["site.unhealthy", "download.completed"]}`` —— 白名单，只收这些
* ``{"events_exclude": ["download.added"]}`` —— 黑名单，其余都收
* ``{"min_level": "warning"}`` —— 只收 warning 及以上

**不配就收全部**，与 v1.11.0 行为完全一致（不能让升级后有人突然收不到通知）。

## 告警去抖

``site.unhealthy`` 这类告警在巡检里可能反复触发（比如每 3 小时一轮，站点一直坏着）。
:func:`should_suppress` 提供按 key 的冷却窗口，同一条告警在窗口内只发一次。
"""

from __future__ import annotations

import asyncio
import inspect
import time
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

#: 通知级别的严重度序（用于 ``min_level`` 过滤）
LEVEL_RANK: dict[str, int] = {
    NotifyLevel.INFO.value: 0,
    NotifyLevel.SUCCESS.value: 1,
    NotifyLevel.WARNING.value: 2,
    NotifyLevel.ERROR.value: 3,
}

#: 去抖记录 ``{key: 到期时间戳}``。放内存即可：进程重启后重新发一条告警
#: 是可接受的（比漏发好），没必要为此加一张表。
_SUPPRESS: dict[str, float] = {}


def should_suppress(key: str, *, window_seconds: int) -> bool:
    """同一条告警在冷却窗口内是否应被抑制。

    **为什么需要**：站点掉线后每轮巡检都会命中「连续失败达阈值」，
    若不去抖，用户会每 3 小时收到同一条「站点异常」——几次之后就学会
    忽略这个通知，等于告警失效。返回 ``True`` 表示这次别发了。

    ``window_seconds <= 0`` 表示不去抖（总是发送）。
    """
    if window_seconds <= 0:
        return False
    now = time.time()
    # 顺手清理过期项，避免长期运行后字典无限增长
    for stale in [k for k, expire in _SUPPRESS.items() if expire <= now]:
        _SUPPRESS.pop(stale, None)
    if _SUPPRESS.get(key, 0) > now:
        return True
    _SUPPRESS[key] = now + window_seconds
    return False


def clear_suppression(key: str) -> None:
    """清掉某个 key 的抑制记录。

    **恢复通知必须调用它**：否则「坏→好→又坏」时，第二次异常还落在
    上一次的冷却窗口里会被静默吞掉，用户就再也收不到这个站的告警了。
    """
    _SUPPRESS.pop(key, None)


def reset_suppression() -> None:
    """清空去抖状态（测试用）。"""
    _SUPPRESS.clear()


def channel_accepts(
    options: dict[str, Any] | None, *, event: str | None, level: str
) -> bool:
    """判断某渠道是否接收这条通知。

    三种声明方式（都写在站点 ``options`` 里），**不配则全收**：

    * ``events``：白名单。只有列出的事件才推。
    * ``events_exclude``：黑名单。列出的不推，其余都推。
    * ``min_level``：级别下限，如 ``warning`` 表示只要 warning/error。

    白名单与黑名单同时配置时**白名单优先**（更明确的意图）。
    事件名支持前缀通配：``site.`` 可匹配 ``site.unhealthy``。
    """
    config = options or {}
    if not isinstance(config, dict):
        return True

    min_level = str(config.get("min_level") or "").strip().lower()
    if min_level in LEVEL_RANK and (
        LEVEL_RANK.get(str(level).lower(), 0) < LEVEL_RANK[min_level]
    ):
        return False

    def _listed(raw: Any) -> list[str]:
        if isinstance(raw, str):
            items = [raw]
        elif isinstance(raw, (list, tuple, set)):
            items = [str(item) for item in raw]
        else:
            return []
        return [item.strip() for item in items if str(item).strip()]

    def _matches(name: str, patterns: list[str]) -> bool:
        # 前缀通配：配 "site." 就能一次覆盖 site.unhealthy / site.recovered
        return any(
            name == pattern or (pattern.endswith(".") and name.startswith(pattern))
            for pattern in patterns
        )

    allow = _listed(config.get("events"))
    if allow:
        # 白名单模式下，无事件名的通知（手动测试推送等）一律放行，
        # 否则用户在界面点「测试」会以为渠道坏了
        return True if not event else _matches(str(event), allow)

    deny = _listed(config.get("events_exclude"))
    return not (deny and event and _matches(str(event), deny))


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
    suppress_key: str | None = None,
    suppress_seconds: int = 0,
) -> int:
    """向**关心这个事件**的已启用渠道推送通知，返回成功渠道数。

    ``suppress_key`` / ``suppress_seconds``：给告警类通知去抖用。
    同一个 key 在窗口内只发一次，避免站点一直坏着就每轮推一条。
    """
    if suppress_key and should_suppress(suppress_key, window_seconds=suppress_seconds):
        logger.info("通知已按去抖规则抑制：%s（%s）", title, suppress_key)
        return 0

    all_channels = site_service.notifiers()
    # 按渠道自己声明的事件订阅过滤（不配的渠道收全部，与 v1.11.0 行为一致）
    channels = [
        channel
        for channel in all_channels
        if channel_accepts(
            getattr(channel, "config", {}).get("options"), event=event, level=level
        )
    ]
    if all_channels and not channels:
        logger.debug(
            "事件 %s 没有匹配的通知渠道（共 %d 个渠道均已声明不接收）",
            event,
            len(all_channels),
        )
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
                    # 没有任何匹配渠道时也算"已处理"：这是用户主动配的过滤结果，
                    # 不是故障，不该在通知历史里显示成一片失败
                    success=success > 0 or not channels,
                    payload=payload or {},
                )
            )
    except Exception as exc:  # pragma: no cover
        logger.warning("通知记录写入失败: %s", exc)

    if event:
        await emit(event, {"title": title, "body": body, **(payload or {})})
    return success
