"""网盘管理测试：存储 Provider、选盘策略、转存链路（全程离线）。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.providers.panstorage.alist import AListStorage
from app.providers.panstorage.base import BasePanStorage, PanFile, PanQuota, SaveResult
from app.providers.panstorage.local_dir import LocalDirStorage
from app.providers.panstorage.quark import QuarkStorage
from app.providers.panstorage.webdav import WebDavStorage
from app.providers.registry import list_providers, load_builtin_providers
from app.schemas.enums import ProviderKind
from app.services import pan_storage as pan_service

load_builtin_providers()


def run(coro):
    """在同步测试里跑异步函数。"""
    return asyncio.run(coro)


# ---------------------------------------------------------------- 注册与契约
def test_panstorage_providers_registered():
    """三个网盘存储都必须注册到 panstorage 分类下。"""
    names = {item["name"] for item in list_providers(ProviderKind.PANSTORAGE.value)}
    assert {"alist", "quark", "local_dir", "webdav"} <= names


def test_all_panstorage_are_subclass_of_base():
    """契约：panstorage 分类里的 Provider 必须实现 BasePanStorage。"""
    for cls in (AListStorage, QuarkStorage, LocalDirStorage, WebDavStorage):
        assert issubclass(cls, BasePanStorage)
        assert cls.kind == ProviderKind.PANSTORAGE.value


def test_quota_percent_and_free():
    """容量对象要能算出剩余与百分比；总量未知时不能除零。"""
    quota = PanQuota(total=1000, used=250)
    assert quota.free == 750
    assert quota.percent == 25.0
    unknown = PanQuota()
    assert unknown.percent == 0.0 and unknown.free == 0
    assert unknown.to_dict()["total"] == 0


def test_normalize_and_join_path():
    """路径规范化：反斜杠、重复斜杠、尾斜杠都要处理掉。"""
    storage = LocalDirStorage({"name": "t", "options": {}})
    assert storage.normalize_path(None) == "/"
    assert storage.normalize_path("影视/剧集/") == "/影视/剧集"
    assert storage.normalize_path("\\\\a\\\\b") == "/a/b"
    assert storage.normalize_path("//a//b//") == "/a/b"
    assert storage.join_path("/影视", "剧集", "S01") == "/影视/剧集/S01"
    assert storage.join_path("", "") == "/"


def test_base_default_methods_degrade_gracefully():
    """未实现的能力必须返回空值而不是抛异常（优雅降级）。"""

    class Minimal(BasePanStorage):
        name = "minimal"

        async def list_dir(self, path: str = "/") -> list[PanFile]:
            return []

        async def save_share(self, share_url, *, password=None, target_dir=None):
            return SaveResult(False, "不支持")

    storage = Minimal({"name": "m"})
    assert run(storage.quota()).total == 0
    assert run(storage.mkdir("/x")) is False
    assert run(storage.delete("/x")) is False
    assert run(storage.download_url("/x")) is None


# ---------------------------------------------------------------- 本地目录网盘
@pytest.fixture
def local_storage(tmp_path):
    """指向临时目录的本地"伪网盘"。"""
    (tmp_path / "影视" / "剧集").mkdir(parents=True)
    (tmp_path / "影视" / "a.mkv").write_bytes(b"0" * 2048)
    (tmp_path / "b.txt").write_text("hello", encoding="utf-8")
    return LocalDirStorage(
        {"name": "本地盘", "url": str(tmp_path), "enabled": True, "options": {"root_path": "/"}}
    )


def test_local_dir_list_dir_sorts_dirs_first(local_storage):
    files = run(local_storage.list_dir("/"))
    assert [item.name for item in files] == ["影视", "b.txt"]
    assert files[0].is_dir and not files[1].is_dir
    assert files[1].size == 5
    assert files[0].path == "/影视"


def test_local_dir_nested_and_size(local_storage):
    files = run(local_storage.list_dir("/影视"))
    names = {item.name: item for item in files}
    assert names["a.mkv"].size == 2048
    assert names["剧集"].is_dir
    assert names["a.mkv"].path == "/影视/a.mkv"


def test_local_dir_mkdir_and_delete(local_storage):
    assert run(local_storage.mkdir("/新建/子目录")) is True
    assert any(item.name == "新建" for item in run(local_storage.list_dir("/")))
    assert run(local_storage.delete("/新建")) is True
    assert not any(item.name == "新建" for item in run(local_storage.list_dir("/")))
    # 删不存在的路径要返回 False 而不是抛异常
    assert run(local_storage.delete("/不存在")) is False


def test_local_dir_quota_reports_disk(local_storage):
    quota = run(local_storage.quota())
    assert quota.total > 0 and quota.used > 0


def test_local_dir_path_traversal_blocked(local_storage):
    """路径越界防护：不能用 .. 逃出根目录。"""
    with pytest.raises(ValueError):
        local_storage._resolve("/../../etc")
    # 对外接口不抛异常，只返回空
    assert run(local_storage.list_dir("/../..")) == []
    assert run(local_storage.delete("/../../x")) is False


def test_local_dir_does_not_support_save(local_storage):
    result = run(local_storage.save_share("https://pan.quark.cn/s/abc"))
    assert result.success is False
    assert "不支持" in result.message


def test_local_dir_health_check(local_storage, tmp_path):
    ok, message = run(local_storage.health_check())
    assert ok is True and "可访问" in message

    broken = LocalDirStorage({"name": "x", "url": str(tmp_path / "缺失")})
    ok, message = run(broken.health_check())
    assert ok is False and "不存在" in message

    empty = LocalDirStorage({"name": "x"})
    ok, message = run(empty.health_check())
    assert ok is False and "base_dir" in message


# ---------------------------------------------------------------- 夸克
def test_quark_parse_share_id():
    assert QuarkStorage.parse_share_id("https://pan.quark.cn/s/186546bac72a") == "186546bac72a"
    assert QuarkStorage.parse_share_id("https://pan.quark.cn/s/abc?pwd=1234") == "abc"
    assert QuarkStorage.parse_share_id("https://pan.baidu.com/s/1xxx") == ""
    assert QuarkStorage.parse_share_id(None) == ""


def test_quark_save_share_rejects_bad_input():
    storage = QuarkStorage({"name": "夸克", "options": {"cookie": "c=1"}})
    result = run(storage.save_share("https://pan.baidu.com/s/1"))
    assert result.success is False and "夸克分享链接" in result.message

    no_cookie = QuarkStorage({"name": "夸克"})
    result = run(no_cookie.save_share("https://pan.quark.cn/s/abc"))
    assert result.success is False and "Cookie" in result.message


def test_quark_save_share_happy_path(monkeypatch):
    """用假 API 走通四步转存流程。"""
    storage = QuarkStorage({"name": "夸克", "options": {"cookie": "c=1", "root_path": "/影视"}})
    calls: list[str] = []

    async def fake_call(path, *, method="GET", params=None, body=None):
        calls.append(path)
        if path == "/share/sharepage/token":
            return {"data": {"stoken": "ST"}}
        if path == "/share/sharepage/detail":
            return {"data": {"list": [{"fid": "f1", "share_fid_token": "t1"}]}}
        if path == "/share/sharepage/save":
            return {"data": {"task_id": "task-1"}}
        if path == "/task":
            return {"data": {"status": 2}}
        if path == "/file/sort":
            return {"data": {"list": []}}
        return None

    monkeypatch.setattr(storage, "_call", fake_call)
    result = run(storage.save_share("https://pan.quark.cn/s/abc", password="1234"))
    assert result.success is True
    assert result.file_count == 1
    assert result.saved_path == "/影视"
    assert "/share/sharepage/token" in calls and "/share/sharepage/save" in calls


def test_quark_save_share_failure_paths(monkeypatch):
    """链接失效 / 空分享 / 提交失败都要给明确原因。"""
    storage = QuarkStorage({"name": "夸克", "options": {"cookie": "c=1"}})

    async def no_token(path, **kwargs):
        return {"data": {}}

    monkeypatch.setattr(storage, "_call", no_token)
    result = run(storage.save_share("https://pan.quark.cn/s/abc"))
    assert result.success is False and "token" in result.message

    async def empty_share(path, **kwargs):
        if path == "/share/sharepage/token":
            return {"data": {"stoken": "ST"}}
        return {"data": {"list": []}}

    monkeypatch.setattr(storage, "_call", empty_share)
    result = run(storage.save_share("https://pan.quark.cn/s/abc"))
    assert result.success is False and "没有可转存" in result.message


# ---------------------------------------------------------------- AList
def test_alist_list_dir_parses_and_sorts(monkeypatch):
    storage = AListStorage({"name": "AList", "url": "http://alist:5244/", "api_key": "K"})

    async def fake_request(path, *, method="POST", body=None):
        assert path == "/api/fs/list"
        return {
            "content": [
                {"name": "zeta.mkv", "is_dir": False, "size": 10, "modified": "2026-01-01"},
                {"name": "剧集", "is_dir": True, "size": 0},
                {"name": ""},
                "坏数据",
            ]
        }

    monkeypatch.setattr(storage, "_request", fake_request)
    files = run(storage.list_dir("影视/"))
    assert [item.name for item in files] == ["剧集", "zeta.mkv"]
    assert files[1].path == "/影视/zeta.mkv"


def test_alist_base_url_normalized():
    storage = AListStorage({"name": "A", "url": "http://alist:5244/"})
    assert storage.base_url == "http://alist:5244"
    assert AListStorage({"name": "A"}).base_url == ""


def test_alist_save_share_requires_link():
    storage = AListStorage({"name": "A", "url": "http://alist:5244"})
    result = run(storage.save_share(""))
    assert result.success is False and "缺少" in result.message


def test_alist_save_share_appends_password(monkeypatch):
    """提取码要按 AList 约定拼成 ?pwd=。"""
    storage = AListStorage({"name": "A", "url": "http://alist:5244", "api_key": "K",
                            "options": {"root_path": "/影视", "offline_tool": "115 Cloud"}})
    captured: dict[str, Any] = {}

    async def fake_request(path, *, method="POST", body=None):
        captured["path"] = path
        captured["body"] = body
        return {"tasks": [{"id": "1"}]}

    monkeypatch.setattr(storage, "_request", fake_request)
    result = run(storage.save_share("https://pan.quark.cn/s/abc", password="1234"))
    assert result.success is True
    assert captured["path"] == "/api/fs/add_offline_download"
    assert "pwd=1234" in captured["body"]["urls"][0]
    assert captured["body"]["tool"] == "115 Cloud"
    assert captured["body"]["path"] == "/影视"


# ---------------------------------------------------------------- 选盘策略
class _FakeStorage(BasePanStorage):
    """可控的假网盘，用于测试服务层逻辑。"""

    def __init__(self, name: str, *, can_save: bool = True, ok: bool = True) -> None:
        super().__init__({"name": f"{name}-站点", "id": 1, "enabled": True})
        self.name = name
        self.supports_save = can_save
        self._ok = ok
        self.saved: list[str] = []

    async def list_dir(self, path: str = "/") -> list[PanFile]:
        return [PanFile(name="x", path="/x")]

    async def save_share(self, share_url, *, password=None, target_dir=None):
        self.saved.append(share_url)
        if not self._ok:
            return SaveResult(False, "配额不足")
        return SaveResult(True, "已转存", saved_path="/影视", file_count=2)


def test_pick_for_share_prefers_same_vendor(monkeypatch):
    """夸克分享要优先给夸克盘，而不是列表里的第一个。"""
    alist = _FakeStorage("alist")
    quark = _FakeStorage("quark")
    monkeypatch.setattr(pan_service, "storages", lambda **kwargs: [alist, quark])

    assert pan_service._pick_for_share("https://pan.quark.cn/s/abc") is quark
    # 没有同家网盘时退回第一个可转存的
    assert pan_service._pick_for_share("https://pan.baidu.com/s/1") is alist


def test_pick_for_share_skips_readonly(monkeypatch):
    readonly = _FakeStorage("local_dir", can_save=False)
    monkeypatch.setattr(pan_service, "storages", lambda **kwargs: [readonly])
    assert pan_service._pick_for_share("https://pan.quark.cn/s/abc") is None


def test_default_storage_prefer(monkeypatch):
    alist = _FakeStorage("alist")
    quark = _FakeStorage("quark")
    monkeypatch.setattr(pan_service, "storages", lambda **kwargs: [alist, quark])
    assert pan_service.default_storage("quark") is quark
    assert pan_service.default_storage() is alist
    monkeypatch.setattr(pan_service, "storages", lambda **kwargs: [])
    assert pan_service.default_storage() is None


# ---------------------------------------------------------------- 服务层
def test_save_share_without_storage(monkeypatch):
    """没有配置网盘时必须给出明确提示而不是报错。"""
    monkeypatch.setattr(pan_service, "storages", lambda **kwargs: [])
    result = run(pan_service.save_share("https://pan.quark.cn/s/abc"))
    assert result["success"] is False
    assert "站点管理" in result["message"]


def test_save_share_records_and_notifies(monkeypatch, client):
    """转存成功要落记录，可通过 list_save_records 查回。"""
    quark = _FakeStorage("quark")
    monkeypatch.setattr(pan_service, "storages", lambda **kwargs: [quark])

    result = run(pan_service.save_share("https://pan.quark.cn/s/rec1", password="9999"))
    assert result["success"] is True
    assert result["saved_path"] == "/影视"

    records = pan_service.list_save_records(limit=10)
    assert any(item["share_url"].endswith("/rec1") for item in records)
    hit = next(item for item in records if item["share_url"].endswith("/rec1"))
    assert hit["success"] is True and hit["file_count"] == 2


def test_save_share_failure_records_message(monkeypatch, client):
    bad = _FakeStorage("quark", ok=False)
    monkeypatch.setattr(pan_service, "storages", lambda **kwargs: [bad])
    result = run(pan_service.save_share("https://pan.quark.cn/s/rec2"))
    assert result["success"] is False and "配额" in result["message"]
    records = pan_service.list_save_records(limit=10)
    hit = next(item for item in records if item["share_url"].endswith("/rec2"))
    assert hit["success"] is False and "配额" in hit["message"]


def test_overview_and_list_files(monkeypatch, client, tmp_path):
    """总览与目录浏览：容量、面包屑用的 parent 都要正确。"""
    (tmp_path / "剧集").mkdir()
    storage = LocalDirStorage(
        {"name": "本地盘", "id": 77, "url": str(tmp_path), "enabled": True}
    )
    monkeypatch.setattr(pan_service, "storages", lambda **kwargs: [storage])
    monkeypatch.setattr(pan_service, "get_storage", lambda site_id: storage)

    data = run(pan_service.overview())
    assert data["total"] == 1
    item = data["items"][0]
    assert item["name"] == "本地盘"
    assert item["supports_save"] is False
    assert item["quota"]["total"] > 0

    files = run(pan_service.list_files(77, "/剧集"))
    assert files["success"] is True
    assert files["path"] == "/剧集"
    assert files["parent"] == "/"

    root = run(pan_service.list_files(77, "/"))
    assert root["parent"] is None


def test_list_files_unknown_site(monkeypatch):
    monkeypatch.setattr(pan_service, "get_storage", lambda site_id: None)
    result = run(pan_service.list_files(999))
    assert result["success"] is False and result["items"] == []


def test_transfer_pending_end_to_end(monkeypatch, client):
    """端到端：盘搜命中 → 登记 pending → 批量转存 → 任务变 TRANSFERRED。"""
    from app.db.models import DownloadTask
    from app.db.session import session_scope
    from app.schemas.enums import ResourceKind, TaskStatus

    with session_scope() as session:
        task = DownloadTask(
            title="测试网盘剧集 S01E01",
            kind=ResourceKind.PAN.value,
            link="https://pan.quark.cn/s/pending1",
            status=TaskStatus.PENDING.value,
            meta={"password": "8888"},
        )
        session.add(task)
        session.flush()
        task_id = task.id

    quark = _FakeStorage("quark")
    monkeypatch.setattr(pan_service, "storages", lambda **kwargs: [quark])

    stats = run(pan_service.transfer_pending(limit=10, notify=False))
    assert stats["saved"] >= 1
    assert any(item["task_id"] == task_id for item in stats["details"])

    with session_scope() as session:
        refreshed = session.get(DownloadTask, task_id)
        assert refreshed.status == TaskStatus.TRANSFERRED.value
        assert refreshed.meta["pan_storage"] == "quark-站点"
        assert refreshed.meta["saved_path"] == "/影视"

    # 已转存的任务不会再出现在待处理队列里
    again = run(pan_service.transfer_pending(limit=10, notify=False))
    assert all(item["task_id"] != task_id for item in again.get("details", []))


def test_transfer_pending_without_storage(monkeypatch, client):
    from app.db.models import DownloadTask
    from app.db.session import session_scope
    from app.schemas.enums import ResourceKind, TaskStatus

    with session_scope() as session:
        session.add(
            DownloadTask(
                title="无盘可转",
                kind=ResourceKind.PAN.value,
                link="https://pan.quark.cn/s/nostorage",
                status=TaskStatus.PENDING.value,
            )
        )

    monkeypatch.setattr(pan_service, "storages", lambda **kwargs: [])
    stats = run(pan_service.transfer_pending(limit=10, notify=False))
    assert stats["saved"] == 0
    assert "站点管理" in stats.get("message", "")


def test_add_download_auto_saves_pan_resource(monkeypatch, client):
    """M2-7 验收：网盘资源入库时自动转存，任务直接为已入库状态。"""
    from app.db.models import DownloadTask
    from app.db.session import session_scope
    from app.schemas.enums import ResourceKind, TaskStatus
    from app.services import download as download_service

    quark = _FakeStorage("quark")
    monkeypatch.setattr(pan_service, "storages", lambda **kwargs: [quark])

    result = run(
        download_service.add_download(
            {
                "title": "自动转存测试 2026 1080p",
                "kind": ResourceKind.PAN.value,
                "link": "https://pan.quark.cn/s/auto1",
                "password": "1111",
                "site": "PanSou",
                "extra": {"pan_type": "quark"},
            }
        )
    )
    assert result is not None
    assert quark.saved == ["https://pan.quark.cn/s/auto1"]

    with session_scope() as session:
        task = session.get(DownloadTask, result.id)
        assert task.status == TaskStatus.TRANSFERRED.value
        assert task.meta["pan_storage"] == "quark-站点"
        assert task.meta["saved_path"] == "/影视"


def test_add_download_pan_stays_pending_without_storage(monkeypatch, client):
    """没配网盘时保持原有 pending 行为，等人工或定时任务处理。"""
    from app.db.models import DownloadTask
    from app.db.session import session_scope
    from app.schemas.enums import ResourceKind, TaskStatus
    from app.services import download as download_service

    monkeypatch.setattr(pan_service, "storages", lambda **kwargs: [])
    result = run(
        download_service.add_download(
            {
                "title": "没有网盘时的登记",
                "kind": ResourceKind.PAN.value,
                "link": "https://pan.quark.cn/s/auto2",
            }
        )
    )
    with session_scope() as session:
        task = session.get(DownloadTask, result.id)
        assert task.status == TaskStatus.PENDING.value


def test_add_download_respects_auto_save_switch(monkeypatch, client):
    """CF_PAN_AUTO_SAVE=false 时不自动转存。"""
    from app.core.config import settings
    from app.db.models import DownloadTask
    from app.db.session import session_scope
    from app.schemas.enums import ResourceKind, TaskStatus
    from app.services import download as download_service

    quark = _FakeStorage("quark")
    monkeypatch.setattr(pan_service, "storages", lambda **kwargs: [quark])
    monkeypatch.setattr(settings, "PAN_AUTO_SAVE", False)

    result = run(
        download_service.add_download(
            {
                "title": "开关关闭",
                "kind": ResourceKind.PAN.value,
                "link": "https://pan.quark.cn/s/auto3",
            }
        )
    )
    assert quark.saved == []
    with session_scope() as session:
        task = session.get(DownloadTask, result.id)
        assert task.status == TaskStatus.PENDING.value


def test_make_dir_and_delete_via_service(monkeypatch, client, tmp_path):
    storage = LocalDirStorage({"name": "本地盘", "id": 5, "url": str(tmp_path), "enabled": True})
    monkeypatch.setattr(pan_service, "get_storage", lambda site_id: storage)

    assert run(pan_service.make_dir(5, "/新目录"))["success"] is True
    assert (tmp_path / "新目录").is_dir()
    assert run(pan_service.delete_file(5, "/新目录"))["success"] is True
    assert not (tmp_path / "新目录").exists()


def test_resolve_download_url_degrades(monkeypatch, client, tmp_path):
    """本地目录不支持直链，要给明确提示而不是 500。"""
    storage = LocalDirStorage({"name": "本地盘", "id": 5, "url": str(tmp_path), "enabled": True})
    monkeypatch.setattr(pan_service, "get_storage", lambda site_id: storage)
    result = run(pan_service.resolve_download_url(5, "/a.mkv"))
    assert result["success"] is False


def test_test_storage_reports_capacity(monkeypatch, client, tmp_path):
    storage = LocalDirStorage({"name": "本地盘", "id": 5, "url": str(tmp_path), "enabled": True})
    monkeypatch.setattr(pan_service, "get_storage", lambda site_id: storage)
    result = run(pan_service.test_storage(5))
    assert result["success"] is True
    assert "/" in result["capacity_text"]


# ---------------------------------------------------------------- API
def test_pan_api_endpoints(client, auth_headers):
    """网盘 API 在未配置网盘时也要 200 + 空列表（优雅降级）。"""
    overview = client.get("/api/v1/pan", headers=auth_headers)
    assert overview.status_code == 200
    assert overview.json()["success"] is True

    for path in ("/api/v1/pan/pending", "/api/v1/pan/records"):
        response = client.get(path, headers=auth_headers)
        assert response.status_code == 200, path
        assert "items" in response.json()

    # 未指定且无可用网盘 → 400 明确报错
    save = client.post(
        "/api/v1/pan/save",
        headers=auth_headers,
        json={"share_url": "https://pan.quark.cn/s/apitest"},
    )
    assert save.status_code == 400
    assert "网盘" in save.json()["detail"]

    files = client.get("/api/v1/pan/files?site_id=99999", headers=auth_headers)
    assert files.status_code == 404

    transfer = client.post("/api/v1/pan/transfer", headers=auth_headers)
    assert transfer.status_code == 200


def test_pan_api_requires_auth(client):
    assert client.get("/api/v1/pan").status_code == 401
    assert client.post("/api/v1/pan/save", json={"share_url": "x"}).status_code == 401


def test_pan_transfer_job_registered():
    """网盘转存必须是一个可在界面上调周期的内置任务。"""
    from app.services.scheduler import JOB_PAN_TRANSFER, builtin_specs

    specs = {spec.key: spec for spec in builtin_specs()}
    assert "pan_transfer" in specs
    assert specs["pan_transfer"].job_id == JOB_PAN_TRANSFER
    assert specs["pan_transfer"].trigger == "interval"


def test_pan_storage_sites_seeded(client):
    """init_db 要预置三个网盘存储示例站点（默认关闭）。"""
    from app.db.init_db import DEFAULT_SITES

    pan_sites = [
        item for item in DEFAULT_SITES if item["kind"] == ProviderKind.PANSTORAGE.value
    ]
    assert {item["provider"] for item in pan_sites} == {"alist", "quark", "local_dir", "webdav"}
    assert all(item["enabled"] is False for item in pan_sites)


# ------------------------------------------- 分享增量能力（v1.4.0 分享追更用）
def test_base_list_share_and_save_share_files_defaults():
    """基类默认行为：看不清分享内部就返回空清单，逐文件转存退回整体转存。

    这两条默认值是「分享追更」在不支持增量的网盘上仍能工作的前提。
    """

    class Minimal(BasePanStorage):
        name = "minimal_share"

        async def list_dir(self, path: str = "/") -> list[PanFile]:
            return []

        async def save_share(self, share_url, *, password=None, target_dir=None):
            return SaveResult(True, "整体转存", file_count=7)

    storage = Minimal({})
    assert run(storage.list_share("https://x/s/abc")) == []
    outcome = run(storage.save_share_files("https://x/s/abc", [PanFile("a.mkv", "/a.mkv")]))
    assert outcome.success is True
    assert outcome.file_count == 7, "不支持逐文件时必须退回整体转存"


def test_quark_list_share_carries_share_fid_token(monkeypatch):
    """夸克列分享要把 share_fid_token 带进 extra——转存时必须回传它。"""
    storage = QuarkStorage({"url": "https://drive-pc.quark.cn", "cookie": "x=1"})

    async def fake_token(share_id, password):
        return "stoken-1"

    async def fake_items(share_id, stoken):
        return [
            {"fid": "f1", "file_name": "第01集.mkv", "size": 100, "share_fid_token": "t1"},
            {"fid": "f2", "file_name": "子目录", "dir": True, "share_fid_token": "t2"},
            {"file_name": "缺 fid 的条目"},
        ]

    monkeypatch.setattr(storage, "_share_token", fake_token)
    monkeypatch.setattr(storage, "_share_items", fake_items)

    files = run(storage.list_share("https://pan.quark.cn/s/abcdef"))
    assert [item.name for item in files] == ["第01集.mkv", "子目录"]
    assert files[0].extra["share_fid_token"] == "t1"
    assert files[0].file_id == "f1"
    assert files[1].is_dir is True


def test_quark_list_share_without_cookie_returns_empty():
    """没配 Cookie 时返回空而不是抛异常（界面会提示去填 Cookie）。"""
    storage = QuarkStorage({"url": "https://drive-pc.quark.cn"})
    assert run(storage.list_share("https://pan.quark.cn/s/abcdef")) == []


def test_quark_save_share_files_submits_selected_fids(monkeypatch):
    """逐文件转存只提交选中的 fid，这是「只转存新增集」的关键。"""
    storage = QuarkStorage({"url": "https://drive-pc.quark.cn", "cookie": "x=1"})
    captured = {}

    async def fake_token(share_id, password):
        return "stoken-1"

    async def fake_submit(share_id, stoken, fid_list, token_list, target_dir):
        captured.update(
            {"fids": fid_list, "tokens": token_list, "target": target_dir}
        )
        return SaveResult(True, f"已转存 {len(fid_list)} 个文件", file_count=len(fid_list))

    monkeypatch.setattr(storage, "_share_token", fake_token)
    monkeypatch.setattr(storage, "_submit_save", fake_submit)

    files = [
        PanFile("第03集.mkv", "/第03集.mkv", file_id="f3", extra={"share_fid_token": "t3"}),
        PanFile("第04集.mkv", "/第04集.mkv", file_id="f4", extra={"share_fid_token": "t4"}),
    ]
    outcome = run(storage.save_share_files("https://pan.quark.cn/s/abcdef", files, target_dir="/来自分享"))
    assert outcome.success is True
    assert captured["fids"] == ["f3", "f4"]
    assert captured["tokens"] == ["t3", "t4"]
    assert captured["target"] == "/来自分享"


def test_quark_save_share_files_rejects_bad_input():
    """空清单 / 非法链接 / 无 Cookie 都要给出明确原因。"""
    storage = QuarkStorage({"url": "https://drive-pc.quark.cn", "cookie": "x=1"})
    assert run(storage.save_share_files("https://pan.quark.cn/s/abc", [])).success is False
    bad = run(
        storage.save_share_files("https://not-quark.example/x", [PanFile("a", "/a", file_id="1")])
    )
    assert bad.success is False and "分享链接" in bad.message

    no_cookie = QuarkStorage({"url": "https://drive-pc.quark.cn"})
    result = run(
        no_cookie.save_share_files(
            "https://pan.quark.cn/s/abcdef", [PanFile("a", "/a", file_id="1")]
        )
    )
    assert result.success is False and "Cookie" in result.message
