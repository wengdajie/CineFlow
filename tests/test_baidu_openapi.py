"""百度网盘：分享链接解析放宽 + 登录态误诊修复 + 开放平台授权。

三条都来自用户实测反馈：
1. 「网盘资源下载提示：不是有效的百度网盘分享链接」→ 正则写死了 ``/s/1``
2. 「连不上百度网盘（网络或代理问题），登录态未知」→ Cookie 过期时百度回
   HTTP 400，被 ``fetch_json`` 吞成 None，于是误报成网络问题
3. 「扫码后依然失效」→ 网页 Cookie 寿命只有小时级，改用官方 OAuth
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.providers.panstorage.baidu import BaiduPanStorage
from app.services.panlogin import baidu_openapi

# --------------------------------------------------------------------------
# 1. 分享链接解析
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # 经典形态：/s/1 + 短码
        ("https://pan.baidu.com/s/1AbCdEfGhIj", "AbCdEfGhIj"),
        ("http://pan.baidu.com/s/1AbCdEfGhIj", "AbCdEfGhIj"),
        ("pan.baidu.com/s/1AbCdEfGhIj", "AbCdEfGhIj"),
        ("https://pan.baidu.com/s/1AbCdEfGhIj?pwd=abcd", "AbCdEfGhIj"),
        ("https://pan.baidu.com/s/1AbCdEfGhIj#list/path=%2F", "AbCdEfGhIj"),
        # 用户直接粘贴分享文案（最常见的输入方式）
        ("链接: https://pan.baidu.com/s/1AbCdEfGhIj 提取码: 1a2b", "AbCdEfGhIj"),
        # surl 形态
        ("https://pan.baidu.com/share/init?surl=AbCdEfGhIj", "AbCdEfGhIj"),
        ("https://pan.baidu.com/share/init?surl=AbCdEfGhIj&pwd=1234", "AbCdEfGhIj"),
        # 旧代码误杀的两类 ↓↓↓
        ("https://yun.baidu.com/s/1AbCdEfGhIj", "AbCdEfGhIj"),
        ("https://eyun.baidu.com/s/1AbCdEfGhIj", "AbCdEfGhIj"),
        # 新版短码不以 1 开头
        ("https://pan.baidu.com/s/xKq9v2Lm", "xKq9v2Lm"),
    ],
)
def test_parse_share_id_accepts_real_world_shapes(url: str, expected: str) -> None:
    assert BaiduPanStorage.parse_share_id(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://terabox.com/s/1AbCdEfGhIj",  # 别的网盘，必须拒
        "https://example.com/whatever",
        "这不是链接",
    ],
)
def test_parse_share_id_rejects_non_baidu(url: str) -> None:
    """放宽不等于什么都收：非百度域名仍要拒，否则会拿错误短码去请求。"""
    assert BaiduPanStorage.parse_share_id(url) == ""


@pytest.mark.asyncio
async def test_save_share_no_longer_rejects_new_style_code() -> None:
    """不以 1 开头的短码不能在入口就被判"不是有效链接"。"""
    storage = BaiduPanStorage({})  # 没 Cookie
    result = await storage.save_share("https://pan.baidu.com/s/xKq9v2Lm")
    assert result.success is False
    # 关键：错误原因应该是"没配 Cookie"，而不是"链接无效"
    assert "不是有效" not in result.message, result.message
    assert "Cookie" in result.message, result.message


# --------------------------------------------------------------------------
# 2. Cookie 过期（HTTP 400）不能误报成网络问题
# --------------------------------------------------------------------------


def _patch_baidu_transport(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    from app.utils import http as http_mod

    real = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(http_mod.httpx, "AsyncClient", factory)


@pytest.mark.asyncio
async def test_keepalive_http400_reports_expired_not_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cookie 过期时百度回 HTTP 400 —— 必须说"登录态失效"，不能说"网络问题"。

    这是用户报告的原句：「连不上百度网盘（网络或代理问题），登录态未知」。
    实测 pan.baidu.com 明明可达，是 Cookie 死了。旧实现走 ``fetch_json``，
    ``raise_for_status()`` 把 400 抛成异常 → 返回 None → ``_last_errno``
    停在 None → 命中"网络问题"分支，把用户的排查方向整体带偏。
    """
    _patch_baidu_transport(
        monkeypatch, lambda request: httpx.Response(400, text="Bad Request")
    )
    storage = BaiduPanStorage({"cookie": "BDUSS=expired"})
    ok, message = await storage.keep_alive()
    assert ok is False
    assert "网络" not in message, f"HTTP 400 被误报成网络问题：{message}"
    assert "失效" in message, message
    assert "400" in message, "消息里要带状态码，方便判断是登录态还是代理"


@pytest.mark.asyncio
async def test_keepalive_errno_minus6_reports_expired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """200 + errno=-6 也是登录态失效。"""
    _patch_baidu_transport(
        monkeypatch,
        lambda request: httpx.Response(200, json={"errno": -6, "show_msg": "用户未登录"}),
    )
    storage = BaiduPanStorage({"cookie": "BDUSS=expired"})
    ok, message = await storage.keep_alive()
    assert ok is False
    assert "errno=-6" in message
    assert "网络" not in message, message


@pytest.mark.asyncio
async def test_keepalive_real_network_failure_still_says_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """反向保险：**真的**连不上时仍要说网络问题，别把这条也改坏了。"""

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    _patch_baidu_transport(monkeypatch, boom)
    storage = BaiduPanStorage({"cookie": "BDUSS=whatever"})
    ok, message = await storage.keep_alive()
    assert ok is False
    assert "网络" in message, f"真实网络故障没报成网络问题：{message}"


# --------------------------------------------------------------------------
# 3. 开放平台授权（OAuth 设备码）
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openapi_start_requires_app_credentials() -> None:
    """没填 AppKey/SecretKey 时要明确要求填写，而不是抛异常或假装在等授权。"""
    session = await baidu_openapi.start("", "")
    assert session.status == "failed"
    assert "AppKey" in session.message


@pytest.mark.asyncio
async def test_openapi_invalid_client_says_key_wrong(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AppKey 填错时百度回 invalid_client —— 要说"核对 AppKey"，不是"网络失败"。"""
    _patch_baidu_transport(
        monkeypatch,
        lambda request: httpx.Response(
            401, json={"error": "invalid_client", "error_description": "unknown client id"}
        ),
    )
    session = await baidu_openapi.start("bad_key", "bad_secret")
    assert session.status == "failed"
    assert "AppKey" in session.message
    assert "网络" not in session.message


@pytest.mark.asyncio
async def test_openapi_authorization_pending_is_not_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """用户还没点授权时百度回 authorization_pending —— **不能**判成失败。

    判成失败的后果是二维码刚出现就显示"授权失败"，用户根本没机会去点同意。
    """
    _patch_baidu_transport(
        monkeypatch,
        lambda request: httpx.Response(200, json={"error": "authorization_pending"}),
    )
    session = baidu_openapi.LoginSession(token="t", provider="baidu_openapi")
    session.extra = {"device_code": "dc", "client_id": "id", "client_secret": "sec"}
    result = await baidu_openapi.poll(session)
    assert result.status == "waiting", f"pending 被判成 {result.status}"
    assert "等待授权" in result.message


@pytest.mark.asyncio
async def test_openapi_poll_success_keeps_refresh_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """授权成功必须把 refresh_token 一起带回来。

    只存 access_token 的话 30 天后过期又要重新授权 —— 那就退化成
    和网页 Cookie 一样的短命凭据，这条路的意义就没了。
    """
    _patch_baidu_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json={
                "access_token": "AT",
                "refresh_token": "RT",
                "expires_in": 2592000,
            },
        ),
    )
    session = baidu_openapi.LoginSession(token="t", provider="baidu_openapi")
    session.extra = {"device_code": "dc", "client_id": "id", "client_secret": "sec"}
    result = await baidu_openapi.poll(session)
    assert result.status == "success"
    assert result.extra["access_token"] == "AT"
    assert result.extra["refresh_token"] == "RT", "refresh_token 丢了，等于没做长期授权"


@pytest.mark.asyncio
async def test_openapi_refresh_returns_new_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """refresh_token 换新 access_token —— 这是"永不再扫码"的关键。"""
    _patch_baidu_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200, json={"access_token": "NEW_AT", "refresh_token": "NEW_RT", "expires_in": 100}
        ),
    )
    result = await baidu_openapi.refresh("old_rt", "id", "sec")
    assert result is not None
    assert result["access_token"] == "NEW_AT"
    assert result["refresh_token"] == "NEW_RT"


@pytest.mark.asyncio
async def test_openapi_refresh_failure_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """refresh_token 也废了要返回 None，让上层知道必须重新授权（ADR-82）。"""
    _patch_baidu_transport(
        monkeypatch,
        lambda request: httpx.Response(400, json={"error": "invalid_grant"}),
    )
    assert await baidu_openapi.refresh("dead_rt", "id", "sec") is None


def test_openapi_declared_as_no_cookie_no_transfer() -> None:
    """能力声明必须诚实：开放平台没有 Cookie，且不支持转存分享。

    文档里写清楚很重要 —— 否则用户授权完发现转存还是不行，
    会以为这又是个假功能。
    """
    from app.services import pan_login

    meta = pan_login.PROVIDERS["baidu_openapi"]
    assert meta["cookie"] is False
    assert meta["needs_app_credentials"] is True
    assert "转存" in meta["note"], "必须说明开放平台不支持转存分享链接"
