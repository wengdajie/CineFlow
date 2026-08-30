"""站点健康探测：主动发现「静默失效」的站点。

为什么必须主动探测：
PT 站点 Cookie 过期、盘搜服务下线、私站被墙，**表现全都是搜索返回 0 条**
而不是抛错。用户看到的现象是"订阅好久没动静"，很难联想到站点掉线——
这是同类项目里被反复吐槽的排障黑洞。

判定分三档（``SiteHealthStatus``）：
- ``ok``       ：探测成功且有结果
- ``degraded`` ：能连通但**返回 0 条**或极慢 → 最典型的 Cookie 过期信号
- ``down``     ：连不通 / 报错 / 超时

连续 ``CF_SITE_HEALTH_FAIL_THRESHOLD`` 次非 ok 才告警（单次波动不打扰用户），
恢复时再发一条恢复通知——只在**状态翻转**时通知，避免每轮刷屏。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from sqlalchemy import delete, func, select

from app.core.config import settings
from app.core.logger import get_logger
from app.db.base import utcnow
from app.db.models import SiteConfig, SiteHealthRecord
from app.db.session import session_scope
from app.providers.base import SearchProvider
from app.schemas.enums import EventType, NotifyLevel, ProviderKind, SiteHealthStatus
from app.services import sites as site_service

logger = get_logger(__name__)

#: 探测用的中性关键词。刻意用一个常见字，避免"因为没有这部片"而误判掉线。
PROBE_KEYWORD = "的"

#: 单站探测超时（秒）。比正常搜索更短：健康探测不该拖住整轮巡检。
PROBE_TIMEOUT = 20

#: 超过这个耗时即使成功也算 degraded（能连但慢到没法用）
SLOW_MS = 15000

#: 每个站点最多保留多少条历史记录（够看出趋势，又不会无限膨胀）
KEEP_PER_SITE = 50


async def _probe_provider(provider: Any, kind: str) -> tuple[str, int, str]:
    """探测单个 Provider，返回 ``(status, result_count, message)``。"""
    # 优先用 Provider 自己的 health_check（下载器/媒体服务器/通知只有这个）
    if not isinstance(provider, SearchProvider):
        ok, message = await provider.health_check()
        if ok:
            return SiteHealthStatus.OK.value, 0, message or "连通正常"
        return SiteHealthStatus.DOWN.value, 0, message or "连通失败"

    # 搜索类站点：真的搜一次，因为 health_check 探首页往往是通的，
    # 而 Cookie 过期只会体现在搜索结果上
    try:
        results = await asyncio.wait_for(
            provider.search(PROBE_KEYWORD), timeout=PROBE_TIMEOUT
        )
    except asyncio.TimeoutError:
        return SiteHealthStatus.DOWN.value, 0, f"搜索超时（>{PROBE_TIMEOUT}s）"
    except Exception as exc:
        return SiteHealthStatus.DOWN.value, 0, f"搜索异常：{exc}"[:255]

    count = len(results or [])
    if count == 0:
        return (
            SiteHealthStatus.DEGRADED.value,
            0,
            "可连通但搜索 0 结果，常见原因：Cookie/passkey 过期、站点改版、被墙",
        )
    return SiteHealthStatus.OK.value, count, f"搜索返回 {count} 条"


def _trim_history(session: Any, site_name: str) -> None:
    """只保留最近 KEEP_PER_SITE 条记录。"""
    ids = list(
        session.execute(
            select(SiteHealthRecord.id)
            .where(SiteHealthRecord.site_name == site_name)
            .order_by(SiteHealthRecord.id.desc())
            .offset(KEEP_PER_SITE)
        ).scalars()
    )
    if ids:
        session.execute(delete(SiteHealthRecord).where(SiteHealthRecord.id.in_(ids)))


def _recent_statuses(session: Any, site_name: str, limit: int) -> list[str]:
    """取最近 N 次状态（新→旧）。"""
    return list(
        session.execute(
            select(SiteHealthRecord.status)
            .where(SiteHealthRecord.site_name == site_name)
            .order_by(SiteHealthRecord.id.desc())
            .limit(limit)
        ).scalars()
    )


async def check_site(site_id: int, *, notify: bool = True) -> dict[str, Any]:
    """探测单个站点并落一条记录。

    ``notify=False`` 用于用户手点「探测」：他正盯着屏幕看结果，
    再推一条通知过去纯属噪音。
    """
    with session_scope() as session:
        site = session.get(SiteConfig, site_id)
        if not site:
            return {"success": False, "message": "站点不存在"}
        config = site_service.site_to_config(site)
        kind = site.kind
        name = site.name
        provider_name = site.provider

    from app.providers.registry import create_provider

    provider = create_provider(provider_name, config)
    if provider is None:
        status, count, message = (
            SiteHealthStatus.DOWN.value,
            0,
            f"未知 provider：{provider_name}",
        )
        latency = 0
    else:
        started = time.perf_counter()
        status, count, message = await _probe_provider(provider, kind)
        latency = int((time.perf_counter() - started) * 1000)
        if status == SiteHealthStatus.OK.value and latency > SLOW_MS:
            status = SiteHealthStatus.DEGRADED.value
            message = f"响应过慢（{latency}ms），可能影响巡检"

    threshold = max(1, int(settings.SITE_HEALTH_FAIL_THRESHOLD))
    alert: str | None = None
    with session_scope() as session:
        previous = _recent_statuses(session, name, threshold)
        session.add(
            SiteHealthRecord(
                site_id=site_id,
                site_name=name,
                kind=kind,
                provider=provider_name,
                status=status,
                latency_ms=latency,
                result_count=count,
                message=message,
            )
        )
        site = session.get(SiteConfig, site_id)
        if site is not None:
            site.last_status = f"{status}：{message}"[:255]
            site.last_check_at = utcnow()

        bad = status != SiteHealthStatus.OK.value
        # 连续失败次数达到阈值 → 告警（含本次）
        consecutive = 1 if bad else 0
        if bad:
            for item in previous:
                if item == SiteHealthStatus.OK.value:
                    break
                consecutive += 1
        if bad and consecutive >= threshold:
            alert = "unhealthy"
            if settings.SITE_AUTO_DISABLE and site is not None and site.enabled:
                site.enabled = False
                logger.warning("站点 %s 连续 %d 次异常，已自动停用", name, consecutive)
        elif not bad and previous and previous[0] != SiteHealthStatus.OK.value:
            alert = "recovered"
        # 先 flush 让本次记录参与计数，否则裁剪永远多留一条
        session.flush()
        _trim_history(session, name)

    result = {
        "success": status == SiteHealthStatus.OK.value,
        "site_id": site_id,
        "site": name,
        "status": status,
        "latency_ms": latency,
        "result_count": count,
        "message": message,
    }
    if alert and notify:
        await _notify(alert, result)
    result["alert"] = alert
    return result


async def _notify(kind: str, result: dict[str, Any]) -> None:
    """状态翻转时才通知（避免每轮刷屏）。"""
    from app.services import notify as notify_service

    if kind == "unhealthy":
        await notify_service.send(
            f"站点异常：{result['site']}",
            f"状态：{result['status']}\n{result['message']}\n"
            "请检查 Cookie / passkey / 站点地址是否仍然有效。",
            level=NotifyLevel.WARNING.value,
            event=EventType.SITE_UNHEALTHY.value,
            # 去抖：站点一直坏着时，每轮巡检都会命中告警条件。
            # 按「站点 + 告警类型」做 key，冷却窗口内只推一条。
            suppress_key=f"site.unhealthy:{result['site']}",
            suppress_seconds=int(settings.NOTIFY_ALERT_COOLDOWN_MINUTES) * 60,
        )
    else:
        # 恢复时必须清掉该站的抑制记录：否则「坏→好→又坏」的第二次异常
        # 会因为还在冷却窗口内而被静默吞掉，用户就再也收不到告警了。
        notify_service.clear_suppression(f"site.unhealthy:{result['site']}")
        await notify_service.send(
            f"站点已恢复：{result['site']}",
            result["message"],
            level=NotifyLevel.SUCCESS.value,
            event=EventType.SITE_RECOVERED.value,
        )


async def check_all(*, kinds: list[str] | None = None, notify: bool = True) -> dict[str, Any]:
    """探测全部已启用站点。

    只探**已启用**的站点：禁用站点不参与业务，探它纯属浪费请求。
    """
    target_kinds = kinds or [
        ProviderKind.INDEXER.value,
        ProviderKind.PAN.value,
        ProviderKind.PANSTORAGE.value,
        ProviderKind.DOWNLOADER.value,
        ProviderKind.MEDIASERVER.value,
    ]
    with session_scope() as session:
        site_ids = list(
            session.execute(
                select(SiteConfig.id)
                .where(SiteConfig.enabled.is_(True))
                .where(SiteConfig.kind.in_(target_kinds))
                .order_by(SiteConfig.priority.asc())
            ).scalars()
        )

    if not site_ids:
        return {"success": True, "checked": 0, "items": [], "message": "没有已启用的站点"}

    items = []
    for site_id in site_ids:
        try:
            items.append(await check_site(site_id, notify=notify))
        except Exception as exc:  # 单站异常不能中断整轮
            logger.warning("站点 %s 健康探测异常: %s", site_id, exc)
            items.append({"success": False, "site_id": site_id, "status": "down", "message": str(exc)[:200]})

    unhealthy = [item for item in items if item.get("status") != SiteHealthStatus.OK.value]
    logger.info("站点健康巡检完成：%d 个，异常 %d 个", len(items), len(unhealthy))
    return {
        "success": True,
        "checked": len(items),
        "unhealthy": len(unhealthy),
        "items": items,
    }


def overview() -> dict[str, Any]:
    """健康总览：每站最新状态 + 计数。"""
    with session_scope() as session:
        sites = list(
            session.execute(select(SiteConfig).order_by(SiteConfig.priority.asc())).scalars()
        )
        rows = []
        counts = {"ok": 0, "degraded": 0, "down": 0, "unknown": 0}
        for site in sites:
            latest = session.execute(
                select(SiteHealthRecord)
                .where(SiteHealthRecord.site_name == site.name)
                .order_by(SiteHealthRecord.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            status = latest.status if latest else "unknown"
            counts[status] = counts.get(status, 0) + 1
            rows.append(
                {
                    "site_id": site.id,
                    "site": site.name,
                    "kind": site.kind,
                    "provider": site.provider,
                    "enabled": site.enabled,
                    "status": status,
                    "latency_ms": latest.latency_ms if latest else 0,
                    "result_count": latest.result_count if latest else 0,
                    "message": latest.message if latest else "尚未探测",
                    "checked_at": latest.created_at.isoformat()
                    if latest and latest.created_at
                    else None,
                }
            )
        total_records = session.execute(select(func.count(SiteHealthRecord.id))).scalar() or 0

    return {
        "success": True,
        "counts": counts,
        "total_records": total_records,
        "enabled": bool(settings.SITE_HEALTH_ENABLED),
        "interval_minutes": int(settings.SITE_HEALTH_INTERVAL_MINUTES),
        "fail_threshold": int(settings.SITE_HEALTH_FAIL_THRESHOLD),
        "auto_disable": bool(settings.SITE_AUTO_DISABLE),
        "items": rows,
    }


def list_records(*, site_name: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """历史探测记录（用于看"从哪天开始不行的"）。"""
    with session_scope() as session:
        stmt = select(SiteHealthRecord).order_by(SiteHealthRecord.id.desc()).limit(limit)
        if site_name:
            stmt = (
                select(SiteHealthRecord)
                .where(SiteHealthRecord.site_name == site_name)
                .order_by(SiteHealthRecord.id.desc())
                .limit(limit)
            )
        records = list(session.execute(stmt).scalars())
        return [
            {
                "id": item.id,
                "site": item.site_name,
                "kind": item.kind,
                "provider": item.provider,
                "status": item.status,
                "latency_ms": item.latency_ms,
                "result_count": item.result_count,
                "message": item.message,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in records
        ]


def unhealthy_sites() -> list[str]:
    """当前处于非 ok 状态的站点名（下载器选择时会避开）。"""
    with session_scope() as session:
        names = list(
            session.execute(
                select(SiteConfig.name).where(SiteConfig.enabled.is_(True))
            ).scalars()
        )
        bad = []
        for name in names:
            latest = session.execute(
                select(SiteHealthRecord.status)
                .where(SiteHealthRecord.site_name == name)
                .order_by(SiteHealthRecord.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            if latest and latest != SiteHealthStatus.OK.value:
                bad.append(name)
        return bad


def downloader_health() -> dict[str, str]:
    """下载器名 → 最新状态（供负载均衡避开挂掉的下载器）。"""
    with session_scope() as session:
        rows = list(
            session.execute(
                select(SiteConfig.name).where(
                    SiteConfig.kind == ProviderKind.DOWNLOADER.value
                )
            ).scalars()
        )
        health: dict[str, str] = {}
        for name in rows:
            latest = session.execute(
                select(SiteHealthRecord.status)
                .where(SiteHealthRecord.site_name == name)
                .order_by(SiteHealthRecord.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            health[name] = latest or "unknown"
        return health
