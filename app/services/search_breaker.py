"""搜索熔断器：让「反复吃满超时预算」的站点暂时不参与聚合搜索。

**为什么需要它**（实测数据，`凡人修仙传`，8 个启用站点）：

    YouTube 视频搜索   ms=24997  raw=0   ← 吃满整个 25s 预算，0 条结果
    PanSou 盘搜        ms=19552  raw=2529
    追剧 zhuiju.us     ms=4636   raw=10
    Mukaku 影视站      ms=2649   raw=217
    Nyaa 动漫          ms=1857   raw=75
    Bilibili 视频搜索  ms=842    raw=20

整体耗时 **25.4s**，而除 YouTube/PanSou 之外的站点 5s 内就全回来了。
`asyncio.gather` 要等最慢的那个，所以**一个连不通的站决定了所有人的等待时间**
（ADR-66 把「每关键词各一份超时」改成了「站点总预算」，但预算本身仍会被吃满）。

站点级超时预算解决的是「不要成倍放大」，熔断解决的是「不要一直重复交学费」：
一个站连续 N 次吃满预算且零结果，就把它冷却一段时间。冷却期内直接跳过，
搜索立刻回到「最慢的健康站」的速度。

**刻意的设计取舍**：

1. **只对「吃满预算且零结果」计数**。返回结果的慢站不熔断——慢但有用的站
   （PanSou 就是）不该被剔掉；正常的「快速返回空」也不计数，
   否则冷门片搜不到会把好站误伤。
2. **只在内存里记**，不落库。熔断是运行时的自我保护，重启后重新给机会；
   落库会让「站点临时抽风」变成需要人工清理的持久状态。
3. **冷却到期自动恢复**，且恢复后**从零开始计数**（半开：给一次完整机会）。
4. **绝不静默**。跳过要在诊断里如实写明原因和剩余冷却时间，
   否则就成了 ADR-20 那种「结果变少但没人知道为什么」的静默故障。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger(__name__)


@dataclass
class _SiteState:
    """单个站点的连续超时计数与冷却截止时间。"""

    strikes: int = 0
    open_until: float = 0.0
    last_reason: str = ""
    trips: int = 0
    history: list[int] = field(default_factory=list)


#: 站点名 → 状态。进程内存，重启即清空（见模块文档第 2 条）。
_STATES: dict[str, _SiteState] = {}


def _now() -> float:
    return time.monotonic()


def enabled() -> bool:
    return bool(getattr(settings, "SEARCH_BREAKER_ENABLED", True))


def _threshold() -> int:
    return max(1, int(getattr(settings, "SEARCH_BREAKER_THRESHOLD", 3) or 3))


def _cooldown() -> float:
    return max(0.0, float(getattr(settings, "SEARCH_BREAKER_COOLDOWN_MINUTES", 10) or 0) * 60)


def is_open(site: str) -> bool:
    """该站点当前是否处于冷却（应跳过）。"""
    if not enabled():
        return False
    state = _STATES.get(site)
    if not state or state.open_until <= 0:
        return False
    if _now() >= state.open_until:
        # 冷却到期：半开，清零计数重新给一次完整机会
        state.open_until = 0.0
        state.strikes = 0
        logger.info("站点 %s 熔断冷却结束，恢复参与搜索", site)
        return False
    return True


def remaining(site: str) -> int:
    """冷却剩余秒数（未熔断返回 0）。"""
    state = _STATES.get(site)
    if not state or state.open_until <= 0:
        return 0
    return max(0, int(state.open_until - _now()))


def skip_reason(site: str) -> str:
    """跳过原因，必须能让用户看懂并知道何时恢复。"""
    state = _STATES.get(site)
    left = remaining(site)
    if left <= 0:
        return ""
    detail = (state.last_reason if state else "") or "连续超时"
    return f"已暂时跳过（{detail}），约 {max(1, left // 60)} 分钟后自动重试"


def record_timeout(site: str, elapsed_ms: int, reason: str = "") -> bool:
    """记一次「吃满预算且零结果」。返回本次是否触发熔断。"""
    if not enabled():
        return False
    state = _STATES.setdefault(site, _SiteState())
    state.strikes += 1
    state.last_reason = reason or f"连续 {state.strikes} 次超时无结果"
    state.history.append(int(elapsed_ms))
    del state.history[:-10]
    if state.strikes >= _threshold():
        cooldown = _cooldown()
        if cooldown <= 0:
            return False
        state.open_until = _now() + cooldown
        state.trips += 1
        logger.warning(
            "站点 %s 连续 %d 次吃满搜索预算且无结果，熔断 %.0f 分钟",
            site,
            state.strikes,
            cooldown / 60,
        )
        return True
    return False


def record_success(site: str) -> None:
    """站点正常返回（哪怕是空结果）就清零，避免误伤。"""
    state = _STATES.get(site)
    if state and (state.strikes or state.open_until):
        state.strikes = 0
        state.open_until = 0.0


def snapshot() -> list[dict[str, object]]:
    """当前熔断状态，供接口/界面展示（不含内部对象）。"""
    rows = []
    for site, state in sorted(_STATES.items()):
        rows.append(
            {
                "site": site,
                "strikes": state.strikes,
                "open": remaining(site) > 0,
                "remaining_seconds": remaining(site),
                "trips": state.trips,
                "reason": state.last_reason,
                "recent_ms": list(state.history),
            }
        )
    return rows


def reset(site: str | None = None) -> int:
    """手动恢复：用户改完站点配置后不该还要等冷却结束。"""
    if site:
        return 1 if _STATES.pop(site, None) is not None else 0
    count = len(_STATES)
    _STATES.clear()
    return count
