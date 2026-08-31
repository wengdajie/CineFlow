"""迅雷下载器（NAS 本地 CGI）。

**适用场景**：用户在 NAS（飞牛 / 群晖 / 威联通）上装了「迅雷」套件并已在
套件里绑定自己的迅雷账号，本 Provider 把那台迅雷当成一个普通下载器来投递
磁力链接，文件落在 NAS 本地磁盘。

**为什么只做本地 CGI、不做云端账号登录**（见 ADR-27）：
迅雷云端 ``api-pan.xunlei.com`` 的调用需要逆向出来的签名
（``captcha_sign`` 用一串硬编码盐、``device_sign``，client_id/secret 从
官方 App 里扒），这些会随 App 版本失效；而且免费账号**每日只能云添加 3 次**，
频繁提交还会触发风控验证。那条路属于对抗风控，与 ADR-24 的口径一致：不做。
本地 CGI 路径不碰这些——鉴权用的是套件自己发的本地 JWT，我们只调本机接口。

**鉴权说明**：``pan-auth`` 不是迅雷账号密码，而是从套件 Web 页面的
``uiauth()`` JS 函数里抠出来的本地 token，所以界面上不需要填用户名密码。
但 NAS 上的迅雷套件**必须已经绑定过迅雷账号**，否则接口直接 500。
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from urllib.parse import quote

import httpx

from app.core.logger import get_logger
from app.providers.downloader.base import BaseDownloader, TorrentState
from app.providers.registry import register
from app.schemas.enums import ResourceKind, TaskStatus
from app.utils.http import build_headers, normalize_endpoint

logger = get_logger(__name__)

#: 套件把所有接口挂在这个 CGI 下面（群晖第三方套件的固定布局）
_CGI = "/webman/3rdparty/pan-xunlei-com/index.cgi"

#: 从 Web 页面 JS 里抠本地鉴权 token
_UIAUTH_RE = re.compile(r'uiauth\(value\)\{\s*return\s*"([^"]+)"')

#: 迅雷任务 phase -> 内部状态
_PHASE_MAP = {
    "PHASE_TYPE_PENDING": TaskStatus.PENDING.value,
    "PHASE_TYPE_RUNNING": TaskStatus.DOWNLOADING.value,
    "PHASE_TYPE_PAUSED": TaskStatus.PAUSED.value,
    "PHASE_TYPE_ERROR": TaskStatus.FAILED.value,
    "PHASE_TYPE_COMPLETE": TaskStatus.COMPLETED.value,
}

#: 列任务时的 filters（已完成 / 未完成两组），值是 URL 编码后的 JSON
_FILTER_DONE = quote(
    json.dumps(
        {
            "phase": {"in": "PHASE_TYPE_COMPLETE"},
            "type": {"in": "user#download-url,user#download"},
        },
        separators=(",", ":"),
    )
)
_FILTER_ACTIVE = quote(
    json.dumps(
        {
            "phase": {
                "in": "PHASE_TYPE_PENDING,PHASE_TYPE_RUNNING,"
                "PHASE_TYPE_PAUSED,PHASE_TYPE_ERROR"
            },
            "type": {"in": "user#download-url,user#download"},
        },
        separators=(",", ":"),
    )
)
#: 取下载根目录时只要文件夹
_FILTER_FOLDER = quote(
    json.dumps({"kind": {"eq": "drive#folder"}}, separators=(",", ":"))
)


@register
class XunleiDownloader(BaseDownloader):
    """迅雷（NAS 本地）。"""

    name = "xunlei"
    display_name = "迅雷（NAS 本地）"

    #: 迅雷收磁力/种子，也支持 HTTP 直链离线下载。
    supported_kinds = (
        ResourceKind.TORRENT.value,
        ResourceKind.MAGNET.value,
        ResourceKind.DIRECT.value,
    )

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        #: 本地鉴权 token，失效后置空重新抠
        self._auth: str = ""
        #: 远程设备 id（一个迅雷账号可绑多台 NAS）
        self._device_id: str = ""
        #: 下载根目录的 folder id
        self._folder_id: str = ""
        #: 最近一次失败的可读原因，health_check 用它回给界面
        self._last_error: str = ""

    @property
    def base_url(self) -> str:
        # 迅雷套件默认端口 5055；用户常漏协议或复制带空格，统一兜住
        return normalize_endpoint(
            self.config.get("url"), default="http://127.0.0.1:5055"
        )

    @property
    def _api(self) -> str:
        return f"{self.base_url}{_CGI}"

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(self.config.get("timeout") or 20),
            headers=build_headers(),
            follow_redirects=True,
            verify=False,
        )

    async def _pan_auth(self, client: httpx.AsyncClient) -> str:
        """取本地鉴权 token（带缓存）。"""
        if self._auth:
            return self._auth
        try:
            response = await client.get(f"{self._api}/")
        except Exception as exc:
            # NAS 关机 / 地址填错 / 端口不对都走这里。必须降级成 None，
            # 否则异常会一路冒到调用方，让"测试连接"变成 500 而不是一句提示
            self._last_error = f"无法连接迅雷套件：{exc}"
            logger.warning("迅雷取本地鉴权失败 %s: %s", self.base_url, exc)
            return ""
        match = _UIAUTH_RE.search(response.text)
        if not match:
            self._last_error = (
                "无法从迅雷套件页面取得本地鉴权 token："
                "请确认地址指向 NAS 上的迅雷套件，且套件已正常启动"
            )
            logger.error("%s (%s)", self._last_error, self.base_url)
            return ""
        self._auth = match.group(1)
        return self._auth

    def _reset(self) -> None:
        """丢弃缓存，下次调用重新取 token 与设备信息。

        和 qBittorrent 那边同样的教训：token 会过期、套件会重启，
        只要缓存了就必须有失效后重新获取的路径，否则表现为
        "一开始能用、过一阵全挂"。
        """
        self._auth = ""
        self._device_id = ""
        self._folder_id = ""

    async def _request(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
    ) -> Any | None:
        """带「鉴权失效自动重取」的 JSON 请求。失败返回 ``None``，不抛异常。"""
        for attempt in range(2):
            auth = await self._pan_auth(client)
            if not auth:
                return None
            headers = {
                "pan-auth": auth,
                "device-space": "",
                "content-type": "application/json",
            }
            try:
                response = await client.request(
                    method,
                    f"{self._api}{path}",
                    headers=headers,
                    content=json.dumps(body) if body is not None else None,
                )
            except Exception as exc:
                self._last_error = f"无法连接迅雷套件：{exc}"
                logger.warning("迅雷请求 %s 异常: %s", path, exc)
                return None
            if response.status_code == 500:
                # 套件里没绑定迅雷账号时就是这个，必须说清楚否则用户无从下手
                self._last_error = (
                    "迅雷套件未绑定账号：请先在 NAS 的迅雷页面扫码登录你自己的迅雷账号"
                )
                logger.error("%s (%s)", self._last_error, self.base_url)
                return None
            if response.status_code in (401, 403) and attempt == 0:
                logger.info("迅雷本地鉴权已失效，重新获取后重试：%s", path)
                self._reset()
                continue
            if response.status_code >= 400:
                self._last_error = f"迅雷接口返回 {response.status_code}"
                logger.warning("迅雷接口 %s 返回 %s", path, response.status_code)
                return None
            try:
                payload = response.json()
            except Exception as exc:
                self._last_error = f"迅雷响应不是合法 JSON：{exc}"
                return None
            if isinstance(payload, dict) and payload.get("error_code") == 403:
                if attempt == 0:
                    self._reset()
                    continue
                self._last_error = "迅雷本地鉴权被拒绝（403）"
                return None
            return payload
        return None

    async def _ensure_device(self, client: httpx.AsyncClient) -> bool:
        """解析出 device_id 与下载根目录 folder_id（带缓存）。"""
        if self._device_id and self._folder_id:
            return True
        payload = await self._request(
            client, "GET", "/drive/v1/tasks?type=user%23runner&device_space="
        )
        if payload is None:
            # _request 已经写好了更准确的原因（连不上 / 未绑定账号 / 鉴权失败），
            # 这里绝不能覆盖成"没有绑定设备"——那会把用户引到错误的排查方向
            return False
        tasks = payload.get("tasks") or []
        if not tasks:
            self._last_error = (
                "迅雷账号下没有绑定任何远程设备："
                "请在迅雷 App 里确认这台 NAS 已添加为远程设备"
            )
            logger.error("%s (%s)", self._last_error, self.base_url)
            return False
        wanted = str(self.option("device_name") or "").strip()
        for task in tasks:
            params = task.get("params") or {}
            if not wanted or task.get("name") == wanted:
                self._device_id = str(params.get("target") or "")
                if self._device_id:
                    break
        if not self._device_id:
            self._last_error = f"未找到名为 {wanted} 的迅雷设备" if wanted else "无法解析迅雷设备 id"
            logger.error("%s", self._last_error)
            return False

        payload = await self._request(
            client,
            "GET",
            f"/drive/v1/files?space={quote(self._device_id)}&limit=200"
            f"&parent_id=&filters={_FILTER_FOLDER}&page_token=&device_space=",
        )
        if payload is None:
            return False
        files = payload.get("files") or []
        if not files:
            self._last_error = "无法取得迅雷下载根目录"
            return False
        wanted_dir = str(self.option("download_root_dir") or "").strip()
        if wanted_dir:
            for item in files:
                if item.get("name") == wanted_dir:
                    self._folder_id = str(item.get("id") or "")
                    break
            if not self._folder_id:
                self._last_error = f"未找到下载目录 {wanted_dir}"
                logger.error("%s", self._last_error)
                return False
        else:
            first = files[0]
            self._folder_id = str(first.get("parent_id") or first.get("id") or "")
        return bool(self._folder_id)

    async def add(
        self,
        link: str,
        *,
        save_path: str | None = None,
        category: str | None = None,
        paused: bool = False,
        cookie: str | None = None,
    ) -> str | None:
        """提交磁力链接。

        迅雷不接受任意本地路径，只能选它自己管理的下载目录，所以
        ``save_path`` 在这里被当作**子目录名**使用（取最后一段）；
        ``paused`` 不被支持——迅雷提交后立刻开始下载。
        """
        async with self._client() as client:
            if not await self._ensure_device(client):
                return None
            # 先解析资源，拿到任务名与文件列表
            listed = await self._request(
                client, "POST", "/drive/v1/resource/list?device_space=", body={"urls": link}
            )
            resources = ((listed or {}).get("list") or {}).get("resources") or []
            if not resources:
                self._last_error = "迅雷无法解析该链接（可能是死种或不支持的协议）"
                logger.warning("%s: %s", self._last_error, link[:80])
                return None
            root = resources[0]
            task_name = str(root.get("name") or "")
            total_count = root.get("file_count") or 1

            # 递归收集文件下标，迅雷要求显式给出要下的文件
            indexes: list[str] = []
            total_size = 0

            def walk(items: list[dict[str, Any]]) -> None:
                nonlocal total_size
                for item in items:
                    if item.get("is_dir"):
                        walk((item.get("dir") or {}).get("resources") or [])
                    else:
                        indexes.append(str(item.get("file_index") or 0))
                        total_size += int(item.get("file_size") or 0)

            walk(resources)

            parent_id = self._folder_id
            sub_dir = str(save_path or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
            if sub_dir and sub_dir not in (".", ".."):
                created = await self._request(
                    client,
                    "POST",
                    "/drive/v1/files?device_space=",
                    body={
                        "parent_id": self._folder_id,
                        "name": sub_dir,
                        "space": self._device_id,
                        "kind": "drive#folder",
                    },
                )
                new_id = ((created or {}).get("file") or {}).get("id")
                if new_id:
                    parent_id = str(new_id)

            before = {item.external_id for item in await self._tasks(client)}
            result = await self._request(
                client,
                "POST",
                "/drive/v1/task?device_space=",
                body={
                    "type": "user#download-url",
                    "name": task_name,
                    "file_name": task_name,
                    "file_size": str(total_size),
                    "space": self._device_id,
                    "params": {
                        "target": self._device_id,
                        "url": link,
                        "total_file_count": str(total_count),
                        "parent_folder_id": parent_id,
                        "sub_file_index": ",".join(indexes),
                        "file_id": "",
                    },
                },
            )
            if result is None:
                logger.error("迅雷添加失败：%s", self._last_error or "未知原因")
                return None
            # 提交接口不一定回任务 id，回了就直接用
            task_id = str(((result or {}).get("task") or {}).get("id") or "")
            if task_id:
                return task_id
            # 否则轮询任务列表，用"新出现的任务"定位（同 qBittorrent 的做法）
            for _ in range(10):
                await asyncio.sleep(0.6)
                for item in await self._tasks(client):
                    if item.external_id not in before:
                        return item.external_id
            logger.warning("迅雷已提交但未取到任务 id: %s", link[:80])
            return None

    def _to_state(self, item: dict[str, Any]) -> TorrentState:
        params = item.get("params") or {}
        # 迅雷的 progress 是 0-100 整数，内部统一用 0-1 浮点
        progress = float(item.get("progress") or 0) / 100.0
        size = int(item.get("file_size") or 0)
        return TorrentState(
            external_id=str(item.get("id") or ""),
            name=str(item.get("name") or ""),
            status=_PHASE_MAP.get(str(item.get("phase")), TaskStatus.DOWNLOADING.value),
            progress=progress,
            size=size,
            downloaded=int(progress * size),
            speed=int(params.get("speed") or 0),
            save_path=str(params.get("real_path") or ""),
            content_path=str(params.get("real_path") or ""),
            error=str(item.get("message") or "") or None,
        )

    async def _tasks(self, client: httpx.AsyncClient) -> list[TorrentState]:
        """取全部任务（已完成 + 未完成两次请求）。"""
        if not await self._ensure_device(client):
            return []
        states: list[TorrentState] = []
        space = quote(self._device_id)
        for flt in (_FILTER_ACTIVE, _FILTER_DONE):
            payload = await self._request(
                client,
                "GET",
                f"/drive/v1/tasks?space={space}&page_token=&filters={flt}"
                "&limit=200&device_space=",
            )
            for item in (payload or {}).get("tasks") or []:
                if isinstance(item, dict):
                    states.append(self._to_state(item))
        return states

    async def list_tasks(self, category: str | None = None) -> list[TorrentState]:
        async with self._client() as client:
            return await self._tasks(client)

    async def get(self, external_id: str) -> TorrentState | None:
        # 迅雷没有"按 id 查单个任务"的接口，只能在列表里找
        async with self._client() as client:
            for item in await self._tasks(client):
                if item.external_id == external_id:
                    return item
        return None

    async def remove(self, external_id: str, *, delete_files: bool = False) -> bool:
        async with self._client() as client:
            if not await self._ensure_device(client):
                return False
            result = await self._request(
                client,
                "DELETE",
                f"/drive/v1/tasks?task_ids={quote(external_id)}"
                f"&delete_files={'true' if delete_files else 'false'}&device_space=",
            )
            return result is not None

    async def health_check(self) -> tuple[bool, str]:
        self._last_error = ""
        async with self._client() as client:
            if not await self._ensure_device(client):
                return False, self._last_error or "无法连接迅雷套件"
            return True, f"连接正常，设备已就绪（下载目录 id {self._folder_id}）"
