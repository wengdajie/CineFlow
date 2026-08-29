"""夸克网盘：**只支持 Cookie 导入 + 即时校验**（不支持扫码）。

**为什么不做扫码**（重要，别让后来人白费功夫）：夸克网页登录走
``open-api-drive.quark.cn``，实测裸请求返回::

    {"errno":10001,"error_info":"公参缺失，x-pan-client-id, x-pan-tm, x-pan-token不能为空"}

这三个"公参"是客户端**签名**参数，要拿到必须逆向夸克前端的签名算法。
逆向签名以绕过风控，与 ADR-34（不硬刚站点反爬）立场一致，故**明确不做**。
``su.quark.cn`` / ``passport.quark.cn`` 等候选端点实测均为 404 或无法连接。

所以夸克这里提供的价值是：**把 Cookie 粘进来时立刻告诉你有没有效**，
而不是等到半夜转存任务失败才发现填错了。这已经解决了绝大部分实际痛点
（原先粘完只能靠"保存"然后盲等）。
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.logger import get_logger
from app.services.panlogin import UA
from app.utils.http import async_client

logger = get_logger(__name__)

PROVIDER = "quark"
API_BASE = "https://drive-pc.quark.cn/1/clouddrive"


def _headers(cookie: str) -> dict[str, str]:
    return {
        "User-Agent": UA,
        "Cookie": cookie.strip(),
        "Referer": "https://pan.quark.cn/",
        "Content-Type": "application/json",
    }


async def verify(cookie: str) -> tuple[bool, str, dict[str, Any]]:
    """校验夸克 Cookie 是否有效。"""
    if not cookie.strip():
        return False, "Cookie 为空", {}
    try:
        async with async_client(timeout=15, headers=_headers(cookie)) as client:
            response = await client.get(
                f"{API_BASE}/member",
                params={"pr": "ucpro", "fr": "pc", "fetch_subscribe": "false"},
            )
    except httpx.HTTPError as exc:
        return False, f"校验请求失败（网络不通？）：{exc}", {}
    try:
        payload = response.json()
    except ValueError:
        return False, "Cookie 无效或已过期（接口未返回 JSON）", {}

    if not isinstance(payload, dict):
        return False, "响应格式异常", {}
    # 夸克未登录时返回 401 + code=31001 require login
    if response.status_code == 401 or payload.get("code") == 31001:
        return False, "Cookie 无效或已过期（夸克要求重新登录）", {}
    if payload.get("status") == 200 or payload.get("code") == 0:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        nickname = str((data or {}).get("nickname") or "")
        return True, f"Cookie 有效{'：' + nickname if nickname else ''}", {
            "nickname": nickname,
            "vip": (data or {}).get("member_type"),
        }
    return False, str(payload.get("message") or "Cookie 无效"), {}
