"""STRM 同步测试：增量、幂等、失效清理、302 换链、两种链接模式。

用假网盘 Provider，全程离线。
"""

from __future__ import annotations

import asyncio
import time

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
        site_id = site.id
    yield site_id
    with session_scope() as session:
        session.query(StrmRecord).filter_by(site_id=site_id).delete()
        session.query(SiteConfig).filter_by(id=site_id).delete()


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
def test_scoped_sync_preserves_other_source_directories(fake_pan, pan_site, tmp_path):
    FakePan.tree["/剧集2"] = [PanFile("other.mkv", "/剧集2/other.mkv")]
    run(strm_sync.sync_storage(pan_site, pan_path="/剧集2", strm_subdir="other"))
    run(strm_sync.sync_storage(pan_site, pan_path="/剧集", clean=True))
    assert (tmp_path / "strm/other/other.strm").exists()


def test_complete_empty_directory_cleans_only_its_scope(fake_pan, pan_site, tmp_path):
    run(strm_sync.sync_storage(pan_site, pan_path="/剧集"))
    FakePan.tree["/剧集"] = []
    result = run(strm_sync.sync_storage(pan_site, pan_path="/剧集", clean=True))
    assert result["removed"] == 2
    assert not _strm_files(tmp_path)


def test_directory_error_cannot_delete_previous_strm(fake_pan, pan_site, monkeypatch, tmp_path):
    run(strm_sync.sync_storage(pan_site))
    original = fake_pan.list_dir

    async def failed(path="/"):
        if path == "/剧集":
            raise OSError("temporary failure")
        return [*(await original(path)), PanFile("ok.mkv", "/ok.mkv")]

    monkeypatch.setattr(fake_pan, "list_dir", failed)
    result = run(strm_sync.sync_storage(pan_site, clean=True))
    assert result["truncated"] is True
    assert result["removed"] == 0
    assert "S01E01.strm" in _strm_files(tmp_path)


def test_depth_limit_marks_scan_incomplete(fake_pan):
    _, incomplete = run(strm_sync._walk(fake_pan, "/", max_depth=0))
    assert incomplete is True


def test_remote_paths_cannot_escape_output_directory(fake_pan, pan_site, tmp_path):
    FakePan.tree["/剧集"].append(PanFile("escape.mkv", "/剧集/../../escape.mkv"))
    result = run(strm_sync.sync_storage(pan_site, pan_path="/剧集", clean=True))
    assert result["truncated"] is True and result["created"] == 2
    assert not (tmp_path / "escape.strm").exists()
    assert strm_sync._output_path("/剧集2/other.mkv", "/剧集", tmp_path) is None
    assert strm_sync._output_path("/剧集", "/剧集", tmp_path) is None


@pytest.mark.parametrize(("module", "class_name", "method"), [
    ("quark", "QuarkStorage", "_call"),
    ("pan115", "Pan115Storage", "_call"),
    ("baidu", "BaiduPanStorage", "_call"),
    ("baidu_openapi", "BaiduOpenApiStorage", "_call"),
    ("alist", "AListStorage", "_request"),
    ("webdav", "WebDavStorage", "_request"),
])
def test_native_listing_failure_is_not_an_empty_directory(module, class_name, method, monkeypatch):
    from importlib import import_module
    from unittest.mock import AsyncMock

    storage = getattr(import_module(f"app.providers.panstorage.{module}"), class_name)({})
    result = (503, "unavailable") if module == "webdav" else None
    monkeypatch.setattr(storage, method, AsyncMock(return_value=result))
    with pytest.raises(OSError):
        run(storage.list_dir_for_sync("/"))
    files, incomplete = run(strm_sync._walk(storage, "/"))
    assert files == [] and incomplete is True


@pytest.mark.parametrize("module", ["quark", "pan115", "baidu"])
def test_native_page_limit_keeps_results_but_disables_cleanup(module, monkeypatch):
    from unittest.mock import AsyncMock

    from app.providers.panstorage.baidu import BaiduPanStorage
    from app.providers.panstorage.pan115 import Pan115Storage
    from app.providers.panstorage.quark import QuarkStorage

    count = 1000 if module == "baidu" else 200
    if module == "quark":
        storage = QuarkStorage({})
        payload = {"data": {"list": [{"file_name": f"{i}.mkv", "fid": str(i), "file_type": 1} for i in range(count)]}}
    elif module == "pan115":
        storage = Pan115Storage({})
        payload = {"data": [{"n": f"{i}.mkv", "fid": str(i + 1)} for i in range(count)]}
    else:
        storage = BaiduPanStorage({})
        payload = {"list": [{"server_filename": f"{i}.mkv", "path": f"/{i}.mkv", "fs_id": i + 1} for i in range(count)]}
    monkeypatch.setattr(storage, "_call", AsyncMock(return_value=payload))
    files, incomplete = run(strm_sync._walk(storage, "/"))
    assert incomplete is True and len(files) == count
    assert all(item.path.startswith("/") for _, item in files)


def test_webdav_partial_failure_cannot_enable_cleanup(monkeypatch):
    from unittest.mock import AsyncMock

    from app.providers.panstorage.webdav import WebDavStorage

    xml = '<d:multistatus xmlns:d="DAV:"><d:response><d:href>/missing.mkv</d:href><d:status>HTTP/1.1 404 Not Found</d:status></d:response></d:multistatus>'
    storage = WebDavStorage({"url": "https://example.invalid"})
    monkeypatch.setattr(storage, "_request", AsyncMock(return_value=(207, xml)))
    _, incomplete = run(strm_sync._walk(storage, "/"))
    assert incomplete is True


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
def test_play_requires_record_bound_token_and_rotation(fake_pan, pan_site, tmp_path, client, auth_headers):
    run(strm_sync.sync_storage(pan_site, pan_path="/剧集"))
    paths = sorted((tmp_path / "strm").rglob("*.strm"))
    old = paths[0].read_text(encoding="utf-8")
    other = paths[1].read_text(encoding="utf-8")
    assert client.get(old, follow_redirects=False).status_code == 302
    assert client.get(old.split("?")[0], follow_redirects=False).status_code == 404
    assert client.get(old.split("?")[0] + "?token=无效", follow_redirects=False).status_code == 404
    assert client.get(old.split("?")[0] + "?" + other.split("?")[1], follow_redirects=False).status_code == 404
    response = client.post("/api/v1/strm/rewrite?rotate=true", headers=auth_headers)
    assert response.json()["rewritten"] == 2
    assert client.get(old, follow_redirects=False).status_code == 404
    assert client.get(paths[0].read_text(encoding="utf-8"), follow_redirects=False).status_code == 302


def test_rewrite_migrates_legacy_proxy_without_cloud_request(fake_pan, pan_site, tmp_path):
    run(strm_sync.sync_storage(pan_site, pan_path="/剧集"))
    with session_scope() as session:
        for record in session.query(StrmRecord):
            record.play_token = None
    result = strm_sync.rewrite_play_links()
    assert result == {"rewritten": 2, "failed": 0}
    assert all("?token=" in p.read_text() for p in (tmp_path / "strm").rglob("*.strm"))


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
    # 早退路径也必须给出截断结论：sync_all / API 无条件读这个键，
    # 缺键会变成 KeyError（内部错误），把"网盘没启用"这个明确原因吃掉。
    assert result["truncated"] is False


def test_sync_empty_directory_reports_no_video(fake_pan, pan_site):
    """目录里没有视频时明确告知，避免用户以为「同步成功但没文件」。"""
    FakePan.tree["/空目录"] = []
    result = run(strm_sync.sync_storage(pan_site, pan_path="/空目录"))
    assert result["created"] == 0
    assert "没有找到视频文件" in result["message"]

# ---------------------------------------------------------------------------
# 遍历闸门：大网盘不能把同步端点拖成永不返回（v1.24.0 live 实测）
# ---------------------------------------------------------------------------


class SlowDeepPan(BasePanStorage):
    """每层都有子目录、每次 list_dir 都很慢的网盘。

    模拟实测到的真实情况：一个百度网盘账号只下钻 2 层就产生
    **127 次目录请求 / 94.6s**（平均 0.74s/次），而默认 max_depth=12。
    """

    name = "slow_deep_pan"
    display_name = "慢速深层假网盘"
    delay = 0.01
    calls = 0

    async def list_dir(self, path: str = "/") -> list[PanFile]:
        type(self).calls += 1
        await asyncio.sleep(self.delay)
        base = self.normalize_path(path)
        # 每个目录都有 3 个子目录 + 1 个视频 —— 无限宽且无限深
        items = [
            PanFile(name=f"d{i}", path=f"{base.rstrip('/')}/d{i}", is_dir=True)
            for i in range(3)
        ]
        items.append(
            PanFile(
                name="movie.mkv",
                path=f"{base.rstrip('/')}/movie.mkv",
                is_dir=False,
                size=100,
            )
        )
        return items

    async def save_share(self, share_url, *, password=None, target_dir=None):
        return SaveResult(False, "不支持")


def test_walk_stops_at_dir_budget_and_reports_truncation() -> None:
    """目录数闸门必须生效，并**如实回报被截断**。

    只限递归深度是不够的：深度 12 且每层 3 个子目录 = 3^12 个目录请求。
    实测 `POST /strm/sync` 因此 600s 仍不返回，用户点一下就永远转圈。
    """
    SlowDeepPan.calls = 0
    pan = SlowDeepPan({"name": "慢盘"})
    files, truncated = run(strm_sync._walk(pan, "/", max_dirs=25, budget=60))
    assert truncated is True, "达到目录上限必须回报截断"
    # 上限是"不再新增目录请求"，允许已在栈上的那层跑完，故用宽松上界
    assert SlowDeepPan.calls <= 40, f"闸门没拦住，实际请求 {SlowDeepPan.calls} 次"
    assert files, "截断也应保留已经扫到的结果"


def test_walk_stops_at_time_budget() -> None:
    """墙钟预算必须生效——网盘慢的时候，目录数还没到上限就该收工。"""
    SlowDeepPan.calls = 0
    pan = SlowDeepPan({"name": "慢盘"})
    started = time.monotonic()
    _files, truncated = run(strm_sync._walk(pan, "/", max_dirs=100000, budget=0.4))
    elapsed = time.monotonic() - started
    assert truncated is True
    # 给足余量：预算到点后仍要把当前这层的 await 跑完
    assert elapsed < 15, f"预算没起作用，耗了 {elapsed:.1f}s"


def test_small_pan_is_not_marked_truncated() -> None:
    """反向担保：正常小盘不能被误标成截断。

    否则这个标记就成了永久警告，用户学会忽略它，等于没有。
    """
    FakePan.tree = {
        "/": [PanFile(name="a.mkv", path="/a.mkv", is_dir=False, size=10)],
    }
    files, truncated = run(strm_sync._walk(FakePan({"name": "小盘"}), "/"))
    assert truncated is False
    assert len(files) == 1


def test_truncated_sync_does_not_claim_no_videos(fake_pan, pan_site, monkeypatch) -> None:
    """截断且没扫到视频时，消息不能说成「这里没有视频」。

    「还没扫到」和「确实没有」是两件事（ADR-82）。说成后者的话，
    用户会以为盘里真没东西，而不是去缩小同步范围。
    """

    async def fake_walk(*_args, **_kwargs):
        return [], True

    monkeypatch.setattr(strm_sync, "_walk", fake_walk)
    result = run(strm_sync.sync_storage(pan_site, pan_path="/"))
    assert result["truncated"] is True
    assert "没有找到视频文件" not in result["message"]
    assert "只" in result["message"] or "一部分" in result["message"]


def test_sync_all_surfaces_truncation_in_top_level_message(monkeypatch) -> None:
    """截断要冒泡到顶层 message，否则界面上仍是一句听着像干完了的话。"""

    class _S:
        name = "x"
        site_name = "某盘"
        config = {"id": 7}
        root_path = "/"

    monkeypatch.setattr(strm_sync, "_storages", lambda: [_S()])

    async def fake_sync(site_id, **_kw):
        return {"site_id": site_id, "created": 0, "removed": 0, "truncated": True}

    monkeypatch.setattr(strm_sync, "sync_storage", fake_sync)
    summary = run(strm_sync.sync_all(notify=False))
    assert summary["truncated"] is True
    assert "一部分" in summary["message"], summary["message"]

def test_sync_all_shares_one_budget_across_storages(monkeypatch) -> None:
    """总预算必须**跨盘共享**，不能每个盘各自重新计时。

    实测教训：单盘 90s 闸门加完后 `POST /strm/sync` 仍然 180s 超时 ——
    实机有 3 个启用的网盘，90×3 = 270s。给每一步设上限 ≠ 给整件事设上限。
    """

    class _S:
        def __init__(self, sid, name):
            self.config = {"id": sid}
            self.site_name = name
            self.root_path = "/"

    monkeypatch.setattr(
        strm_sync, "_storages", lambda: [_S(1, "盘A"), _S(2, "盘B"), _S(3, "盘C")]
    )
    monkeypatch.setattr(strm_sync, "SYNC_ALL_BUDGET_SECONDS", 1.0)
    monkeypatch.setattr(strm_sync, "MIN_WALK_BUDGET_SECONDS", 0.05)

    budgets: list[float] = []

    async def fake_sync(site_id, *, budget=None, **_kw):
        budgets.append(budget)
        await asyncio.sleep(0.05)
        return {"site_id": site_id, "created": 0, "removed": 0, "truncated": False}

    monkeypatch.setattr(strm_sync, "sync_storage", fake_sync)
    started = time.monotonic()
    run(strm_sync.sync_all(notify=False))
    elapsed = time.monotonic() - started

    # 无条件断言：参数刻意选成三个盘都能轮到，所以必须拿到 3 个预算值。
    # 断言若带 `if len(...) >= 2` 这类前置条件，注入"不共享"时会因为
    # 只跑了一个盘而假绿 —— 这正是第一版写法犯的错。
    assert len(budgets) == 3, f"应当三个盘都轮到，实际 {budgets}"
    # 共享的可观测证据要选对不变式：
    # 配额**总和**不是不变式 —— 公平分配下快盘用不完的时间会回流给后面的盘，
    # 实测 0.33+0.48+0.89=1.70 > 总预算 1.0，而整体只花了 0.15s，并没有超时。
    # 按总和断言会把正确实现判成错的。真正的不变式是下面两条：
    for b in budgets:
        assert b <= strm_sync.SYNC_ALL_BUDGET_SECONDS, (
            f"单盘配额超过总预算，说明每个盘各自重新计时：{budgets}"
        )
    assert elapsed <= strm_sync.SYNC_ALL_BUDGET_SECONDS * 3, (
        f"整体耗时没有被总预算约束：{elapsed:.2f}s"
    )


def test_sync_all_records_which_storages_never_got_scanned(monkeypatch) -> None:
    """预算用尽后剩下的盘要被明确记下来，而不是悄悄跳过。"""

    class _S:
        def __init__(self, sid, name):
            self.config = {"id": sid}
            self.site_name = name
            self.root_path = "/"

    monkeypatch.setattr(
        strm_sync, "_storages", lambda: [_S(1, "盘A"), _S(2, "盘B"), _S(3, "盘C")]
    )
    monkeypatch.setattr(strm_sync, "SYNC_ALL_BUDGET_SECONDS", 0.5)
    monkeypatch.setattr(strm_sync, "MIN_WALK_BUDGET_SECONDS", 0.2)

    async def fake_sync(site_id, *, budget=None, **_kw):
        await asyncio.sleep(0.35)
        return {"site_id": site_id, "created": 0, "removed": 0, "truncated": False}

    monkeypatch.setattr(strm_sync, "sync_storage", fake_sync)
    summary = run(strm_sync.sync_all(notify=False))
    assert summary["skipped_storages"], "预算用尽却没记下哪些盘没轮到"
    assert "没轮到" in summary["message"], summary["message"]


def test_budget_exhausted_storage_is_not_reported_as_empty(monkeypatch) -> None:
    """预算用尽的盘不能报成「没有视频」——那是个假结论。

    「没轮到扫」和「扫完了没东西」必须区分，否则用户会以为盘是空的。
    """

    class _S:
        def __init__(self, sid, name):
            self.config = {"id": sid}
            self.site_name = name
            self.root_path = "/"

    monkeypatch.setattr(strm_sync, "_storages", lambda: [_S(1, "盘A"), _S(2, "盘B")])
    monkeypatch.setattr(strm_sync, "SYNC_ALL_BUDGET_SECONDS", 0.3)
    monkeypatch.setattr(strm_sync, "MIN_WALK_BUDGET_SECONDS", 0.25)

    async def fake_sync(site_id, *, budget=None, **_kw):
        await asyncio.sleep(0.3)
        return {"site_id": site_id, "created": 0, "removed": 0, "truncated": False}

    monkeypatch.setattr(strm_sync, "sync_storage", fake_sync)
    summary = run(strm_sync.sync_all(notify=False))

    skipped = [d for d in summary["details"] if "未开始扫描" in d.get("message", "")]
    assert skipped, "没有任何盘被标成「未开始扫描」"
    for item in skipped:
        assert "没有找到视频" not in item["message"]
        # 「一个目录都没看过」与「只扫了一部分」是两种不同的结果。
        # 混成同一个 truncated 标记时，顶层消息会把同一个盘既算进
        # "只扫了一部分"又算进"没轮到"（实测输出里出现过这个自相矛盾）。
        assert item["not_scanned"] is True, "没轮到扫必须有自己的标记"
        assert item["truncated"] is False, "没扫过就谈不上「只扫了一部分」"
    # 顶层仍必须让用户知道这一轮不完整
    assert summary["truncated"] is True
    assert "没轮到" in summary["message"]
    # 同一个盘不能被两种说法各算一次：没轮到的盘不得同时出现在"只扫了一部分"里
    skipped_names = {d["site_name"] for d in skipped}
    partial_names = {
        d.get("site_name") for d in summary["details"] if d.get("truncated")
    }
    assert not (skipped_names & partial_names), (
        f"同一个盘被既算成「没轮到」又算成「只扫了一部分」：{skipped_names & partial_names}"
    )


def test_sync_all_with_fast_storages_uses_full_budget(monkeypatch) -> None:
    """反向担保：盘都很快时不能有任何盘被跳过。

    否则这个闸门会在正常情况下也误伤，用户会发现「有些盘永远不同步」。
    """

    class _S:
        def __init__(self, sid, name):
            self.config = {"id": sid}
            self.site_name = name
            self.root_path = "/"

    monkeypatch.setattr(
        strm_sync, "_storages", lambda: [_S(i, f"盘{i}") for i in range(1, 6)]
    )

    async def fake_sync(site_id, *, budget=None, **_kw):
        return {"site_id": site_id, "created": 1, "removed": 0, "truncated": False}

    monkeypatch.setattr(strm_sync, "sync_storage", fake_sync)
    summary = run(strm_sync.sync_all(notify=False))
    assert summary["skipped_storages"] == []
    assert summary["created"] == 5
    assert summary["truncated"] is False
    assert "没轮到" not in summary["message"]

# ---------------------------------------------------------------------------
# 元数据阶段闸门：加完遍历闸门后全盘同步实测**仍然 617s**（v1.24.0）
# ---------------------------------------------------------------------------


class SlowMetaPan(BasePanStorage):
    """换直链很慢的网盘，用来复现元数据阶段的真实代价。

    实测：每个元数据文件要 **1.4s**（换直链 + 下载两次网络往返），
    一个盘 255 个字幕/NFO 就是 **359s** —— 比遍历（90s）还慢，
    而当时这一步**完全没有闸门**。
    """

    name = "slow_meta_pan"
    display_name = "慢元数据假网盘"
    delay = 0.05
    calls = 0

    async def list_dir(self, path: str = "/") -> list[PanFile]:
        return []

    async def save_share(self, share_url, *, password=None, target_dir=None):
        return SaveResult(False, "不支持")

    async def download_url(self, path: str, *, file_id: str | None = None) -> str | None:
        type(self).calls += 1
        await asyncio.sleep(self.delay)
        return None  # 换不到直链就跳过下载，本用例只量"每个文件都要花时间"这件事


def _meta_files(count: int) -> list[tuple[str, PanFile]]:
    return [
        ("/剧集", PanFile(f"s{i}.srt", f"/剧集/s{i}.srt", is_dir=False, size=1))
        for i in range(count)
    ]


def test_metadata_download_stops_at_time_budget(tmp_path) -> None:
    """元数据下载必须有墙钟预算，且如实回报被截断。

    字幕缺了片子照样能播 —— 这一步是**可选增强**，超时该砍掉它，
    而不是让整个同步请求永不返回。
    """
    SlowMetaPan.calls = 0
    pan = SlowMetaPan({"name": "慢盘"})
    count, truncated = run(
        strm_sync._sync_metadata(pan, _meta_files(200), "/", tmp_path, budget=0.3)
    )
    assert truncated is True, "达到预算必须回报截断"
    assert SlowMetaPan.calls < 200, f"闸门没拦住，实际请求 {SlowMetaPan.calls} 次"
    assert count == 0  # 本假盘换不到直链，所以成功数为 0；这里量的是闸门


def test_metadata_download_stops_at_file_count(tmp_path) -> None:
    """条数上限必须生效——网盘很快时预算用不完，也不该无限下。"""
    SlowMetaPan.calls = 0
    pan = SlowMetaPan({"name": "慢盘"})
    pan.delay = 0.0
    _count, truncated = run(
        strm_sync._sync_metadata(
            pan, _meta_files(50), "/", tmp_path, budget=999, max_files=10
        )
    )
    assert truncated is True
    assert SlowMetaPan.calls <= 10, f"条数上限没生效，实际 {SlowMetaPan.calls}"


def test_small_metadata_set_is_not_marked_truncated(tmp_path) -> None:
    """反向担保：正常数量的元数据不能被误标截断。"""
    SlowMetaPan.calls = 0
    pan = SlowMetaPan({"name": "慢盘"})
    pan.delay = 0.0
    _count, truncated = run(
        strm_sync._sync_metadata(pan, _meta_files(3), "/", tmp_path)
    )
    assert truncated is False
    assert SlowMetaPan.calls == 3


def test_sync_storage_budget_covers_metadata_phase(fake_pan, pan_site, monkeypatch) -> None:
    """``budget`` 必须管住**全部**阶段，不能只管遍历。

    实测教训：只给遍历加闸门后，全盘同步仍要 617s ——
    因为元数据阶段拿的是自己那份独立的 45s，与遍历的 90s 相加。
    """
    monkeypatch.setattr(settings, "STRM_SYNC_METADATA", True)
    seen: list[float] = []

    async def fake_meta(_storage, _files, _base, _root, *, budget, max_files=None):
        seen.append(budget)
        return 0, False

    monkeypatch.setattr(strm_sync, "_sync_metadata", fake_meta)
    run(strm_sync.sync_storage(pan_site, pan_path="/剧集", budget=10.0))
    assert seen, "元数据阶段没被调用"
    # 传入总预算 10s，遍历已经花掉一点，所以元数据拿到的必须 **少于** 10s，
    # 而且远小于 METADATA_BUDGET_SECONDS（45s）——证明它用的是剩余量
    assert seen[0] <= 10.0, f"元数据阶段拿到了独立预算：{seen}"
    assert seen[0] < strm_sync.METADATA_BUDGET_SECONDS


def test_metadata_truncation_marks_sync_as_truncated(fake_pan, pan_site, monkeypatch) -> None:
    """元数据被砍也算截断，必须冒泡到 stats。

    否则用户看到"同步完成"但字幕只有一半，无从判断是网盘没有还是被砍了。
    """
    monkeypatch.setattr(settings, "STRM_SYNC_METADATA", True)

    async def fake_meta(_storage, _files, _base, _root, *, budget, max_files=None):
        return 2, True

    monkeypatch.setattr(strm_sync, "_sync_metadata", fake_meta)
    result = run(strm_sync.sync_storage(pan_site, pan_path="/剧集"))
    assert result["metadata"] == 2
    assert result["truncated"] is True, "元数据被砍却报告成完整同步"

def test_slow_storage_does_not_starve_later_storages(monkeypatch) -> None:
    """一个慢盘不能把总预算吃干，让后面的盘永远轮不到。

    实测（v1.24.0）：`GET /pan` 里百度网盘排在本地目录之前。
    百度那个盘扫满 90s，本地目录（实测 **0.0s** 就能扫完）却被报成
    「本轮总预算已用尽，未开始扫描」—— 排在后面的盘等于永久不同步，
    而这个盘明明一瞬间就能扫完。所以配额要按剩余盘数**均分**，
    不能先到先得。
    """

    class _S:
        def __init__(self, sid, name):
            self.config = {"id": sid}
            self.site_name = name
            self.root_path = "/"

    monkeypatch.setattr(
        strm_sync, "_storages", lambda: [_S(1, "慢盘"), _S(2, "快盘")]
    )
    monkeypatch.setattr(strm_sync, "SYNC_ALL_BUDGET_SECONDS", 1.0)
    monkeypatch.setattr(strm_sync, "MIN_WALK_BUDGET_SECONDS", 0.05)

    scanned: list[int] = []

    async def fake_sync(site_id, *, budget=None, **_kw):
        scanned.append(site_id)
        # 慢盘把**自己那份配额**用光（模拟遍历到达上限）
        if site_id == 1:
            await asyncio.sleep(budget)
            return {"site_id": site_id, "created": 0, "removed": 0, "truncated": True}
        return {"site_id": site_id, "created": 5, "removed": 0, "truncated": False}

    monkeypatch.setattr(strm_sync, "sync_storage", fake_sync)
    summary = run(strm_sync.sync_all(notify=False))

    assert scanned == [1, 2], f"快盘被慢盘饿死了，实际扫了 {scanned}"
    assert summary["skipped_storages"] == [], "快盘不该被跳过"
    assert summary["created"] == 5, "快盘的结果没被计入"

def test_truncated_sync_does_not_clean_unseen_records(fake_pan, pan_site, tmp_path, monkeypatch) -> None:
    """遍历被截断时**绝对不能**清理"这次没见到"的记录。

    实测后果（v1.24.0，这是本轮最严重的一个缺陷）：
    一次被截断的全盘同步返回 `removed: 636` —— 把上一轮刚生成的 636 个 STRM
    **全删了**。于是每跑一次就在"新增 636"和"清理 636"之间来回翻，
    媒体库反复丢片重扫。

    清理的判据是"这次没见到 = 源文件已消失"，而截断意味着
    "很多文件只是还没扫到"。把"没看到"当成"不存在"就会误删（ADR-82）。
    """
    # 先正常同步一次，建立 2 条记录 + 2 个 STRM
    run(strm_sync.sync_storage(pan_site, pan_path="/剧集", clean=True))
    assert len(_strm_files(tmp_path)) == 2

    # 第二次同步：遍历被截断，只看到其中一个文件
    async def truncated_walk(*_args, **_kwargs):
        return [("/剧集", FakePan.tree["/剧集"][0])], True

    monkeypatch.setattr(strm_sync, "_walk", truncated_walk)
    result = run(strm_sync.sync_storage(pan_site, pan_path="/剧集", clean=True))

    assert result["truncated"] is True
    assert result["removed"] == 0, "截断时清理了没扫到的记录，会造成反复删建"
    # 文件必须还在盘上
    assert len(_strm_files(tmp_path)) == 2, f"STRM 被误删：{_strm_files(tmp_path)}"
    # 而且要说明为什么没清理
    assert "跳过失效清理" in result["message"], result["message"]


def test_complete_sync_still_cleans_disappeared_records(fake_pan, pan_site, tmp_path) -> None:
    """反向担保：**没有**截断时清理必须照常工作。

    否则上面那个修复就等于把失效清理整个关掉了 —— 网盘上删掉的片子
    会永远留着死 STRM，媒体库里点开一片报错。
    """
    run(strm_sync.sync_storage(pan_site, pan_path="/剧集", clean=True))
    assert len(_strm_files(tmp_path)) == 2

    # 真的从网盘上删掉一集（不是扫不到，是确实没了）
    FakePan.tree["/剧集"] = FakePan.tree["/剧集"][1:]
    result = run(strm_sync.sync_storage(pan_site, pan_path="/剧集", clean=True))

    assert result["truncated"] is False
    assert result["removed"] == 1, "完整同步时失效清理没有工作"
    assert len(_strm_files(tmp_path)) == 1
