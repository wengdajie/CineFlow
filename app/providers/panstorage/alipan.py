"""阿里云盘原文件下载；账号令牌用于读取，不隐式转存分享。"""

from __future__ import annotations

import re
import time
from typing import Any

from app.providers.panstorage.base import BasePanStorage, PanFile, SaveResult
from app.providers.registry import register
from app.utils.http import fetch_json

API = "https://api.alipan.com"
SHARE_RE = re.compile(r"(?:alipan|aliyundrive)\.com/s/([A-Za-z0-9]+)")


@register
class AlipanStorage(BasePanStorage):
    """阿里云盘（Web 账号令牌）。"""

    name = "alipan"
    display_name = "阿里云盘"
    supports_save = False
    supports_delete = False
    supports_strict_list = True
    supports_share_download = True

    def download_headers(self, url: str) -> dict[str, str]:
        return {"Referer": "https://www.alipan.com/"}

    async def _access(self, *, refresh: bool = False) -> str:
        access = str(self.option("access_token") or self.config.get("api_key") or "")
        if access and not refresh and float(self.option("expires_at") or 0) > time.time() + 60:
            return access
        token = self.option("refresh_token") or self.config.get("password")
        if not token:
            if access and not refresh:
                return access
            raise ValueError("阿里云盘账号未授权或令牌已失效，请更新账号令牌")
        data = await fetch_json(
            "https://auth.alipan.com/v2/account/token",
            method="POST",
            json_body={"refresh_token": token, "grant_type": "refresh_token"},
        )
        if not isinstance(data, dict) or not data.get("access_token"):
            raise ValueError("阿里云盘账号授权已失效，请更新 refresh_token")
        options = {
            key: value
            for key, value in (self.config.get("options") or {}).items()
            if key not in ("access_token", "refresh_token")
        }
        options["expires_at"] = time.time() + int(data.get("expires_in") or 7200)
        self.config["options"] = options
        self.config["api_key"] = data["access_token"]
        self.config["password"] = data.get("refresh_token") or token
        if self.config.get("id"):
            from app.db.models import SiteConfig
            from app.db.session import session_scope

            with session_scope() as session:
                site = session.get(SiteConfig, self.config["id"])
                if site:
                    site.options = options
                    site.api_key = data["access_token"]
                    site.password = self.config["password"]
        return str(data["access_token"])

    async def _call(
        self, path: str, body: dict[str, Any], *, share_token: str = ""
    ) -> dict[str, Any]:
        for attempt in range(2):
            headers = {"Authorization": f"Bearer {await self._access(refresh=bool(attempt))}"}
            if share_token:
                headers["x-share-token"] = share_token
            data = await fetch_json(
                API + path,
                method="POST",
                json_body=body,
                headers=headers,
                timeout=self.config.get("timeout"),
            )
            if isinstance(data, dict) and not data.get("code"):
                return data
            if not isinstance(data, dict) or data.get("code") not in (
                "AccessTokenInvalid",
                "AccessTokenExpired",
            ):
                break
        raise ValueError("阿里云盘读取失败，请检查账号授权、提取码及文件下载权限")

    async def _items(
        self, parent: str, *, share_id: str = "", share_token: str = ""
    ) -> list[dict[str, Any]]:
        body: dict[str, Any] = {"parent_file_id": parent, "limit": 200, "marker": ""}
        if share_id:
            body["share_id"] = share_id
        else:
            drive = await self._call("/v2/user/get", {})
            body["drive_id"] = drive.get("resource_drive_id") or drive.get("default_drive_id")
        items = []
        seen = set()
        for _ in range(50):
            data = await self._call("/adrive/v3/file/list", body, share_token=share_token)
            batch = data.get("items")
            if not isinstance(batch, list):
                raise ValueError("阿里云盘未返回完整文件列表")
            items.extend(batch)
            marker = data.get("next_marker") or ""
            if not marker:
                return items
            if marker in seen:
                break
            seen.add(marker)
            body["marker"] = marker
        raise ValueError("阿里云盘目录分页过多或重复，未提交部分下载")

    @staticmethod
    def _file(item: dict[str, Any], prefix: str) -> PanFile:
        name = str(item.get("name") or "")
        if (
            not name
            or "/" in name
            or "\\" in name
            or name in (".", "..")
            or not item.get("file_id")
        ):
            raise ValueError("阿里云盘返回了无效文件信息")
        return PanFile(
            name,
            prefix.rstrip("/") + "/" + name,
            is_dir=item.get("type") == "folder",
            size=int(item.get("size") or 0),
            file_id=str(item["file_id"]),
            extra={"drive_id": item.get("drive_id")},
        )

    async def _folder_id(self, path: str) -> str:
        parent, prefix = "root", ""
        for part in self.normalize_path(path).strip("/").split("/"):
            if not part:
                continue
            entries = [self._file(item, prefix) for item in await self._items(parent)]
            found = next((entry for entry in entries if entry.name == part and entry.is_dir), None)
            if not found:
                raise ValueError("阿里云盘目录不存在")
            parent, prefix = found.file_id, found.path
        return parent

    async def list_dir(self, path: str = "/", *, strict: bool = False) -> list[PanFile]:
        return [self._file(item, path) for item in await self._items(await self._folder_id(path))]

    async def download_url(self, path: str, *, file_id: str | None = None) -> str | None:
        parent = path.rsplit("/", 1)[0] or "/"
        found = next(
            (
                entry
                for entry in await self.list_dir(parent)
                if (entry.file_id == file_id if file_id else entry.path == path)
                and not entry.is_dir
            ),
            None,
        )
        if not found:
            return None
        data = await self._call(
            "/v2/file/get_download_url",
            {"drive_id": found.extra["drive_id"], "file_id": found.file_id, "expire_sec": 600},
        )
        return data.get("url") or data.get("download_url")

    async def share_downloads(
        self, share_url: str, *, password: str | None = None
    ) -> list[PanFile]:
        match = SHARE_RE.search(share_url)
        if not match:
            raise ValueError("不是有效的阿里云盘分享")
        share_id = match[1]
        token_data = await self._call(
            "/v2/share_link/get_share_token", {"share_id": share_id, "share_pwd": password or ""}
        )
        token = token_data.get("share_token")
        if not token:
            raise ValueError("阿里云盘分享已失效或提取码不正确")
        files, queue, seen = [], [("root", "", 0)], {"root"}
        while queue:
            parent, prefix, depth = queue.pop(0)
            for raw in await self._items(parent, share_id=share_id, share_token=token):
                item = self._file(raw, prefix)
                if item.is_dir:
                    if depth >= 8 or item.file_id in seen:
                        raise ValueError("阿里云盘分享目录过深或重复")
                    seen.add(item.file_id)
                    queue.append((item.file_id, item.path, depth + 1))
                    continue
                if len(files) >= 200:
                    raise ValueError("分享超过 200 个文件，请选择较小的分享")
                data = await self._call(
                    "/v2/file/get_share_link_download_url",
                    {
                        "drive_id": raw.get("drive_id"),
                        "file_id": item.file_id,
                        "share_id": share_id,
                        "expire_sec": 600,
                    },
                    share_token=token,
                )
                item.download_url = data.get("download_url")
                files.append(item)
        return files

    async def save_share(
        self, share_url: str, *, password: str | None = None, target_dir: str | None = None
    ) -> SaveResult:
        return SaveResult(False, "阿里云盘当前提供原文件下载，请在官方客户端执行转存")

    async def health_check(self) -> tuple[bool, str]:
        try:
            await self._call("/v2/user/get", {})
            return True, "阿里云盘账号可用"
        except ValueError as exc:
            return False, str(exc)
