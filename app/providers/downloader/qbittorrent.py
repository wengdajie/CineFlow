"""qBittorrent 下载器（Web API v2）。"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from typing import Any

import httpx

from app.core.logger import get_logger
from app.providers.downloader.base import BaseDownloader, TorrentState
from app.providers.registry import register
from app.schemas.enums import TaskStatus
from app.utils.http import build_headers, normalize_endpoint

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
        #: 最近一次失败的可读原因，health_check 用它回给界面而不是笼统的"登录失败"
        self._last_error: str = ""

    @property
    def base_url(self) -> str:
        # 用户常漏协议或复制带空格，统一兜住（见 normalize_endpoint 的说明）
        return normalize_endpoint(
            self.config.get("url"), default="http://127.0.0.1:8080"
        )

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
                # qB 登录成功返回 "Ok."，密码错返回 "Fails."（都是 200）；
                # 403 是"登录尝试过多被临时封禁"，要单独说明否则用户会一直重试。
                if response.status_code == 403:
                    self._last_error = (
                        "qBittorrent 拒绝登录（403）：通常是失败次数过多被临时封禁，"
                        "等几分钟或在 qB 里调整「Web UI 封禁设置」后重试"
                    )
                    logger.error("%s: %s", self._last_error, self.base_url)
                    await client.aclose()
                    return None
                if response.status_code != 200 or "Fails" in response.text:
                    self._last_error = "用户名或密码错误"
                    logger.error("qBittorrent 登录失败（账号或密码错误）: %s", self.base_url)
                    await client.aclose()
                    return None
            else:
                # 没填用户名时不能直接认为"免密可用"：qB 只对本机放行，
                # 容器/跨主机访问仍需认证。先探一次，403 就明确告诉用户要填账号，
                # 而不是等后面每个业务请求都 403、日志里全是无意义的报错。
                probe = await client.get("/api/v2/app/version")
                if probe.status_code in (401, 403):
                    self._last_error = (
                        "qBittorrent 需要认证：请填写用户名和密码，"
                        "或在 qB 的「Web UI」里勾选对本机/局域网免密"
                    )
                    logger.error("%s (%s)", self._last_error, self.base_url)
                    await client.aclose()
                    return None
        except Exception as exc:
            self._last_error = f"无法连接：{exc}"
            logger.error("qBittorrent 连接失败 %s: %s", self.base_url, exc)
            await client.aclose()
            return None
        self._client = client
        return client

    async def _reset_session(self) -> None:
        """丢弃当前会话，下次调用会重新登录。"""
        client, self._client = self._client, None
        if client is not None:
            # 关闭失败不影响重登录（连接可能已被对端断掉）
            with contextlib.suppress(Exception):
                await client.aclose()

    async def _request(
        self, method: str, path: str, **kwargs: Any
    ) -> httpx.Response | None:
        """带「会话失效自动重登录」的请求封装。

        **为什么必须有**：qB 的 SID Cookie 会过期（默认 1 小时），
        用户重启 qB 也会让旧会话失效。而 ``_session()`` 一旦缓存了 client
        就永远直接复用——实测会话作废后**永不重新登录**，
        之后每次调用都 403，表现为"一开始能用，过一阵就全挂了"，
        且日志里只有一串 403 看不出原因。这是 qB 调用失败最主要的成因。

        这里遇到 401/403 时重置会话重登录一次再重试。只重试一次：
        如果是账号真的错了，重试再多也没用，反而拖慢每个请求。
        """
        for attempt in range(2):
            client = await self._session()
            if not client:
                return None
            try:
                response = await client.request(method, path, **kwargs)
            except Exception as exc:
                logger.warning("qBittorrent 请求 %s 异常: %s", path, exc)
                await self._reset_session()
                if attempt == 1:
                    return None
                continue
            if response.status_code in (401, 403) and attempt == 0:
                logger.info("qBittorrent 会话已失效，重新登录后重试：%s", path)
                await self._reset_session()
                continue
            return response
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
        # 用一次性标签定位刚提交的任务（做法参考 MoviePilot）。
        # 原先是"记下添加前的 hash 集合，再取差集"，那在并发投递时会认错：
        # 实测同时添加 3 个磁力，三次调用**返回同一个 hash**，
        # 于是三个任务的进度/完成判定全都盯在同一个种子上。
        # 唯一标签没有这个问题——每次只查自己那一个。
        marker = f"cineflow-{uuid.uuid4().hex[:12]}"
        tags = self.option("tags", "CineFlow")
        data["tags"] = f"{tags},{marker}" if tags else marker

        response = await self._request("POST", "/api/v2/torrents/add", data=data)
        if response is None:
            logger.error("qBittorrent 添加失败：%s", self._last_error or "无法连接")
            return None
        if response.status_code != 200:
            logger.error(
                "qBittorrent 添加失败 %s: %s", response.status_code, response.text[:200]
            )
            return None

        # qB 接受请求后要先解析元数据才会出现在列表里，磁力尤其慢，所以要轮询
        external_id: str | None = None
        for _ in range(10):
            await asyncio.sleep(0.6)
            external_id = await self._find_by_tag(marker)
            if external_id:
                break
        if not external_id:
            logger.warning("qBittorrent 已提交但未取到任务 hash: %s", link[:80])
            await self._remove_tag(marker)
            return None
        # 临时标签用完即删，否则 qB 的标签列表会被一次性标签污染。
        # 失败也不影响主流程（任务已经拿到了）。
        await self._remove_tag(marker)
        return external_id

    async def _find_by_tag(self, marker: str) -> str | None:
        """按一次性标签查任务 hash。"""
        response = await self._request(
            "GET", "/api/v2/torrents/info", params={"tag": marker}
        )
        if response is None:
            return None
        try:
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return None
        for item in payload if isinstance(payload, list) else []:
            hash_value = str(item.get("hash") or "")
            if hash_value:
                return hash_value
        return None

    async def _remove_tag(self, marker: str) -> None:
        """删除一次性标签（连同它在 qB 全局标签列表里的登记）。"""
        await self._request("POST", "/api/v2/torrents/deleteTags", data={"tags": marker})

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
        params: dict[str, Any] = {}
        if category or self.option("category"):
            params["category"] = category or self.option("category")
        response = await self._request("GET", "/api/v2/torrents/info", params=params)
        if response is None:
            return []
        try:
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            logger.warning("qBittorrent 获取任务列表失败: %s", exc)
            return []
        return [self._to_state(item) for item in payload if isinstance(item, dict)]

    async def get(self, external_id: str) -> TorrentState | None:
        response = await self._request(
            "GET", "/api/v2/torrents/info", params={"hashes": external_id}
        )
        if response is None:
            return None
        try:
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
        response = await self._request(
            "GET", "/api/v2/torrents/files", params={"hash": external_id}
        )
        if response is None:
            return []
        try:
            response.raise_for_status()
            return [str(item.get("name") or "") for item in response.json()]
        except Exception:
            return []

    async def remove(self, external_id: str, *, delete_files: bool = False) -> bool:
        response = await self._request(
            "POST",
            "/api/v2/torrents/delete",
            data={
                "hashes": external_id,
                "deleteFiles": "true" if delete_files else "false",
            },
        )
        return response is not None and response.status_code == 200

    async def _simple_action(self, action: str, external_id: str) -> bool:
        response = await self._request(
            "POST", f"/api/v2/torrents/{action}", data={"hashes": external_id}
        )
        return response is not None and response.status_code == 200

    async def pause(self, external_id: str) -> bool:
        # qB 5.x 改名为 stop，两者都尝试
        return await self._simple_action("stop", external_id) or await self._simple_action(
            "pause", external_id
        )

    async def resume(self, external_id: str) -> bool:
        return await self._simple_action("start", external_id) or await self._simple_action(
            "resume", external_id
        )

    #: qB 有现成的全局限速接口，能力位如实开启
    supports_speed_limit = True

    async def set_speed_limit(
        self, *, download_kb: int | None = None, upload_kb: int | None = None
    ) -> bool:
        """设置全局限速。

        ⚠️ **qB 的接口单位是 B/s，不是 KB/s**（``setDownloadLimit`` 的 ``limit``
        参数）。传 1024 得到的是 1KB/s 而不是 1MB/s——这个单位坑不做换算的话，
        用户设「10MB/s」会得到 10KB/s，看起来像"限速没生效反而更慢了"。
        ``0`` 表示不限速。
        """
        ok = True
        if download_kb is not None:
            response = await self._request(
                "POST",
                "/api/v2/transfer/setDownloadLimit",
                data={"limit": max(0, int(download_kb)) * 1024},
            )
            ok = ok and response is not None and response.status_code == 200
        if upload_kb is not None:
            response = await self._request(
                "POST",
                "/api/v2/transfer/setUploadLimit",
                data={"limit": max(0, int(upload_kb)) * 1024},
            )
            ok = ok and response is not None and response.status_code == 200
        return ok

    async def get_speed_limit(self) -> dict[str, int] | None:
        """读回当前全局限速（换算成 KB/s）。"""
        down = await self._request("GET", "/api/v2/transfer/downloadLimit")
        up = await self._request("GET", "/api/v2/transfer/uploadLimit")
        if down is None or up is None:
            return None
        try:
            return {
                "download_kb": int(down.text.strip() or 0) // 1024,
                "upload_kb": int(up.text.strip() or 0) // 1024,
            }
        except (TypeError, ValueError):
            return None

    async def health_check(self) -> tuple[bool, str]:
        self._last_error = ""
        response = await self._request("GET", "/api/v2/app/version")
        if response is None:
            # 把具体原因回给界面：是密码错、要认证、还是根本连不上
            return False, self._last_error or "登录失败或无法连接"
        if response.status_code in (401, 403):
            return False, self._last_error or "认证失败（请检查用户名/密码）"
        try:
            response.raise_for_status()
        except Exception as exc:
            return False, str(exc)
        version = response.text.strip()
        return True, f"连接正常，版本 {version}" if version else "连接正常"
