"""百度网盘扫码登录（passport 通道）。

流程::

    1. GET https://passport.baidu.com/v2/api/getqrcode?lp=pc&qrloginfrom=pc&gid=<gid>
            → { imgurl, sign }
       二维码图片： https://passport.baidu.com/v2/api/qrcode?sign=<sign>&lp=pc
    2. GET https://passport.baidu.com/channel/unicast?channel_id=<sign>&gid=<gid>&tpl=netdisk
       **这是长轮询**：没人扫码时会挂住十几秒才返回（实测 3s 超时是正常现象，
       不是接口坏了）。扫码确认后返回体里带 ``v`` 字段（bduss 票据）。
    3. GET https://passport.baidu.com/v3/login/main/qrbdusslogin?bduss=<v>&...
       跟随重定向，从响应 Cookie 里取 ``BDUSS`` / ``STOKEN``。

**如实说明局限**：百度对非官方客户端的风控比 115 严格得多，
可能出现「扫码成功但换不到可用 Cookie」或要求二次验证的情况。
所以百度同时保留 **Cookie 导入** 路径作为兜底。
"""

from __future__ import annotations

import json
import re
import secrets
from typing import Any

import httpx

from app.core.logger import get_logger
from app.services.panlogin import UA, LoginSession, _new_token
from app.utils.http import async_client

logger = get_logger(__name__)

QRCODE_URL = "https://passport.baidu.com/v2/api/getqrcode"
QR_IMAGE_URL = "https://passport.baidu.com/v2/api/qrcode"
UNICAST_URL = "https://passport.baidu.com/channel/unicast"
QRLOGIN_URL = "https://passport.baidu.com/v3/login/main/qrbdusslogin"

PROVIDER = "baidu"


def _as_int(value: Any, default: int = -1) -> int:
    """安全转 int。

    **不要写成 ``int(value or default)``**：``0`` 是这些接口的「成功」值，
    但在 Python 里是假值，会被 ``or`` 直接吃掉换成默认值，导致成功永远判不出来。
    这个坑在 status 和 errno 上各踩了一次，所以抽成独立函数复用。
    """
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _headers() -> dict[str, str]:
    return {"User-Agent": UA, "Referer": "https://pan.baidu.com/"}


async def start() -> LoginSession:
    """申请二维码。"""
    session = LoginSession(token=_new_token(), provider=PROVIDER)
    gid = secrets.token_hex(16).upper()
    try:
        async with async_client(timeout=15, headers=_headers()) as client:
            response = await client.get(
                QRCODE_URL, params={"lp": "pc", "qrloginfrom": "pc", "gid": gid}
            )
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        session.status = "failed"
        session.message = f"申请二维码失败：{exc}"
        return session

    sign = str((payload or {}).get("sign") or "") if isinstance(payload, dict) else ""
    if not sign:
        session.status = "failed"
        session.message = "百度未返回二维码 sign（接口可能已调整）"
        return session

    session.extra = {"sign": sign, "gid": gid}
    session.qr_image = f"{QR_IMAGE_URL}?sign={sign}&lp=pc&qrloginfrom=pc"
    session.qr_content = f"https://wappass.baidu.com/wp/?qrlogin&sign={sign}"
    session.message = "请用百度网盘 App 扫码"
    return session


async def poll(session: LoginSession) -> LoginSession:
    """长轮询扫码状态；确认后换 Cookie。"""
    sign = str(session.extra.get("sign") or "")
    gid = str(session.extra.get("gid") or "")
    if not sign:
        session.status = "failed"
        session.message = "会话缺少 sign"
        return session

    try:
        async with async_client(timeout=20, headers=_headers()) as client:
            response = await client.get(
                UNICAST_URL,
                params={"channel_id": sign, "gid": gid, "tpl": "netdisk", "_sdkFrom": "1"},
            )
            text = response.text
    except httpx.TimeoutException:
        # 长轮询没等到事件就是"还没人扫"，不是错误
        session.message = "等待扫码中…"
        return session
    except httpx.HTTPError as exc:
        session.message = f"查询状态失败：{exc}"
        return session

    bduss = _extract_bduss(text)
    if not bduss:
        session.message = "等待扫码中…"
        return session
    session.status = "scanned"
    session.message = "已扫码，正在换取登录态…"
    return await _exchange(session, bduss, gid)


def _extract_bduss(text: str) -> str:
    """从 unicast 响应里取 bduss 票据。

    返回体形如 ``{"channel_v":"{\\"status\\":0,\\"v\\":\\"xxx\\"}"}``——
    是**双层 JSON**（内层被当字符串塞进外层），所以要解两次。
    解析失败时退回正则，避免因为格式微调就完全失效。
    """
    if not text:
        return ""
    try:
        outer = json.loads(text)
        inner_raw = outer.get("channel_v") if isinstance(outer, dict) else None
        if inner_raw:
            inner = json.loads(inner_raw)
            if isinstance(inner, dict) and _as_int(inner.get("status")) == 0:
                return str(inner.get("v") or "")
    except (ValueError, TypeError):
        pass
    match = re.search(r'"v"\s*:\s*"([^"]+)"', text)
    return match.group(1) if match else ""


async def _exchange(session: LoginSession, bduss: str, gid: str) -> LoginSession:
    """用 bduss 票据换网页 Cookie。"""
    try:
        async with async_client(timeout=20, headers=_headers()) as client:
            await client.get(
                QRLOGIN_URL,
                params={
                    "bduss": bduss,
                    "u": "https://pan.baidu.com/disk/home",
                    "loginVersion": "v4",
                    "qrcode": "1",
                    "tpl": "netdisk",
                    "gid": gid,
                },
                follow_redirects=True,
            )
            jar = {c.name: c.value for c in client.cookies.jar}
    except httpx.HTTPError as exc:
        session.status = "failed"
        session.message = f"换取 Cookie 失败：{exc}"
        return session

    if not jar.get("BDUSS"):
        session.status = "failed"
        session.message = (
            "扫码已确认但未取到 BDUSS（百度风控可能要求二次验证）。"
            "请改用「Cookie 导入」方式"
        )
        return session

    session.cookie = "; ".join(f"{k}={v}" for k, v in jar.items() if v)
    session.status = "success"
    session.message = "登录成功"
    return session


async def verify(cookie: str) -> tuple[bool, str, dict[str, Any]]:
    """校验百度网盘 Cookie（也用于 Cookie 导入即时验证）。"""
    if not cookie.strip():
        return False, "Cookie 为空", {}
    headers = dict(_headers())
    headers["Cookie"] = cookie.strip()
    try:
        async with async_client(timeout=15, headers=headers) as client:
            response = await client.get(
                "https://pan.baidu.com/api/quota",
                params={"checkfree": 1, "checkexpire": 1},
            )
    except httpx.HTTPError as exc:
        return False, f"校验请求失败（网络不通？）：{exc}", {}
    try:
        payload = response.json()
    except ValueError:
        return False, "Cookie 无效或已过期（接口未返回 JSON）", {}

    if not isinstance(payload, dict):
        return False, "响应格式异常", {}
    if _as_int(payload.get("errno")) == 0:
        total = int(payload.get("total") or 0)
        used = int(payload.get("used") or 0)
        return True, "Cookie 有效", {"total": total, "used": used}
    return False, f"Cookie 无效（errno={payload.get('errno')}）", {}
