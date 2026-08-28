"""STRM 同步测试：增量、幂等、失效清理、302 换链、两种链接模式。

用假网盘 Provider，全程离线。
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.config import settings
from app.db.models import SiteConfig, StrmRecord
from app.db.session import session_scope
from app.providers.panstorage.base import BasePanStorage, PanFile, SaveResult
from app.services import strm_sync


def run(coro):
    return asyncio.run(coro)


class FakePan(BasePanStorage):
    """内存假网盘：目录树可在测试里随时改，用来模拟「新增/删除文件」。"""

    name = "fake_strm_pan"
    display_name = "假网盘"

    #: 类级共享目录树，方便测试内改动
    tree: dict[str, list[PanFile]] = {}

    async def list_dir(self, path: str = "/") -> list[PanFile]:
        return list(self.tree.get(self.normalize_path(path), []))

    async def save_share(self, share_url, *, password=None, target_dir=None):
        return SaveResult(False, "假网盘不支持转存")

    async def download_url(self, path: str, *, file_id: str | None = None) -> str | None:
        return "http://cdn.example.com" + path


@pytest.fixture(scope="module")
def pan_site(client):
    """建一条 sites 记录满足 strm_records 的外键约束，返回 site_id。

    模块级：sites.name 有唯一约束，每个用例重复插会冲突。
    """
    with session_scope() as session:
        site = SiteConfig(
            name="STRM 测试盘",
            kind="panstorage",
            provider="fake_strm_pan",
            url="http://127.0.0.1",
            enabled=True,
        )
        session.add(site)
        session.flush()
        return site.id


@pytest.fixture
def fake_pan(monkeypatch, pan_site, tmp_path):
    """把 STRM 根目录指到临时目录，并把假网盘注入 strm_sync。

    每个用例都清空 strm_records：``tmp_path`` 每次都是新目录，
    若沿用上个用例留下的记录，「新增/未变」的统计就没法断言了。
    """
    with session_scope() as session:
        session.query(StrmRecord).delete()

    FakePan.tree = {
        "/": [PanFile("剧集", "/剧集", is_dir=True)],
        "/剧集": [
            PanFile("S01E01.mkv", "/剧集/S01E01.mkv", size=100, file_id="f1"),
            PanFile("S01E02.mkv", "/剧集/S01E02.mkv", size=200, file_id="f2"),
            PanFile("说明.txt", "/剧集/说明.txt", size=1),
        ],
    }
    storage = FakePan({"id": pan_site, "name": "STRM 测试盘"})
    monkeypatch.setattr(strm_sync, "_get_storage", lambda site_id: storage)
    monkeypatch.setattr(settings, "STRM_DIR", tmp_path / "strm")
    monkeypatch.setattr(settings, "STRM_SYNC_METADATA", False)
    return storage


def _strm_files(tmp_path):
    return sorted(p.name for p in (tmp_path / "strm").rglob("*.strm"))


# ------------------------------------------------------------- 生成与幂等
def test_sync_creates_strm_only_for_video_files(fake_pan, pan_site, tmp_path):
    """只给视频文件生成 STRM，txt 之类不管。"""
    result = run(strm_sync.sync_storage(pan_site, pan_path="/剧集"))
    assert result["created"] == 2
    assert result["skipped"] == 0
    assert _strm_files(tmp_path) == ["S01E01.strm", "S01E02.strm"]
    assert "说明.strm" not in _strm_files(tmp_path)


def test_sync_is_idempotent(fake_pan, pan_site, tmp_path):
    """二次同步内容没变就全部 skip，不能重复写盘（否则媒体库会重扫）。"""
    run(strm_sync.sync_storage(pan_site, pan_path="/剧集"))
    again = run(strm_sync.sync_storage(pan_site, pan_path="/剧集"))
    assert again["created"] == 0
    assert again["skipped"] == 2


def test_sync_picks_up_new_files_incrementally(fake_pan, pan_site, tmp_path):
    """网盘新增一集，只该新增一个 STRM。"""
    run(strm_sync.sync_storage(pan_site, pan_path="/剧集"))
    FakePan.tree["/剧集"].append(PanFile("S01E03.mkv", "/剧集/S01E03.mkv", size=300))
    result = run(strm_sync.sync_storage(pan_site, pan_path="/剧集"))
    assert result["created"] == 1
    assert result["skipped"] == 2
    assert "S01E03.strm" in _strm_files(tmp_path)


# --------------------------------------------------------------- 失效清理
def test_sync_cleans_strm_when_source_disappears(fake_pan, pan_site, tmp_path):
    """源文件从网盘消失 → 删掉对应 STRM，避免媒体库出现点不开的空剧集。"""
    run(strm_sync.sync_storage(pan_site, pan_path="/剧集"))
    FakePan.tree["/剧集"] = [PanFile("S01E01.mkv", "/剧集/S01E01.mkv", size=100)]
    result = run(strm_sync.sync_storage(pan_site, pan_path="/剧集", clean=True))
    assert result["removed"] == 1
    assert _strm_files(tmp_path) == ["S01E01.strm"]


def test_clean_can_be_disabled(fake_pan, pan_site, tmp_path):
    """clean=False 时保留 STRM（有人用网盘做冷备，文件会临时下线）。"""
    run(strm_sync.sync_storage(pan_site, pan_path="/剧集"))
    FakePan.tree["/剧集"] = [PanFile("S01E01.mkv", "/剧集/S01E01.mkv", size=100)]
    result = run(strm_sync.sync_storage(pan_site, pan_path="/剧集", clean=False))
    assert result["removed"] == 0
    assert len(_strm_files(tmp_path)) == 2


# ------------------------------------------------------------- 两种链接模式
def test_proxy_mode_writes_internal_302_endpoint(fake_pan, pan_site, tmp_path):
    """默认 proxy 模式写自家 302 端点，链接永不过期。"""
    run(strm_sync.sync_storage(pan_site, pan_path="/剧集", link_mode="proxy"))
    content = next((tmp_path / "strm").rglob("*.strm")).read_text(encoding="utf-8")
    assert "/api/v1/strm/play/" in content


def test_direct_mode_writes_pan_direct_link(fake_pan, pan_site, tmp_path):
    """direct 模式写网盘直链，NAS 零流量但链接会过期。"""
    run(strm_sync.sync_storage(pan_site, pan_path="/剧集", link_mode="direct"))
    contents = [p.read_text(encoding="utf-8") for p in (tmp_path / "strm").rglob("*.strm")]
    assert all(item.startswith("http://cdn.example.com/") for item in contents)


def test_strm_subdir_isolates_multiple_storages(fake_pan, pan_site, tmp_path):
    """多盘并存时按盘名分子目录，避免不同盘的同名剧集互相覆盖。"""
    run(strm_sync.sync_storage(pan_site, pan_path="/剧集", strm_subdir="盘A"))
    assert (tmp_path / "strm" / "盘A" / "S01E01.strm").exists()


# ------------------------------------------------------------------ 302 换链
def test_resolve_play_url_returns_current_direct_link(fake_pan, pan_site):
    """302 端点靠记录 ID 实时换直链。"""
    run(strm_sync.sync_storage(pan_site, pan_path="/剧集"))
    with session_scope() as session:
        record_id = session.query(StrmRecord).first().id
    url, message = run(strm_sync.resolve_play_url(record_id))
    assert url is not None and url.startswith("http://cdn.example.com/")
    assert message == "ok"


def test_resolve_play_url_missing_record(client):
    """记录不存在时给明确信息（API 层据此回 404，播放器会显示错误而非卡住）。"""
    url, message = run(strm_sync.resolve_play_url(99999999))
    assert url is None
    assert "不存在" in message


# ------------------------------------------------------------------ 统计
def test_stats_and_list_records(fake_pan, pan_site):
    run(strm_sync.sync_storage(pan_site, pan_path="/剧集"))
    data = strm_sync.stats()
    assert data["total"] >= 2
    assert data["alive"] >= 2
    assert data["total_size_text"]
    assert data["link_mode"] in ("proxy", "direct")

    records = strm_sync.list_records(site_id=pan_site)
    assert len(records) >= 2
    assert all(item["site_id"] == pan_site for item in records)
    assert all(item["size_text"] for item in records)
    assert strm_sync.list_records(site_id=pan_site, alive_only=True)


def test_sync_missing_storage_returns_message(client):
    """网盘不存在时不能抛异常，要给用户可读的原因。"""
    result = run(strm_sync.sync_storage(99999999))
    assert result["created"] == 0
    assert "不存在" in result["message"] or "未启用" in result["message"]


def test_sync_empty_directory_reports_no_video(fake_pan, pan_site):
    """目录里没有视频时明确告知，避免用户以为「同步成功但没文件」。"""
    FakePan.tree["/空目录"] = []
    result = run(strm_sync.sync_storage(pan_site, pan_path="/空目录"))
    assert result["created"] == 0
    assert "没有找到视频文件" in result["message"]
