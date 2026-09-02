"""百度网盘直连（Cookie 鉴权）。

**为什么必须有这个文件**：``services/panlogin/baidu.py`` 早就实现了百度扫码登录，
但 ``providers/panstorage/`` 下**一直没有 baidu**。后果是扫码成功后
``apply_cookie()`` 会建出一条 ``provider="baidu"`` 的站点并启用，而
``create_provider("baidu")`` 返回 ``None`` —— 站点在列表里躺着，网盘总览里
却查不到它，转存/浏览/保活全部静默跳过。用户看到"登录成功"，实际得到一个
**僵尸站点**。本文件把这条链补上。

用的是百度网盘 web 接口（与浏览器同源，Cookie 鉴权）::

    列目录   GET  /api/list?dir=<路径>
    容量     GET  /api/quota?checkfree=1&checkexpire=1
    元信息   GET  /api/filemetas?target=["<路径>"]&dlink=1
    文件管理 POST /api/filemanager?opera=<rename|move|copy|delete>
    新建目录 POST /api/create?a=commit
    搜索     GET  /api/search?key=<词>&recursion=1
    bdstoken GET  /api/gettemplatevariable?fields=["bdstoken"]

**两个百度特有的坑（都已处理）**：

1. **写操作必须带 ``bdstoken``**：这是从模板变量接口现取的 CSRF 令牌，
   缺了会直接 ``errno=-6``（身份验证失败）。这里取一次缓存在实例上。
2. **直链 ``dlink`` 不能直接投给下载器**：用浏览器 UA 请求 dlink 会 403，
   必须带 ``User-Agent: netdisk``。所以 :meth:`download_url` 自己带着
   ``netdisk`` UA 请求一次、**取 302 的 Location 作为最终 CDN 直链**返回，
   这样 aria2/浏览器拿到的就是可直接下的地址，而不是一个必然 403 的 dlink。

> 接口非公开，字段可能随官方调整变化。所有失败都优雅降级为可读错误消息，
> 不抛异常打断调用方。百度对非官方客户端风控比 115 严，转存为 best-effort。
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.core.logger import get_logger
from app.providers.panstorage.base import BasePanStorage, PanFile, PanQuota, SaveResult
from app.providers.registry import register
from app.utils.http import async_client, fetch_json

logger = get_logger(__name__)

API_BASE = "https://pan.baidu.com"
#: 分享链接形如 https://pan.baidu.com/s/1AbCdEf 或 .../share/init?surl=AbCdEf
SHARE_RE = re.compile(r"pan\.baidu\.com/s/1([0-9a-zA-Z_-]+)")
SURL_RE = re.compile(r"[?&]surl=([0-9a-zA-Z_-]+)")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
#: 取 dlink 真实地址时必须用这个 UA，否则 403
NETDISK_UA = "netdisk;P2SP;3.0.20.11"


def _as_int(value: Any, default: int = -1) -> int:
    """安全转 int。

    与 ``panlogin/baidu.py`` 里同名函数同一个理由：``errno=0`` 才是成功，
    而 ``0`` 在 Python 里是假值，写成 ``int(x or default)`` 会把成功吃掉。
    """
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@register
class BaiduPanStorage(BasePanStorage):
    """百度网盘。"""

    name = "baidu"
    display_name = "百度网盘"

    # web 接口支持完整文件管理
    supports_rename = True
    supports_move = True
    supports_search = True
    supports_keepalive = True

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._bdstoken: str = ""

    @property
    def cookie(self) -> str:
        # 用 option()：Cookie 既可能在站点 cookie 字段、也可能在 options 里
        return str(self.option("cookie") or "").strip()

    def _headers(self, *, ua: str = UA) -> dict[str, str]:
        return {
            "Cookie": self.cookie,
            "User-Agent": ua,
            "Referer": "https://pan.baidu.com/disk/home",
        }

    async def _call(
        self,
        path: str,
        *,
        method: str = "GET",
        params: dict[str, Any] | None = None,
        data: Any = None,
    ) -> dict[str, Any] | None:
        """调百度接口。``errno != 0`` 视为失败并记录原因。"""
        if not self.cookie:
            return None
        merged = {"clienttype": 0, "app_id": 250528, "web": 1, **(params or {})}
        payload = await fetch_json(
            f"{API_BASE}{path}",
            method=method,
            params=merged,
            data=data,
            headers=self._headers(),
            timeout=self.config.get("timeout"),
        )
        if not isinstance(payload, dict):
            return None
        errno = _as_int(payload.get("errno"), 0)
        if errno != 0:
            logger.warning(
                "百度网盘 %s 失败：errno=%s %s",
                path,
                errno,
                payload.get("show_msg") or payload.get("errmsg") or "",
            )
            return None
        return payload

    async def _token(self) -> str:
        """取并缓存 bdstoken（写操作的 CSRF 令牌）。"""
        if self._bdstoken:
            return self._bdstoken
        payload = await self._call(
            "/api/gettemplatevariable", params={"fields": json.dumps(["bdstoken"])}
        )
        result = (payload or {}).get("result") or {}
        if isinstance(result, dict):
            self._bdstoken = str(result.get("bdstoken") or "")
        return self._bdstoken

    # ---------------- 目录 ----------------
    @staticmethod
    def _parse_files(items: Any) -> list[PanFile]:
        files: list[PanFile] = []
        if not isinstance(items, list):
            return files
        for item in items:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "")
            name = str(item.get("server_filename") or "")
            if not name and path:
                name = path.rsplit("/", 1)[-1]
            if not name:
                continue
            files.append(
                PanFile(
                    name=name,
                    path=path or "/" + name,
                    is_dir=bool(_as_int(item.get("isdir"), 0)),
                    size=int(item.get("size") or 0),
                    file_id=str(item.get("fs_id") or ""),
                    modified_at=str(item.get("server_mtime") or "") or None,
                )
            )
        return files

    async def list_dir(self, path: str = "/") -> list[PanFile]:
        target = self.normalize_path(path)
        payload = await self._call(
            "/api/list",
            params={
                "dir": target,
                "order": "name",
                "desc": 0,
                "start": 0,
                "limit": 1000,
                "showempty": 0,
            },
        )
        files = self._parse_files((payload or {}).get("list") or [])
        files.sort(key=lambda f: (not f.is_dir, f.name.lower()))
        return files

    # ---------------- 容量 ----------------
    async def quota(self) -> PanQuota:
        payload = await self._call(
            "/api/quota", params={"checkfree": 1, "checkexpire": 1}
        )
        if not payload:
            return PanQuota()
        return PanQuota(
            total=int(payload.get("total") or 0), used=int(payload.get("used") or 0)
        )

    # ---------------- 文件管理 ----------------
    async def _filemanager(self, opera: str, filelist: Any) -> bool:
        """文件管理统一入口（rename/move/copy/delete 都走它）。"""
        token = await self._token()
        if not token:
            logger.warning("百度网盘取不到 bdstoken，写操作无法进行（Cookie 可能已过期）")
            return False
        payload = await self._call(
            "/api/filemanager",
            method="POST",
            params={"opera": opera, "async": 2, "onnest": "fail", "bdstoken": token},
            data={"filelist": json.dumps(filelist, ensure_ascii=False)},
        )
        return payload is not None

    async def mkdir(self, path: str) -> bool:
        target = self.normalize_path(path)
        if target == "/":
            return False
        token = await self._token()
        if not token:
            return False
        payload = await self._call(
            "/api/create",
            method="POST",
            params={"a": "commit", "bdstoken": token},
            data={"path": target, "isdir": 1, "size": 0, "block_list": "[]"},
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
        name = str(new_name or "").strip()
        target = self.normalize_path(path)
        # 拒绝带路径分隔符的新名字：那是移动，不是改名（和 local_dir 一致）
        if not name or "/" in name or "\\" in name or target == "/":
            return False
        return await self._filemanager("rename", [{"path": target, "newname": name}])

    async def move(
        self, path: str, target_dir: str, *, file_id: str | None = None
    ) -> bool:
        target = self.normalize_path(path)
        dest = self.normalize_path(target_dir)
        if target == "/" or not dest:
            return False
        name = target.rsplit("/", 1)[-1]
        return await self._filemanager(
            "move", [{"path": target, "dest": dest, "newname": name}]
        )

    async def copy(
        self, path: str, target_dir: str, *, file_id: str | None = None
    ) -> bool:
        target = self.normalize_path(path)
        dest = self.normalize_path(target_dir)
        if target == "/" or not dest:
            return False
        name = target.rsplit("/", 1)[-1]
        return await self._filemanager(
            "copy", [{"path": target, "dest": dest, "newname": name}]
        )

    async def search(self, keyword: str, *, limit: int = 50) -> list[PanFile]:
        word = str(keyword or "").strip()
        if not word:
            return []
        payload = await self._call(
            "/api/search",
            params={
                "key": word,
                "dir": "/",
                "recursion": 1,
                "page": 1,
                "num": max(1, min(int(limit or 50), 200)),
            },
        )
        files = self._parse_files((payload or {}).get("list") or [])
        return files[: max(1, min(int(limit or 50), 200))]

    # ---------------- 直链 ----------------
    async def download_url(self, path: str, *, file_id: str | None = None) -> str | None:
        """换取可直接下载的 CDN 直链。

        两步：先用 filemetas 取 ``dlink``，再**带 netdisk UA** 请求 dlink
        取 302 的 Location。只返回 dlink 是不够的 —— 下载器用自己的 UA
        请求它一定 403，那属于把失败推迟到下载阶段。
        """
        target = self.normalize_path(path)
        if target == "/":
            return None
        payload = await self._call(
            "/api/filemetas",
            params={"target": json.dumps([target], ensure_ascii=False), "dlink": 1},
        )
        items = (payload or {}).get("info") or (payload or {}).get("list") or []
        dlink = ""
        if isinstance(items, list) and items and isinstance(items[0], dict):
            dlink = str(items[0].get("dlink") or "")
        if not dlink:
            return None
        try:
            async with async_client(
                timeout=self.config.get("timeout") or 20,
                headers=self._headers(ua=NETDISK_UA),
                follow_redirects=False,
            ) as client:
                response = await client.get(dlink)
        except httpx.HTTPError as exc:
            logger.warning("百度网盘换取直链失败：%s", exc)
            # 拿不到最终地址时退回 dlink，至少让调用方有东西可试
            return dlink
        location = response.headers.get("location") or ""
        return location or dlink

    async def keep_alive(self) -> tuple[bool, str]:
        """轻量查一次容量刷新登录态。"""
        if not self.cookie:
            return False, "未配置 Cookie（请先扫码登录或导入 Cookie）"
        payload = await self._call(
            "/api/quota", params={"checkfree": 1, "checkexpire": 1}
        )
        if payload:
            return True, "百度网盘登录态正常"
        return False, "百度网盘 Cookie 可能已过期，请重新扫码登录"

    # ---------------- 转存 ----------------
    @staticmethod
    def parse_share_id(share_url: str) -> str:
        """取分享短码（``/s/1xxxx`` 里 1 之后的部分，或 ``surl=`` 参数）。"""
        raw = str(share_url or "")
        match = SHARE_RE.search(raw)
        if match:
            return match.group(1)
        match = SURL_RE.search(raw)
        return match.group(1) if match else ""

    async def save_share(
        self,
        share_url: str,
        *,
        password: str | None = None,
        target_dir: str | None = None,
    ) -> SaveResult:
        """转存百度分享（best-effort）。

        **如实说明**：百度的 ``/share/transfer`` 需要 ``shareid`` + ``uk`` +
        带密码校验换来的 ``BDCLND`` Cookie，且对非官方客户端风控很严
        （常见验证码/需登录 App 确认）。这里按公开流程实现，
        失败时给出明确原因，方便用户改用 AList 或手动转存 —— 而不是
        假装成功让追更任务半夜静默失败。
        """
        short = self.parse_share_id(share_url)
        if not short:
            return SaveResult(success=False, message="不是有效的百度网盘分享链接")
        if not self.cookie:
            return SaveResult(
                success=False, message="未配置百度网盘 Cookie，请先扫码登录"
            )

        token = await self._token()
        if not token:
            return SaveResult(
                success=False, message="取不到 bdstoken，Cookie 可能已过期"
            )

        # 1) 有密码先校验，换 BDCLND 票据
        sekey = ""
        if password:
            verified = await fetch_json(
                f"{API_BASE}/share/verify",
                method="POST",
                params={"surl": short, "bdstoken": token, "t": "0", "channel": "chunlei", "web": 1, "clienttype": 0},
                data={"pwd": password, "vcode": "", "vcode_str": ""},
                headers=self._headers(),
                timeout=self.config.get("timeout"),
            )
            if not isinstance(verified, dict) or _as_int(verified.get("errno"), 0) != 0:
                return SaveResult(
                    success=False,
                    message="提取码校验失败（错误的提取码，或百度要求验证码）",
                )
            sekey = str(verified.get("randsk") or "")

        # 2) 取分享页拿 shareid / uk / fs_id
        headers = self._headers()
        if sekey:
            headers["Cookie"] = f"{self.cookie}; BDCLND={sekey}"
        try:
            async with async_client(
                timeout=self.config.get("timeout") or 20, headers=headers
            ) as client:
                page = await client.get(f"{API_BASE}/s/1{short}")
                text = page.text
        except httpx.HTTPError as exc:
            return SaveResult(success=False, message=f"打开分享页失败：{exc}")

        shareid = self._pick(text, r'"shareid"\s*:\s*"?(\d+)')
        uk = self._pick(text, r'"uk"\s*:\s*"?(\d+)')
        fs_ids = re.findall(r'"fs_id"\s*:\s*"?(\d+)', text)
        if not shareid or not uk or not fs_ids:
            return SaveResult(
                success=False,
                message=(
                    "分享页未返回转存所需参数（链接可能已失效、需要提取码，"
                    "或触发了百度风控）"
                ),
            )

        # 3) 提交转存
        dest = self.normalize_path(target_dir or self.root_path)
        result = await fetch_json(
            f"{API_BASE}/share/transfer",
            method="POST",
            params={
                "shareid": shareid,
                "from": uk,
                "bdstoken": token,
                "channel": "chunlei",
                "web": 1,
                "clienttype": 0,
            },
            data={
                "fsidlist": json.dumps([int(i) for i in dict.fromkeys(fs_ids)]),
                "path": dest,
            },
            headers=headers,
            timeout=self.config.get("timeout"),
        )
        if not isinstance(result, dict):
            return SaveResult(success=False, message="转存请求无响应")
        errno = _as_int(result.get("errno"), 0)
        if errno == 0:
            return SaveResult(
                success=True,
                message="已转存",
                saved_path=dest,
                file_count=len(dict.fromkeys(fs_ids)),
            )
        return SaveResult(
            success=False,
            message=self._transfer_error(errno, result),
        )

    @staticmethod
    def _pick(text: str, pattern: str) -> str:
        match = re.search(pattern, text or "")
        return match.group(1) if match else ""

    @staticmethod
    def _transfer_error(errno: int, payload: dict[str, Any]) -> str:
        """把百度的 errno 翻成人话。

        直接把 ``errno=-9`` 抛给用户毫无意义；这几个是转存最常撞上的。
        """
        table = {
            -1: "分享链接已失效",
            -3: "分享文件已被删除",
            -6: "身份验证失败（Cookie 已过期，请重新登录）",
            -8: "目标目录中已存在同名文件",
            -9: "提取码错误或链接需要提取码",
            -10: "网盘容量不足",
            12: "部分文件转存失败（可能已存在同名文件）",
            105: "链接地址格式不正确",
            -62: "触发百度风控（需要验证码，请稍后再试或改用手动转存）",
        }
        hint = table.get(errno)
        if hint:
            return hint
        return f"百度拒绝了转存请求（errno={errno} {payload.get('show_msg') or ''}）".strip()

    async def health_check(self) -> tuple[bool, str]:
        if not self.cookie:
            return False, "未配置 Cookie"
        return await self.keep_alive()
