"""百度网盘「开放平台授权」存储（access_token 鉴权，xpan 接口）。

**为什么必须有这个文件**（v1.24.0 复盘查出的僵尸站点，与 baidu.py 同一个病）：

v1.23.0 加了 ``services/panlogin/baidu_openapi.py``，用户授权成功后
``apply_openapi_tokens()`` 会建出一条 ``provider="baidu_openapi"`` 的
**panstorage 站点并启用**。但 ``providers/panstorage/`` 下一直没有对应实现，
于是 ``create_provider("baidu_openapi")`` 返回 ``None``：

| 现象 | 实测（v1.23.0） |
|---|---|
| ``GET /pan`` 总览 | 站点**不出现**（``storages()`` 拿不到实例就跳过） |
| 浏览 / 保活 / 下载直链 | 全部静默跳过 |
| 站点列表 | 站点躺在那里，``enabled=True``、``last_status="授权成功"`` |

用户看到「授权成功」，实际得到一个什么都不干的空壳 —— 这正是 v1.16.0
给 ``baidu`` 修过的那个 bug，v1.23.0 新增登录通道时又原样犯了一遍。
根因是「登录 Provider」与「存储 Provider」是两套注册表，加前者不会强制加后者。
本文件补上这条链，``tests/test_baidu_openapi_storage.py`` 钉住
「每个声明支持登录的 panstorage 通道都必须能实例化出存储 Provider」。

用的是官方开放平台 ``xpan`` 接口（OAuth access_token 鉴权）::

    用户信息 GET  /rest/2.0/xpan/nas?method=uinfo
    容量     GET  /api/quota?checkfree=1&checkexpire=1
    列目录   GET  /rest/2.0/xpan/file?method=list&dir=<路径>
    搜索     GET  /rest/2.0/xpan/file?method=search&key=<词>&recursion=1
    元信息   GET  /rest/2.0/xpan/multimedia?method=filemetas&fsids=[..]&dlink=1
    文件管理 POST /rest/2.0/xpan/file?method=filemanager&opera=<copy|move|rename|delete>
    新建目录 POST /rest/2.0/xpan/file?method=create&isdir=1

**与 ``baidu.py``（网页 Cookie）的分工 —— 不是替代关系**：

| 能力 | 网页 Cookie (``baidu``) | 开放平台 (``baidu_openapi``) |
|---|---|---|
| 凭据寿命 | 小时级，受设备/IP 风控 | ``refresh_token`` 10 年，可静默刷新 |
| 浏览 / 容量 / 直链 / 文件管理 | ✅ | ✅ |
| **转存他人分享链接** | ✅ | ❌ 官方无此接口 |

所以 :attr:`supports_save` 明确为 ``False``：``/share/transfer`` 只存在于网页
通道，开放平台 ``xpan`` 没有对等接口。**宁可声明不支持，也不要假装支持** ——
能力位为 False 时前端直接不渲染「转存」按钮（见 ``base.capabilities()``），
用户不会点了才发现没用。想转存分享就同时保留一个 ``baidu`` Cookie 站点。

**access_token 过期自动刷新**：``access_token`` 只有 30 天。检测到令牌失效
（``errno`` ∈ :data:`TOKEN_INVALID_ERRNOS`）时用 ``refresh_token`` 换一份新的、
**回写站点 options**、再重试一次原请求。不回写就等于每次调用都刷新一遍，
30 天后 ``refresh_token`` 也会因为拿不到最新那份而失效。

> 已否决的做法：内置公共 AppKey（所有用户共用配额，被刷爆一起挂，且
> SecretKey 进开源仓库本身就是泄密）—— 与 ``panlogin/baidu_openapi.py`` 同一裁决。
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.logger import get_logger
from app.providers.panstorage.base import (
    BasePanStorage,
    IncompleteDirectory,
    PanFile,
    PanQuota,
    SaveResult,
)
from app.providers.registry import register
from app.utils.http import async_client

logger = get_logger(__name__)

API_BASE = "https://pan.baidu.com"
XPAN_FILE = "/rest/2.0/xpan/file"
XPAN_NAS = "/rest/2.0/xpan/nas"
XPAN_MULTIMEDIA = "/rest/2.0/xpan/multimedia"

#: 取 dlink 真实地址时必须用这个 UA，否则 403（与网页通道同一个坑）
NETDISK_UA = "netdisk;P2SP;3.0.20.11"

#: 「access_token 无效/过期」的 errno。实测坏 token 调 uinfo 回 ``errno=20017``
#: （nas access token invalid）；xpan 文件类接口回 ``-6``；官方文档另列 111。
#: 只有这些才值得去刷新令牌 —— 把别的 errno 也当令牌问题会导致
#: 「目录不存在」这类错误被无限重试。
TOKEN_INVALID_ERRNOS = frozenset({-6, 111, 20017})


def _as_int(value: Any, default: int = -1) -> int:
    """安全转 int。

    与 ``panstorage/baidu.py`` 同一个理由：``errno=0`` 才是成功，而 ``0``
    在 Python 里是假值，写成 ``int(x or default)`` 会把成功吃掉。
    """
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@register
class BaiduOpenApiStorage(BasePanStorage):
    """百度网盘（开放平台授权）。"""

    supports_strict_list = True
    name = "baidu_openapi"
    display_name = "百度网盘（开放平台授权）"

    #: dlink 与网页通道同一个坑：非 netdisk 系 UA 请求大文件必然 403
    direct_link_user_agent = NETDISK_UA

    #: 开放平台**没有**转存他人分享的接口，如实声明（见模块文档表格）
    supports_save = False
    supports_delete = True
    supports_rename = True
    supports_move = True
    supports_search = True
    supports_keepalive = True

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        #: 最近一次调用的 errno / HTTP 状态码，用于把「令牌废了」与
        #: 「网络不通」分开报（ADR-82：失败必须可区分）
        self._last_errno: int | None = None
        self._last_http_status: int | None = None
        #: 一次调用链里只允许刷新一次，避免坏 refresh_token 打成死循环
        self._refreshed_once = False

    # ---------------- 凭据 ----------------
    @property
    def access_token(self) -> str:
        return str(self.option("access_token") or "").strip()

    @property
    def refresh_token(self) -> str:
        return str(self.option("refresh_token") or "").strip()

    @property
    def client_id(self) -> str:
        return str(self.option("client_id") or "").strip()

    @property
    def client_secret(self) -> str:
        return str(self.option("client_secret") or "").strip()

    def _store_tokens(self, tokens: dict[str, Any]) -> None:
        """把刷新到的令牌写回内存配置**和**站点记录。

        两处都写：内存那份让当前这次调用链立刻能用，落库那份让下次
        进程/任务也能用。只写内存的话每次调用都要刷新一遍，
        而百度对刷新频率也有限制。
        """
        access = str(tokens.get("access_token") or "")
        if not access:
            return
        options = dict(self.config.get("options") or {})
        options["access_token"] = access
        refreshed = str(tokens.get("refresh_token") or "")
        if refreshed:
            options["refresh_token"] = refreshed
        options["expires_in"] = _as_int(tokens.get("expires_in"), 0)
        self.config["options"] = options
        self.config["api_key"] = access

        site_id = self.config.get("id")
        if not site_id:
            return
        # 延迟导入：providers 层被 services.sites 导入，模块级反向导入会成环
        try:
            from app.db.models import SiteConfig
            from app.db.session import session_scope

            with session_scope() as session:
                site = session.get(SiteConfig, site_id)
                if not site:
                    return
                merged = dict(site.options or {})
                merged.update(
                    {
                        "access_token": access,
                        "expires_in": options["expires_in"],
                    }
                )
                if refreshed:
                    merged["refresh_token"] = refreshed
                site.options = merged
                site.api_key = access
                site.last_status = "授权已自动刷新"
        except Exception as exc:  # 刷新是锦上添花，不能因此让业务调用失败
            logger.warning("百度开放平台令牌回写失败（本次调用仍可用）：%s", exc)

    async def _refresh_token(self) -> bool:
        """用 refresh_token 换新 access_token 并落库。成功返回 True。"""
        if self._refreshed_once:
            return False
        self._refreshed_once = True
        if not (self.refresh_token and self.client_id and self.client_secret):
            return False
        # 延迟导入：登录编排属 services 层
        from app.services.panlogin import baidu_openapi as login

        tokens = await login.refresh(
            self.refresh_token, self.client_id, self.client_secret
        )
        if not tokens:
            return False
        self._store_tokens(tokens)
        logger.info("百度开放平台 access_token 已自动刷新")
        return True

    # ---------------- 通用请求 ----------------
    async def _call(
        self,
        path: str,
        *,
        method: str = "GET",
        params: dict[str, Any] | None = None,
        data: Any = None,
        _retry: bool = True,
    ) -> dict[str, Any] | None:
        """调 xpan 接口。``errno != 0`` 视为失败并记录 errno。

        令牌失效时**自动刷新一次并重试**，所以调用方不必关心 30 天有效期。
        """
        self._last_errno = None
        self._last_http_status = None
        token = self.access_token
        if not token:
            return None
        merged = {"access_token": token, **(params or {})}
        try:
            async with async_client(
                timeout=self.config.get("timeout"),
                headers={"User-Agent": "pan.baidu.com"},
            ) as client:
                response = await client.request(
                    method, f"{API_BASE}{path}", params=merged, data=data
                )
            self._last_http_status = response.status_code
            payload = response.json()
        except Exception as exc:  # 真正的网络层失败才是"连不上"
            logger.warning("百度开放平台 %s 请求失败：%s", path, exc)
            return None
        if not isinstance(payload, dict):
            return None
        errno = _as_int(payload.get("errno"), 0)
        self._last_errno = errno
        if errno == 0:
            return payload
        if errno in TOKEN_INVALID_ERRNOS and _retry and await self._refresh_token():
            # 刷新成功就重试一次；_retry=False 防止无限递归
            return await self._call(
                path, method=method, params=params, data=data, _retry=False
            )
        logger.warning(
            "百度开放平台 %s 失败：errno=%s %s",
            path,
            errno,
            payload.get("errmsg") or "",
        )
        return None

    def _auth_hint(self, action: str) -> str:
        """把失败翻成**可执行**的下一步（ADR-82）。"""
        if not self.access_token:
            return (
                f"{action}失败：未完成百度开放平台授权，"
                "请到「网盘管理 → 百度网盘（开放平台授权）」扫码授权"
            )
        if self._last_errno in TOKEN_INVALID_ERRNOS:
            if not self.refresh_token:
                return (
                    f"{action}失败：access_token 已失效（errno={self._last_errno}），"
                    "且没有 refresh_token 可用，请重新授权"
                )
            return (
                f"{action}失败：access_token 已失效（errno={self._last_errno}）"
                "且自动刷新未成功，refresh_token 可能已被吊销，请重新授权"
            )
        if self._last_errno is None:
            return f"{action}失败：连不上百度网盘或响应不是 JSON，请检查网络/代理"
        return f"{action}失败：百度返回 errno={self._last_errno}"

    # ---------------- 目录 ----------------
    @staticmethod
    def _parse_files(items: Any) -> list[PanFile]:
        """把 xpan 的 list 结构转成统一 PanFile。"""
        files: list[PanFile] = []
        if not isinstance(items, list):
            return files
        for item in items:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "")
            name = str(item.get("server_filename") or item.get("filename") or "")
            if not name and path:
                name = path.rsplit("/", 1)[-1]
            if not name:
                continue
            is_dir = _as_int(item.get("isdir"), 0) == 1
            fs_id = item.get("fs_id")
            files.append(
                PanFile(
                    name=name,
                    path=path or f"/{name}",
                    is_dir=is_dir,
                    size=0 if is_dir else _as_int(item.get("size"), 0),
                    file_id=str(fs_id) if fs_id is not None else None,
                    modified_at=str(item.get("server_mtime") or "") or None,
                )
            )
        return files

    async def list_dir(self, path: str = "/", *, strict: bool = False) -> list[PanFile]:
        target = self.normalize_path(path)
        payload = await self._call(
            XPAN_FILE,
            params={
                "method": "list",
                "dir": target,
                "order": "name",
                "start": 0,
                "limit": 1000,
                "web": 1,
            },
        )
        if strict and (not payload or not isinstance(payload.get("list"), list)):
            raise OSError("网盘目录读取失败或响应无效")
        files = self._parse_files((payload or {}).get("list") or [])
        if strict and (len(payload["list"]) >= 1000 or len(files) != len(payload["list"])):
            raise IncompleteDirectory(files)
        files.sort(key=lambda f: (not f.is_dir, f.name.lower()))
        return files

    async def search(self, keyword: str, *, limit: int = 50) -> list[PanFile]:
        word = str(keyword or "").strip()
        if not word:
            return []
        capped = max(1, min(_as_int(limit, 50), 200))
        payload = await self._call(
            XPAN_FILE,
            params={
                "method": "search",
                "key": word,
                "dir": "/",
                "recursion": 1,
                "num": capped,
                "web": 1,
            },
        )
        return self._parse_files((payload or {}).get("list") or [])[:capped]

    # ---------------- 容量 ----------------
    async def quota(self) -> PanQuota:
        payload = await self._call(
            "/api/quota", params={"checkfree": 1, "checkexpire": 1}
        )
        if not payload:
            return PanQuota()
        return PanQuota(
            total=_as_int(payload.get("total"), 0),
            used=_as_int(payload.get("used"), 0),
        )

    # ---------------- 文件管理 ----------------
    async def _filemanager(self, opera: str, filelist: Any) -> bool:
        """rename / move / copy / delete 的统一入口。"""
        if not self.access_token:
            logger.warning("%s", self._auth_hint(f"{opera} 操作"))
            return False
        payload = await self._call(
            XPAN_FILE,
            method="POST",
            params={"method": "filemanager", "opera": opera},
            data={
                "async": 0,
                "filelist": json.dumps(filelist, ensure_ascii=False),
                "ondup": "fail",
            },
        )
        if payload is None:
            logger.warning("%s", self._auth_hint(f"{opera} 操作"))
            return False
        return True

    async def mkdir(self, path: str) -> bool:
        target = self.normalize_path(path)
        if target == "/":
            return False
        payload = await self._call(
            XPAN_FILE,
            method="POST",
            params={"method": "create"},
            data={"path": target, "isdir": 1, "size": 0, "rtype": 0},
        )
        return payload is not None

    async def delete(self, path: str, *, file_id: str | None = None) -> bool:
        target = self.normalize_path(path)
        if target == "/":
            return False
        return await self._filemanager("delete", [target])

    async def rename(
        self, path: str, new_name: str, *, file_id: str | None = None
    ) -> bool:
        target = self.normalize_path(path)
        name = str(new_name or "").strip()
        if target == "/" or not name or "/" in name:
            return False
        return await self._filemanager("rename", [{"path": target, "newname": name}])

    async def move(
        self, path: str, target_dir: str, *, file_id: str | None = None
    ) -> bool:
        source = self.normalize_path(path)
        dest = self.normalize_path(target_dir)
        if source == "/":
            return False
        return await self._filemanager(
            "move",
            [{"path": source, "dest": dest, "newname": source.rsplit("/", 1)[-1]}],
        )

    async def copy(
        self, path: str, target_dir: str, *, file_id: str | None = None
    ) -> bool:
        source = self.normalize_path(path)
        dest = self.normalize_path(target_dir)
        if source == "/":
            return False
        return await self._filemanager(
            "copy",
            [{"path": source, "dest": dest, "newname": source.rsplit("/", 1)[-1]}],
        )

    # ---------------- 直链 ----------------
    async def download_url(self, path: str, *, file_id: str | None = None) -> str | None:
        """换取可直接下载的 CDN 直链。

        xpan 的 filemetas 按 ``fsids`` 取（不是路径），所以没带 ``file_id``
        时先 list 父目录反查一次 fs_id。拿到 dlink 后还要**带 netdisk UA**
        请求一次取 302 Location —— 与网页通道同一个坑：下载器用自己的 UA
        请求 dlink 必然 403，那属于把失败推迟到下载阶段。
        """
        target = self.normalize_path(path)
        if target == "/":
            return None
        fs_id = str(file_id or "").strip()
        if not fs_id:
            parent = target.rsplit("/", 1)[0] or "/"
            for item in await self.list_dir(parent):
                if item.path == target and not item.is_dir:
                    fs_id = str(item.file_id or "")
                    break
        if not fs_id:
            return None
        payload = await self._call(
            XPAN_MULTIMEDIA,
            params={
                "method": "filemetas",
                "fsids": json.dumps([_as_int(fs_id, 0)]),
                "dlink": 1,
            },
        )
        items = (payload or {}).get("list") or []
        dlink = ""
        if isinstance(items, list) and items and isinstance(items[0], dict):
            dlink = str(items[0].get("dlink") or "")
        if not dlink:
            return None
        # dlink 本身要带 access_token 才能用
        separator = "&" if "?" in dlink else "?"
        dlink = f"{dlink}{separator}access_token={self.access_token}"
        try:
            async with async_client(
                timeout=self.config.get("timeout") or 20,
                headers={"User-Agent": NETDISK_UA},
                follow_redirects=False,
            ) as client:
                response = await client.get(dlink)
        except httpx.HTTPError as exc:
            logger.warning("百度开放平台换取直链失败：%s", exc)
            return dlink
        return response.headers.get("location") or dlink

    # ---------------- 转存（不支持）----------------
    async def save_share(
        self,
        share_url: str,
        *,
        password: str | None = None,
        target_dir: str | None = None,
    ) -> SaveResult:
        """开放平台**没有**转存他人分享的接口，如实拒绝。

        不能悄悄返回成功，也不能抛异常：调用方（订阅巡检/分享追更）
        需要一句能照着做的话。想转存分享请再加一个网页 Cookie 的
        ``baidu`` 站点，两者可以共存。
        """
        return SaveResult(
            False,
            "百度开放平台授权不支持转存他人分享链接（官方 xpan 接口无此能力）。"
            "请在「网盘管理」另外用 Cookie 方式添加一个「百度网盘」站点，"
            "转存会自动走那条通道",
        )

    # ---------------- 探活 ----------------
    async def keep_alive(self) -> tuple[bool, str]:
        """查一次用户信息刷新授权态（顺带触发 access_token 自动刷新）。"""
        if not self.access_token:
            return False, "未完成开放平台授权（请先扫码授权）"
        payload = await self._call(XPAN_NAS, params={"method": "uinfo"})
        if payload:
            name = str(payload.get("baidu_name") or "")
            return True, f"百度开放平台授权正常{f'（{name}）' if name else ''}"
        return False, self._auth_hint("授权探活")

    async def health_check(self) -> tuple[bool, str]:
        return await self.keep_alive()
