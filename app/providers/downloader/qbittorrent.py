"""qBittorrent 下载器（Web API v2）。"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.logger import get_logger
from app.providers.downloader.base import BaseDownloader, TorrentState
from app.providers.registry import register
from app.schemas.enums import TaskStatus
from app.utils.http import build_headers

logger = get_logger(__name__)

# qBittorrent state -> 内部状态
_STATE_MAP = {
    "error": TaskStatus.FAILED.value,
    "missingFiles": TaskStatus.FAILED.value,
    "uploading": TaskStatus.COMPLETED.value,
    "pausedUP": TaskStatus.COMPLETED.value,
    "stoppedUP": TaskStatus.COMPLETED.value,
    "queuedUP": TaskStatus.COMPLETED.value,
    "stalledUP": TaskStatus.COMPLETED.value,
    "checkingUP": TaskStatus.COMPLETED.value,
    "forcedUP": TaskStatus.COMPLETED.value,
    "allocating": TaskStatus.DOWNLOADING.value,
    "downloading": TaskStatus.DOWNLOADING.value,
    "metaDL": TaskStatus.DOWNLOADING.value,
    "pausedDL": TaskStatus.PAUSED.value,
    "stoppedDL": TaskStatus.PAUSED.value,
    "queuedDL": TaskStatus.PENDING.value,
    "stalledDL": TaskStatus.DOWNLOADING.value,
    "checkingDL": TaskStatus.DOWNLOADING.value,
    "forcedDL": TaskStatus.DOWNLOADING.value,
    "checkingResumeData": TaskStatus.PENDING.value,
    "moving": TaskStatus.DOWNLOADING.value,
}


@register
class QbittorrentDownloader(BaseDownloader):
    """qBittorrent。"""

    name = "qbittorrent"
    display_name = "qBittorrent"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._client: httpx.AsyncClient | None = None

    @property
    def base_url(self) -> str:
        return str(self.config.get("url") or "http://127.0.0.1:8080").rstrip("/")

    async def _session(self) -> httpx.AsyncClient | None:
        """登录并缓存会话。"""
        if self._client is not None:
            return self._client
        client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.config.get("timeout") or 20),
            headers=build_headers({"Referer": self.base_url}),
            follow_redirects=True,
            verify=False,
        )
        username = self.config.get("username") or ""
        password = self.config.get("password") or ""
        try:
            if username:
                response = await client.post(
                    "/api/v2/auth/login",
                    data={"username": username, "password": password},
                )
                if response.status_code != 200 or "Fails" in response.text:
                    logger.error("qBittorrent 登录失败: %s", self.base_url)
                    await client.aclose()
                    return None
        except Exception as exc:
            logger.error("qBittorrent 连接失败 %s: %s", self.base_url, exc)
            await client.aclose()
            return None
        self._client = client
        return client

    async def add(
        self,
        link: str,
        *,
        save_path: str | None = None,
        category: str | None = None,
        paused: bool = False,
        cookie: str | None = None,
    ) -> str | None:
        client = await self._session()
        if not client:
            return None

        data: dict[str, Any] = {
            "urls": link,
            "paused": "true" if paused else "false",
            "autoTMM": "false",
        }
        target_path = save_path or self.default_save_path()
        if target_path:
            data["savepath"] = target_path
        if category or self.option("category"):
            data["category"] = category or self.option("category")
        if cookie:
            data["cookie"] = cookie
        tags = self.option("tags", "CineFlow")
        if tags:
            data["tags"] = tags

        # 记录添加前的 hash 集合，用于定位新任务
        before = {item.external_id for item in await self.list_tasks()}
        try:
            response = await client.post("/api/v2/torrents/add", data=data)
            if response.status_code != 200:
                logger.error("qBittorrent 添加失败 %s: %s", response.status_code, response.text[:200])
                return None
        except Exception as exc:
            logger.error("qBittorrent 添加异常: %s", exc)
            return None

        import asyncio

        for _ in range(10):
            await asyncio.sleep(0.6)
            after = await self.list_tasks()
            new = [item for item in after if item.external_id not in before]
            if new:
                return new[0].external_id
        logger.warning("qBittorrent 已提交但未取到任务 hash: %s", link[:80])
        return None

    def _to_state(self, item: dict[str, Any]) -> TorrentState:
        raw_state = str(item.get("state") or "")
        return TorrentState(
            external_id=str(item.get("hash") or ""),
            name=str(item.get("name") or ""),
            status=_STATE_MAP.get(raw_state, TaskStatus.DOWNLOADING.value),
            progress=float(item.get("progress") or 0.0),
            size=int(item.get("size") or item.get("total_size") or 0),
            downloaded=int(item.get("completed") or item.get("downloaded") or 0),
            speed=int(item.get("dlspeed") or 0),
            eta=int(item.get("eta") or 0),
            save_path=str(item.get("save_path") or ""),
            content_path=str(item.get("content_path") or ""),
            error=raw_state if raw_state in ("error", "missingFiles") else None,
        )

    async def list_tasks(self, category: str | None = None) -> list[TorrentState]:
        client = await self._session()
        if not client:
            return []
        params: dict[str, Any] = {}
        if category or self.option("category"):
            params["category"] = category or self.option("category")
        try:
            response = await client.get("/api/v2/torrents/info", params=params)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            logger.warning("qBittorrent 获取任务列表失败: %s", exc)
            return []
        return [self._to_state(item) for item in payload if isinstance(item, dict)]

    async def get(self, external_id: str) -> TorrentState | None:
        client = await self._session()
        if not client:
            return None
        try:
            response = await client.get(
                "/api/v2/torrents/info", params={"hashes": external_id}
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            logger.warning("qBittorrent 查询任务失败: %s", exc)
            return None
        if not payload:
            return None
        state = self._to_state(payload[0])
        state.files = await self._files(external_id)
        return state

    async def _files(self, external_id: str) -> list[str]:
        client = await self._session()
        if not client:
            return []
        try:
            response = await client.get(
                "/api/v2/torrents/files", params={"hash": external_id}
            )
            response.raise_for_status()
            return [str(item.get("name") or "") for item in response.json()]
        except Exception:
            return []

    async def remove(self, external_id: str, *, delete_files: bool = False) -> bool:
        client = await self._session()
        if not client:
            return False
        try:
            response = await client.post(
                "/api/v2/torrents/delete",
                data={
                    "hashes": external_id,
                    "deleteFiles": "true" if delete_files else "false",
                },
            )
            return response.status_code == 200
        except Exception:
            return False

    async def _simple_action(self, action: str, external_id: str) -> bool:
        client = await self._session()
        if not client:
            return False
        try:
            response = await client.post(
                f"/api/v2/torrents/{action}", data={"hashes": external_id}
            )
            return response.status_code == 200
        except Exception:
            return False

    async def pause(self, external_id: str) -> bool:
        # qB 5.x 改名为 stop，两者都尝试
        return await self._simple_action("stop", external_id) or await self._simple_action(
            "pause", external_id
        )

    async def resume(self, external_id: str) -> bool:
        return await self._simple_action("start", external_id) or await self._simple_action(
            "resume", external_id
        )

    async def health_check(self) -> tuple[bool, str]:
        client = await self._session()
        if not client:
            return False, "登录失败或无法连接"
        try:
            response = await client.get("/api/v2/app/version")
            response.raise_for_status()
            return True, f"连接正常，版本 {response.text.strip()}"
        except Exception as exc:
            return False, str(exc)
