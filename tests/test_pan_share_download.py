"""下载意图、跨平台账号隔离与原文件解析契约（不使用真实账号）。"""

import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest

from app.core.pan_links import pan_family
from app.providers.downloader.aria2 import Aria2Downloader
from app.providers.panstorage import alipan, baidu, pan115, quark
from app.providers.panstorage.base import PanFile
from app.schemas.models import DownloadRequest
from app.services import download, download_routing, pan_download, pan_storage
from app.utils.pan115_rsa import encrypt


@pytest.mark.parametrize(
    ("url", "family"),
    [
        ("https://pan.baidu.com/s/test", "baidu"),
        ("https://pan.quark.cn/s/test", "quark"),
        ("https://www.alipan.com/s/test", "alipan"),
        ("https://www.aliyundrive.com/s/test", "alipan"),
        ("https://115cdn.com/s/test", "pan115"),
        ("https://115.com/s/test", "pan115"),
        ("https://evil.test/?url=pan.baidu.com", None),
        ("https://pan.quark.cn.evil.test/s/id", None),
        ("file://pan.baidu.com/s/test", None),
        ("https://pan.baidu.com@evil.test", None),
        ("https://[invalid", None),
    ],
)
def test_share_platform_uses_hostname(url, family):
    assert pan_family(url) == family


@pytest.mark.parametrize(
    "url", ["https://pan.quark.cn/s/a", "https://alipan.com/s/a", "https://115.com/s/a"]
)
def test_other_platform_never_uses_baidu_account(monkeypatch, url):
    monkeypatch.setattr(
        pan_storage, "storages", lambda: [baidu.BaiduPanStorage({"cookie": "expired"})]
    )
    assert pan_storage._pick_for_share(url) is None
    result = asyncio.run(pan_storage.prepare_share_download(url))
    assert not result["success"]
    assert "账号" in result["message"]
    assert "百度" not in result["message"]


def test_default_download_does_not_save_and_does_not_persist_signed_urls(
    monkeypatch, client, tmp_path
):
    assert (
        DownloadRequest(title="Test", link="https://115.com/s/a", kind="pan").pan_mode == "download"
    )

    async def forbidden(*args, **kwargs):
        pytest.fail("下载入口不得执行转存")

    async def prepare(*args):
        return {
            "success": True,
            "origin": "share",
            "site_id": 55,
            "storage": "115",
            "saved_path": "/",
            "download_files": [
                {
                    "relative": "Show/one.mkv",
                    "path": "/Show/one.mkv",
                    "url": "https://cdn.test/signed?secret=temporary",
                    "headers": {"Cookie": "private"},
                }
            ],
        }

    calls = []
    downloader = Aria2Downloader({"name": "aria2"})

    async def rpc(method, params=None):
        calls.append((method, params))
        return "gid-download"

    monkeypatch.setattr(downloader, "_call", rpc)
    monkeypatch.setattr(download, "_try_auto_save_pan", forbidden)
    monkeypatch.setattr(pan_storage, "save_share", forbidden)
    monkeypatch.setattr(pan_storage, "prepare_share_download", prepare)
    monkeypatch.setattr(download_routing, "candidates_for", lambda *args: [downloader])
    task = asyncio.run(
        download.add_download(
            {"title": "Test", "link": "https://115.com/s/a", "kind": "pan"},
            save_path=str(tmp_path),
            notify=False,
        )
    )
    assert task.status == "downloading"
    assert task.meta["pan_origin"] == "share"
    assert task.meta["pan_download_ids"] == ["gid-download"]
    assert "secret" not in json.dumps(task.meta)
    assert "private" not in json.dumps(task.meta)
    assert calls[0][1][1]["header"] == ["Cookie: private"]


@pytest.mark.parametrize("metadata", [{"pan_origin": "share"}, {}])
def test_retry_share_download_never_transfers(monkeypatch, client, metadata):
    from app.db.models import DownloadTask
    from app.db.session import session_scope

    with session_scope() as session:
        task = DownloadTask(
            title="retry direct",
            link="https://115.com/s/retry",
            kind="pan",
            status="pending",
            meta=metadata,
        )
        session.add(task)
        session.flush()
        task_id = task.id

    async def prepare(*args):
        return {"success": False, "message": "115 账号失效"}

    async def forbidden(link, **kwargs):
        assert link != "https://115.com/s/retry", "直接下载重试不得转存"
        return {"success": False, "message": "unrelated fixture"}

    monkeypatch.setattr(pan_storage, "storages", lambda: [object()])
    monkeypatch.setattr(pan_storage, "prepare_share_download", prepare)
    monkeypatch.setattr(pan_storage, "save_share", forbidden)
    stats = asyncio.run(pan_storage.transfer_pending(task_id=task_id, notify=False))
    assert stats["pending"] == 1
    with session_scope() as session:
        assert session.get(DownloadTask, task_id).error == "115 账号失效"


def test_quark_authorized_share_and_cookie_scope(monkeypatch):
    storage = quark.QuarkStorage({"cookie": "quark-account"})
    calls = []

    async def call(path, **kwargs):
        calls.append(path)
        if path.endswith("/token"):
            return {"data": {"stoken": "share-token"}}
        if path.endswith("/detail"):
            return {"data": {"list": [{"fid": "1", "file_name": "one.mkv", "file_type": 1}]}}
        return {"data": [{"fid": "1", "download_url": "https://cdn.quark.cn/one"}]}

    monkeypatch.setattr(storage, "_call", call)
    files = asyncio.run(storage.share_downloads("https://pan.quark.cn/s/test"))
    assert files[0].download_url == "https://cdn.quark.cn/one"
    assert all("save" not in path for path in calls)
    assert storage.download_headers(files[0].download_url)["Cookie"] == "quark-account"
    assert "Cookie" not in storage.download_headers("https://quark.cn.evil.test/one")
    assert "Cookie" not in storage.download_headers("http://cdn.quark.cn/one")


def test_quark_permission_denied_does_not_save(monkeypatch):
    storage = quark.QuarkStorage({"cookie": "account"})

    async def listing(*args, **kwargs):
        return [PanFile("one.mkv", "/one.mkv", file_id="1")]

    async def call(path, **kwargs):
        assert path == "/file/download"
        return None

    monkeypatch.setattr(storage, "list_share", listing)
    monkeypatch.setattr(storage, "_call", call)
    with pytest.raises(ValueError, match="未授权直接下载"):
        asyncio.run(storage.share_downloads("https://pan.quark.cn/s/test"))


def test_quark_partial_share_listing_does_not_download(monkeypatch):
    storage = quark.QuarkStorage({"cookie": "account"})
    monkeypatch.setattr(quark, "_SHARE_PAGE_SIZE", 1)

    async def call(path, **kwargs):
        if path.endswith("/token"):
            return {"data": {"stoken": "token"}}
        assert path.endswith("/detail"), "目录未完整读取，不得换取直链或转存"
        batch = (
            [{"fid": "1", "file_name": "one.mkv", "file_type": 1}]
            if kwargs["params"]["_page"] == 1
            else None
        )
        return {"data": {"list": batch}}

    monkeypatch.setattr(storage, "_call", call)
    with pytest.raises(ValueError, match="未完整"):
        asyncio.run(storage.share_downloads("https://pan.quark.cn/s/test"))


def test_baidu_partial_share_listing_does_not_download(monkeypatch):
    storage = baidu.BaiduPanStorage({})
    monkeypatch.setattr(baidu, "_SHARE_PAGE_SIZE", 1)

    async def context(*args):
        return {
            "shareid": "1",
            "uk": "2",
            "bdstoken": "token",
            "headers": {},
            "sign": "s",
            "timestamp": "1",
        }, ""

    async def fetch(url, **kwargs):
        assert url.endswith("/share/list"), "目录未完整读取，不得换取直链或转存"
        batch = (
            [{"fs_id": "1", "server_filename": "one.mkv"}]
            if kwargs["params"]["page"] == 1
            else None
        )
        return {"errno": 0, "list": batch}

    monkeypatch.setattr(storage, "_share_context", context)
    monkeypatch.setattr(baidu, "fetch_json", fetch)
    with pytest.raises(ValueError, match="未完整"):
        asyncio.run(storage.share_downloads("https://pan.baidu.com/s/test"))


def test_alipan_refresh_share_pagination_headers_and_own_files(monkeypatch):
    storage = alipan.AlipanStorage({"password": "refresh-old"})
    calls = []

    async def fetch(
        url, *, method="GET", params=None, data=None, json_body=None, headers=None, timeout=None
    ):
        calls.append((url, json_body, headers))
        if url.endswith("/account/token"):
            return {
                "access_token": "access-new",
                "refresh_token": "refresh-new",
                "expires_in": 7200,
            }
        assert headers["Authorization"] == "Bearer access-new"
        if url.endswith("/get_share_token"):
            assert json_body["share_pwd"] == "1234"
            return {"share_token": "share-token"}
        if url.endswith("/user/get"):
            return {"resource_drive_id": "drive"}
        if url.endswith("/file/list"):
            if "share_id" in json_body:
                assert headers["x-share-token"] == "share-token"
            return {
                "items": [{"name": "one.mkv", "type": "file", "file_id": "1", "drive_id": "drive"}],
                "next_marker": "",
            }
        if url.endswith("/get_share_link_download_url"):
            assert json_body["share_id"] == "test"
            assert headers["x-share-token"] == "share-token"
        return {"download_url": "https://cdn.test/one"}

    monkeypatch.setattr(alipan, "fetch_json", fetch)
    files = asyncio.run(storage.share_downloads("https://www.alipan.com/s/test", password="1234"))
    assert files[0].download_url == "https://cdn.test/one"
    assert asyncio.run(storage.download_url("/one.mkv")) == "https://cdn.test/one"
    assert sum(url.endswith("/account/token") for url, _, _ in calls) == 1
    assert storage.config["password"] == "refresh-new"
    assert "refresh_token" not in storage.config["options"]
    assert all("save" not in url and "copy" not in url for url, _, _ in calls)
    assert storage.download_headers(files[0].download_url) == {"Referer": "https://www.alipan.com/"}
    monkeypatch.setattr(pan_storage, "storages", lambda: [storage])
    assert download_routing.has_pan_account()


def test_alipan_pagination_repetition_is_rejected(monkeypatch):
    storage = alipan.AlipanStorage({})

    async def call(*args, **kwargs):
        return {"items": [], "next_marker": "same"}

    monkeypatch.setattr(storage, "_call", call)
    with pytest.raises(ValueError, match="重复"):
        asyncio.run(storage._items("root", share_id="test"))


def test_alipan_rotated_credentials_are_not_exposed_in_site_api(monkeypatch, client, auth_headers):
    from app.db.models import SiteConfig
    from app.db.session import session_scope

    with session_scope() as session:
        site = SiteConfig(
            name="alipan-token-rotation", kind="panstorage", provider="alipan", enabled=False
        )
        session.add(site)
        session.flush()
        site_id = site.id
    storage = alipan.AlipanStorage({"id": site_id, "password": "old-private-refresh"})

    async def fetch(*args, **kwargs):
        return {
            "access_token": "new-private-access",
            "refresh_token": "new-private-refresh",
            "expires_in": 7200,
        }

    monkeypatch.setattr(alipan, "fetch_json", fetch)
    assert asyncio.run(storage._access()) == "new-private-access"
    response = client.get("/api/v1/sites", headers=auth_headers)
    exposed = next(item for item in response.json() if item["id"] == site_id)
    assert "private" not in json.dumps(exposed)
    assert exposed["has_credentials"]
    with session_scope() as session:
        assert session.get(SiteConfig, site_id).password == "new-private-refresh"


def test_115_share_download_uses_encrypted_app_endpoint(monkeypatch):
    storage = pan115.Pan115Storage({"cookie": "account"})
    requests = []

    async def call(path, **kwargs):
        assert path == "/share/snap"
        assert kwargs["params"]["receive_code"] == "1234"
        return {"state": True, "data": {"list": [{"n": "one.mkv", "fid": "1", "s": "12"}]}}

    async def fetch(url, **kwargs):
        requests.append((url, kwargs))
        assert url == "https://proapi.115.com/app/share/downurl"
        assert (
            kwargs["data"]["data"]
            == encrypt(
                json.dumps({"share_code": "test", "receive_code": "1234", "file_id": "1"})
            ).decode()
        )
        assert "115Browser" in kwargs["headers"]["User-Agent"]
        return {"state": True, "data": "encoded-response"}

    monkeypatch.setattr(storage, "_call", call)
    monkeypatch.setattr(pan115, "fetch_json", fetch)
    monkeypatch.setattr(pan115, "decrypt", lambda value: b'{"url":{"url":"https://cdn.test/one"}}')
    files = asyncio.run(storage.share_downloads("https://115cdn.com/s/test?password=1234"))
    assert files[0].download_url == "https://cdn.test/one"
    assert len(requests) == 1


def test_115_protocol_matches_upstream_vector_on_supported_pythons():
    # p115rsacipher 0.0.1 reference output; guards byte order and block padding.
    body = '{"share_code":"test","receive_code":"1234","file_id":"1"}'
    assert (
        hashlib.sha256(encrypt(body)).hexdigest()
        == "bf9540e75b12c54a5b24c9d5397f7525e92b42eb6dd723497adc668b65e3823a"
    )


def test_baidu_share_download_only_reads(monkeypatch):
    storage = baidu.BaiduPanStorage({})

    async def context(*args):
        return {
            "shareid": "share",
            "uk": "owner",
            "bdstoken": "token",
            "headers": {},
            "sign": "signature",
            "timestamp": "123",
        }, ""

    async def listing(*args):
        return [{"server_filename": "one.mkv", "fs_id": "1"}]

    async def fetch(url, **kwargs):
        assert url.endswith("/api/sharedownload")
        assert kwargs["data"]["fid_list"] == "[1]"
        return {"errno": 0, "list": [{"fs_id": "1", "dlink": "https://cdn.test/one"}]}

    monkeypatch.setattr(storage, "_share_context", context)
    monkeypatch.setattr(storage, "_share_list_dir", listing)
    monkeypatch.setattr(baidu, "fetch_json", fetch)
    assert (
        asyncio.run(storage.share_downloads("https://pan.baidu.com/s/test"))[0].download_url
        == "https://cdn.test/one"
    )
    assert "Cookie" not in storage.download_headers("https://baidu.com.evil.test/file")
    assert "Cookie" not in storage.download_headers("http://cdn.baidupcs.com/file")


@pytest.mark.parametrize(("key", "value"), [("X-Test\r\nCookie", "bad"), ("Cookie", "bad\nheader")])
def test_aria2_rejects_invalid_platform_headers(key, value):
    with pytest.raises(ValueError, match="换行"):
        asyncio.run(Aria2Downloader({}).add("https://cdn.test/file", headers={key: value}))


def test_cloud_download_routing_requires_aria2(monkeypatch):
    from app.services import sites

    downloaders = [
        SimpleNamespace(name=name, accepts=lambda kind: True) for name in ("xunlei", "aria2")
    ]
    monkeypatch.setattr(sites, "downloader_candidates", lambda prefer: downloaders)
    assert [item.name for item in download_routing.candidates_for("pan")] == ["aria2"]
    assert len(download_routing.candidates_for("direct")) == 2


@pytest.mark.parametrize("relative", ["../one.mkv", "/one.mkv", "C:/one.mkv", "x\\one.mkv"])
def test_share_path_rejected_before_aria2(relative):
    with pytest.raises(ValueError, match="路径"):
        asyncio.run(
            pan_download.resolve_files(
                {"download_files": [{"relative": relative, "url": "https://cdn.test/one"}]}
            )
        )


def test_partial_share_never_queues(monkeypatch):
    async def listing(*args, **kwargs):
        return [
            PanFile("one.mkv", "/one.mkv", download_url="https://cdn.test/one"),
            PanFile("two.mkv", "/two.mkv"),
        ]

    storage = SimpleNamespace(
        name="quark", site_name="Quark", supports_share_download=True, share_downloads=listing
    )
    monkeypatch.setattr(pan_storage, "storages", lambda: [storage])
    assert not asyncio.run(pan_storage.prepare_share_download("https://pan.quark.cn/s/test"))[
        "success"
    ]
