"""网盘文件管理测试：能力位、rename/move/copy/search、保活巡检（全程离线）。"""

from __future__ import annotations

import asyncio

import pytest

from app.providers.panstorage.alist import AListStorage
from app.providers.panstorage.base import BasePanStorage
from app.providers.panstorage.local_dir import LocalDirStorage
from app.providers.panstorage.quark import QuarkStorage
from app.providers.panstorage.webdav import WebDavStorage
from app.services import pan_storage as pan_service


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- 能力位
def test_capabilities_shape():
    """能力位字典的键必须齐全，前端依赖这些键渲染按钮。"""
    caps = LocalDirStorage({}).capabilities()
    assert set(caps) == {"save", "delete", "rename", "move", "search", "keepalive"}
    assert all(isinstance(v, bool) for v in caps.values())


def test_quark_and_alist_support_full_management():
    """夸克与 AList 有完整文件管理接口，能力位应全开。"""
    for cls in (QuarkStorage, AListStorage):
        caps = cls({}).capabilities()
        assert caps["rename"] and caps["move"] and caps["search"] and caps["keepalive"]


def test_webdav_has_move_but_no_search():
    """WebDAV 原生有 MOVE/COPY，但没有搜索能力。"""
    caps = WebDavStorage({}).capabilities()
    assert caps["rename"] and caps["move"]
    assert caps["search"] is False


def test_base_defaults_degrade_not_raise():
    """基类默认实现必须优雅降级（返回 False/空），不能抛异常。"""

    class Dummy(BasePanStorage):
        name = "dummy"

        async def list_dir(self, path="/"):
            return []

        async def save_share(self, share_url, *, password=None, target_dir=None):
            return None

    dummy = Dummy({})
    assert run(dummy.rename("/a", "b")) is False
    assert run(dummy.move("/a", "/b")) is False
    assert run(dummy.copy("/a", "/b")) is False
    assert run(dummy.search("x")) == []


# ---------------------------------------------------------------- 本地目录实现
@pytest.fixture
def local_store(tmp_path):
    (tmp_path / "movie.mkv").write_text("data", encoding="utf-8")
    (tmp_path / "剧集").mkdir()
    (tmp_path / "剧集" / "ep01.mp4").write_text("x", encoding="utf-8")
    return LocalDirStorage({"options": {"base_dir": str(tmp_path)}}), tmp_path


def test_local_rename(local_store):
    store, root = local_store
    assert run(store.rename("/movie.mkv", "renamed.mkv")) is True
    assert (root / "renamed.mkv").exists()
    assert not (root / "movie.mkv").exists()


def test_local_rename_rejects_path_separators(local_store):
    """新名称带路径分隔符等于偷偷移动，可能越界，必须拒绝。"""
    store, root = local_store
    assert run(store.rename("/movie.mkv", "../evil.mkv")) is False
    assert run(store.rename("/movie.mkv", "sub/evil.mkv")) is False
    assert run(store.rename("/movie.mkv", "sub\\evil.mkv")) is False
    assert run(store.rename("/movie.mkv", "  ")) is False
    assert (root / "movie.mkv").exists(), "非法改名不能动原文件"


def test_local_rename_missing_file(local_store):
    store, _ = local_store
    assert run(store.rename("/nope.mkv", "x.mkv")) is False


def test_local_move(local_store):
    store, root = local_store
    assert run(store.move("/movie.mkv", "/剧集")) is True
    assert (root / "剧集" / "movie.mkv").exists()
    assert not (root / "movie.mkv").exists()


def test_local_move_creates_target_dir(local_store):
    """目标目录不存在时应自动创建，而不是直接失败。"""
    store, root = local_store
    assert run(store.move("/movie.mkv", "/新建/深层")) is True
    assert (root / "新建" / "深层" / "movie.mkv").exists()


def test_local_copy_file_and_dir(local_store):
    store, root = local_store
    assert run(store.copy("/movie.mkv", "/剧集")) is True
    assert (root / "movie.mkv").exists(), "复制不能删除源文件"
    assert (root / "剧集" / "movie.mkv").exists()

    assert run(store.copy("/剧集", "/备份")) is True
    assert (root / "备份" / "剧集" / "ep01.mp4").exists()


def test_local_search(local_store):
    store, _ = local_store
    hits = run(store.search("ep01"))
    assert [f.name for f in hits] == ["ep01.mp4"]
    assert hits[0].path == "/剧集/ep01.mp4"


def test_local_search_is_case_insensitive(local_store):
    store, _ = local_store
    assert len(run(store.search("MOVIE"))) == 1


def test_local_search_respects_limit(local_store):
    store, root = local_store
    for i in range(10):
        (root / f"file{i}.txt").write_text("x", encoding="utf-8")
    assert len(run(store.search("file", limit=3))) == 3


def test_local_search_empty_keyword(local_store):
    store, _ = local_store
    assert run(store.search("")) == []


# ---------------------------------------------------------------- 夸克（离线打桩）
def make_quark(responses):
    """构造一个夸克实例，把 _call 换成查表返回，完全不联网。"""
    store = QuarkStorage({"options": {"cookie": "fake=1"}})
    calls = []

    async def fake_call(path, *, method="GET", params=None, body=None):
        calls.append({"path": path, "method": method, "params": params, "body": body})
        return responses.get(path)

    store._call = fake_call  # type: ignore[method-assign]
    return store, calls


def test_quark_rename_posts_expected_body():
    store, calls = make_quark({"/file/rename": {"code": 0}})

    async def fake_fid(path, file_id=None):
        return "fid-123"

    store._fid_of = fake_fid  # type: ignore[method-assign]
    assert run(store.rename("/a.mkv", "b.mkv")) is True
    assert calls[0]["path"] == "/file/rename"
    assert calls[0]["body"] == {"fid": "fid-123", "file_name": "b.mkv"}


def test_quark_rename_rejects_root_and_empty():
    store, calls = make_quark({"/file/rename": {"code": 0}})
    assert run(store.rename("/a.mkv", "")) is False
    assert not calls, "空名称不该发请求"


def test_quark_search_parses_path_str():
    """夸克搜索结果要用 path_str 拼出完整路径，否则前端无法直接操作。"""
    store, _ = make_quark(
        {
            "/file/search": {
                "code": 0,
                "data": {
                    "list": [
                        {
                            "file_name": "ep02.mkv",
                            "fid": "f2",
                            "size": 100,
                            "path_str": "/影视/剧集",
                            "file_type": 1,
                        }
                    ]
                },
            }
        }
    )
    hits = run(store.search("ep02"))
    assert len(hits) == 1
    assert hits[0].path == "/影视/剧集/ep02.mkv"
    assert hits[0].file_id == "f2"


def test_quark_search_empty_keyword():
    store, calls = make_quark({})
    assert run(store.search("  ")) == []
    assert not calls


def test_quark_move_requires_existing_target():
    store, _ = make_quark({"/file/move": {"code": 0}})

    async def fake_fid(path, file_id=None):
        return "fid-1"

    async def fake_resolve(path):
        return ""  # 目标目录不存在

    store._fid_of = fake_fid  # type: ignore[method-assign]
    store._resolve_fid = fake_resolve  # type: ignore[method-assign]
    assert run(store.move("/a.mkv", "/不存在")) is False


def test_quark_keep_alive_detects_expired_cookie():
    store, _ = make_quark({"/member": {"code": 0, "data": {}}})
    ok, message = run(store.keep_alive())
    assert ok is False
    assert "失效" in message


def test_quark_keep_alive_without_cookie():
    store = QuarkStorage({})
    ok, message = run(store.keep_alive())
    assert ok is False
    assert "Cookie" in message


# ---------------------------------------------------------------- 服务层
def test_service_rejects_unknown_site(client):
    """依赖 client 装置：它会完成建表与初始化（服务层要查 sites 表）。"""
    for coro in (
        pan_service.rename_file(999999, "/a", "b"),
        pan_service.move_file(999999, "/a", "/b"),
        pan_service.search_files(999999, "kw"),
    ):
        result = run(coro)
        assert result["success"] is False


def test_service_keep_alive_all_never_raises(client):
    """保活巡检必须永远返回结构化结果，不能因为某个盘异常而崩。"""
    result = run(pan_service.keep_alive_all())
    assert "total" in result and "failed" in result and "items" in result


# ---------------------------------------------------------------- API
def test_pan_capabilities_exposed(client, auth_headers):
    """总览接口必须下发能力位，前端据此渲染按钮。"""
    response = client.get("/api/v1/pan", headers=auth_headers)
    assert response.status_code == 200
    for item in response.json().get("items", []):
        assert "capabilities" in item


def test_pan_keep_alive_endpoint(client, auth_headers):
    response = client.post("/api/v1/pan/keep-alive", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["success"] is True


def test_pan_rename_unknown_site_returns_400(client, auth_headers):
    response = client.post(
        "/api/v1/pan/rename",
        headers=auth_headers,
        json={"site_id": 999999, "path": "/a", "new_name": "b"},
    )
    assert response.status_code == 400


def test_pan_search_requires_keyword(client, auth_headers):
    response = client.get(
        "/api/v1/pan/search?site_id=1&keyword=", headers=auth_headers
    )
    assert response.status_code == 422


def test_pan_endpoints_require_auth(client):
    """未认证一律 401，不能泄露网盘结构。"""
    assert client.post("/api/v1/pan/keep-alive").status_code == 401
    assert client.get("/api/v1/pan/search?site_id=1&keyword=x").status_code == 401
