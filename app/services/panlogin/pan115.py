"""115 网盘扫码登录。

流程（全部为官方公开接口，实测可用）::

    1. GET  https://qrcodeapi.115.com/api/1.0/web/1.0/token/
             → { uid, time, sign, qrcode }
    2. 二维码图片： https://qrcodeapi.115.com/api/1.0/web/1.0/qrcode?uid=<uid>
       （也可以直接把 qrcode 字段的 URL 编成二维码，内容一样）
    3. GET  https://qrcodeapi.115.com/get/status/?uid=&time=&sign=
             → data.status  0=等待 1=已扫待确认 2=确认登录 -2=取消
       这是**长轮询**，未扫码时会挂住数十秒才返回，属正常。
    4. POST https://passportapi.115.com/app/1.0/web/1.0/login/qrcode/
             account=<uid> → 返回 cookie（在 data.cookie 里）

注：``status`` 拿到 2 之后才能换 Cookie，提前换会失败。
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.logger import get_logger
from app.services.panlogin import UA, LoginSession, _new_token
from app.utils.http import async_client

logger = get_logger(__name__)

TOKEN_URL = "https://qrcodeapi.115.com/api/1.0/web/1.0/token/"
QR_IMAGE_URL = "https://qrcodeapi.115.com/api/1.0/web/1.0/qrcode"
STATUS_URL = "https://qrcodeapi.115.com/get/status/"
LOGIN_URL = "https://passportapi.115.com/app/1.0/web/1.0/login/qrcode/"

PROVIDER = "pan115"


def _headers() -> dict[str, str]:
    return {"User-Agent": UA, "Referer": "https://115.com/"}


async def start() -> LoginSession:
    """申请二维码，返回待轮询的会话。"""
    session = LoginSession(token=_new_token(), provider=PROVIDER)
    try:
        async with async_client(timeout=15, headers=_headers()) as client:
            response = await client.get(TOKEN_URL)
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        session.status = "failed"
        session.message = f"申请二维码失败：{exc}"
        return session

    data = (payload or {}).get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or not data.get("uid"):
        session.status = "failed"
        session.message = "115 未返回二维码信息（接口可能已调整）"
        return session

    session.extra = {
        "uid": str(data.get("uid")),
        "time": data.get("time"),
        "sign": str(data.get("sign") or ""),
    }
    session.qr_content = str(data.get("qrcode") or "")
    session.qr_image = f"{QR_IMAGE_URL}?uid={session.extra['uid']}"
    session.message = "请用 115 App 扫码"
    return session


async def poll(session: LoginSession) -> LoginSession:
    """查询扫码状态；确认后换取 Cookie。"""
    uid = str(session.extra.get("uid") or "")
    if not uid:
        session.status = "failed"
        session.message = "会话缺少 uid"
        return session

    url = (
        f"{STATUS_URL}?uid={uid}&time={session.extra.get('time')}"
        f"&sign={session.extra.get('sign')}"
    )
    try:
        # 这是长轮询接口，超时按"还没人扫"处理而不是报错
        async with async_client(timeout=25, headers=_headers()) as client:
            response = await client.get(url)
            payload = response.json()
    except httpx.TimeoutException:
        session.message = "等待扫码中…"
        return session
    except (httpx.HTTPError, ValueError) as exc:
        session.message = f"查询状态失败：{exc}"
        return session

    data = (payload or {}).get("data") if isinstance(payload, dict) else None
    status = int((data or {}).get("status") or 0) if isinstance(data, dict) else 0
    if status == 1:
        session.status = "scanned"
        session.message = "已扫码，请在手机上确认登录"
        return session
    if status == 2:
        return await _exchange(session, uid)
    if status < 0:
        session.status = "expired"
        session.message = "二维码已取消或失效，请重新获取"
        return session
    session.message = "等待扫码中…"
    return session


async def _exchange(session: LoginSession, uid: str) -> LoginSession:
    """用已确认的 uid 换 Cookie。"""
    try:
        async with async_client(timeout=20, headers=_headers()) as client:
            response = await client.post(LOGIN_URL, data={"account": uid, "app": "web"})
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        session.status = "failed"
        session.message = f"换取 Cookie 失败：{exc}"
        return session

    data = (payload or {}).get("data") if isinstance(payload, dict) else None
    cookie_map = (data or {}).get("cookie") if isinstance(data, dict) else None
    if not isinstance(cookie_map, dict) or not cookie_map:
        session.status = "failed"
        session.message = "115 未返回 Cookie（可能未完成确认）"
        return session

    session.cookie = "; ".join(f"{k}={v}" for k, v in cookie_map.items() if v)
    session.nickname = str((data or {}).get("user_id") or "")
    session.status = "success"
    session.message = "登录成功"
    return session


async def verify(cookie: str) -> tuple[bool, str, dict[str, Any]]:
    """校验一段 115 Cookie 是否有效（也用于 Cookie 导入时即时验证）。"""
    if not cookie.strip():
        return False, "Cookie 为空", {}
    headers = dict(_headers())
    headers["Cookie"] = cookie.strip()
    try:
        async with async_client(timeout=15, headers=headers) as client:
            response = await client.get(
                "https://webapi.115.com/files",
                params={"aid": 1, "cid": 0, "limit": 1, "offset": 0},
            )
    except httpx.HTTPError as exc:
        return False, f"校验请求失败（网络不通？）：{exc}", {}
    try:
        payload = response.json()
    except ValueError:
        # Cookie 无效时 115 会返回登录页 HTML 而不是 JSON。
        # 直接把"解析失败"抛给用户毫无意义，要翻译成他能行动的信息。
        return False, "Cookie 无效或已过期（接口返回了登录页而非数据）", {}

    if not isinstance(payload, dict):
        return False, "响应格式异常", {}
    if payload.get("state"):
        return True, "Cookie 有效", {"count": payload.get("count")}
    return False, str(payload.get("error") or payload.get("msg") or "Cookie 无效或已过期"), {}
