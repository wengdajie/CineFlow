"""百度网盘「开放平台授权」登录（OAuth 2.0 设备码流程）。

**为什么要有这条路**（用户实测反馈：「扫码后依然失效」）：

原有的 passport 扫码拿到的是**网页版 Cookie**（``BDUSS``/``STOKEN``）。
实测这条路的问题不是"扫不出来"，而是**扫出来也活不久**：

| 场景 | 实测结果 |
|---|---|
| 无 Cookie 调 ``/api/quota`` | ``errno=-6``（账户已过期，重新登陆） |
| 过期 Cookie 调 ``/api/quota`` | **HTTP 400**（连 errno 都拿不到） |
| 网页 Cookie 存活时长 | 小时级，且受设备/IP 风控影响随时作废 |

所以「重新扫码」只是把同一个短命凭据再换一份，用户体验就是"扫了也没用"。

**开放平台授权解决的是凭据寿命问题**：
``refresh_token`` 官方有效期 **10 年**，``access_token`` 30 天，
且可以静默刷新 —— 不需要再扫码。这是官方支持的通道，不涉及任何风控对抗
（符合 ADR-34：不逆向、不绕验证码）。

流程（设备码模式，适合 NAS 这种无浏览器回调的场景）::

    1. POST/GET https://openapi.baidu.com/oauth/2.0/device/code
         ?response_type=device_code&client_id=<AppKey>&scope=basic,netdisk
       → { device_code, user_code, verification_url, qrcode_url, expires_in, interval }
    2. 用户在手机/电脑打开 verification_url（或扫 qrcode_url）输入 user_code 授权
    3. 轮询 https://openapi.baidu.com/oauth/2.0/token
         ?grant_type=device_token&code=<device_code>&client_id=&client_secret=
       → 未授权时回 { error: "authorization_pending" }（**这不是错误**，要继续等）
       → 成功回 { access_token, refresh_token, expires_in }

**必须如实说明的前提**：需要用户自己去
https://pan.baidu.com/union 申请一个应用，拿到 AppKey / SecretKey。
本项目不内置公共 AppKey —— 内置的话所有用户共用一个配额，
一旦被刷爆或封禁，所有人一起挂；而且把 SecretKey 放进开源仓库
本身就是泄密。

**必须如实说明的能力边界**（实测 + 官方文档）：
开放平台 ``xpan`` 接口覆盖：用户信息、容量、文件列表、
文件管理（复制/移动/删除/重命名）、下载直链（dlink）。
**不包含「转存他人分享链接」** —— ``/share/transfer`` 只有网页通道有。
所以：

* 登录态稳定性、浏览、下载 → 用开放平台授权（推荐）
* **转存别人的分享链接 → 仍然只能靠网页 Cookie**

两者不是替代关系，是互补。UI 上必须讲清楚，否则用户授权完发现
转存还是不行，会以为又是个假功能。
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.logger import get_logger
from app.services.panlogin import UA, LoginSession, _new_token
from app.utils.http import async_client

logger = get_logger(__name__)

PROVIDER = "baidu_openapi"

DEVICE_CODE_URL = "https://openapi.baidu.com/oauth/2.0/device/code"
TOKEN_URL = "https://openapi.baidu.com/oauth/2.0/token"

#: 申请 access_token 时要的权限：basic=基础信息，netdisk=网盘读写
SCOPE = "basic,netdisk"

#: 设备码模式下"用户还没点授权"的标记。**不能当失败**，
#: 否则二维码刚弹出来就被判成登录失败。
PENDING_ERRORS = {"authorization_pending", "slow_down"}


def _headers() -> dict[str, str]:
    return {"User-Agent": UA}


async def start(client_id: str, client_secret: str) -> LoginSession:
    """申请设备码，返回可展示的授权入口。"""
    session = LoginSession(token=_new_token(), provider=PROVIDER)
    if not client_id or not client_secret:
        session.status = "failed"
        session.message = (
            "请先填写百度开放平台的 AppKey 与 SecretKey"
            "（在 https://pan.baidu.com/union 创建应用后获取）"
        )
        return session
    try:
        async with async_client(timeout=15, headers=_headers()) as client:
            response = await client.get(
                DEVICE_CODE_URL,
                params={
                    "response_type": "device_code",
                    "client_id": client_id,
                    "scope": SCOPE,
                },
            )
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        session.status = "failed"
        session.message = f"申请设备码失败：{exc}"
        return session

    if not isinstance(payload, dict) or payload.get("error"):
        # invalid_client 就是 AppKey/SecretKey 填错了，直接说清楚，
        # 别让用户以为是网络问题（实测坏 client_id 回 401 invalid_client）
        err = (payload or {}).get("error") or "未知错误"
        desc = (payload or {}).get("error_description") or ""
        session.status = "failed"
        if err == "invalid_client":
            session.message = "AppKey 或 SecretKey 无效，请到百度开放平台核对后重填"
        else:
            session.message = f"申请设备码失败：{err} {desc}".strip()
        return session

    device_code = str(payload.get("device_code") or "")
    user_code = str(payload.get("user_code") or "")
    if not device_code:
        session.status = "failed"
        session.message = "百度未返回 device_code（接口可能已调整）"
        return session

    session.extra = {
        "device_code": device_code,
        "client_id": client_id,
        "client_secret": client_secret,
        "interval": int(payload.get("interval") or 5),
    }
    # 官方给的就是可直接展示的二维码图，不用自己生成
    session.qr_image = str(payload.get("qrcode_url") or "")
    verification = str(payload.get("verification_url") or "https://openapi.baidu.com/device")
    session.qr_content = f"{verification}?code={user_code}" if user_code else verification
    session.message = f"请打开 {verification} 并输入授权码 {user_code}（或直接扫码）"
    return session


async def poll(session: LoginSession) -> LoginSession:
    """轮询授权结果；成功后把 token 放进 ``session.extra``。"""
    device_code = str(session.extra.get("device_code") or "")
    client_id = str(session.extra.get("client_id") or "")
    client_secret = str(session.extra.get("client_secret") or "")
    if not device_code:
        session.status = "failed"
        session.message = "会话缺少 device_code"
        return session

    try:
        async with async_client(timeout=20, headers=_headers()) as client:
            response = await client.get(
                TOKEN_URL,
                params={
                    "grant_type": "device_token",
                    "code": device_code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
            payload = response.json()
    except httpx.TimeoutException:
        session.message = "等待授权中…"
        return session
    except (httpx.HTTPError, ValueError) as exc:
        session.message = f"查询授权状态失败：{exc}"
        return session

    if not isinstance(payload, dict):
        session.message = "等待授权中…"
        return session

    error = str(payload.get("error") or "")
    if error in PENDING_ERRORS:
        # 用户还没在网页上点"同意"，属正常中间态
        session.message = "等待授权中…"
        return session
    if error == "expired_token":
        session.status = "expired"
        session.message = "授权码已过期，请重新生成"
        return session
    if error:
        session.status = "failed"
        session.message = f"授权失败：{error} {payload.get('error_description') or ''}".strip()
        return session

    access_token = str(payload.get("access_token") or "")
    refresh_token = str(payload.get("refresh_token") or "")
    if not access_token:
        session.message = "等待授权中…"
        return session

    session.status = "success"
    session.message = "授权成功"
    # 这些是凭据，和 cookie 一样只在内存里待一下就写库
    session.extra["access_token"] = access_token
    session.extra["refresh_token"] = refresh_token
    session.extra["expires_in"] = int(payload.get("expires_in") or 0)
    return session


async def refresh(refresh_token: str, client_id: str, client_secret: str) -> dict[str, Any] | None:
    """用 refresh_token 换新的 access_token。

    ``refresh_token`` 官方有效期 10 年，所以这条路可以做到**永不再扫码**。
    返回 ``None`` 表示刷新失败（refresh_token 也废了，才需要重新授权）。
    """
    if not refresh_token or not client_id or not client_secret:
        return None
    try:
        async with async_client(timeout=20, headers=_headers()) as client:
            response = await client.get(
                TOKEN_URL,
                params={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("百度 refresh_token 刷新失败：%s", exc)
        return None
    if not isinstance(payload, dict) or payload.get("error") or not payload.get("access_token"):
        logger.warning(
            "百度 refresh_token 被拒：%s", (payload or {}).get("error") or "无 access_token"
        )
        return None
    return {
        "access_token": str(payload.get("access_token")),
        "refresh_token": str(payload.get("refresh_token") or refresh_token),
        "expires_in": int(payload.get("expires_in") or 0),
    }


async def verify(access_token: str) -> tuple[bool, str, dict[str, Any]]:
    """校验 access_token（顺带取昵称与容量）。"""
    if not access_token.strip():
        return False, "access_token 为空", {}
    try:
        async with async_client(timeout=15, headers=_headers()) as client:
            info = await client.get(
                "https://pan.baidu.com/rest/2.0/xpan/nas",
                params={"method": "uinfo", "access_token": access_token},
            )
            payload = info.json()
    except (httpx.HTTPError, ValueError) as exc:
        return False, f"校验请求失败（网络不通？）：{exc}", {}
    if not isinstance(payload, dict):
        return False, "响应格式异常", {}
    errno = payload.get("errno")
    if errno in (0, None) and payload.get("baidu_name") is not None:
        return True, "授权有效", {"nickname": str(payload.get("baidu_name") or "")}
    # 实测坏 token 回 errno=20017 "nas access token invalid"
    return False, f"授权无效（errno={errno} {payload.get('errmsg') or ''}）".strip(), {}
