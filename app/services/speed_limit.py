"""下载器限速时段（夜间跑满 / 白天限速）。

**要解决的问题**（路线图 v1.11.0 记录、v1.12.0 落地）：家里白天要用网，
下载器满速跑会把上行/下行吃干，视频会议和网页都卡；但夜里没人用，
限速纯属浪费。原先只能去下载器自己的界面里手动切，或者干脆不限。

## 为什么把「跨午夜」单独拎出来说

时段配置最容易错的就是 ``23:00-07:00`` 这种**跨午夜**区间。
朴素写法 ``start <= now <= end`` 对它恒为假（23:00 > 07:00），
于是"夜间不限速"永远不会生效。:func:`in_window` 显式区分两种情形，
并配了针对性回归测试。

## 只改「全局」限速，不动单任务

对标 MoviePilot 的 qB 模块有一堆 per-torrent 的限速方法，这里刻意不做：
用户的诉求是"整台机器别占满带宽"，全局限速就够；per-torrent 限速属于
PT 保种的精细调优，应该在下载器自己的界面里做（同 ADR-56 的取舍）。

## 幂等

每次巡检都会把目标限速重新下发一遍，**不做「和上次一样就跳过」的优化**：
用户可能在下载器界面里手动改过，我们下次巡检应该把它纠正回来，
而不是因为"我记得已经设过了"而放任不管。
"""

from __future__ import annotations

from datetime import time as dtime
from typing import Any

from app.core.config import settings
from app.core.logger import get_logger
from app.services import settings_store

logger = get_logger(__name__)

#: 存进 ``settings`` 表的键
KEY_SPEED_LIMIT = "downloader_speed_limit"


def parse_hhmm(raw: str) -> dtime | None:
    """解析 ``HH:MM``；非法返回 ``None``（不抛，避免一个错值让巡检崩掉）。"""
    text = str(raw or "").strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) != 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except (TypeError, ValueError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return dtime(hour=hour, minute=minute)


def in_window(now: dtime, start: dtime, end: dtime) -> bool:
    """``now`` 是否落在 ``[start, end)`` 内，**正确处理跨午夜**。

    * 同日区间 ``09:00-18:00``：``start <= now < end``
    * 跨午夜区间 ``23:00-07:00``：``now >= start`` **或** ``now < end``

    这是本模块最容易写错的一处：用朴素比较，跨午夜时段永远不会命中。
    ``start == end`` 视为**全天**（用户想 24 小时都限速时的自然写法）。
    """
    if start == end:
        return True
    if start < end:
        return start <= now < end
    return now >= start or now < end


def default_config() -> dict[str, Any]:
    """默认配置：关闭，且限速值为 0（不限）。"""
    return {
        "enabled": False,
        # 「限速时段」——落在这个区间内使用 limit_*，区间外使用 off_peak_*
        "start": "08:00",
        "end": "23:00",
        "download_kb": 0,
        "upload_kb": 0,
        # 时段外（通常是夜间）的限速，0 = 不限速跑满
        "off_peak_download_kb": 0,
        "off_peak_upload_kb": 0,
    }


def get_config() -> dict[str, Any]:
    """读取当前配置（默认值 + 用户覆盖）。"""
    config = default_config()
    stored = settings_store.get_setting(KEY_SPEED_LIMIT, {}) or {}
    if isinstance(stored, dict):
        for key in config:
            if key in stored and stored[key] is not None:
                config[key] = stored[key]
    config["enabled"] = bool(config["enabled"])
    for key in (
        "download_kb",
        "upload_kb",
        "off_peak_download_kb",
        "off_peak_upload_kb",
    ):
        try:
            config[key] = max(0, int(config[key] or 0))
        except (TypeError, ValueError):
            config[key] = 0
    return config


def normalize(payload: dict[str, Any]) -> dict[str, Any]:
    """校验并规范化一份配置，非法时抛 ``ValueError``。"""
    current = get_config()
    result = dict(current)
    if "enabled" in payload and payload["enabled"] is not None:
        result["enabled"] = bool(payload["enabled"])
    for key in ("start", "end"):
        if key in payload and payload[key] is not None:
            value = str(payload[key]).strip()
            if parse_hhmm(value) is None:
                raise ValueError(f"{key} 需为 HH:MM 格式，收到：{payload[key]!r}")
            result[key] = value
    for key in (
        "download_kb",
        "upload_kb",
        "off_peak_download_kb",
        "off_peak_upload_kb",
    ):
        if key in payload and payload[key] is not None:
            try:
                value = int(payload[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} 需为整数（KB/s）") from exc
            if value < 0:
                raise ValueError(f"{key} 不能为负数（0 表示不限速）")
            # 10 GB/s 以上显然是把 B/s 当 KB/s 填了，拦住比默默接受好
            if value > 10 * 1024 * 1024:
                raise ValueError(f"{key} 数值过大，请确认单位是 KB/s")
            result[key] = value
    return result


def save_config(payload: dict[str, Any]) -> dict[str, Any]:
    """保存配置并返回规范化后的结果。"""
    config = normalize(payload)
    settings_store.set_setting(KEY_SPEED_LIMIT, config)
    return config


def target_limits(
    config: dict[str, Any] | None = None, *, now: dtime | None = None
) -> dict[str, Any]:
    """算出此刻应该用哪一组限速。

    返回 ``{"download_kb", "upload_kb", "phase"}``，``phase`` 是
    ``peak``（限速时段内）/ ``off_peak``（时段外）/ ``disabled``（功能关闭）。
    """
    config = config or get_config()
    if not config.get("enabled"):
        return {"download_kb": 0, "upload_kb": 0, "phase": "disabled"}

    start = parse_hhmm(str(config.get("start"))) or dtime(0, 0)
    end = parse_hhmm(str(config.get("end"))) or dtime(0, 0)
    from datetime import datetime
    from zoneinfo import ZoneInfo

    if now is None:
        try:
            now = datetime.now(ZoneInfo(settings.TIMEZONE)).time()
        except Exception:  # pragma: no cover - 时区名非法时退回本地时间
            now = datetime.now().time()

    if in_window(now, start, end):
        return {
            "download_kb": int(config.get("download_kb") or 0),
            "upload_kb": int(config.get("upload_kb") or 0),
            "phase": "peak",
        }
    return {
        "download_kb": int(config.get("off_peak_download_kb") or 0),
        "upload_kb": int(config.get("off_peak_upload_kb") or 0),
        "phase": "off_peak",
    }


async def apply_now(*, now: dtime | None = None) -> dict[str, Any]:
    """把当前时段对应的限速下发给所有支持限速的下载器。

    不支持限速的下载器（迅雷本地 CGI / yt-dlp）会被如实标为 ``skipped``，
    **不假装成功**——否则用户会以为迅雷也被限速了。
    """
    from app.services import sites as site_service

    config = get_config()
    target = target_limits(config, now=now)
    if target["phase"] == "disabled":
        return {
            "success": True,
            "applied": 0,
            "phase": "disabled",
            "message": "限速时段未启用",
            "items": [],
        }

    items: list[dict[str, Any]] = []
    applied = 0
    for downloader in site_service.downloaders():
        if not getattr(downloader, "supports_speed_limit", False):
            items.append(
                {
                    "name": downloader.site_name,
                    "provider": downloader.name,
                    "skipped": True,
                    "success": False,
                    "message": "该下载器不支持运行时限速",
                }
            )
            continue
        try:
            ok = await downloader.set_speed_limit(
                download_kb=target["download_kb"], upload_kb=target["upload_kb"]
            )
        except Exception as exc:  # 单个下载器异常不能中断整轮
            ok = False
            message = f"限速下发异常：{exc}"[:200]
        else:
            message = "已下发" if ok else "下发失败（检查连接与认证）"
        applied += int(bool(ok))
        items.append(
            {
                "name": downloader.site_name,
                "provider": downloader.name,
                "skipped": False,
                "success": bool(ok),
                "message": message,
            }
        )

    def _text(value: int) -> str:
        return "不限速" if not value else f"{value} KB/s"

    logger.info(
        "限速时段 %s：下行 %s、上行 %s，成功下发 %d/%d",
        target["phase"],
        _text(target["download_kb"]),
        _text(target["upload_kb"]),
        applied,
        len([i for i in items if not i["skipped"]]),
    )
    return {
        "success": True,
        "applied": applied,
        "phase": target["phase"],
        "download_kb": target["download_kb"],
        "upload_kb": target["upload_kb"],
        "message": (
            f"{'限速时段内' if target['phase'] == 'peak' else '限速时段外'}："
            f"下行 {_text(target['download_kb'])}、上行 {_text(target['upload_kb'])}"
        ),
        "items": items,
    }


def describe() -> dict[str, Any]:
    """给设置页用的完整描述（配置 + 此刻生效值）。"""
    config = get_config()
    target = target_limits(config)
    return {
        "config": config,
        "current": target,
        "default": default_config(),
        "interval_minutes": int(settings.SPEED_LIMIT_INTERVAL_MINUTES),
    }
