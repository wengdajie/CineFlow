"""网盘账号登录（扫码 / Cookie 导入）。

**为什么需要它**：原先所有网盘都只能「去浏览器 F12 复制 Cookie 再粘进来」。
这对普通 NAS 用户门槛过高，也是 T3FAP 做得比本项目好的地方。

**能做到什么程度（必须如实说明）**：

* ``115``  ✅ 官方开放了完整的扫码登录接口（申请 uid → 出二维码 → 轮询状态 →
  换 Cookie），实测可用，是本模块支持得最好的网盘。
* ``baidu`` ✅ 走 passport 扫码。拿到的是网页版 Cookie（``BDUSS`` 等）。
* ``quark`` ⚠️ **不支持扫码**。夸克的登录接口需要签名过的客户端参数
  （``x-pan-client-id`` / ``x-pan-tm`` / ``x-pan-token``），逆向它属于对抗风控，
  与 ADR-34 的立场不符。夸克只提供 **Cookie 导入 + 即时校验**。
* 其余网盘（AList / WebDAV / 本地目录）本来就是账号密码或无需登录，不在此列。

**设计原则**：扫码会话只存在内存里（``_SESSIONS``），不落库。
Cookie 是最高敏感度凭据，登录成功后**立即写入对应站点记录**并丢弃会话，
不做任何中间持久化。会话 10 分钟过期。
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from app.core.logger import get_logger

logger = get_logger(__name__)

#: 扫码会话有效期（秒）。二维码本身也是分钟级过期，给 10 分钟足够。
SESSION_TTL = 600

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


@dataclass
class LoginSession:
    """一次扫码登录会话。"""

    token: str
    provider: str
    #: 二维码内容（给前端渲染成图，或直接给出可点链接）
    qr_content: str = ""
    #: 二维码图片地址（部分网盘直接给图，前端走 /images 代理显示）
    qr_image: str = ""
    #: waiting（等扫码）/ scanned（已扫待确认）/ success / expired / failed
    status: str = "waiting"
    message: str = "请用手机 App 扫码"
    created_at: float = field(default_factory=time.time)
    #: 各网盘自己的中间态（uid/sign/time 等），不外泄给前端
    extra: dict[str, Any] = field(default_factory=dict)
    #: 登录成功后的 Cookie，取走一次即清空
    cookie: str = ""
    nickname: str = ""

    @property
    def expired(self) -> bool:
        return time.time() - self.created_at > SESSION_TTL

    def to_dict(self) -> dict[str, Any]:
        """给前端的视图：**绝不包含 cookie**。"""
        return {
            "token": self.token,
            "provider": self.provider,
            "qr_content": self.qr_content,
            "qr_image": self.qr_image,
            "status": self.status,
            "message": self.message,
            "nickname": self.nickname,
            "expires_in": max(0, int(SESSION_TTL - (time.time() - self.created_at))),
        }


_SESSIONS: dict[str, LoginSession] = {}
_LOCK = asyncio.Lock()


def _new_token() -> str:
    return secrets.token_urlsafe(18)


async def _gc() -> None:
    """顺手清掉过期会话，避免内存里堆积。"""
    async with _LOCK:
        for token in [t for t, s in _SESSIONS.items() if s.expired]:
            _SESSIONS.pop(token, None)


async def get_session(token: str) -> LoginSession | None:
    """取一个**仍然有效**的会话；过期的会顺带被回收掉。"""
    await _gc()
    return _SESSIONS.get(token)


async def peek_session(token: str) -> LoginSession | None:
    """取会话，**包括已过期的**，且不触发回收。

    为什么要单独开一个口子：轮询接口需要区分「这个 token 从来不存在」和
    「二维码过期了」——前者要提示重新打开登录窗口，后者只要点一下刷新二维码。
    如果统一走 ``get_session``，过期会话会在查询的同一瞬间被 gc 掉，
    两种情况就都塌成同一句「会话不存在」，用户没法判断该做什么。
    """
    return _SESSIONS.get(token)


async def put_session(session: LoginSession) -> None:
    async with _LOCK:
        _SESSIONS[session.token] = session


async def drop_session(token: str) -> None:
    async with _LOCK:
        _SESSIONS.pop(token, None)


def reset_state() -> None:
    """清空全部会话（测试用）。"""
    _SESSIONS.clear()
