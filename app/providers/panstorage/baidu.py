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
   缺了会直接 ``errno=-6``（身份验证失败）。这里取一次缓存在实例上，
   模板变量接口取不到时**再从 ``/disk/home`` 的 HTML 里正则兜底**。
   实测：无 Cookie 与垃圾 Cookie 都返回 ``errno=-6``、``result=[]``，
   两者完全同形 —— 所以错误消息必须按 ``_last_errno`` + 有没有 Cookie
   分开说，否则「没登录」会被误报成「登录过期」，用户照着提示白忙一场。
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
from urllib.parse import urlsplit

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
from app.utils.http import async_client, fetch_json

logger = get_logger(__name__)

#: 网盘首页 HTML 里的 bdstoken（模板变量接口拿不到时的兜底取法）
_BDSTOKEN_RE = re.compile(r'bdstoken["\s:=]+([0-9a-f]{32})')

API_BASE = "https://pan.baidu.com"
#: 分享链接的三种真实形态（实测用户粘贴的内容）：
#:   https://pan.baidu.com/s/1AbCdEf          —— 最常见，短码带前导 1
#:   https://pan.baidu.com/s/xKq9v2Lm         —— 新版短码**没有**前导 1
#:   https://pan.baidu.com/share/init?surl=AbCdEf
#:
#: ⚠️ 旧正则写死了 ``/s/1``（把 1 当分隔符吃掉），于是：
#:   ① 不以 1 开头的新短码被判「不是有效的百度网盘分享链接」；
#:   ② 域名写死 pan.baidu.com，yun.baidu.com / eyun.baidu.com 全部误杀。
#: 这两类都是**能打开的正常链接**，却在入口就被拒——用户看到的就是
#: 「网盘资源下载提示：不是有效的百度网盘分享链接」。
#:
#: 现在：域名放宽到 (pan|yun|eyun).baidu.com，短码整体捕获后再剥前导 1。
#: 前导 1 必须剥掉，因为下游 ``surl`` 参数要的是不含它的部分。
SHARE_RE = re.compile(r"(?:pan|yun|eyun)\.baidu\.com/s/([0-9a-zA-Z_-]+)")
SURL_RE = re.compile(r"[?&]surl=([0-9a-zA-Z_-]+)")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
#: 取 dlink 真实地址时必须用这个 UA，否则 403
NETDISK_UA = "netdisk;P2SP;3.0.20.11"

#: 列分享内容的单页条数 / 最多翻几页 / 递归深度 / 最多收集多少文件。
#: 与夸克实现同一套闸门理由：分享结构是别人给的，不能假设它有多浅多小。
_SHARE_PAGE_SIZE = 100
_SHARE_MAX_PAGES = 50
_SHARE_MAX_DEPTH = 4
_SHARE_MAX_FILES = 3000


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

    supports_strict_list = True
    name = "baidu"
    display_name = "百度网盘"

    #: 百度 CDN 直链按 UA 鉴权：换成浏览器/aria2 默认 UA 时，
    #: 小文件能过、**超过 ~50MB 直接 403**。投给下载器时必须一起带上。
    direct_link_user_agent = NETDISK_UA

    # web 接口支持完整文件管理
    supports_rename = True
    supports_move = True
    supports_search = True
    supports_keepalive = True
    supports_share_download = True

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._bdstoken: str = ""
        #: 最近一次 _call 的 errno（None = 没发出去或响应不是 JSON）
        self._last_errno: int | None = None
        #: 最后一次 HTTP 状态码。Cookie 过期时百度回 400，必须靠它区分
        #: 「登录态失效」与「网络不通」——两者以前都报成网络问题。
        self._last_http_status: int | None = None

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

    def download_headers(self, url: str) -> dict[str, str]:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        headers = {"Referer": "https://pan.baidu.com/"}
        if parsed.scheme == "https" and any(host == domain or host.endswith("." + domain)
                                             for domain in ("baidu.com", "baidupcs.com")):
            headers["Cookie"] = getattr(self, "_share_download_cookie", self.cookie)
        return headers

    async def _call(
        self,
        path: str,
        *,
        method: str = "GET",
        params: dict[str, Any] | None = None,
        data: Any = None,
    ) -> dict[str, Any] | None:
        """调百度接口。``errno != 0`` 视为失败并记录原因。

        兼容既有 19 处调用点的语义（失败返回 ``None``），但**把最后一次
        errno 留在 ``self._last_errno``** —— 上层要区分「Cookie 没配」
        「Cookie 过期」「被风控」时需要它，而 ``None`` 三者同形（ADR-82）。
        """
        self._last_errno = None
        self._last_http_status = None
        if not self.cookie:
            return None
        merged = {"clienttype": 0, "app_id": 250528, "web": 1, **(params or {})}
        # ⚠️ 不能用 fetch_json：它把 4xx 也归成 None。
        #
        # 实测：**Cookie 过期时百度 /api/quota 回的是 HTTP 400**（不是 200+errno）。
        # 走 fetch_json 时 raise_for_status() 抛异常 → 返回 None → `_last_errno`
        # 停留在 None → 上层判成「连不上百度网盘（网络或代理问题），登录态未知」。
        # 于是用户看到的是"网络问题"，实际是 Cookie 死了；重新扫码也没用，
        # 因为提示把他引向了错误的方向。这里自己发请求以保住状态码。
        url = f"{API_BASE}{path}"
        try:
            async with async_client(
                timeout=self.config.get("timeout"), headers=self._headers()
            ) as client:
                response = await client.request(
                    method, url, params=merged, data=data
                )
            self._last_http_status = response.status_code
            payload = response.json()
        except Exception as exc:  # 真正的网络层失败才是"连不上"
            logger.warning("百度网盘 %s 请求失败：%s", path, exc)
            return None
        if not isinstance(payload, dict):
            return None
        errno = _as_int(payload.get("errno"), 0)
        self._last_errno = errno
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
        """取并缓存 bdstoken（写操作的 CSRF 令牌）。

        **两条取法，缺一不可**（实测）：模板变量接口是主路；它在某些
        账号态下会返回 ``errno=0`` 但 ``result`` 里没有 bdstoken，
        此时从 ``/disk/home`` 的 HTML 里正则捞一次仍能拿到。
        只有两条都空才算真失败。
        """
        if self._bdstoken:
            return self._bdstoken
        payload = await self._call(
            "/api/gettemplatevariable", params={"fields": json.dumps(["bdstoken"])}
        )
        result = (payload or {}).get("result") or {}
        if isinstance(result, dict):
            self._bdstoken = str(result.get("bdstoken") or "")
        if not self._bdstoken:
            self._bdstoken = await self._token_from_html()
        return self._bdstoken

    async def _token_from_html(self) -> str:
        """兜底：从网盘首页 HTML 里正则提取 bdstoken。

        未登录时百度会 302 到 ``/login?redirecturl=...``，那个页面里
        **没有** bdstoken —— 实测确认过，所以捞不到就是没登录，
        不必再重试。
        """
        if not self.cookie:
            return ""
        try:
            async with async_client(
                timeout=self.config.get("timeout") or 15, follow_redirects=True
            ) as client:
                response = await client.get(
                    f"{API_BASE}/disk/home", headers=self._headers()
                )
        except (httpx.HTTPError, OSError) as exc:  # 网络层问题，不是登录问题
            logger.warning("百度网盘取首页失败：%s", exc)
            return ""
        matched = _BDSTOKEN_RE.search(response.text or "")
        return matched.group(1) if matched else ""

    def _auth_hint(self, action: str) -> str:
        """把「取不到 bdstoken」翻成**可执行**的下一步。

        原来一律说「Cookie 可能已过期」，但实测 ``errno=-6`` 在
        **没配 Cookie** 和 **Cookie 过期** 两种情况下完全相同，
        而用户该做的事不一样。这里按已知信号分开说。
        """
        if not self.cookie:
            return f"{action}失败：未配置百度网盘 Cookie，请先在「网盘管理」扫码登录"
        errno = self._last_errno
        if errno == -6:
            return (
                f"{action}失败：百度返回 errno=-6（身份校验不通过），"
                "Cookie 已失效或缺少 BDUSS/STOKEN 字段，请重新扫码登录"
            )
        if errno is None:
            return (
                f"{action}失败：连不上百度网盘或响应不是 JSON，"
                "请检查网络/代理后重试"
            )
        return (
            f"{action}失败：取不到 bdstoken（百度 errno={errno}），"
            "多为登录态异常或触发风控，请重新扫码登录"
        )

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

    async def list_dir(self, path: str = "/", *, strict: bool = False) -> list[PanFile]:
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
        if strict and (not payload or not isinstance(payload.get("list"), list)):
            raise OSError("网盘目录读取失败或响应无效")
        files = self._parse_files((payload or {}).get("list") or [])
        if strict and (len(payload["list"]) >= 1000 or len(files) != len(payload["list"])):
            raise IncompleteDirectory(files)
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
            logger.warning("百度网盘%s", self._auth_hint("写操作"))
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
        if self._last_errno == -6:
            return False, "百度网盘 Cookie 已过期或失效（errno=-6），请重新扫码登录"
        # HTTP 4xx：请求到达了百度但被拒——实测 Cookie 过期就是 400。
        # 这**不是**网络问题，不能让用户去查代理。
        if self._last_http_status is not None and 400 <= self._last_http_status < 500:
            return False, (
                f"百度网盘登录态已失效（HTTP {self._last_http_status}）。"
                "网页 Cookie 风控很严、往往几小时就废；"
                "建议改用「百度开放平台授权」（设置 → 网盘 → 百度 → 开放平台授权），"
                "用 refresh_token 长期免扫码"
            )
        if self._last_errno is None:
            return False, "连不上百度网盘（网络或代理问题），登录态未知"
        return False, f"百度网盘保活失败（errno={self._last_errno}），可能触发风控"

    # ---------------- 转存 ----------------
    @staticmethod
    def parse_share_id(share_url: str) -> str:
        """取分享短码（``/s/1xxxx`` 里 1 之后的部分，或 ``surl=`` 参数）。"""
        raw = str(share_url or "")
        match = SHARE_RE.search(raw)
        if match:
            code = match.group(1)
            # ``/s/1xxxx`` 里的前导 1 是百度的固定前缀，不属于短码本身；
            # 新版短码没有它。两种都要归一成「不含前导 1」的形式。
            return code[1:] if code.startswith("1") and len(code) > 1 else code
        match = SURL_RE.search(raw)
        return match.group(1) if match else ""

    @staticmethod
    def parse_share_code(share_url: str) -> str:
        """取分享短码的**原始形态**（含 ``/s/`` 后面的第一个字符）。

        与 :meth:`parse_share_id` 的区别，是这个项目踩过的一个真实坑：

        | 用户粘的链接 | ``parse_share_id``（给 ``surl=`` 用） | 分享页真实地址 |
        |---|---|---|
        | ``/s/1AbCdEf``  | ``AbCdEf``   | ``/s/1AbCdEf`` |
        | ``/s/xKq9v2Lm`` | ``xKq9v2Lm`` | ``/s/xKq9v2Lm`` |

        旧代码只有 ``parse_share_id``，然后把分享页地址写成
        ``f"/s/1{short}"`` —— 对第一行是对的，对第二行拼出了
        ``/s/1xKq9v2Lm`` 这个**不存在的页面**。百度对不存在的分享页返回
        一个正常的 HTML（不是 404），于是正则取不到 ``shareid``/``uk``，
        用户看到的是「分享页未返回转存所需参数（链接可能已失效…）」——
        一条把**代码拼错了 URL** 说成**链接失效**的错误消息，
        照着提示换链接永远修不好。
        """
        raw = str(share_url or "")
        match = SHARE_RE.search(raw)
        if match:
            return match.group(1)
        match = SURL_RE.search(raw)
        # surl= 形态本身就是不含前导 1 的，回填时要补上（百度页面地址的固定前缀）
        return f"1{match.group(1)}" if match else ""

    def share_page_url(self, share_url: str) -> str:
        """分享页地址。取不到短码时返回空串。"""
        code = self.parse_share_code(share_url)
        return f"{API_BASE}/s/{code}" if code else ""

    async def _verify_password(self, short: str, token: str, password: str) -> tuple[str, str]:
        """校验提取码，换 ``BDCLND`` 票据。返回 ``(sekey, 错误消息)``。"""
        verified = await fetch_json(
            f"{API_BASE}/share/verify",
            method="POST",
            params={
                "surl": short,
                "bdstoken": token,
                "t": "0",
                "channel": "chunlei",
                "web": 1,
                "clienttype": 0,
            },
            data={"pwd": password, "vcode": "", "vcode_str": ""},
            headers=self._headers(),
            timeout=self.config.get("timeout"),
        )
        if not isinstance(verified, dict) or _as_int(verified.get("errno"), 0) != 0:
            return "", "提取码校验失败（错误的提取码，或百度要求验证码）"
        return str(verified.get("randsk") or ""), ""

    async def _share_context(
        self, share_url: str, password: str | None
    ) -> tuple[dict[str, Any], str]:
        """取转存/列举分享都要用的上下文。

        返回 ``({shareid, uk, sekey, fs_ids, headers}, 错误消息)``；
        错误消息非空时第一个值不可用。把这段从 :meth:`save_share` 里
        抽出来，是因为 :meth:`list_share` 需要**完全一样**的前置步骤，
        复制一遍必然会漏掉其中一步（比如忘了带 BDCLND）。
        """
        short = self.parse_share_id(share_url)
        page_url = self.share_page_url(share_url)
        if not short or not page_url:
            return {}, "不是有效的百度网盘分享链接"
        if not self.cookie:
            return {}, "未配置百度网盘 Cookie，请先扫码登录"
        token = await self._token()
        if not token:
            return {}, self._auth_hint("转存")

        sekey = ""
        if password:
            sekey, error = await self._verify_password(short, token, password)
            if error:
                return {}, error

        headers = self._headers()
        if sekey:
            headers["Cookie"] = f"{self.cookie}; BDCLND={sekey}"
        try:
            async with async_client(
                timeout=self.config.get("timeout") or 20, headers=headers
            ) as client:
                page = await client.get(page_url)
                text = page.text
        except httpx.HTTPError as exc:
            return {}, f"打开分享页失败：{exc}"

        shareid = self._pick(text, r'"shareid"\s*:\s*"?(\d+)')
        uk = self._pick(text, r'"uk"\s*:\s*"?(\d+)')
        fs_ids = list(dict.fromkeys(re.findall(r'"fs_id"\s*:\s*"?(\d+)', text)))
        if not shareid or not uk or not fs_ids:
            need_pwd = "请输入提取码" in text or "init?surl" in text
            if need_pwd and not password:
                return {}, "该分享需要提取码，请填写后重试"
            return {}, (
                "分享页未返回转存所需参数（链接可能已失效、需要提取码，"
                "或触发了百度风控）"
            )
        return {
            "shareid": shareid,
            "uk": uk,
            "sekey": sekey,
            "fs_ids": fs_ids,
            "headers": headers,
            "bdstoken": token,
            "sign": self._pick(text, r'"sign"\s*:\s*"([^"]+)"'),
            "timestamp": self._pick(text, r'"timestamp"\s*:\s*"?(\d+)'),
        }, ""

    async def share_downloads(self, share_url: str, *, password: str | None = None) -> list[PanFile]:
        context, error = await self._share_context(share_url, password)
        if error:
            raise ValueError(error.replace("转存", "读取分享"))
        if not context.get("sign") or not context.get("timestamp"):
            raise ValueError("百度分享未返回直接下载授权，请在官方页面下载，或明确转存后从盘内下载")
        self._share_download_cookie = context["headers"].get("Cookie", self.cookie)
        files, queue, seen = [], [("/", 0)], {"/"}
        while queue:
            directory, depth = queue.pop(0)
            items = await self._share_list_dir(context, directory)
            if getattr(self, "_share_read_incomplete", False):
                raise ValueError("百度分享目录未完整读取，未提交部分下载")
            for raw in items:
                name = str(raw.get("server_filename") or raw.get("filename") or "")
                if not name or "/" in name or "\\" in name or name in (".", ".."):
                    raise ValueError("百度返回了无效文件名")
                path = self.join_path(directory, name)
                if _as_int(raw.get("isdir"), 0):
                    if depth >= 8 or path in seen or len(seen) >= 200:
                        raise ValueError("百度分享目录过深、过多或重复")
                    seen.add(path)
                    queue.append((path, depth + 1))
                else:
                    if len(files) >= 200:
                        raise ValueError("分享超过 200 个文件，请选择较小的分享")
                    files.append(PanFile(name, path, file_id=str(raw.get("fs_id") or ""), size=_as_int(raw.get("size"), 0)))
        if not files:
            return []
        payload = await fetch_json(f"{API_BASE}/api/sharedownload", method="POST",
                                   params={"sign": context["sign"], "timestamp": context["timestamp"],
                                           "bdstoken": context["bdstoken"], "web": 1, "clienttype": 0},
                                   data={"encrypt": 0, "product": "share", "uk": context["uk"],
                                         "primaryid": context["shareid"], "fid_list": json.dumps([int(f.file_id) for f in files]),
                                         "extra": json.dumps({"sekey": context.get("sekey") or ""})},
                                   headers={**context["headers"], "User-Agent": self.direct_link_user_agent})
        if not isinstance(payload, dict) or payload.get("errno") != 0:
            raise ValueError("百度未授权直接下载该分享，请检查登录状态、下载权限及分享有效性；未执行转存")
        links = {str(raw.get("fs_id")): raw.get("dlink") for raw in payload.get("list", [])}
        for item in files:
            item.download_url = links.get(item.file_id)
        return files

    async def list_share(
        self, share_url: str, *, password: str | None = None
    ) -> list[PanFile]:
        """列出百度分享**内部**的文件清单（供「分享追更」做增量判断）。

        **为什么必须有它**：基类的默认实现返回空列表，而
        ``pan_subscribe.check_one()`` 看到空列表就走「退化路径」——
        整体转存一次、往 ``saved_files`` 里塞一个 ``__whole_share__``
        哨兵，从此**永不再转**。也就是说百度分享的「追更」实测只能更新
        一次，之后每轮巡检都回「该网盘不支持增量，整体转存已完成过」。
        连载剧第 13 集起全部拿不到，而且**不报错**。

        用的是分享列举接口（与浏览器打开分享页时的请求同源）::

            GET /share/list?shareid=&uk=&dir=<路径>&page=&num=&sekey=

        递归进子目录，深度/总数设闸门，理由同夸克实现。
        """
        context, error = await self._share_context(share_url, password)
        if error:
            logger.info("列举百度分享失败：%s", error)
            return []

        files: list[PanFile] = []
        queue: list[tuple[str, int]] = [("/", 0)]
        seen: set[str] = {"/"}
        truncated = False

        while queue and not truncated:
            directory, depth = queue.pop(0)
            for item in await self._share_list_dir(context, directory):
                name = str(item.get("server_filename") or item.get("filename") or "")
                if not name:
                    continue
                is_dir = _as_int(item.get("isdir"), 0) == 1
                path = self.join_path(directory, name)
                fs_id = str(item.get("fs_id") or "")
                if is_dir:
                    files.append(
                        PanFile(name=name, path=path, is_dir=True, file_id=fs_id or None)
                    )
                    if depth + 1 <= _SHARE_MAX_DEPTH and path not in seen:
                        seen.add(path)
                        queue.append((path, depth + 1))
                    continue
                files.append(
                    PanFile(
                        name=name,
                        path=path,
                        is_dir=False,
                        size=_as_int(item.get("size"), 0) or 0,
                        file_id=fs_id or None,
                        # 转存要按 fs_id 提交；父目录用来算落地后的真实路径
                        extra={"share_dir": directory},
                    )
                )
                if len(files) >= _SHARE_MAX_FILES:
                    truncated = True
                    break

        if truncated:
            logger.warning(
                "百度分享文件数超过 %d，只列出了前一部分（增量判断可能不完整）",
                _SHARE_MAX_FILES,
            )
        return files

    async def _share_list_dir(
        self, context: dict[str, Any], directory: str
    ) -> list[dict[str, Any]]:
        """列分享内某一层目录，翻页到底。"""
        collected: list[dict[str, Any]] = []
        self._share_read_incomplete = False
        page = 1
        while page <= _SHARE_MAX_PAGES:
            params = {
                "shareid": context["shareid"],
                "uk": context["uk"],
                "order": "other",
                "desc": 1,
                "showempty": 0,
                "web": 1,
                "clienttype": 0,
                "channel": "chunlei",
                "page": page,
                "num": _SHARE_PAGE_SIZE,
                "dir": directory,
            }
            if directory == "/":
                # 根目录要显式声明，否则百度回 errno=-9（参数错误）
                params["root"] = 1
            if context.get("sekey"):
                params["sekey"] = context["sekey"]
            payload = await fetch_json(
                f"{API_BASE}/share/list",
                params=params,
                headers=context["headers"],
                timeout=self.config.get("timeout"),
            )
            if not isinstance(payload, dict):
                self._share_read_incomplete = True
                break
            if _as_int(payload.get("errno"), 0) != 0:
                self._share_read_incomplete = True
                logger.info(
                    "百度分享列举 %s 返回 errno=%s", directory, payload.get("errno")
                )
                break
            batch = payload.get("list")
            if not isinstance(batch, list):
                self._share_read_incomplete = True
                break
            if not batch:
                break
            collected.extend(item for item in batch if isinstance(item, dict))
            if len(batch) < _SHARE_PAGE_SIZE:
                break
            page += 1
        if page > _SHARE_MAX_PAGES:
            self._share_read_incomplete = True
        return collected

    async def save_share_files(
        self,
        share_url: str,
        files: list[PanFile],
        *,
        password: str | None = None,
        target_dir: str | None = None,
    ) -> SaveResult:
        """只转存分享内指定的若干文件（增量转存）。

        基类默认实现会退回**整体转存**，对百度是错的：整体转存会把
        已经存过的集数再存一遍，网盘里堆出一串 ``xxx(1).mp4``。
        这里按 ``fs_id`` 精确提交。
        """
        if not files:
            return SaveResult(False, "没有需要转存的文件")
        fs_ids = [str(item.file_id) for item in files if item.file_id]
        if not fs_ids:
            return SaveResult(False, "选中的文件缺少 fs_id，无法转存")
        context, error = await self._share_context(share_url, password)
        if error:
            return SaveResult(False, error)
        return await self._submit_transfer(context, fs_ids, target_dir)

    async def _submit_transfer(
        self, context: dict[str, Any], fs_ids: list[str], target_dir: str | None
    ) -> SaveResult:
        """提交 ``/share/transfer``。整体转存与增量转存共用这一段。"""
        dest = self.normalize_path(target_dir or self.root_path)
        unique = list(dict.fromkeys(fs_ids))
        result = await fetch_json(
            f"{API_BASE}/share/transfer",
            method="POST",
            params={
                "shareid": context["shareid"],
                "from": context["uk"],
                "bdstoken": context["bdstoken"],
                "channel": "chunlei",
                "web": 1,
                "clienttype": 0,
            },
            data={
                "fsidlist": json.dumps([int(i) for i in unique]),
                "path": dest,
            },
            headers=context["headers"],
            timeout=self.config.get("timeout"),
        )
        if not isinstance(result, dict):
            return SaveResult(success=False, message="转存请求无响应")
        errno = _as_int(result.get("errno"), 0)
        if errno == 0:
            return SaveResult(
                success=True,
                message=f"已转存 {len(unique)} 个文件",
                saved_path=dest,
                file_count=len(unique),
                saved_files=[
                    str(item["to"]) for item in (result.get("extra") or {}).get("list", [])
                    if isinstance(item, dict) and item.get("to")
                ],
            )
        return SaveResult(success=False, message=self._transfer_error(errno, result))

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
        context, error = await self._share_context(share_url, password)
        if error:
            return SaveResult(success=False, message=error)
        return await self._submit_transfer(context, context["fs_ids"], target_dir)

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
