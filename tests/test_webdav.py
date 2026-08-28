"""WebDAV 存储 Provider 测试：用本地假 DAV 服务器，全程离线不触外网。"""

from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.providers.panstorage.base import BasePanStorage
from app.providers.panstorage.webdav import WebDavStorage
from app.providers.registry import list_providers, load_builtin_providers
from app.schemas.enums import ProviderKind

load_builtin_providers()

MULTISTATUS = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
 <d:response><d:href>/dav/media/</d:href><d:propstat><d:prop>
   <d:resourcetype><d:collection/></d:resourcetype></d:prop>
   <d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>
 <d:response><d:href>/dav/media/%E5%BA%86%E4%BD%99%E5%B9%B4/</d:href><d:propstat><d:prop>
   <d:resourcetype><d:collection/></d:resourcetype>
   <d:getlastmodified>Tue, 01 Apr 2025 10:00:00 GMT</d:getlastmodified></d:prop>
   <d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>
 <d:response><d:href>/dav/media/movie.mkv</d:href><d:propstat><d:prop>
   <d:resourcetype/><d:getcontentlength>1048576</d:getcontentlength></d:prop>
   <d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>
</d:multistatus>"""

QUOTA = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:"><d:response><d:href>/dav/</d:href><d:propstat><d:prop>
<d:quota-available-bytes>300</d:quota-available-bytes>
<d:quota-used-bytes>700</d:quota-used-bytes></d:prop></d:propstat></d:response></d:multistatus>"""


class _Handler(BaseHTTPRequestHandler):
    """最小可用的假 WebDAV 服务端。"""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, code: int, body: bytes = b"") -> None:
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_PROPFIND(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode()
        # 没带认证一律 401，用来验证 Provider 的错误提示
        if not self.headers.get("Authorization"):
            return self._send(401)
        return self._send(207, (QUOTA if "quota" in raw else MULTISTATUS).encode())

    def do_MKCOL(self):
        self._send(201)

    def do_DELETE(self):
        self._send(204)


@pytest.fixture(scope="module")
def dav_server():
    """起一个后台假 DAV 服务器，返回它的端口。"""
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


@pytest.fixture
def storage(dav_server):
    return WebDavStorage(
        {
            "url": f"http://127.0.0.1:{dav_server}/dav",
            "username": "u",
            "password": "p",
            "options": {"embed_credentials": 1},
        }
    )


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------- 契约
def test_webdav_registered_as_panstorage():
    """WebDAV 必须注册到 panstorage 分类，界面才能选到它。"""
    names = {item["name"] for item in list_providers(ProviderKind.PANSTORAGE.value)}
    assert "webdav" in names
    assert issubclass(WebDavStorage, BasePanStorage)
    assert WebDavStorage.kind == ProviderKind.PANSTORAGE.value


def test_webdav_does_not_pretend_to_support_share_save(storage):
    """WebDAV 没有分享转存概念：要明确说不支持，而不是假装成功。"""
    assert WebDavStorage.supports_save is False
    result = run(storage.save_share("https://pan.example.com/s/abc"))
    assert result.success is False
    assert "不支持" in result.message


# ------------------------------------------------------------------- 列目录
def test_list_dir_parses_multistatus(storage):
    """PROPFIND 响应要解析成目录在前、文件在后，且剔除「自身」条目。"""
    files = run(storage.list_dir("/media"))
    assert [item.name for item in files] == ["庆余年", "movie.mkv"]
    assert files[0].is_dir is True
    assert files[1].is_dir is False
    assert files[1].size == 1048576
    # 挂载前缀 /dav 要被剥掉，换算成网盘内部路径
    assert files[0].path == "/media/庆余年"
    assert files[1].path == "/media/movie.mkv"


def test_list_dir_returns_empty_on_unreachable_server():
    """服务器连不上时返回空列表而不是抛异常（优雅降级）。"""
    storage = WebDavStorage({"url": "http://127.0.0.1:1/dav", "username": "u", "password": "p"})
    assert run(storage.list_dir("/")) == []


def test_parse_propfind_tolerates_broken_xml(storage):
    """服务端返回非 XML（如反代的 HTML 错误页）时不能崩。"""
    assert storage._parse_propfind("<html>502 Bad Gateway</html>", "/") == []


# ------------------------------------------------------------------- 容量
def test_quota_sums_available_and_used(storage):
    """WebDAV 只给「可用」和「已用」，总量要自己加出来。"""
    quota = run(storage.quota())
    assert quota.used == 700
    assert quota.total == 1000
    assert quota.free == 300
    assert quota.percent == 70.0


# ------------------------------------------------------------------- 写操作
def test_mkdir_and_delete(storage):
    assert run(storage.mkdir("/media/新建目录")) is True
    assert run(storage.delete("/media/movie.mkv")) is True


def test_download_url_encodes_and_embeds_credentials(storage):
    """中文与空格要 percent 编码；开了 embed_credentials 才内嵌账号密码。"""
    url = run(storage.download_url("/media/庆余年/第 1 集.mkv"))
    assert "u:p@127.0.0.1" in url
    assert "%E5%BA%86%E4%BD%99%E5%B9%B4" in url
    assert "%E7%AC%AC%201%20%E9%9B%86.mkv" in url or "%E7%AC%AC+1+%E9%9B%86.mkv" in url


def test_download_url_without_embed_option(dav_server):
    """默认不内嵌凭据——URL 里出现明文密码是有风险的，必须显式开启。"""
    storage = WebDavStorage(
        {"url": f"http://127.0.0.1:{dav_server}/dav", "username": "u", "password": "p"}
    )
    url = run(storage.download_url("/media/movie.mkv"))
    assert "u:p@" not in url
    assert url.endswith("/media/movie.mkv")


def test_auth_header_is_basic(storage):
    """302/STRM 场景要能拿到 Basic Auth 头。"""
    header = storage.auth_header()
    assert header["Authorization"].startswith("Basic ")
    assert WebDavStorage({"url": "http://x"}).auth_header() == {}


# ------------------------------------------------------------------- 健康检查
def test_health_check_success(storage):
    ok, message = run(storage.health_check())
    assert ok is True
    assert "根目录" in message


def test_health_check_reports_auth_failure(dav_server):
    """不填账号 → 假服务器回 401 → 要给出「认证失败」而不是笼统报错。"""
    storage = WebDavStorage({"url": f"http://127.0.0.1:{dav_server}/dav"})
    ok, message = run(storage.health_check())
    assert ok is False
    assert "认证失败" in message


def test_health_check_without_url():
    ok, message = run(WebDavStorage({}).health_check())
    assert ok is False
    assert "未配置" in message


def test_describe_declares_direct_link(storage):
    """WebDAV 天然支持直链，STRM 页面要据此展示能力。"""
    info = storage.describe()
    assert info["direct_link"] is True
    assert info["supports_save"] is False
