"""下载器 Provider 的连接与容错回归测试（不依赖真实网络）。

**这些测试钉住的是一类"看着配对了却不工作"的故障**，全部来自 v1.11.0 修
qBittorrent"调用不了"时的实测复现——那次发现的不是一个 bug 而是四个：

1. 会话失效后**永不重新登录**（最主要成因）：qB 的 SID 会过期、qB 重启也会
   让旧会话作废，而 Provider 缓存了 client 就一直复用，之后每个请求都 403，
   表现为"一开始能用、过一阵全挂"，日志里只有一串 403 看不出原因；
2. 地址漏协议（``127.0.0.1:8080``）→ httpx 直接抛"缺少 http://"；
3. 地址首尾带空格（复制粘贴极常见）→ 同样报缺协议，但界面上"看着是对的"；
4. 没填用户名时不做探测就当"免密可用"→ 之后每个请求 403。

用 ``httpx.MockTransport`` 在传输层仿真下载器，因此不需要起真服务。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import unquote_plus

import httpx
import pytest

from app.providers.downloader.aria2 import Aria2Downloader
from app.providers.downloader.qbittorrent import QbittorrentDownloader
from app.providers.downloader.transmission import TransmissionDownloader
from app.providers.downloader.xunlei import XunleiDownloader
from app.utils.http import normalize_endpoint


def _patch_transport(monkeypatch: pytest.MonkeyPatch, module: Any, handler: Any) -> None:
    """让目标模块里创建的 AsyncClient 都走 MockTransport。"""
    real = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", factory)


# --------------------------------------------------------------------------
# 地址规范化：三个下载器共用
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("127.0.0.1:8080", "http://127.0.0.1:8080"),
        ("  http://127.0.0.1:8080  ", "http://127.0.0.1:8080"),
        ("\u3000http://127.0.0.1:8080", "http://127.0.0.1:8080"),
        ("//nas.local:8080", "http://nas.local:8080"),
        ("http://nas.local:8080/", "http://nas.local:8080"),
        ("https://nas.local", "https://nas.local"),
        ("", "http://fallback:1"),
        (None, "http://fallback:1"),
    ],
)
def test_normalize_endpoint(raw: str | None, expected: str) -> None:
    assert normalize_endpoint(raw, default="http://fallback:1") == expected


def test_qbittorrent_base_url_survives_sloppy_input() -> None:
    """漏协议/带空格都要能用——这是用户最常见的两种输入。"""
    assert (
        QbittorrentDownloader({"url": " 10.0.0.2:8080 "}).base_url
        == "http://10.0.0.2:8080"
    )
    assert QbittorrentDownloader({}).base_url == "http://127.0.0.1:8080"


def test_transmission_url_keeps_rpc_path_and_protocol() -> None:
    """飞牛 fnOS / 群晖自带的就是 Transmission，默认端点必须带协议与 /rpc。"""
    assert (
        TransmissionDownloader({"url": "127.0.0.1:9091"}).base_url
        == "http://127.0.0.1:9091/transmission/rpc"
    )
    assert (
        TransmissionDownloader({"url": "http://nas:9091/transmission/rpc"}).base_url
        == "http://nas:9091/transmission/rpc"
    )
    assert TransmissionDownloader({}).base_url == "http://127.0.0.1:9091/transmission/rpc"


def test_aria2_url_keeps_jsonrpc_path_and_protocol() -> None:
    assert Aria2Downloader({"url": "127.0.0.1:6800"}).base_url == "http://127.0.0.1:6800/jsonrpc"
    assert Aria2Downloader({}).base_url == "http://127.0.0.1:6800/jsonrpc"


# --------------------------------------------------------------------------
# qBittorrent 登录与会话
# --------------------------------------------------------------------------


class _QbFake:
    """仿真 qBittorrent Web API v2。"""

    def __init__(self, *, password: str = "pw", require_auth: bool = True) -> None:
        self.password = password
        self.require_auth = require_auth
        self.logins = 0
        self.login_attempts = 0
        self.sid = "SID1"
        self.ban = False
        self.calls: list[str] = []
        self.torrents: list[dict[str, Any]] = []
        self.deleted_tags: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(path)
        if path == "/api/v2/auth/login":
            if self.ban:
                return httpx.Response(403, text="")
            self.login_attempts += 1
            body = request.content.decode()
            if f"password={self.password}" not in body:
                return httpx.Response(200, text="Fails.")
            self.logins += 1
            return httpx.Response(
                200, text="Ok.", headers={"set-cookie": f"SID={self.sid}; Path=/"}
            )
        # 业务接口：校验会话
        if self.require_auth:
            cookie = request.headers.get("cookie") or ""
            if f"SID={self.sid}" not in cookie:
                return httpx.Response(403, text="Forbidden")
        if path == "/api/v2/app/version":
            return httpx.Response(200, text="v5.0.4")
        if path == "/api/v2/torrents/add":
            body = dict(
                item.split("=", 1)
                for item in request.content.decode().split("&")
                if "=" in item
            )
            name = unquote_plus(body.get("urls", ""))
            tags = unquote_plus(body.get("tags", ""))
            self.torrents.append(
                {
                    "hash": f"h{len(self.torrents) + 1:04d}",
                    "name": name,
                    "state": "downloading",
                    "progress": 0.1,
                    "size": 100,
                    "completed": 10,
                    "tags": tags,
                }
            )
            return httpx.Response(200, text="Ok.")
        if path == "/api/v2/torrents/deleteTags":
            body = dict(
                item.split("=", 1)
                for item in request.content.decode().split("&")
                if "=" in item
            )
            gone = unquote_plus(body.get("tags", ""))
            self.deleted_tags.append(gone)
            for item in self.torrents:
                item["tags"] = ",".join(
                    x for x in item["tags"].split(",") if x != gone
                )
            return httpx.Response(200, text="Ok.")
        if path == "/api/v2/torrents/info":
            params = request.url.params
            items = list(self.torrents)
            if "tag" in params:
                wanted = params["tag"]
                items = [i for i in items if wanted in i["tags"].split(",")]
            return httpx.Response(200, json=items)
        return httpx.Response(200, text="Ok.")


@pytest.mark.asyncio
async def test_qbittorrent_healthy_with_correct_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.providers.downloader import qbittorrent as mod

    fake = _QbFake()
    _patch_transport(monkeypatch, mod, fake)
    dl = mod.QbittorrentDownloader(
        {"url": "127.0.0.1:8080", "username": "admin", "password": "pw"}
    )
    assert await dl.health_check() == (True, "连接正常，版本 v5.0.4")


@pytest.mark.asyncio
async def test_qbittorrent_relogins_after_session_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """会话失效后必须自动重新登录——这是 qB"用一会儿就全挂"的根因。

    这条是本组最重要的断言：把 ``_request`` 的重试逻辑去掉，它会立刻变红。
    """
    from app.providers.downloader import qbittorrent as mod

    fake = _QbFake()
    _patch_transport(monkeypatch, mod, fake)
    dl = mod.QbittorrentDownloader(
        {"url": "http://127.0.0.1:8080", "username": "admin", "password": "pw"}
    )
    assert (await dl.health_check())[0] is True
    assert fake.logins == 1

    # 模拟 qB 重启：旧 SID 立即作废
    fake.sid = "SID2"
    ok, message = await dl.health_check()
    assert ok is True, f"会话失效后没能自愈：{message}"
    assert fake.logins == 2, "没有重新登录，说明会话失效自愈逻辑丢了"


@pytest.mark.asyncio
async def test_qbittorrent_relogin_happens_only_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """密码真的错时不能无限重试，否则每个请求都要多打几轮。"""
    from app.providers.downloader import qbittorrent as mod

    fake = _QbFake()
    _patch_transport(monkeypatch, mod, fake)
    dl = mod.QbittorrentDownloader(
        {"url": "http://127.0.0.1:8080", "username": "admin", "password": "wrong"}
    )
    ok, message = await dl.health_check()
    assert ok is False
    assert "用户名或密码错误" in message
    assert fake.logins == 0
    # 只尝试一次：密码真错时重试再多也没用，只会拖慢每个请求
    assert fake.login_attempts == 1


@pytest.mark.asyncio
async def test_qbittorrent_reports_auth_required_when_no_username(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """留空账号时要先探测，不能默认"免密可用"后让每个请求都 403。"""
    from app.providers.downloader import qbittorrent as mod

    fake = _QbFake()
    _patch_transport(monkeypatch, mod, fake)
    dl = mod.QbittorrentDownloader({"url": "http://127.0.0.1:8080"})
    ok, message = await dl.health_check()
    assert ok is False
    assert "需要认证" in message


@pytest.mark.asyncio
async def test_qbittorrent_passwordless_instance_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """qB 勾了本机免密时，不填账号也应当能用。"""
    from app.providers.downloader import qbittorrent as mod

    fake = _QbFake(require_auth=False)
    _patch_transport(monkeypatch, mod, fake)
    dl = mod.QbittorrentDownloader({"url": "http://127.0.0.1:8080"})
    assert (await dl.health_check())[0] is True


@pytest.mark.asyncio
async def test_qbittorrent_explains_login_ban(monkeypatch: pytest.MonkeyPatch) -> None:
    """403 是"失败次数过多被临时封禁"，要说清楚而不是笼统报错。"""
    from app.providers.downloader import qbittorrent as mod

    fake = _QbFake()
    fake.ban = True
    _patch_transport(monkeypatch, mod, fake)
    dl = mod.QbittorrentDownloader(
        {"url": "http://127.0.0.1:8080", "username": "admin", "password": "pw"}
    )
    ok, message = await dl.health_check()
    assert ok is False
    assert "403" in message and "封禁" in message


@pytest.mark.asyncio
async def test_qbittorrent_unreachable_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    """连不上时要给出可读原因且不抛异常。"""
    from app.providers.downloader import qbittorrent as mod

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    _patch_transport(monkeypatch, mod, boom)
    dl = mod.QbittorrentDownloader(
        {"url": "http://127.0.0.1:8080", "username": "admin", "password": "pw"}
    )
    ok, message = await dl.health_check()
    assert ok is False
    assert "无法连接" in message
    assert await dl.list_tasks() == []
    assert await dl.get("abc") is None
    assert await dl.remove("abc") is False


@pytest.mark.asyncio
async def test_qbittorrent_concurrent_adds_get_distinct_hashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """并发投递时每个任务必须拿到**自己**的 hash。

    原实现用"添加前后 hash 集合取差集"定位新任务，实测同时添加 3 个磁力时
    **三次调用返回同一个 hash**——三个下载任务的进度与完成判定会全部盯在
    同一个种子上（后两个永远不会被判定完成）。改用一次性标签定位后修复。
    """
    from app.providers.downloader import qbittorrent as mod

    fake = _QbFake()
    _patch_transport(monkeypatch, mod, fake)
    dl = mod.QbittorrentDownloader(
        {"url": "http://127.0.0.1:8080", "username": "admin", "password": "pw"}
    )
    links = [f"magnet:?xt=urn:btih:{c * 6}" for c in "ABC"]
    ids = await asyncio.gather(*[dl.add(link) for link in links])

    assert all(ids), f"有任务没拿到 hash：{ids}"
    assert len(set(ids)) == 3, f"并发添加拿到重复 hash：{ids}"
    # 每个 hash 必须对应自己提交的那条链接
    by_hash = {item["hash"]: item["name"] for item in fake.torrents}
    for link, got in zip(links, ids, strict=True):
        assert by_hash[got] == link, f"{link} 拿到的却是 {by_hash[got]}"


@pytest.mark.asyncio
async def test_qbittorrent_cleans_up_temp_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    """一次性定位标签用完必须删掉，否则 qB 标签列表会被垃圾标签淹没。

    同时保留用户配置的正式标签（默认 CineFlow）。
    """
    from app.providers.downloader import qbittorrent as mod

    fake = _QbFake()
    _patch_transport(monkeypatch, mod, fake)
    dl = mod.QbittorrentDownloader(
        {"url": "http://127.0.0.1:8080", "username": "admin", "password": "pw"}
    )
    assert await dl.add("magnet:?xt=urn:btih:AAAAAA")
    remaining = fake.torrents[0]["tags"]
    assert "cineflow-" not in remaining, f"临时标签残留：{remaining}"
    assert remaining == "CineFlow", remaining
    assert any(tag.startswith("cineflow-") for tag in fake.deleted_tags)


# --------------------------------------------------------------------------
# 迅雷（NAS 本地 CGI）
# --------------------------------------------------------------------------

_XL_PAGE = 'function uiauth(value){ return "LOCALJWT"; }'


class _XunleiFake:
    """仿真 NAS 上迅雷套件的 index.cgi。"""

    def __init__(self, *, bound: bool = True) -> None:
        self.token = "LOCALJWT"
        self.bound = bound
        self.page_hits = 0
        self.tasks: list[dict[str, Any]] = []
        self.created_dirs: list[str] = []
        self.submitted: dict[str, Any] | None = None
        self.deleted: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        query = request.url.query.decode()
        if path.endswith("/index.cgi/"):
            self.page_hits += 1
            return httpx.Response(200, text=_XL_PAGE)
        if request.headers.get("pan-auth") != self.token:
            return httpx.Response(403, json={"error_code": 403})
        if "type=user%23runner" in query:
            if not self.bound:
                return httpx.Response(500, json={"error": "not login account"})
            return httpx.Response(
                200,
                json={
                    "tasks": [
                        {"name": "fnos", "params": {"target": "DEV#1"}},
                        {"name": "syno", "params": {"target": "DEV#2"}},
                    ]
                },
            )
        if path.endswith("/drive/v1/files") and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "files": [
                        {"id": "F1", "parent_id": "", "name": "迅雷下载"},
                        {"id": "F2", "parent_id": "", "name": "电影"},
                    ]
                },
            )
        if path.endswith("/drive/v1/files") and request.method == "POST":
            body = json.loads(request.content)
            self.created_dirs.append(str(body.get("name")))
            return httpx.Response(200, json={"file": {"id": "SUB1"}})
        if path.endswith("/drive/v1/resource/list"):
            return httpx.Response(
                200,
                json={
                    "list": {
                        "resources": [
                            {
                                "name": "Movie.2024.2160p",
                                "file_count": 2,
                                "is_dir": True,
                                "dir": {
                                    "resources": [
                                        {
                                            "name": "a.mkv",
                                            "file_index": 0,
                                            "file_size": 100,
                                            "is_dir": False,
                                        },
                                        {
                                            "name": "b.srt",
                                            "file_index": 1,
                                            "file_size": 20,
                                            "is_dir": False,
                                        },
                                    ]
                                },
                            }
                        ]
                    }
                },
            )
        if path.endswith("/drive/v1/task") and request.method == "POST":
            self.submitted = json.loads(request.content)
            self.tasks.append(
                {
                    "id": "T1",
                    "name": self.submitted.get("name"),
                    "phase": "PHASE_TYPE_RUNNING",
                    "progress": 42,
                    "file_size": 120,
                    "params": {"speed": 2048, "real_path": "/vol1/迅雷下载/Movie"},
                }
            )
            return httpx.Response(200, json={"HttpStatus": 0, "task": {"id": "T1"}})
        if path.endswith("/drive/v1/tasks") and request.method == "DELETE":
            ids = httpx.QueryParams(query).get("task_ids", "").split(",")
            self.deleted.extend(ids)
            self.tasks = [t for t in self.tasks if t["id"] not in ids]
            return httpx.Response(200, json={})
        if path.endswith("/drive/v1/tasks"):
            done = "PHASE_TYPE_COMPLETE" in query and "PENDING" not in query
            return httpx.Response(
                200,
                json={
                    "tasks": [
                        t
                        for t in self.tasks
                        if (t["phase"] == "PHASE_TYPE_COMPLETE") == done
                    ]
                },
            )
        return httpx.Response(404, json={})


def _xunlei(monkeypatch: pytest.MonkeyPatch, fake: Any, config: dict[str, Any] | None = None):
    from app.providers.downloader import xunlei as mod

    _patch_transport(monkeypatch, mod, fake)
    base = {"url": "127.0.0.1:5055"}
    base.update(config or {})
    return mod.XunleiDownloader(base)


def test_xunlei_is_registered_as_downloader() -> None:
    from app.providers.registry import get_provider_class, load_builtin_providers

    load_builtin_providers()
    cls = get_provider_class("xunlei")
    assert cls is XunleiDownloader
    assert cls.kind == "downloader"


def test_xunlei_default_endpoint_normalized() -> None:
    assert XunleiDownloader({}).base_url == "http://127.0.0.1:5055"
    assert XunleiDownloader({"url": " nas.local:5055 "}).base_url == "http://nas.local:5055"


@pytest.mark.asyncio
async def test_xunlei_health_check_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    dl = _xunlei(monkeypatch, _XunleiFake())
    ok, message = await dl.health_check()
    assert ok is True, message


@pytest.mark.asyncio
async def test_xunlei_add_magnet_submits_expected_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """提交磁力要先解析资源再建任务，并把文件下标一并带上。"""
    fake = _XunleiFake()
    dl = _xunlei(monkeypatch, fake)
    task_id = await dl.add("magnet:?xt=urn:btih:AAA", save_path="/downloads/movies")
    assert task_id == "T1"
    assert fake.submitted is not None
    params = fake.submitted["params"]
    assert params["url"] == "magnet:?xt=urn:btih:AAA"
    assert params["target"] == "DEV#1"
    assert params["sub_file_index"] == "0,1"
    assert params["total_file_count"] == "2"
    assert fake.submitted["file_size"] == "120"
    # save_path 的最后一段被当作子目录名，迅雷不接受任意本地路径
    assert fake.created_dirs == ["movies"]
    assert params["parent_folder_id"] == "SUB1"


@pytest.mark.asyncio
async def test_xunlei_list_and_get_map_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    """迅雷的 progress 是 0-100，必须换算成内部的 0-1。"""
    fake = _XunleiFake()
    dl = _xunlei(monkeypatch, fake)
    await dl.add("magnet:?xt=urn:btih:AAA")
    tasks = await dl.list_tasks()
    assert len(tasks) == 1
    state = tasks[0]
    assert state.external_id == "T1"
    assert state.status == "downloading"
    assert state.progress == pytest.approx(0.42)
    assert state.size == 120
    assert state.downloaded == 50
    assert state.speed == 2048
    assert (await dl.get("T1")) is not None
    assert (await dl.get("missing")) is None


@pytest.mark.asyncio
async def test_xunlei_remove(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _XunleiFake()
    dl = _xunlei(monkeypatch, fake)
    await dl.add("magnet:?xt=urn:btih:AAA")
    assert await dl.remove("T1", delete_files=True) is True
    assert fake.deleted == ["T1"]
    assert await dl.list_tasks() == []


@pytest.mark.asyncio
async def test_xunlei_refreshes_local_token_when_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """套件重启会换本地 token，缓存失效后必须重新抠页面。

    和 qB 那条同源的教训：只要缓存凭据，就必须有失效重取的路径。
    """
    fake = _XunleiFake()
    dl = _xunlei(monkeypatch, fake)
    assert (await dl.health_check())[0] is True
    hits = fake.page_hits
    dl._auth = "STALE"
    tasks = await dl.list_tasks()
    assert fake.page_hits > hits, "没有重新获取本地鉴权 token"
    assert tasks == []


@pytest.mark.asyncio
async def test_xunlei_selects_device_and_root_dir_by_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一个迅雷账号可绑多台 NAS，要能按名字选设备与下载目录。"""
    dl = _xunlei(
        monkeypatch,
        _XunleiFake(),
        {"options": {"device_name": "syno", "download_root_dir": "电影"}},
    )
    assert (await dl.health_check())[0] is True
    assert dl._device_id == "DEV#2"
    assert dl._folder_id == "F2"


@pytest.mark.asyncio
async def test_xunlei_unknown_device_name_is_explained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dl = _xunlei(monkeypatch, _XunleiFake(), {"options": {"device_name": "nope"}})
    ok, message = await dl.health_check()
    assert ok is False
    assert "nope" in message


@pytest.mark.asyncio
async def test_xunlei_unbound_account_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """套件没绑迅雷账号时接口回 500，要给出可执行的提示。

    早期实现会把这种情况笼统报成"没有绑定任何远程设备"，
    把用户引到错误的排查方向，所以这里钉住措辞。
    """
    dl = _xunlei(monkeypatch, _XunleiFake(bound=False))
    ok, message = await dl.health_check()
    assert ok is False
    assert "未绑定账号" in message


@pytest.mark.asyncio
async def test_xunlei_unreachable_degrades_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NAS 关机/地址填错时，所有方法都必须优雅降级而不是抛异常。"""

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    dl = _xunlei(monkeypatch, boom)
    ok, message = await dl.health_check()
    assert ok is False
    assert "无法连接" in message
    assert await dl.add("magnet:?xt=urn:btih:AAA") is None
    assert await dl.list_tasks() == []
    assert await dl.get("T1") is None
    assert await dl.remove("T1") is False
    # 迅雷没有暂停/恢复接口，基类默认返回 False
    assert await dl.pause("T1") is False
    assert await dl.resume("T1") is False
