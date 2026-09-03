"""网盘登录编排：统一 start / poll / 导入 Cookie / 写入站点。

把「各网盘怎么登录」的差异收在 ``PROVIDERS`` 表里，路由层只认统一动作。
和网盘能力位（ADR-28）一个思路：**能力由后端声明，前端按声明渲染**，
所以前端不需要知道"夸克没有扫码"这件事——它读 ``capabilities`` 就够了。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.core.logger import get_logger
from app.db.models import SiteConfig
from app.db.session import session_scope
from app.schemas.enums import ProviderKind
from app.services.panlogin import (
    LoginSession,
    drop_session,
    get_session,
    peek_session,
    put_session,
)
from app.services.panlogin import baidu as baidu_login
from app.services.panlogin import baidu_openapi as baidu_openapi_login
from app.services.panlogin import pan115 as pan115_login
from app.services.panlogin import quark as quark_login

logger = get_logger(__name__)

#: 各网盘的登录能力声明。
#: ``qrcode``   能不能扫码登录
#: ``cookie``   能不能导入 Cookie。**不是都能**：开放平台授权那条路存的是
#:              OAuth token，不是 Cookie，粘 Cookie 进去只会存出一个用不了的
#:              凭据，所以声明为 False 并在 :func:`apply_cookie` 里真的挡住。
#: ``verify``   有没有即时校验实现
PROVIDERS: dict[str, dict[str, Any]] = {
    "pan115": {
        "label": "115 网盘",
        "qrcode": True,
        "cookie": True,
        "module": pan115_login,
        "note": "官方扫码接口，实测可用",
    },
    "baidu": {
        "label": "百度网盘",
        "qrcode": True,
        "cookie": True,
        "module": baidu_login,
        "note": "passport 扫码；风控较严时请改用 Cookie 导入",
    },
    "baidu_openapi": {
        "label": "百度网盘（开放平台授权）",
        "qrcode": True,
        "cookie": False,
        "module": baidu_openapi_login,
        "note": (
            "官方 OAuth 设备码授权，refresh_token 有效期 10 年、可静默刷新，"
            "不会像网页 Cookie 那样几小时就失效。"
            "需自备 AppKey/SecretKey（pan.baidu.com/union 创建应用）。"
            "注意：开放平台**不支持转存他人分享链接**，转存仍需网页 Cookie"
        ),
        #: 这条路要先收 AppKey/SecretKey 才能出二维码
        "needs_app_credentials": True,
    },
    "quark": {
        "label": "夸克网盘",
        "qrcode": False,
        "cookie": True,
        "module": quark_login,
        "note": "不支持扫码（登录需签名公参，见文档）；支持 Cookie 导入并即时校验",
    },
}


def providers() -> list[dict[str, Any]]:
    """登录能力清单，前端据此渲染按钮。"""
    return [
        {
            "provider": key,
            "label": str(meta["label"]),
            "qrcode": bool(meta["qrcode"]),
            "cookie": bool(meta["cookie"]),
            "note": str(meta["note"]),
            "needs_app_credentials": bool(meta.get("needs_app_credentials")),
        }
        for key, meta in PROVIDERS.items()
    ]


async def start_qrcode(
    provider: str,
    *,
    client_id: str = "",
    client_secret: str = "",
) -> dict[str, Any]:
    """开始一次扫码登录。

    ``client_id``/``client_secret`` 只有开放平台授权那条路要用
    （见 ``needs_app_credentials``）；其余网盘忽略，保持签名统一。
    """
    meta = PROVIDERS.get(provider)
    if not meta:
        return {"success": False, "message": f"不支持的网盘：{provider}"}
    if not meta["qrcode"]:
        return {
            "success": False,
            "message": f"{meta['label']}不支持扫码登录：{meta['note']}",
        }
    if meta.get("needs_app_credentials"):
        session: LoginSession = await meta["module"].start(client_id, client_secret)
    else:
        session = await meta["module"].start()
    await put_session(session)
    if session.status == "failed":
        return {"success": False, "message": session.message, "data": session.to_dict()}
    return {"success": True, "data": session.to_dict()}


async def poll_qrcode(token: str) -> dict[str, Any]:
    """轮询扫码状态。成功时**不返回 Cookie**，只告诉前端可以保存了。"""
    # 用 peek 而不是 get：要能区分「token 不存在」和「二维码过期」，见 peek_session 注释
    session = await peek_session(token)
    if not session:
        return {"success": False, "message": "会话不存在或已过期，请重新获取二维码"}
    if session.expired:
        session.status = "expired"
        session.message = "二维码已过期，请重新获取"
        view = session.to_dict()
        # 过期态只报一次，报完立刻销毁：Cookie 类凭据不留在内存里过夜
        await drop_session(token)
        return {"success": True, "data": view}
    if session.status in {"success", "failed", "expired"}:
        # 终态不再打扰上游接口
        return {"success": True, "data": session.to_dict()}

    meta = PROVIDERS.get(session.provider) or {}
    module = meta.get("module")
    if module is None:
        return {"success": False, "message": "会话对应的网盘已不受支持"}
    session = await module.poll(session)
    await put_session(session)
    return {"success": True, "data": session.to_dict()}


async def verify_cookie(provider: str, cookie: str) -> dict[str, Any]:
    """即时校验一段 Cookie。"""
    meta = PROVIDERS.get(provider)
    if not meta:
        return {"success": False, "message": f"不支持的网盘：{provider}"}
    ok, message, extra = await meta["module"].verify(cookie)
    return {"success": ok, "message": message, "data": extra}


def _target_site(session_db: Any, provider: str, site_id: int | None) -> Any:
    """找到要写入的网盘站点记录。"""
    if site_id:
        return session_db.get(SiteConfig, site_id)
    # 没指定就找同 provider 的第一个网盘存储站点
    return session_db.execute(
        select(SiteConfig)
        .where(SiteConfig.kind == ProviderKind.PANSTORAGE.value)
        .where(SiteConfig.provider == provider)
        .order_by(SiteConfig.id)
    ).scalars().first()


async def apply_cookie(
    provider: str,
    cookie: str,
    *,
    site_id: int | None = None,
    site_name: str | None = None,
    verify: bool = True,
) -> dict[str, Any]:
    """把 Cookie 写入站点记录；没有对应站点则**自动创建并启用**。

    自动创建是有意的：用户刚扫码成功，最不该做的事是让他再去站点管理
    手工建一条记录、还得猜 provider 名该填什么。
    """
    meta = PROVIDERS.get(provider)
    if not meta:
        return {"success": False, "message": f"不支持的网盘：{provider}"}
    # 声明了不支持 Cookie 就必须真的挡住。``baidu_openapi`` 落库的是
    # access_token/refresh_token，把一段 Cookie 塞进 options["cookie"] 会建出
    # 一个「有站点、有凭据、但每次调用都失败」的坏站点；而它的 verify() 收的是
    # access_token，一段 Cookie 恰好也能"校验失败"——于是报错信息指向
    # 「授权无效」，把用户引向重新授权，而真正的问题是他用错了入口（ADR-82）。
    if not meta["cookie"]:
        return {
            "success": False,
            "message": (
                f"{meta['label']}不支持 Cookie 导入：{meta['note']}"
            ),
        }
    cookie = (cookie or "").strip()
    if not cookie:
        return {"success": False, "message": "Cookie 为空"}

    detail: dict[str, Any] = {}
    if verify:
        ok, message, detail = await meta["module"].verify(cookie)
        if not ok:
            # 校验不过就不写库——写进去只会让后续任务静默失败
            return {"success": False, "message": f"未保存：{message}"}

    with session_scope() as session_db:
        site = _target_site(session_db, provider, site_id)
        created = False
        if site is None:
            site = SiteConfig(
                name=site_name or f"{meta['label']}（扫码登录）",
                kind=ProviderKind.PANSTORAGE.value,
                provider=provider,
                url="",
                enabled=True,
                options={},
            )
            session_db.add(site)
            created = True
        site.cookie = cookie
        # Provider 读 Cookie 走 option()，两处都写，避免"填了却读不到"
        options = dict(site.options or {})
        options["cookie"] = cookie
        site.options = options
        site.enabled = True
        site.last_status = "登录成功"
        session_db.flush()
        result = {
            "success": True,
            "message": ("已创建站点并保存凭据" if created else "凭据已更新"),
            "data": {
                "site_id": site.id,
                "site_name": site.name,
                "provider": provider,
                "created": created,
                **detail,
            },
        }
    return result


async def apply_openapi_tokens(
    provider: str,
    extra: dict[str, Any],
    *,
    site_id: int | None = None,
    site_name: str | None = None,
) -> dict[str, Any]:
    """把开放平台授权的 token 写入站点记录。

    与 Cookie 路径分开是因为落库内容不同：这里存的是
    ``access_token`` / ``refresh_token`` / ``client_id`` / ``client_secret``。
    ``refresh_token`` 是长期凭据（10 年），**必须**一起存，否则
    access_token 30 天后过期又要重新授权，等于白做。
    """
    meta = PROVIDERS.get(provider)
    if not meta:
        return {"success": False, "message": f"不支持的网盘：{provider}"}
    access_token = str(extra.get("access_token") or "")
    if not access_token:
        return {"success": False, "message": "未取到 access_token"}

    ok, message, detail = await meta["module"].verify(access_token)
    if not ok:
        return {"success": False, "message": f"未保存：{message}"}

    with session_scope() as session_db:
        site = _target_site(session_db, provider, site_id)
        created = False
        if site is None:
            site = SiteConfig(
                name=site_name or f"{meta['label']}",
                kind=ProviderKind.PANSTORAGE.value,
                provider=provider,
                url="",
                enabled=True,
                options={},
            )
            session_db.add(site)
            created = True
        options = dict(site.options or {})
        options.update(
            {
                "access_token": access_token,
                "refresh_token": str(extra.get("refresh_token") or ""),
                "client_id": str(extra.get("client_id") or ""),
                "client_secret": str(extra.get("client_secret") or ""),
                "expires_in": int(extra.get("expires_in") or 0),
            }
        )
        site.options = options
        site.api_key = access_token
        site.enabled = True
        site.last_status = "授权成功"
        session_db.flush()
        return {
            "success": True,
            "message": ("已创建站点并保存授权" if created else "授权已更新"),
            "data": {
                "site_id": site.id,
                "site_name": site.name,
                "provider": provider,
                "created": created,
                **detail,
            },
        }


async def complete_qrcode(
    token: str, *, site_id: int | None = None, site_name: str | None = None
) -> dict[str, Any]:
    """扫码成功后把 Cookie 落库，并销毁会话。"""
    session = await get_session(token)
    if not session:
        return {"success": False, "message": "会话不存在或已过期"}
    if session.status != "success":
        return {"success": False, "message": f"尚未登录成功（当前：{session.message}）"}
    # 开放平台授权拿到的是 token 而不是 Cookie，落库字段完全不同
    if (PROVIDERS.get(session.provider) or {}).get("needs_app_credentials"):
        result = await apply_openapi_tokens(
            session.provider, session.extra, site_id=site_id, site_name=site_name
        )
        if result.get("success"):
            await drop_session(token)
        return result
    if not session.cookie:
        return {"success": False, "message": f"尚未登录成功（当前：{session.message}）"}
    # 扫码流程已经证明凭据有效，不必再校验一次（多一次请求反而更容易撞风控）
    result = await apply_cookie(
        session.provider,
        session.cookie,
        site_id=site_id,
        site_name=site_name,
        verify=False,
    )
    if result.get("success"):
        await drop_session(token)
    return result
