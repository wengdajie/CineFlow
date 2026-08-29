"""115 网盘直连（Cookie 鉴权）。

115 是国内老牌网盘，影视资源分享量大，且**开放了完整的扫码登录接口**
（见 ``services/panlogin/pan115.py``），所以它是本项目里登录体验最好的网盘。

用的是 115 web 接口::

    列目录   GET  https://webapi.115.com/files?cid=<目录id>&aid=1
    容量     GET  https://webapi.115.com/files/index_info
    改名     POST https://webapi.115.com/files/edit
    移动     POST https://webapi.115.com/files/move
    删除     POST https://webapi.115.com/rb/delete
    搜索     GET  https://webapi.115.com/files/search?search_value=
    直链     GET  https://webapi.115.com/files/download?pickcode=

**转存能力如实说明**：115 的「分享转存」接口（``/share/receive``）需要
分享码 + 接收目录，且对非官方客户端风控较严。这里实现为 best-effort：
失败会返回明确原因而不是假装成功。目录浏览/改名/移动/删除/搜索/直链
都是稳定可用的部分。

> 与夸克一样，接口非公开，字段可能随官方调整变化。所有失败都优雅降级为
> 可读错误消息，不抛异常打断调用方。
"""

from __future__ import annotations

import re
from typing import Any

from app.core.logger import get_logger
from app.providers.panstorage.base import BasePanStorage, PanFile, PanQuota, SaveResult
from app.providers.registry import register
from app.utils.http import fetch_json

logger = get_logger(__name__)

WEB_API = "https://webapi.115.com"
#: 分享链接形如 https://115.com/s/xxxxx?password=1234
SHARE_RE = re.compile(r"115\.com/s/([0-9a-zA-Z_-]+)")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


@register
class Pan115Storage(BasePanStorage):
    """115 网盘。"""

    name = "pan115"
    display_name = "115 网盘"

    # 115 web 接口支持完整文件管理
    supports_rename = True
    supports_move = True
    supports_search = True
    supports_keepalive = True

    @property
    def cookie(self) -> str:
        return str(self.option("cookie") or "").strip()

    def _headers(self) -> dict[str, str]:
        return {
            "Cookie": self.cookie,
            "User-Agent": UA,
            "Referer": "https://115.com/",
        }

    async def _call(
        self,
        path: str,
        *,
        method: str = "GET",
        params: dict[str, Any] | None = None,
        data: Any = None,
    ) -> dict[str, Any] | None:
        """调 115 接口。``state=false`` 视为失败并记录原因。"""
        if not self.cookie:
            return None
        payload = await fetch_json(
            f"{WEB_API}{path}",
            method=method,
            params=params,
            data=data,
            headers=self._headers(),
            timeout=self.config.get("timeout"),
        )
        if not isinstance(payload, dict):
            return None
        if not payload.get("state"):
            logger.warning(
                "115 %s 失败：%s", path, payload.get("error") or payload.get("msg")
            )
            return None
        return payload

    # ---------------- 目录 ----------------
    async def _resolve_cid(self, path: str) -> str:
        """把路径逐级解析成 115 的目录 id（根为 ``0``）。"""
        target = self.normalize_path(path)
        cid = "0"
        if target == "/":
            return cid
        for segment in [s for s in target.split("/") if s]:
            children = await self._list_by_cid(cid)
            match = next((c for c in children if c.name == segment and c.is_dir), None)
            if not match or not match.file_id:
                return ""
            cid = match.file_id
        return cid

    async def _list_by_cid(self, cid: str) -> list[PanFile]:
        payload = await self._call(
            "/files",
            params={
                "aid": 1,
                "cid": cid,
                "o": "user_ptime",
                "asc": 0,
                "offset": 0,
                "show_dir": 1,
                "limit": 200,
                "format": "json",
            },
        )
        return self._parse_files((payload or {}).get("data") or [])

    @staticmethod
    def _parse_files(items: Any) -> list[PanFile]:
        files: list[PanFile] = []
        if not isinstance(items, list):
            return files
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("n") or item.get("file_name") or "")
            if not name:
                continue
            # 115 用 "cid" 表目录、"fid" 表文件；目录项没有 fid
            is_dir = not item.get("fid")
            file_id = str(item.get("fid") or item.get("cid") or "")
            files.append(
                PanFile(
                    name=name,
                    path=name,
                    is_dir=is_dir,
                    size=int(item.get("s") or 0),
                    file_id=file_id,
                    modified_at=str(item.get("t") or "") or None,
                )
            )
        return files

    async def list_dir(self, path: str = "/") -> list[PanFile]:
        target = self.normalize_path(path)
        cid = await self._resolve_cid(target)
        if not cid and target != "/":
            return []
        files = await self._list_by_cid(cid or "0")
        for item in files:
            item.path = self.join_path(target, item.name)
        files.sort(key=lambda f: (not f.is_dir, f.name.lower()))
        return files

    # ---------------- 容量 ----------------
    async def quota(self) -> PanQuota:
        payload = await self._call("/files/index_info")
        info = (payload or {}).get("data") or {}
        space = info.get("space_info") if isinstance(info, dict) else None
        if not isinstance(space, dict):
            return PanQuota()
        try:
            total = int(float((space.get("all_total") or {}).get("size") or 0))
            used = int(float((space.get("all_use") or {}).get("size") or 0))
        except (TypeError, ValueError, AttributeError):
            return PanQuota()
        return PanQuota(total=total, used=used)

    # ---------------- 文件管理 ----------------
    async def mkdir(self, path: str) -> bool:
        target = self.normalize_path(path)
        parent = self.normalize_path("/".join(target.split("/")[:-1]) or "/")
        name = target.split("/")[-1]
        if not name:
            return False
        pid = await self._resolve_cid(parent)
        if not pid and parent != "/":
            return False
        payload = await self._call(
            "/files/add", method="POST", data={"pid": pid or "0", "cname": name}
        )
        return bool(payload)

    async def _find(self, path: str) -> PanFile | None:
        """定位一个文件/目录（返回带 file_id 的项）。"""
        target = self.normalize_path(path)
        if target == "/":
            return None
        parent = self.normalize_path("/".join(target.split("/")[:-1]) or "/")
        name = target.split("/")[-1]
        for item in await self.list_dir(parent):
            if item.name == name:
                return item
        return None

    async def delete(self, path: str, *, file_id: str | None = None) -> bool:
        fid = file_id
        if not fid:
            found = await self._find(path)
            fid = found.file_id if found else None
        if not fid:
            return False
        payload = await self._call(
            "/rb/delete", method="POST", data={"fid[0]": fid, "ignore_warn": 1}
        )
        return bool(payload)

    async def rename(
        self, path: str, new_name: str, *, file_id: str | None = None
    ) -> tuple[bool, str]:
        if "/" in new_name or "\\" in new_name:
            return False, "名称不能包含路径分隔符"
        fid = file_id
        if not fid:
            found = await self._find(path)
            fid = found.file_id if found else None
        if not fid:
            return False, "找不到该文件"
        payload = await self._call(
            "/files/edit", method="POST", data={"fid": fid, "file_name": new_name}
        )
        return (True, "已改名") if payload else (False, "115 拒绝了改名请求")

    async def move(
        self, path: str, target_dir: str, *, file_id: str | None = None
    ) -> tuple[bool, str]:
        fid = file_id
        if not fid:
            found = await self._find(path)
            fid = found.file_id if found else None
        if not fid:
            return False, "找不到该文件"
        pid = await self._resolve_cid(target_dir)
        if not pid and self.normalize_path(target_dir) != "/":
            return False, "目标目录不存在"
        payload = await self._call(
            "/files/move", method="POST", data={"fid[0]": fid, "pid": pid or "0"}
        )
        return (True, "已移动") if payload else (False, "115 拒绝了移动请求")

    async def copy(
        self, path: str, target_dir: str, *, file_id: str | None = None
    ) -> tuple[bool, str]:
        fid = file_id
        if not fid:
            found = await self._find(path)
            fid = found.file_id if found else None
        if not fid:
            return False, "找不到该文件"
        pid = await self._resolve_cid(target_dir)
        if not pid and self.normalize_path(target_dir) != "/":
            return False, "目标目录不存在"
        payload = await self._call(
            "/files/copy", method="POST", data={"fid[0]": fid, "pid": pid or "0"}
        )
        return (True, "已复制") if payload else (False, "115 拒绝了复制请求")

    async def search(self, keyword: str, *, limit: int = 50) -> list[PanFile]:
        if not keyword.strip():
            return []
        payload = await self._call(
            "/files/search",
            params={
                "aid": 1,
                "cid": 0,
                "search_value": keyword.strip(),
                "offset": 0,
                "limit": max(1, min(limit, 200)),
                "format": "json",
            },
        )
        return self._parse_files((payload or {}).get("data") or [])

    async def download_url(self, path: str, *, file_id: str | None = None) -> str | None:
        found = await self._find(path)
        if not found or found.is_dir:
            return None
        # 115 直链要 pickcode；列表接口返回的是 fid，需要再查一次详情
        detail = await self._call("/files/file", method="POST", data={"file_id": found.file_id})
        items = (detail or {}).get("data") or []
        pickcode = ""
        if isinstance(items, list) and items and isinstance(items[0], dict):
            pickcode = str(items[0].get("pick_code") or "")
        if not pickcode:
            return None
        payload = await self._call("/files/download", params={"pickcode": pickcode})
        info = (payload or {}).get("file_url") or (payload or {}).get("data")
        if isinstance(info, dict):
            return str(info.get("url") or "") or None
        return str(info or "") or None

    async def keep_alive(self) -> tuple[bool, str]:
        """轻量调用 index_info 刷新登录态。"""
        if not self.cookie:
            return False, "未配置 Cookie"
        payload = await self._call("/files/index_info")
        if payload:
            return True, "115 登录态正常"
        return False, "115 Cookie 可能已过期，请重新扫码登录"

    # ---------------- 转存 ----------------
    @staticmethod
    def parse_share_id(share_url: str) -> str:
        match = SHARE_RE.search(share_url or "")
        return match.group(1) if match else ""

    async def save_share(
        self,
        share_url: str,
        *,
        password: str | None = None,
        target_dir: str | None = None,
    ) -> SaveResult:
        """转存 115 分享。

        115 对非官方客户端的分享转存风控较严，这里是 best-effort：
        失败时给出明确原因，方便用户改用 AList 或手动转存。
        """
        share_code = self.parse_share_id(share_url)
        if not share_code:
            return SaveResult(success=False, message="不是有效的 115 分享链接")
        if not self.cookie:
            return SaveResult(success=False, message="未配置 115 Cookie，请先扫码登录")
        cid = await self._resolve_cid(target_dir or self.root_path)
        payload = await self._call(
            "/share/receive",
            method="POST",
            data={
                "share_code": share_code,
                "receive_code": password or "",
                "cid": cid or "0",
            },
        )
        if payload:
            return SaveResult(success=True, message="已提交转存")
        return SaveResult(
            success=False,
            message="115 拒绝了转存请求（可能是提取码错误、容量不足或风控限制）",
        )

    async def health_check(self) -> tuple[bool, str]:
        if not self.cookie:
            return False, "未配置 Cookie"
        return await self.keep_alive()
