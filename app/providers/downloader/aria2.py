"""aria2 下载器（JSON-RPC），适合网盘直链与 HTTP 资源。"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.logger import get_logger
from app.providers.downloader.base import BaseDownloader, TorrentState
from app.providers.registry import register
from app.schemas.enums import ResourceKind, TaskStatus
from app.utils.http import fetch_json, normalize_endpoint

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

    #: aria2 是全能选手：HTTP/FTP 直链、磁力、种子文件都收。
    #: 网盘分享链接**不在**其中——那是一个网页地址，必须先由网盘账号
    #: 换成临时直链才能下（见 ``download_routing`` 对 pan 的处理）。
    supported_kinds = (
        ResourceKind.DIRECT.value,
        ResourceKind.TORRENT.value,
        ResourceKind.MAGNET.value,
    )

    @property
    def base_url(self) -> str:
        # 先规范化再拼路径：否则 "127.0.0.1:6800" 会拼成没有协议的地址
        url = normalize_endpoint(self.config.get("url"), default="http://127.0.0.1:6800")
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
        user_agent: str | None = None,
        filename: str | None = None,
    ) -> str | None:
        options: dict[str, Any] = {}
        target_path = save_path or self.default_save_path()
        if target_path:
            options["dir"] = target_path
        if filename:
            from app.utils.strings import safe_filename

            options["out"] = safe_filename(filename)
        # aria2 的 header 选项是**列表**，Cookie 与 UA 要并存而不是互相覆盖。
        headers: list[str] = []
        if cookie:
            headers.append(f"Cookie: {cookie}")
        if user_agent:
            # 同时给 header 与 user-agent 两处：aria2 的 --user-agent 才是
            # 真正作用于 HTTP 请求的那个开关，光塞 header 在部分版本上会被
            # 内建 UA 覆盖掉；两处都给才稳。
            headers.append(f"User-Agent: {user_agent}")
            options["user-agent"] = user_agent
        if headers:
            options["header"] = headers
        if paused:
            options["pause"] = "true"

        method = "aria2.addUri"
        params: list[Any] = [[link], options]
        result = await self._call(method, params)
        return str(result) if result else None

    def _content_path(self, entries: list[dict[str, Any]], fallback: str) -> str:
        """挑出这个任务的"主内容"路径，供入库整理用。

        **不能用 ``files[0]``**（v1.24.0 实测）：aria2 按 **torrent 内的文件序**
        返回，不是按大小。两个真实种子的实际返回顺序：

        | 种子 | ``files[0]`` | 实际的正片 |
        |---|---|---|
        | Big Buck Bunny | ``Big Buck Bunny.en.srt``（**140 B**） | ``.mp4`` 276 MB |
        | Sintel | ``Sintel.de.srt``（1.6 KB） | ``.mp4`` 129 MB，排在 **9 个 .srt 之后** |

        后果是**静默的**：`content_path` 指向字幕，`iter_media_files()` 对单个
        `.srt` 返回空列表，于是入库只在日志里留一行"未发现可整理的媒体文件"，
        任务停在 `completed` 永远不变 `transferred`。用户看到"下载完成"、
        媒体库里什么都没有，而且没有任何报错。

        多文件种子取**最大的那个文件所在目录**而不是文件本身：正片旁边的字幕
        也要一起入库，交给 `transfer_directory()` 递归处理最省事。
        """
        if not entries:
            return fallback
        sized = [
            (int(entry.get("length") or 0), str(entry.get("path") or ""))
            for entry in entries
            if entry.get("path")
        ]
        if not sized:
            return fallback
        if len(sized) == 1:
            return sized[0][1]
        # 最大文件 = 正片；返回它的父目录，让随行字幕一并被整理
        _, largest = max(sized, key=lambda pair: pair[0])
        parent = largest.rsplit("/", 1)[0] if "/" in largest else ""
        return parent or largest

    def _to_state(self, item: dict[str, Any]) -> TorrentState:
        total = int(item.get("totalLength") or 0)
        completed = int(item.get("completedLength") or 0)
        entries = [entry for entry in item.get("files") or [] if isinstance(entry, dict)]
        files = [str(entry.get("path") or "") for entry in entries]
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
            content_path=self._content_path(entries, str(item.get("dir") or "")),
            files=files,
            error=str(item.get("errorMessage") or "") or None,
        )

    #: 磁力链接的元数据阶段最多跟随几层 followedBy。
    #: 实测 aria2 1.37 只有一层（metadata -> 数据），留 3 层是防环上限。
    _FOLLOW_LIMIT = 3

    async def _follow(self, external_id: str) -> dict[str, Any] | None:
        """顺着 ``followedBy`` 找到真正在下数据的那个 GID。

        磁力链接交给 aria2 时，``addUri`` 返回的是**元数据任务**的 GID：
        它只负责从 DHT/peer 拉 torrent 信息，几百 KB 就 ``complete``，
        然后派生一个新 GID 去下真正的内容（``followedBy`` 指向它）。

        如果一直查旧 GID，会看到「已完成、size=466KB」——巡检据此把任务判成
        完成并把那个 ``.torrent`` 当成片源送去入库，而 6GB 正片其实还在下。
        这是实测踩到的（ubuntu-24.04.1 magnet：父 GID complete 466619 字节，
        子 GID active 6114656256 字节），所以这里必须跟随。
        """
        seen: set[str] = set()
        current = external_id
        for _ in range(self._FOLLOW_LIMIT + 1):
            if not current or current in seen:
                break
            seen.add(current)
            item = await self._call("aria2.tellStatus", [current])
            if not isinstance(item, dict):
                return None
            followed = item.get("followedBy") or []
            if isinstance(followed, list) and followed:
                current = str(followed[0])
                continue
            return item
        return None

    async def get(self, external_id: str) -> TorrentState | None:
        result = await self._follow(external_id)
        return self._to_state(result) if result else None

    async def list_tasks(self, category: str | None = None) -> list[TorrentState]:
        states: list[TorrentState] = []
        active = await self._call("aria2.tellActive") or []
        waiting = await self._call("aria2.tellWaiting", [0, 100]) or []
        stopped = await self._call("aria2.tellStopped", [0, 100]) or []
        for group in (active, waiting, stopped):
            for item in group:
                if not isinstance(item, dict):
                    continue
                # 已派生出子任务的元数据条目不是真实下载，列出来只会看到
                # 一堆 466KB 的「已完成」幽灵任务，过滤掉。
                if item.get("followedBy"):
                    continue
                states.append(self._to_state(item))
        return states

    async def remove(self, external_id: str, *, delete_files: bool = False) -> bool:
        # 先删子任务：只删元数据 GID 的话，真正在下的那个会继续跑成孤儿。
        targets: list[str] = []
        head = await self._call("aria2.tellStatus", [external_id])
        if isinstance(head, dict):
            followed = head.get("followedBy") or []
            if isinstance(followed, list):
                targets.extend(str(gid) for gid in followed if gid)
        targets.append(external_id)

        ok = False
        for gid in targets:
            result = await self._call("aria2.remove", [gid])
            if result is None:
                result = await self._call("aria2.removeDownloadResult", [gid])
            ok = ok or result is not None
        return ok

    async def _live_gid(self, external_id: str) -> str:
        """暂停/恢复要作用在真正在下数据的 GID 上（同 ``_follow`` 的理由）。"""
        item = await self._follow(external_id)
        if isinstance(item, dict) and item.get("gid"):
            return str(item["gid"])
        return external_id

    async def pause(self, external_id: str) -> bool:
        gid = await self._live_gid(external_id)
        return await self._call("aria2.pause", [gid]) is not None

    async def resume(self, external_id: str) -> bool:
        gid = await self._live_gid(external_id)
        return await self._call("aria2.unpause", [gid]) is not None

    #: aria2 的 changeGlobalOption 支持运行时改限速
    supports_speed_limit = True

    async def set_speed_limit(
        self, *, download_kb: int | None = None, upload_kb: int | None = None
    ) -> bool:
        """设置全局限速。

        aria2 的 ``max-overall-download-limit`` 接受带单位的字符串（如 ``"5M"``），
        **但值必须是字符串**——传整数会被 aria2 拒绝并回 JSON-RPC 错误。
        这里统一格式成 ``"<n>K"``；``0`` 按 aria2 约定表示不限速。
        """
        options: dict[str, str] = {}
        if download_kb is not None:
            value = max(0, int(download_kb))
            options["max-overall-download-limit"] = f"{value}K" if value else "0"
        if upload_kb is not None:
            value = max(0, int(upload_kb))
            options["max-overall-upload-limit"] = f"{value}K" if value else "0"
        if not options:
            return False
        return await self._call("aria2.changeGlobalOption", [options]) is not None

    async def get_speed_limit(self) -> dict[str, int] | None:
        """读回当前全局限速（aria2 回的是 B/s 字符串，换算成 KB/s）。"""
        result = await self._call("aria2.getGlobalOption")
        if not isinstance(result, dict):
            return None

        def _kb(raw: object) -> int:
            try:
                return int(str(raw or "0").strip() or 0) // 1024
            except (TypeError, ValueError):
                return 0

        return {
            "download_kb": _kb(result.get("max-overall-download-limit")),
            "upload_kb": _kb(result.get("max-overall-upload-limit")),
        }

    async def health_check(self) -> tuple[bool, str]:
        result = await self._call("aria2.getVersion")
        if not result:
            return False, "无法连接 aria2"
        return True, f"连接正常，版本 {result.get('version', '未知')}"
