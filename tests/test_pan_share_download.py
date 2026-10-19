"""下载意图、跨平台账号隔离与原文件解析契约（不使用真实账号）。"""

import asyncio
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from app.core.pan_links import pan_family
from app.providers.downloader.aria2 import Aria2Downloader
from app.providers.panstorage import alipan, baidu, pan115, quark
from app.providers.panstorage.base import PanFile
from app.schemas.models import DownloadRequest
from app.services import download, download_routing, pan_download, pan_storage
from app.utils.pan115_rsa import encrypt


def test_capacity_failure_does_not_become_unconfirmed_timeout(monkeypatch):
    storage = quark.QuarkStorage({"cookie": "test"})
    calls = []

    async def call(path, **kwargs):
        calls.append(path)
        if path.endswith("/save"):
            return {"data": {"task_id": "test-task"}}
        assert path == "/task"
        storage._last_error = "夸克网盘空间不足（code=32003）"
        return None

    monkeypatch.setattr(storage, "_call", call)
    result = asyncio.run(storage._submit_save("share", "token", ["fid"], ["token"], "/"))
    assert result.success is False
    assert "空间不足" in result.message
    assert "未确认完成" not in result.message
    assert calls.count("/task") == 1


def test_rejected_pan_request_leaves_no_download_task(monkeypatch, client, auth_headers):
    from app.db.models import DownloadTask
    from app.db.session import session_scope

    async def prepare(*args):
        return {"success": False, "message": "分享已失效"}

    monkeypatch.setattr(pan_storage, "prepare_share_download", prepare)
    with session_scope() as session:
        before = session.query(DownloadTask).count()
    response = client.post(
        "/api/v1/downloads",
        headers=auth_headers,
        json={"title": "invalid", "link": "https://pan.quark.cn/s/invalid", "kind": "pan"},
    )
    assert response.status_code == 400
    assert "分享已失效" in response.json()["message"]
    with session_scope() as session:
        assert session.query(DownloadTask).count() == before


def test_quark_failed_task_keeps_reason_without_polling_again(monkeypatch):
    storage = quark.QuarkStorage({"cookie": "test"})
    calls = []

    async def call(path, **kwargs):
        calls.append(path)
        return {"data": {"status": 3, "error_msg": "文件已失效"}}

    monkeypatch.setattr(storage, "_call", call)
    assert asyncio.run(storage._wait_task("task")) is False
    assert storage._last_error == "文件已失效"
    assert calls == ["/task"]


def test_quark_processing_task_can_finish_normally(monkeypatch):
    storage = quark.QuarkStorage({"cookie": "test"})
    replies = [
        {"data": {"status": 1}},
        {"data": {"status": 2, "save_as": {"save_as_top_fids": ["new"]}}},
    ]

    async def call(*args, **kwargs):
        return replies.pop(0)

    async def sleep(*args):
        pass

    monkeypatch.setattr(storage, "_call", call)
    monkeypatch.setattr(quark.asyncio, "sleep", sleep)
    assert asyncio.run(storage._wait_task("task")) is True
    assert storage._saved_top_fids == ["new"]


def test_rejection_does_not_delete_a_replacement_claim(monkeypatch, client):
    from app.core.exceptions import DownloadRejected
    from app.db.models import DownloadTask
    from app.db.session import session_scope

    replacement = []

    async def prepare(*args):
        with session_scope() as session:
            old = (
                session.query(DownloadTask).filter_by(link="https://pan.quark.cn/s/replaced").one()
            )
            session.delete(old)
            session.flush()
            row = DownloadTask(
                title="replacement",
                link="https://pan.quark.cn/s/new",
                kind="pan",
                status="submitting",
            )
            session.add(row)
            session.flush()
            replacement.append(row.id)
        return {"success": False, "message": "资源失效"}

    monkeypatch.setattr(pan_storage, "prepare_share_download", prepare)
    with pytest.raises(DownloadRejected):
        asyncio.run(
            download.add_download(
                {"title": "old", "link": "https://pan.quark.cn/s/replaced", "kind": "pan"},
                notify=False,
            )
        )
    with session_scope() as session:
        row = session.get(DownloadTask, replacement[0])
        assert row.title == "replacement"
        session.delete(row)


def test_partial_submission_with_unconfirmed_withdrawal_keeps_recovery_record(
    monkeypatch, client, auth_headers
):
    from app.db.models import DownloadTask
    from app.db.session import session_scope

    class Downloader:
        name = "aria2"
        site_name = "fixture aria2"
        calls = 0

        async def health_check(self):
            return True, "ok"

        async def add(self, *args, **kwargs):
            self.calls += 1
            return "accepted-file" if self.calls == 1 else None

        async def remove(self, *args, **kwargs):
            return False

    async def prepare(*args):
        return {
            "success": True,
            "site_id": 1,
            "saved_path": "/",
            "download_files": [
                {"url": "https://fixture.test/one", "path": "/one.mkv", "relative": "one.mkv"},
                {"url": "https://fixture.test/two", "path": "/two.mkv", "relative": "two.mkv"},
            ],
        }

    monkeypatch.setattr(pan_storage, "prepare_share_download", prepare)
    monkeypatch.setattr(download_routing, "candidates_for", lambda *args: [Downloader()])
    response = client.post(
        "/api/v1/downloads",
        headers=auth_headers,
        json={"title": "partial", "link": "https://pan.quark.cn/s/partial", "kind": "pan"},
    )
    assert response.status_code == 200
    with session_scope() as session:
        row = session.get(DownloadTask, response.json()["task_id"])
        assert row.external_id == "accepted-file"
        assert row.status == "submitting"
        assert "未确认撤回" in row.error


def test_subscription_rejection_continues_and_counts_only_started_downloads(monkeypatch, client):
    from app.core.exceptions import DownloadRejected
    from app.db.models import Subscribe
    from app.db.session import session_scope
    from app.services import subscribe

    resources = [
        {"title": "bad", "kind": "pan", "meta": {"episodes": [1]}},
        {"title": "good", "kind": "magnet", "meta": {"episodes": [2]}},
    ]
    notices = []

    async def refresh(*args):
        return 2

    async def search(*args, **kwargs):
        return resources

    async def add(resource, **kwargs):
        if resource["kind"] == "pan":
            raise DownloadRejected("空间不足")
        return SimpleNamespace(id=1)

    async def notify(*args, **kwargs):
        notices.append(kwargs["payload"]["count"])

    monkeypatch.setattr(subscribe, "refresh_total_episodes", refresh)
    monkeypatch.setattr(subscribe, "compute_missing", lambda *args: [1, 2])
    monkeypatch.setattr(subscribe.search_service, "search_for_subscribe", search)
    monkeypatch.setattr(subscribe, "pick_missing", lambda *args, **kwargs: (resources, set()))
    monkeypatch.setattr(download, "add_download", add)
    monkeypatch.setattr(subscribe.notify_service, "send", notify)
    with session_scope() as session:
        row = Subscribe(title="Rejected sample", media_type="tv", season=1, total_episodes=2)
        session.add(row)
        session.flush()
        identifier = row.id
    result = asyncio.run(subscribe.process_subscribe(identifier))
    assert len(result["downloads"]) == 1
    assert result["errors"] == ["空间不足"]
    assert notices == [1]
    with session_scope() as session:
        row = session.get(Subscribe, identifier)
        assert row.error == "空间不足"
        assert row.lack_episodes == 1
        session.delete(row)


@pytest.mark.parametrize(("url", "provider", "label"), [
    ("https://pan.quark.cn/s/test", "quark", "夸克网盘"),
    ("https://pan.baidu.com/s/test", "baidu", "百度网盘"),
    ("https://alipan.com/s/test", "alipan", "阿里云盘"),
    ("https://115cdn.com/s/test", "pan115", "115 网盘"),
    ("https://pan.xunlei.com/s/test", "xunlei", "迅雷网盘"),
])
def test_pan_resource_exposes_specific_platform_before_actions(url, provider, label):
    from app.providers.base import Resource

    row = Resource(title="test", link=url, kind="pan").to_dict()
    assert row["pan_type"] == label
    assert row["pan_provider"] == provider


@pytest.mark.parametrize(("code", "message", "expected"), [(41012, "好友已取消了分享", "好友已取消了分享"), (32003, "capacity limit[{0}]", "空间不足")])
def test_cancelled_quark_share_reports_platform_error(monkeypatch, code, message, expected):
    storage = quark.QuarkStorage({"cookie": "test"})

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def request(self, *args, **kwargs):
            return httpx.Response(404, json={"code": code, "status": 404, "message": message})

    monkeypatch.setattr(quark, "async_client", lambda **kwargs: Client())
    with pytest.raises(ValueError, match=expected):
        asyncio.run(storage.share_downloads("https://pan.quark.cn/s/test"))


def test_quark_download_uses_cookie_refreshed_by_url_response(monkeypatch):
    storage = quark.QuarkStorage({"options": {"cookie": "__puus=old; __pus=keep"}})

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def request(self, *args, **kwargs):
            return httpx.Response(200, request=httpx.Request("POST", "https://drive-pc.quark.cn/1/clouddrive/file/download"),
                headers={"Set-Cookie": "__puus=new; Domain=.quark.cn; Path=/; Secure; HttpOnly"},
                json={"code": 0, "data": [{"fid": "1", "download_url": "https://dl-pc-zb.pds.quark.cn/fixture"}]})

    monkeypatch.setattr(quark, "async_client", lambda **kwargs: Client())
    url = asyncio.run(storage.download_url("/one.mkv", file_id="1"))
    headers = storage.download_headers(url)
    assert "__puus=new" in headers["Cookie"]
    assert "__puus=old" not in headers["Cookie"]
    assert "__pus=keep" in headers["Cookie"]


def test_each_quark_url_keeps_its_matching_cookie_without_exposing_headers(monkeypatch):
    storage = quark.QuarkStorage({"id": 1, "name": "quark", "cookie": "initial"})
    files = [PanFile("one.mkv", "/one.mkv", file_id="1"), PanFile("two.mkv", "/two.mkv", file_id="2")]

    async def listing(*args, **kwargs):
        return files

    async def call(path, **kwargs):
        fid = kwargs["body"]["fids"][0]
        storage.config["cookie"] = "__puus=private-" + fid
        return {"data": [{"fid": fid, "download_url": "https://cdn.quark.cn/" + fid}]}

    monkeypatch.setattr(storage, "list_share", listing)
    monkeypatch.setattr(storage, "_call", call)
    monkeypatch.setattr(pan_storage, "storages", lambda: [storage])
    result = asyncio.run(pan_storage.prepare_share_download("https://pan.quark.cn/s/test"))
    assert [item["headers"]["Cookie"] for item in result["download_files"]] == ["__puus=private-1", "__puus=private-2"]
    assert all("private-" not in json.dumps(item.to_dict()) for item in files)


def test_failed_pan_parse_is_not_reported_as_queued(monkeypatch, client, auth_headers):
    async def prepare(*args):
        return {"success": False, "message": "好友已取消了分享"}

    monkeypatch.setattr(pan_storage, "prepare_share_download", prepare)
    response = client.post(
        "/api/v1/downloads",
        headers=auth_headers,
        json={
            "title": "test",
            "link": "https://pan.quark.cn/s/test",
            "kind": "pan",
        },
    )
    assert response.json()["success"] is False
    assert response.status_code == 400
    assert "task_id" not in response.json()
    assert "取消" in response.json()["message"]


def test_embedded_share_password_is_used_and_explicit_password_wins(monkeypatch):
    from app.core.pan_links import share_password

    assert share_password("https://pan.quark.cn/s/test?pwd=abcd") == "abcd"
    assert share_password("https://pan.quark.cn/s/test?pwd=abcd", "override") == "override"
    received = []

    class Storage:
        name = "quark"
        supports_share_download = True

        async def share_downloads(self, link, *, password=None):
            received.append(password)
            return []

    monkeypatch.setattr(pan_storage, "storages", lambda: [Storage()])
    asyncio.run(pan_storage.prepare_share_download("https://pan.quark.cn/s/test?pwd=abcd"))
    assert received == ["abcd"]


def test_explicit_save_then_download_is_not_blocked_by_auto_save_setting(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "PAN_AUTO_SAVE", False)
    monkeypatch.setattr(pan_storage, "storages", lambda: [object()])

    async def save(*args, **kwargs):
        return {"success": True, "saved_files": ["/selected.mkv"]}

    monkeypatch.setattr(pan_storage, "save_share", save)
    result = asyncio.run(download._try_auto_save_pan("https://pan.quark.cn/s/test", {"pan_mode": "save_then_download"}))
    assert result["success"] is True


def test_quark_nonowner_share_requires_explicit_save(monkeypatch):
    storage = quark.QuarkStorage({"cookie": "test"})

    async def call(path, **kwargs):
        if path.endswith("/token"):
            return {"data": {"stoken": "test"}}
        assert path.endswith("/detail"), "不得把他人分享 ID 交给盘内下载或转存接口"
        return {"data": {"is_owner": 0, "list": [{"fid": "1", "file_name": "one.mkv", "file_type": 1}]}}

    monkeypatch.setattr(storage, "_call", call)
    with pytest.raises(ValueError, match="转存后下载"):
        asyncio.run(storage.share_downloads("https://pan.quark.cn/s/test"))


def test_quark_saved_root_download_only_contains_new_ids(monkeypatch):
    storage = quark.QuarkStorage({"cookie": "test"})

    async def call(path, **kwargs):
        if path.endswith("/save"):
            return {"data": {"task_id": "task"}}
        if path == "/task":
            return {"data": {"status": 2, "save_as": {"save_as_top_fids": ["new"]}}}
        assert path == "/file/sort"
        return {"data": {"list": [{"fid": "old", "file_name": "old.mkv", "file_type": 1}, {"fid": "new", "file_name": "new.mkv", "file_type": 1}]}}

    monkeypatch.setattr(storage, "_call", call)
    result = asyncio.run(storage._submit_save("share", "token", ["original"], ["fid-token"], "/"))
    assert result.success is True
    assert result.saved_files == ["/new.mkv"]


def test_unavailable_aria2_is_reported_before_resolving_files(monkeypatch):
    class Offline:
        site_name = "aria2 test"

        async def health_check(self):
            return False, "无法连接 aria2"

    async def forbidden(*args):
        pytest.fail("下载器离线时不应换取临时直链")

    monkeypatch.setattr(download_routing, "candidates_for", lambda *args: [Offline()])
    monkeypatch.setattr(pan_download, "resolve_files", forbidden)
    result = asyncio.run(download._land_pan_to_local({"saved_path": "/one.mkv", "site_id": 1}, "/tmp/test", None))
    assert result["success"] is False
    assert "aria2 当前不可用" in result["message"]


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
        if method == "aria2.getVersion":
            return {"version": "test"}
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
    assert next(params for method, params in calls if method == "aria2.addUri")[1]["header"] == ["Cookie: private"]
    options = next(params for method, params in calls if method == "aria2.addUri")[1]
    assert options["follow-torrent"] == "false"
    assert options["follow-metalink"] == "false"


def test_aria2_bt_download_retains_default_torrent_following(monkeypatch):
    downloader = Aria2Downloader({"name": "aria2"})
    calls = []

    async def rpc(method, params=None):
        calls.append(params)
        return "gid"

    monkeypatch.setattr(downloader, "_call", rpc)
    asyncio.run(downloader.add("https://fixture.test/file.torrent"))
    assert "follow-torrent" not in calls[0][1]


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
