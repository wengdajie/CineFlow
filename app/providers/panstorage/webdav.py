"""WebDAV 网盘存储。

**为什么值得单独做**：WebDAV 是标准协议，一份实现即可覆盖
AList、Nextcloud、ownCloud、坚果云、TeraCLOUD、群晖/威联通自带的 WebDAV、
以及各类支持 WebDAV 的网盘中转服务。相比逐家逆向私有 API
（见 docs/04 ADR-02），投入产出比最高。

**协议要点**：
- 列目录 = ``PROPFIND`` + ``Depth: 1``，返回 ``multistatus`` XML
- 建目录 = ``MKCOL``
- 删除   = ``DELETE``
- 直链   = 就是文件 URL 本身（带 Basic Auth），所以 302 播放天然可用

**不支持转存**：WebDAV 没有「转存别人的分享链接」这个概念，
``supports_save = False``，请求会得到明确提示而不是假装成功。
"""

from __future__ import annotations

import urllib.parse
from typing import Any
from xml.etree import ElementTree as ET

from app.core.logger import get_logger
from app.providers.panstorage.base import BasePanStorage, PanFile, PanQuota, SaveResult
from app.providers.registry import register
from app.utils.http import async_client

logger = get_logger(__name__)

#: WebDAV 的 XML 命名空间
_DAV_NS = "{DAV:}"


def _text(node: ET.Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


@register
class WebDavStorage(BasePanStorage):
    """通过 WebDAV 协议访问的网盘/NAS 目录。"""

    name = "webdav"
    display_name = "WebDAV（Nextcloud/坚果云/群晖等）"
    #: WebDAV 无分享转存概念
    supports_save = False
    # WebDAV 原生就有 MOVE/COPY 方法
    supports_rename = True
    supports_move = True
    supports_keepalive = True

    @property
    def base_url(self) -> str:
        """服务地址，如 ``https://dav.example.com/dav``。"""
        return str(self.config.get("url") or self.option("url") or "").rstrip("/")

    @property
    def _auth(self) -> tuple[str, str] | None:
        user = str(self.config.get("username") or self.option("username") or "").strip()
        password = str(
            self.config.get("password") or self.option("password") or ""
        ).strip()
        return (user, password) if user else None

    def _url_for(self, path: str) -> str:
        """拼出资源的完整 URL（对每段做 percent 编码，兼容中文与空格）。"""
        normalized = self.normalize_path(path)
        quoted = "/".join(
            urllib.parse.quote(segment, safe="")
            for segment in normalized.split("/")
            if segment
        )
        return f"{self.base_url}/{quoted}" if quoted else self.base_url + "/"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, str]:
        """发一个 WebDAV 请求，返回 ``(状态码, 响应文本)``。"""
        if not self.base_url:
            return 0, ""
        merged = {"Content-Type": 'application/xml; charset="utf-8"'}
        merged.update(headers or {})
        try:
            async with async_client(timeout=30) as client:
                response = await client.request(
                    method,
                    self._url_for(path),
                    content=body.encode("utf-8") if body else None,
                    headers=merged,
                    auth=self._auth,
                )
                return response.status_code, response.text
        except Exception as exc:
            logger.warning("WebDAV %s %s 失败: %s", method, path, exc)
            return 0, ""

    def _parse_propfind(self, xml_text: str, base_path: str) -> list[PanFile]:
        """解析 ``PROPFIND`` 的 multistatus 响应。

        自身条目（href 等于请求路径）要剔除，否则目录里会出现一个「自己」。
        """
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            logger.warning("WebDAV 响应解析失败: %s", exc)
            return []

        current = self.normalize_path(base_path)
        files: list[PanFile] = []
        for response in root.findall(f"{_DAV_NS}response"):
            href = _text(response.find(f"{_DAV_NS}href"))
            if not href:
                continue
            # href 可能是绝对 URL 也可能是绝对路径，统一取 path 再解码
            raw_path = urllib.parse.urlparse(href).path
            decoded = urllib.parse.unquote(raw_path)
            # 去掉服务地址里的挂载前缀，换算成网盘内部路径
            prefix = urllib.parse.urlparse(self.base_url).path.rstrip("/")
            if prefix and decoded.startswith(prefix):
                decoded = decoded[len(prefix) :]
            item_path = self.normalize_path(decoded)
            if item_path == current:
                continue  # 跳过自身

            props = response.find(f"{_DAV_NS}propstat/{_DAV_NS}prop")
            if props is None:
                continue
            is_dir = props.find(f"{_DAV_NS}resourcetype/{_DAV_NS}collection") is not None
            size_text = _text(props.find(f"{_DAV_NS}getcontentlength"))
            files.append(
                PanFile(
                    name=item_path.rsplit("/", 1)[-1],
                    path=item_path,
                    is_dir=is_dir,
                    size=int(size_text) if size_text.isdigit() else 0,
                    modified_at=_text(props.find(f"{_DAV_NS}getlastmodified")) or None,
                )
            )
        files.sort(key=lambda item: (not item.is_dir, item.name.lower()))
        return files

    async def list_dir(self, path: str = "/") -> list[PanFile]:
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<d:propfind xmlns:d="DAV:"><d:prop>'
            "<d:resourcetype/><d:getcontentlength/><d:getlastmodified/>"
            "</d:prop></d:propfind>"
        )
        status, text = await self._request(
            "PROPFIND", path, body=body, headers={"Depth": "1"}
        )
        if status not in (200, 207):
            return []
        return self._parse_propfind(text, path)

    async def save_share(
        self,
        share_url: str,
        *,
        password: str | None = None,
        target_dir: str | None = None,
    ) -> SaveResult:
        return SaveResult(
            False,
            "WebDAV 不支持从分享链接转存，请改用 AList（离线下载）或夸克网盘",
        )

    async def quota(self) -> PanQuota:
        """通过 ``quota-available-bytes`` 查容量（并非所有服务端都实现）。"""
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<d:propfind xmlns:d="DAV:"><d:prop>'
            "<d:quota-available-bytes/><d:quota-used-bytes/>"
            "</d:prop></d:propfind>"
        )
        status, text = await self._request(
            "PROPFIND", "/", body=body, headers={"Depth": "0"}
        )
        if status not in (200, 207):
            return PanQuota()
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return PanQuota()
        available = used = 0
        for response in root.findall(f"{_DAV_NS}response"):
            props = response.find(f"{_DAV_NS}propstat/{_DAV_NS}prop")
            if props is None:
                continue
            raw_available = _text(props.find(f"{_DAV_NS}quota-available-bytes"))
            raw_used = _text(props.find(f"{_DAV_NS}quota-used-bytes"))
            if raw_available.isdigit():
                available = int(raw_available)
            if raw_used.isdigit():
                used = int(raw_used)
        # WebDAV 只给"可用"和"已用"，总量要自己加
        return PanQuota(total=available + used, used=used)

    async def mkdir(self, path: str) -> bool:
        status, _ = await self._request("MKCOL", path)
        # 405 = 已存在，对"确保目录存在"这个意图来说等价于成功
        return status in (200, 201, 405)

    async def delete(self, path: str, *, file_id: str | None = None) -> bool:
        status, _ = await self._request("DELETE", path)
        return status in (200, 204, 404)

    async def download_url(self, path: str, *, file_id: str | None = None) -> str | None:
        """WebDAV 的文件 URL 就是直链。

        若配了账号密码，把凭据内嵌进 URL，方便播放器/aria2 直接取用。
        注意这会让 URL 里出现明文密码，所以只在需要时才内嵌。
        """
        url = self._url_for(path)
        auth = self._auth
        if not auth or not self.option("embed_credentials"):
            return url
        user, password = auth
        parsed = urllib.parse.urlparse(url)
        credential = f"{urllib.parse.quote(user)}:{urllib.parse.quote(password)}"
        return parsed._replace(netloc=f"{credential}@{parsed.netloc}").geturl()

    # ---------------- 文件管理 ----------------
    async def _move_or_copy(self, method: str, path: str, dest_path: str) -> bool:
        """WebDAV 的 MOVE / COPY 共用同一套语义：靠 Destination 头指定目标。"""
        if not self.base_url:
            return False
        status, _ = await self._request(
            method,
            path,
            headers={
                "Destination": self._url_for(dest_path),
                # 覆盖同名目标，避免 412；用户的意图就是移动过去
                "Overwrite": "T",
            },
        )
        return 200 <= status < 300

    async def rename(
        self, path: str, new_name: str, *, file_id: str | None = None
    ) -> bool:
        name = str(new_name or "").strip()
        if not name:
            return False
        target = self.normalize_path(path)
        parent = self.join_path(*target.split("/")[:-1])
        return await self._move_or_copy("MOVE", target, self.join_path(parent, name))

    async def move(
        self, path: str, target_dir: str, *, file_id: str | None = None
    ) -> bool:
        target = self.normalize_path(path)
        name = target.split("/")[-1]
        if not name:
            return False
        return await self._move_or_copy(
            "MOVE", target, self.join_path(target_dir, name)
        )

    async def copy(
        self, path: str, target_dir: str, *, file_id: str | None = None
    ) -> bool:
        target = self.normalize_path(path)
        name = target.split("/")[-1]
        if not name:
            return False
        return await self._move_or_copy(
            "COPY", target, self.join_path(target_dir, name)
        )

    async def keep_alive(self) -> tuple[bool, str]:
        """WebDAV 是无状态 Basic 认证，探活等价于健康检查。"""
        return await self.health_check()

    def auth_header(self) -> dict[str, str]:
        """给 302 播放/STRM 场景用的 Basic Auth 头。"""
        import base64

        auth = self._auth
        if not auth:
            return {}
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    async def health_check(self) -> tuple[bool, str]:
        if not self.base_url:
            return False, "未配置 WebDAV 地址"
        status, text = await self._request(
            "PROPFIND",
            "/",
            body='<?xml version="1.0" encoding="utf-8"?>'
            '<d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/></d:prop></d:propfind>',
            headers={"Depth": "1"},
        )
        if status in (200, 207):
            count = len(self._parse_propfind(text, "/"))
            return True, f"连接正常，根目录 {count} 个条目"
        if status == 401:
            return False, "认证失败，请检查用户名与密码"
        if status == 0:
            return False, "无法连接，请检查地址与网络"
        return False, f"WebDAV 返回状态码 {status}"

    def describe(self) -> dict[str, Any]:
        """供界面展示的能力说明。"""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "supports_save": self.supports_save,
            "supports_delete": self.supports_delete,
            "direct_link": True,
        }
