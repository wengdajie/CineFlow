"""下载器限速时段测试。

两大重点：

1. **跨午夜时段**（如 23:00~07:00）是这类功能的经典 bug ——
   朴素的 ``start <= now <= end`` 恒为假，必须显式分支；
2. **三个下载器三种单位**，全部实测确认过：
   qB 的接口是 **B/s**（不换算就差 1024 倍）、TR 是 **KB/s** 但必须同时下发
   ``*-enabled`` 开关（否则存了数值继续满速跑）、aria2 的值必须是**字符串**。
"""

from __future__ import annotations

import asyncio
from datetime import time as dtime

import pytest

from app.providers.downloader.aria2 import Aria2Downloader
from app.providers.downloader.qbittorrent import QbittorrentDownloader
from app.providers.downloader.transmission import TransmissionDownloader
from app.providers.downloader.xunlei import XunleiDownloader
from app.providers.downloader.ytdlp import YtDlpDownloader
from app.services import speed_limit


def run(coro):
    return asyncio.run(coro)


# ---------------- parse_hhmm ----------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("00:00", dtime(0, 0)),
        ("08:30", dtime(8, 30)),
        ("23:59", dtime(23, 59)),
        (" 07:05 ", dtime(7, 5)),
    ],
)
def test_parse_hhmm_ok(raw, expected):
    assert speed_limit.parse_hhmm(raw) == expected


@pytest.mark.parametrize(
    "raw", ["", "  ", "24:00", "12:60", "-1:00", "8", "8:5:0", "abc", "12：30", None]
)
def test_parse_hhmm_rejects_garbage(raw):
    """脏值返回 None，让上层报 400，而不是静默当成 00:00。"""
    assert speed_limit.parse_hhmm(raw) is None


# ---------------- in_window：同日 ----------------
@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (dtime(8, 59), False),
        (dtime(9, 0), True),   # 起点含
        (dtime(13, 0), True),
        (dtime(17, 59), True),
        (dtime(18, 0), False),  # 终点不含
        (dtime(23, 0), False),
        (dtime(0, 0), False),
    ],
)
def test_in_window_same_day(now, expected):
    assert speed_limit.in_window(now, dtime(9, 0), dtime(18, 0)) is expected


# ---------------- in_window：跨午夜（核心） ----------------
@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (dtime(23, 0), True),   # 起点含
        (dtime(23, 59), True),
        (dtime(0, 0), True),    # 跨过午夜仍在窗口内
        (dtime(3, 0), True),
        (dtime(6, 59), True),
        (dtime(7, 0), False),   # 终点不含
        (dtime(12, 0), False),
        (dtime(22, 59), False),
    ],
)
def test_in_window_across_midnight(now, expected):
    """23:00~07:00 这类夜间时段必须正确。

    朴素比较（start<=now<=end）在这里恒为假，会导致「夜间限速」整个功能
    静默失效——用户以为配好了，实际从未生效过。
    """
    assert speed_limit.in_window(now, dtime(23, 0), dtime(7, 0)) is expected


@pytest.mark.parametrize("now", [dtime(0, 0), dtime(9, 30), dtime(23, 59)])
def test_in_window_equal_start_end_means_all_day(now):
    """start == end 视为全天生效，而不是"一个瞬间"。"""
    assert speed_limit.in_window(now, dtime(9, 0), dtime(9, 0)) is True


# ---------------- target_limits 三种 phase ----------------
def test_target_limits_disabled_when_off():
    result = speed_limit.target_limits({"enabled": False}, now=dtime(12, 0))
    assert result["phase"] == "disabled"
    assert result["download_kb"] == 0
    assert result["upload_kb"] == 0


def test_target_limits_peak_inside_window():
    config = {
        "enabled": True,
        "start": "09:00",
        "end": "18:00",
        "download_kb": 2048,
        "upload_kb": 512,
        "off_peak_download_kb": 0,
        "off_peak_upload_kb": 0,
    }
    result = speed_limit.target_limits(config, now=dtime(10, 0))
    assert result["phase"] == "peak"
    assert result["download_kb"] == 2048
    assert result["upload_kb"] == 512


def test_target_limits_off_peak_outside_window():
    """时段外用 off_peak 组，通常是 0（夜间跑满）。"""
    config = {
        "enabled": True,
        "start": "09:00",
        "end": "18:00",
        "download_kb": 2048,
        "upload_kb": 512,
        "off_peak_download_kb": 0,
        "off_peak_upload_kb": 0,
    }
    result = speed_limit.target_limits(config, now=dtime(2, 0))
    assert result["phase"] == "off_peak"
    assert result["download_kb"] == 0


def test_target_limits_night_window_config():
    """把限速时段配成夜间时，白天应当落在 off_peak。"""
    config = {
        "enabled": True,
        "start": "23:00",
        "end": "07:00",
        "download_kb": 1024,
        "upload_kb": 128,
        "off_peak_download_kb": 8192,
        "off_peak_upload_kb": 1024,
    }
    assert speed_limit.target_limits(config, now=dtime(1, 0))["phase"] == "peak"
    assert speed_limit.target_limits(config, now=dtime(14, 0))["phase"] == "off_peak"
    assert speed_limit.target_limits(config, now=dtime(14, 0))["download_kb"] == 8192


# ---------------- normalize：拦非法输入 ----------------
def test_normalize_rejects_bad_time():
    with pytest.raises(ValueError, match="HH:MM"):
        speed_limit.normalize({"start": "25:00"})


def test_normalize_rejects_negative():
    with pytest.raises(ValueError, match="\u8d1f\u6570"):
        speed_limit.normalize({"download_kb": -1})


def test_normalize_rejects_non_integer():
    with pytest.raises(ValueError, match="\u6574\u6570"):
        speed_limit.normalize({"upload_kb": "fast"})


def test_normalize_rejects_absurdly_large():
    """>10GB/s 显然是把 B/s 当 KB/s 填了，拦住比默默接受好。"""
    with pytest.raises(ValueError, match="\u5355\u4f4d"):
        speed_limit.normalize({"download_kb": 10 * 1024 * 1024 + 1})


def test_normalize_keeps_zero_as_unlimited():
    """0 是合法值，表示不限速。"""
    result = speed_limit.normalize({"download_kb": 0, "enabled": True})
    assert result["download_kb"] == 0
    assert result["enabled"] is True


def test_default_config_is_off():
    """默认必须关闭：升级上来的用户不该突然被限速。"""
    config = speed_limit.default_config()
    assert config["enabled"] is False
    assert config["download_kb"] == 0


# ---------------- qBittorrent：单位是 B/s ----------------
def test_qb_sends_bytes_per_second():
    """qB 的 setDownloadLimit 单位是 B/s，必须 ×1024。

    不换算的话用户设 10MB/s 实际得到 10KB/s，看起来像"限速让下载更慢了"。
    """
    sent: list[tuple[str, dict]] = []

    class FakeResponse:
        status_code = 200
        text = "5242880"

    async def _request(method, url, **kwargs):
        sent.append((url, kwargs.get("data") or {}))
        return FakeResponse()

    downloader = QbittorrentDownloader({"name": "qb", "url": "http://127.0.0.1:8080"})
    downloader._request = _request
    assert run(downloader.set_speed_limit(download_kb=5120, upload_kb=512)) is True
    limits = {url.rsplit("/", 1)[-1]: data["limit"] for url, data in sent}
    assert limits["setDownloadLimit"] == 5120 * 1024
    assert limits["setUploadLimit"] == 512 * 1024


def test_qb_get_converts_back_to_kb():
    """读回时要从 B/s 换算成 KB/s，否则界面显示的数字大 1024 倍。"""

    class FakeResponse:
        status_code = 200
        text = "5242880"

    async def _request(method, url, **kwargs):
        return FakeResponse()

    downloader = QbittorrentDownloader({"name": "qb", "url": "http://127.0.0.1:8080"})
    downloader._request = _request
    assert run(downloader.get_speed_limit()) == {"download_kb": 5120, "upload_kb": 5120}


# ---------------- Transmission：KB/s + 必须开开关 ----------------
def test_tr_uses_kb_and_sends_enabled_flag():
    """TR 单位本就是 KB/s（别乘 1024），但**必须同时**下发 enabled 开关。

    只设数值不开开关时，TR 会把数值存下来却继续满速跑 ——
    表现为"限速填了但完全没用"，是最难自查的一种失效。
    """
    calls: list[tuple[str, dict]] = []

    async def _call(method, payload=None):
        calls.append((method, payload or {}))
        return {}

    downloader = TransmissionDownloader({"name": "tr", "url": "http://127.0.0.1:9091"})
    downloader._call = _call
    assert run(downloader.set_speed_limit(download_kb=3000, upload_kb=200)) is True
    payload = calls[-1][1]
    assert payload["speed-limit-down"] == 3000, "TR 是 KB/s，不该乘 1024"
    assert payload["speed-limit-up"] == 200
    assert payload["speed-limit-down-enabled"] is True
    assert payload["speed-limit-up-enabled"] is True


def test_tr_zero_disables_instead_of_throttling_to_zero():
    """传 0 要**关开关**而不是限到 0——限到 0 会彻底断流。"""
    calls: list[tuple[str, dict]] = []

    async def _call(method, payload=None):
        calls.append((method, payload or {}))
        return {}

    downloader = TransmissionDownloader({"name": "tr", "url": "http://127.0.0.1:9091"})
    downloader._call = _call
    run(downloader.set_speed_limit(download_kb=0, upload_kb=0))
    payload = calls[-1][1]
    assert payload["speed-limit-down-enabled"] is False
    assert payload["speed-limit-up-enabled"] is False
    assert "speed-limit-down" not in payload
    assert "speed-limit-up" not in payload


def test_tr_get_reports_zero_when_disabled():
    """开关关闭时读回 0（= 不限速），不能报出残留的旧数值。"""

    async def _call(method, payload=None):
        return {
            "speed-limit-down": 3000,
            "speed-limit-down-enabled": False,
            "speed-limit-up": 200,
            "speed-limit-up-enabled": True,
        }

    downloader = TransmissionDownloader({"name": "tr", "url": "http://127.0.0.1:9091"})
    downloader._call = _call
    assert run(downloader.get_speed_limit()) == {"download_kb": 0, "upload_kb": 200}


# ---------------- aria2：值必须是字符串 ----------------
def test_aria2_sends_string_values():
    """aria2 的 changeGlobalOption 值必须是字符串，传整数会被 JSON-RPC 拒绝。"""
    calls: list[tuple[str, list]] = []

    async def _call(method, params=None):
        calls.append((method, params or []))
        return {}

    downloader = Aria2Downloader({"name": "ar", "url": "http://127.0.0.1:6800/jsonrpc"})
    downloader._call = _call
    assert run(downloader.set_speed_limit(download_kb=5120, upload_kb=0)) is True
    options = calls[-1][1][0]
    assert all(isinstance(value, str) for value in options.values())
    assert options["max-overall-download-limit"] == "5120K"
    assert options["max-overall-upload-limit"] == "0"


# ---------------- 不支持限速的下载器要如实标注 ----------------
@pytest.mark.parametrize("cls", [QbittorrentDownloader, TransmissionDownloader, Aria2Downloader])
def test_supported_downloaders_declare_true(cls):
    assert cls.supports_speed_limit is True


@pytest.mark.parametrize("cls", [XunleiDownloader, YtDlpDownloader])
def test_unsupported_downloaders_declare_false(cls):
    """迅雷 CGI 与 yt-dlp 没有运行时限速接口，**不假装支持**。

    假装支持的后果是：用户以为限速了，实际带宽照样被吃满，还查不出原因。
    """
    assert cls.supports_speed_limit is False


# ---------------- apply_now ----------------
def test_apply_now_disabled_short_circuits(monkeypatch):
    """功能关闭时不该去碰任何下载器。"""
    monkeypatch.setattr(speed_limit, "get_config", lambda: speed_limit.default_config())
    result = run(speed_limit.apply_now())
    assert result["phase"] == "disabled"
    assert result["applied"] == 0
    assert result["items"] == []


def test_apply_now_marks_unsupported_as_skipped(monkeypatch):
    """不支持限速的下载器要标 skipped 且 success=False，不能算进成功数。"""

    class Fake:
        name = "xunlei"
        site_name = "\u8fc5\u96f7"
        supports_speed_limit = False

    monkeypatch.setattr(
        speed_limit,
        "get_config",
        lambda: {
            "enabled": True,
            "start": "00:00",
            "end": "23:59",
            "download_kb": 1024,
            "upload_kb": 0,
            "off_peak_download_kb": 0,
            "off_peak_upload_kb": 0,
        },
    )
    monkeypatch.setattr("app.services.sites.downloaders", lambda: [Fake()])
    result = run(speed_limit.apply_now(now=dtime(12, 0)))
    assert result["applied"] == 0
    assert result["items"][0]["skipped"] is True
    assert result["items"][0]["success"] is False


def test_apply_now_survives_downloader_exception(monkeypatch):
    """单个下载器抛异常不能中断整轮下发。"""

    class Boom:
        name = "qbittorrent"
        site_name = "\u574f\u7684"
        supports_speed_limit = True

        async def set_speed_limit(self, **kwargs):
            raise RuntimeError("connection refused")

    class Good:
        name = "transmission"
        site_name = "\u597d\u7684"
        supports_speed_limit = True

        async def set_speed_limit(self, **kwargs):
            return True

    monkeypatch.setattr(
        speed_limit,
        "get_config",
        lambda: {
            "enabled": True,
            "start": "00:00",
            "end": "23:59",
            "download_kb": 1024,
            "upload_kb": 0,
            "off_peak_download_kb": 0,
            "off_peak_upload_kb": 0,
        },
    )
    monkeypatch.setattr("app.services.sites.downloaders", lambda: [Boom(), Good()])
    result = run(speed_limit.apply_now(now=dtime(12, 0)))
    assert result["applied"] == 1
    assert result["items"][0]["success"] is False
    assert result["items"][1]["success"] is True


def test_describe_shape():
    """设置页依赖 describe() 的三块结构，字段名不能漂。"""
    info = speed_limit.describe()
    assert set(info) >= {"config", "current", "default", "interval_minutes"}
    assert "phase" in info["current"]


# ---------------- API 端点 ----------------
def test_get_speed_limit_endpoint(client, auth_headers):
    response = client.get("/api/v1/downloaders/speed-limit", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()["data"]
    assert "config" in data and "current" in data


def test_put_speed_limit_roundtrip(client, auth_headers):
    payload = {
        "enabled": True,
        "start": "23:00",
        "end": "07:00",
        "download_kb": 1024,
        "upload_kb": 128,
        "off_peak_download_kb": 0,
        "off_peak_upload_kb": 0,
    }
    response = client.put(
        "/api/v1/downloaders/speed-limit", json=payload, headers=auth_headers
    )
    assert response.status_code == 200, response.text
    config = response.json()["config"]
    assert config["enabled"] is True
    assert config["start"] == "23:00"
    assert config["end"] == "07:00"

    # 读回来还在（存的是 settings 表，不是内存）
    again = client.get("/api/v1/downloaders/speed-limit", headers=auth_headers)
    assert again.json()["data"]["config"]["download_kb"] == 1024

    # 收尾：关掉，别影响其他用例
    client.put(
        "/api/v1/downloaders/speed-limit",
        json={"enabled": False},
        headers=auth_headers,
    )


def test_put_speed_limit_rejects_bad_time(client, auth_headers):
    """非法时间要 400，不能静默存下坏值。"""
    response = client.put(
        "/api/v1/downloaders/speed-limit",
        json={"start": "99:99"},
        headers=auth_headers,
    )
    assert response.status_code == 400


def test_put_speed_limit_rejects_negative(client, auth_headers):
    """负数由 pydantic 的 ge=0 拦住（422）。"""
    response = client.put(
        "/api/v1/downloaders/speed-limit",
        json={"download_kb": -5},
        headers=auth_headers,
    )
    assert response.status_code == 422


def test_apply_endpoint(client, auth_headers):
    response = client.post(
        "/api/v1/downloaders/speed-limit/apply", headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json()["success"] is True
