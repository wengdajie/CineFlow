"""Transmission 下载器（RPC）。"""

from __future__ import annotations

import base64
from typing import Any

import httpx

from app.core.logger import get_logger
from app.providers.downloader.base import BaseDownloader, TorrentState
from app.providers.registry import register
from app.schemas.enums import TaskStatus
from app.utils.http import build_headers, normalize_endpoint

logger = get_logger(__name__)

_FIELDS = [
    "id",
    "hashString",
    "name",
    "status",
    "percentDone",
    "totalSize",
    "downloadedEver",
    "rateDownload",
    "eta",
    "downloadDir",
    "errorString",
    "files",
]
# Transmission status: 0 停止 1/2 校验 3/4 下载 5/6 做种
_STATUS_MAP = {
    0: TaskStatus.PAUSED.value,
    1: TaskStatus.PENDING.value,
    2: TaskStatus.DOWNLOADING.value,
    3: TaskStatus.PENDING.value,
    4: TaskStatus.DOWNLOADING.value,
    5: TaskStatus.COMPLETED.value,
    6: TaskStatus.COMPLETED.value,
}


@register
class TransmissionDownloader(BaseDownloader):
    """Transmission。"""

    name = "transmission"
    display_name = "Transmission"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._session_id: str = ""

    @property
    def base_url(self) -> str:
        # 飞牛 fnOS 自带的下载器就是 Transmission，默认端点即下面这个
        url = normalize_endpoint(self.config.get("url"), default="http://127.0.0.1:9091")
        return url if url.endswith("/rpc") else f"{url}/transmission/rpc"

    async def _call(self, method: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        """发起 RPC 调用，自动处理 409 会话令牌。"""
        auth = None
        if self.config.get("username"):
            auth = (self.config.get("username") or "", self.config.get("password") or "")

        payload = {"method": method, "arguments": arguments}
        for attempt in range(2):
            headers = build_headers({"X-Transmission-Session-Id": self._session_id})
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(self.config.get("timeout") or 20),
                    verify=False,
                ) as client:
                    response = await client.post(
                        self.base_url, json=payload, headers=headers, auth=auth
                    )
                if response.status_code == 409 and attempt == 0:
                    self._session_id = response.headers.get(
                        "X-Transmission-Session-Id", ""
                    )
                    continue
                response.raise_for_status()
                data = response.json()
                if data.get("result") != "success":
                    logger.warning("Transmission 调用失败: %s", data.get("result"))
                    return None
                return data.get("arguments") or {}
            except Exception as exc:
                if attempt == 1:
                    logger.error("Transmission RPC 异常 %s: %s", method, exc)
        return None

    async def add(
        self,
        link: str,
        *,
        save_path: str | None = None,
        category: str | None = None,
        paused: bool = False,
        cookie: str | None = None,
    ) -> str | None:
        arguments: dict[str, Any] = {"paused": paused}
        target_path = save_path or self.default_save_path()
        if target_path:
            arguments["download-dir"] = target_path
        if cookie:
            arguments["cookies"] = cookie

        if link.startswith("magnet:") or link.startswith("http"):
            arguments["filename"] = link
        else:
            arguments["metainfo"] = base64.b64encode(link.encode()).decode()

        result = await self._call("torrent-add", arguments)
        if not result:
            return None
        added = result.get("torrent-added") or result.get("torrent-duplicate") or {}
        return str(added.get("hashString") or added.get("id") or "") or None

    def _to_state(self, item: dict[str, Any]) -> TorrentState:
        files = [
            str(entry.get("name") or "") for entry in (item.get("files") or [])
        ]
        return TorrentState(
            external_id=str(item.get("hashString") or item.get("id") or ""),
            name=str(item.get("name") or ""),
            status=_STATUS_MAP.get(int(item.get("status") or 0), TaskStatus.DOWNLOADING.value),
            progress=float(item.get("percentDone") or 0.0),
            size=int(item.get("totalSize") or 0),
            downloaded=int(item.get("downloadedEver") or 0),
            speed=int(item.get("rateDownload") or 0),
            eta=int(item.get("eta") or 0),
            save_path=str(item.get("downloadDir") or ""),
            content_path=str(item.get("downloadDir") or ""),
            files=files,
            error=str(item.get("errorString") or "") or None,
        )

    async def list_tasks(self, category: str | None = None) -> list[TorrentState]:
        result = await self._call("torrent-get", {"fields": _FIELDS})
        if not result:
            return []
        return [self._to_state(item) for item in result.get("torrents", [])]

    async def get(self, external_id: str) -> TorrentState | None:
        result = await self._call(
            "torrent-get", {"fields": _FIELDS, "ids": [external_id]}
        )
        torrents = (result or {}).get("torrents") or []
        return self._to_state(torrents[0]) if torrents else None

    async def remove(self, external_id: str, *, delete_files: bool = False) -> bool:
        result = await self._call(
            "torrent-remove",
            {"ids": [external_id], "delete-local-data": delete_files},
        )
        return result is not None

    async def pause(self, external_id: str) -> bool:
        return await self._call("torrent-stop", {"ids": [external_id]}) is not None

    async def resume(self, external_id: str) -> bool:
        return await self._call("torrent-start", {"ids": [external_id]}) is not None

    async def health_check(self) -> tuple[bool, str]:
        result = await self._call("session-get", {})
        if result is None:
            return False, "无法连接 Transmission"
        return True, f"连接正常，版本 {result.get('version', '未知')}"
