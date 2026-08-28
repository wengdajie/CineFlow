"""AList 网盘网关。

AList 是常见的 NAS 网盘聚合网关：一个 AList 实例可以同时挂载夸克、阿里、
百度、115、天翼、本地磁盘等，CineFlow 只对接 AList 就等于接了所有盘。
因此这是**推荐的网盘接入方式**。

鉴权：AList v3 用 ``/api/auth/login`` 换 token，或直接用固定令牌。
本实现两者都支持：填 ``api_key`` 走固定令牌；填用户名密码走登录换取（带缓存）。
"""

from __future__ import annotations

import time
from typing import Any

from app.core.logger import get_logger
from app.providers.panstorage.base import BasePanStorage, PanFile, PanQuota, SaveResult
from app.providers.registry import register
from app.utils.http import fetch_json

logger = get_logger(__name__)


@register
class AListStorage(BasePanStorage):
    """AList v3 网关。"""

    name = "alist"
    display_name = "AList 网盘网关"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._token: str | None = None
        self._token_at: float = 0.0

    # ---------------- 基础 ----------------
    @property
    def base_url(self) -> str:
        return str(self.config.get("url") or "").rstrip("/")

    async def _auth_token(self) -> str:
        """取鉴权 token：固定令牌优先，否则用账号密码登录（缓存 2 小时）。"""
        fixed = str(self.option("api_key") or "").strip()
        if fixed:
            return fixed

        # 缓存未过期直接复用，避免每次请求都登录
        if self._token and (time.time() - self._token_at) < 7200:
            return self._token

        username = str(self.option("username") or "").strip()
        password = str(self.option("password") or "").strip()
        if not username or not password:
            return ""

        payload = await fetch_json(
            f"{self.base_url}/api/auth/login",
            method="POST",
            json_body={"username": username, "password": password},
            timeout=self.config.get("timeout"),
        )
        token = ((payload or {}).get("data") or {}).get("token")
        if token:
            self._token = str(token)
            self._token_at = time.time()
            return self._token
        logger.warning("AList 登录失败：%s", (payload or {}).get("message"))
        return ""

    async def _request(
        self, path: str, *, method: str = "POST", body: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """带鉴权的 AList API 调用，返回 ``data`` 部分。"""
        if not self.base_url:
            return None
        token = await self._auth_token()
        headers = {"Authorization": token} if token else {}
        payload = await fetch_json(
            f"{self.base_url}{path}",
            method=method,
            json_body=body,
            headers=headers,
            timeout=self.config.get("timeout"),
        )
        if not payload:
            return None
        if payload.get("code") not in (200, None):
            logger.warning("AList %s 返回 %s: %s", path, payload.get("code"), payload.get("message"))
            return None
        data = payload.get("data")
        return data if isinstance(data, dict) else {"_raw": data}

    # ---------------- 能力实现 ----------------
    async def list_dir(self, path: str = "/") -> list[PanFile]:
        target = self.normalize_path(path)
        data = await self._request(
            "/api/fs/list",
            body={"path": target, "page": 1, "per_page": 0, "refresh": False},
        )
        if not data:
            return []
        content = data.get("content") or []
        files: list[PanFile] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            if not name:
                continue
            files.append(
                PanFile(
                    name=name,
                    path=self.join_path(target, name),
                    is_dir=bool(item.get("is_dir")),
                    size=int(item.get("size") or 0),
                    modified_at=item.get("modified"),
                    extra={"sign": item.get("sign") or ""},
                )
            )
        # 目录在前，同类按名称排序，符合文件管理器直觉
        files.sort(key=lambda f: (not f.is_dir, f.name.lower()))
        return files

    async def save_share(
        self,
        share_url: str,
        *,
        password: str | None = None,
        target_dir: str | None = None,
    ) -> SaveResult:
        """通过 AList 的离线下载能力转存分享链接。

        AList 的 ``/api/fs/add_offline_download`` 支持把链接交给挂载的
        存储驱动（如 115/迅雷/PikPak 的离线下载，或 aria2/qBittorrent 工具）。
        """
        if not share_url:
            return SaveResult(False, "缺少分享链接")

        target = self.normalize_path(target_dir or self.root_path)
        tool = str(self.option("offline_tool") or "SimpleHttp")
        # 部分网盘分享需要把提取码拼进链接，AList 约定用 ?pwd= 传递
        url = share_url
        if password and "pwd=" not in url:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}pwd={password}"

        data = await self._request(
            "/api/fs/add_offline_download",
            body={
                "path": target,
                "urls": [url],
                "tool": tool,
                "delete_policy": "delete_on_upload_succeed",
            },
        )
        if data is None:
            return SaveResult(False, "AList 拒绝或不可达（检查地址、令牌与离线下载工具）")
        tasks = data.get("tasks") or data.get("_raw") or []
        count = len(tasks) if isinstance(tasks, list) else 1
        return SaveResult(
            True,
            f"已提交 AList 离线转存（工具：{tool}）",
            saved_path=target,
            file_count=count,
        )

    async def quota(self) -> PanQuota:
        """AList 本身不暴露聚合容量，按挂载存储数量降级返回空容量。"""
        return PanQuota()

    async def mkdir(self, path: str) -> bool:
        data = await self._request("/api/fs/mkdir", body={"path": self.normalize_path(path)})
        return data is not None

    async def delete(self, path: str, *, file_id: str | None = None) -> bool:
        target = self.normalize_path(path)
        parent = self.join_path(*target.split("/")[:-1]) if target != "/" else "/"
        name = target.split("/")[-1]
        if not name:
            return False
        data = await self._request("/api/fs/remove", body={"dir": parent, "names": [name]})
        return data is not None

    async def download_url(self, path: str, *, file_id: str | None = None) -> str | None:
        target = self.normalize_path(path)
        data = await self._request("/api/fs/get", body={"path": target})
        if not data:
            return None
        raw = data.get("raw_url")
        return str(raw) if raw else None

    async def health_check(self) -> tuple[bool, str]:
        if not self.base_url:
            return False, "未配置 AList 地址"
        token = await self._auth_token()
        if not token:
            return False, "鉴权失败：请填写 api_key（固定令牌）或正确的用户名密码"
        files = await self.list_dir(self.root_path)
        return True, f"连接正常，根目录 {len(files)} 个条目"
