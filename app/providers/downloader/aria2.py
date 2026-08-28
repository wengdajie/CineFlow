"""aria2 下载器（JSON-RPC），适合网盘直链与 HTTP 资源。"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.logger import get_logger
from app.providers.downloader.base import BaseDownloader, TorrentState
from app.providers.registry import register
from app.schemas.enums import TaskStatus
from app.utils.http import fetch_json

logger = get_logger(__name__)

_STATUS_MAP = {
    "active": TaskStatus.DOWNLOADING.value,
    "waiting": TaskStatus.PENDING.value,
    "paused": TaskStatus.PAUSED.value,
    "error": TaskStatus.FAILED.value,
    "complete": TaskStatus.COMPLETED.value,
    "removed": TaskStatus.CANCELED.value,
}


@register
class Aria2Downloader(BaseDownloader):
    """aria2。"""

    name = "aria2"
    display_name = "aria2"

    @property
    def base_url(self) -> str:
        url = str(self.config.get("url") or "http://127.0.0.1:6800").rstrip("/")
        return url if url.endswith("/jsonrpc") else f"{url}/jsonrpc"

    async def _call(self, method: str, params: list[Any] | None = None) -> Any:
        token = self.config.get("api_key") or self.config.get("password") or ""
        payload_params: list[Any] = []
        if token:
            payload_params.append(f"token:{token}")
        payload_params.extend(params or [])
        payload = {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": method,
            "params": payload_params,
        }
        result = await fetch_json(
            self.base_url,
            method="POST",
            json_body=payload,
            timeout=self.config.get("timeout"),
        )
        if not result:
            return None
        if "error" in result:
            logger.warning("aria2 调用失败 %s: %s", method, result["error"])
            return None
        return result.get("result")

    async def add(
        self,
        link: str,
        *,
        save_path: str | None = None,
        category: str | None = None,
        paused: bool = False,
        cookie: str | None = None,
    ) -> str | None:
        options: dict[str, Any] = {}
        target_path = save_path or self.default_save_path()
        if target_path:
            options["dir"] = target_path
        if cookie:
            options["header"] = [f"Cookie: {cookie}"]
        if paused:
            options["pause"] = "true"

        method = "aria2.addUri"
        params: list[Any] = [[link], options]
        result = await self._call(method, params)
        return str(result) if result else None

    def _to_state(self, item: dict[str, Any]) -> TorrentState:
        total = int(item.get("totalLength") or 0)
        completed = int(item.get("completedLength") or 0)
        files = [str(entry.get("path") or "") for entry in item.get("files") or []]
        name = ""
        bittorrent = item.get("bittorrent") or {}
        if isinstance(bittorrent, dict):
            name = str((bittorrent.get("info") or {}).get("name") or "")
        if not name and files:
            name = files[0].rsplit("/", 1)[-1]
        return TorrentState(
            external_id=str(item.get("gid") or ""),
            name=name,
            status=_STATUS_MAP.get(str(item.get("status")), TaskStatus.DOWNLOADING.value),
            progress=(completed / total) if total else 0.0,
            size=total,
            downloaded=completed,
            speed=int(item.get("downloadSpeed") or 0),
            save_path=str(item.get("dir") or ""),
            content_path=files[0] if files else str(item.get("dir") or ""),
            files=files,
            error=str(item.get("errorMessage") or "") or None,
        )

    async def get(self, external_id: str) -> TorrentState | None:
        result = await self._call("aria2.tellStatus", [external_id])
        return self._to_state(result) if result else None

    async def list_tasks(self, category: str | None = None) -> list[TorrentState]:
        states: list[TorrentState] = []
        active = await self._call("aria2.tellActive") or []
        waiting = await self._call("aria2.tellWaiting", [0, 100]) or []
        stopped = await self._call("aria2.tellStopped", [0, 100]) or []
        for group in (active, waiting, stopped):
            states.extend(self._to_state(item) for item in group if isinstance(item, dict))
        return states

    async def remove(self, external_id: str, *, delete_files: bool = False) -> bool:
        result = await self._call("aria2.remove", [external_id])
        if result is None:
            result = await self._call("aria2.removeDownloadResult", [external_id])
        return result is not None

    async def pause(self, external_id: str) -> bool:
        return await self._call("aria2.pause", [external_id]) is not None

    async def resume(self, external_id: str) -> bool:
        return await self._call("aria2.unpause", [external_id]) is not None

    async def health_check(self) -> tuple[bool, str]:
        result = await self._call("aria2.getVersion")
        if not result:
            return False, "无法连接 aria2"
        return True, f"连接正常，版本 {result.get('version', '未知')}"
