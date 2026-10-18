"""媒体库本地文件操作（浏览/重命名/移动/删除）测试。

v1.24.0 起本项目不做刮削（ADR-96），媒体库页只负责"看本地下载了什么 + 手工收拾"，
这些测试就是这套能力的行为契约。**断言都落在真实文件系统状态与索引行上**，
不断言源码文本 —— 之前断言源码文本导致过假绿。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.exceptions import ConfigError, NotFoundError
from app.db.models import LibraryFile
from app.db.session import session_scope
from app.services import library as library_service


def _purge_index() -> None:
    """只清 ops_case 子树下的索引行。

    会话级数据库是共享的，上一条用例留下的行会串到下一条
    （实测让 ``forgotten`` 从 1 变成 2 —— 是装置不干净，不是实现有 bug）。
    但**不能整表清空**：别的测试文件也往 library_files 写数据，全清会连坐。
    """
    prefix = str(settings.LIBRARY_DIR / "ops_case")
    with session_scope() as session:
        for row in session.execute(select(LibraryFile)).scalars().all():
            if (row.path or "").startswith(prefix):
                session.delete(row)


@pytest.fixture(scope="module", autouse=True)
def _tables():
    """本文件多数用例直接调服务层，不经过 ``client``，所以要自己保证表存在。

    建表原本只发生在 ``client`` fixture 的应用启动钩子里 —— 于是
    ``pytest tests/test_library_ops.py`` 单跑会 26 个 setup error
    （``no such table: library_files``），而全量跑却是绿的，因为别的文件先起了 app。
    这种"只在单跑时红"的装置依赖最费时间，直接在这里断掉。
    """
    from app.db.init_db import create_tables

    create_tables()


@pytest.fixture
def lib_tree():
    """在真实 LIBRARY_DIR 下造一棵小树，用完连目录带索引一起清掉。"""
    import shutil

    root = settings.LIBRARY_DIR / "ops_case"
    if root.exists():
        shutil.rmtree(root)
    _purge_index()
    (root / "Show S01").mkdir(parents=True)
    (root / "Show S01" / "ep1.mkv").write_bytes(b"0" * 2048)
    (root / "Show S01" / "ep1.srt").write_text("sub", encoding="utf-8")
    (root / "loose.mkv").write_bytes(b"0" * 1024)
    yield root
    if root.exists():
        shutil.rmtree(root)
    _purge_index()


def _index(path, title="Show"):
    with session_scope() as session:
        session.add(
            LibraryFile(path=str(path), title=title, media_type="tv", size=2048)
        )


def _indexed_paths() -> set[str]:
    with session_scope() as session:
        return {row[0] for row in session.execute(select(LibraryFile.path)).all()}


# ---------------- 路径越界防护 ----------------
def test_resolve_rejects_path_outside_roots(tmp_path):
    """库外的绝对路径必须被拒（而不是"没结果"式静默通过）。"""
    outside = tmp_path / "evil.mkv"
    outside.write_bytes(b"0")
    with pytest.raises(ConfigError) as exc:
        library_service.resolve_library_path(outside)
    assert "超出允许范围" in str(exc.value)


def test_resolve_rejects_dotdot_traversal(lib_tree):
    """``..`` 穿越会被 resolve() 展开后拦下。"""
    with pytest.raises(ConfigError):
        library_service.resolve_library_path(
            settings.LIBRARY_DIR / ".." / ".." / "windows"
        )


def test_resolve_accepts_inside_library(lib_tree):
    resolved = library_service.resolve_library_path(lib_tree / "loose.mkv")
    assert resolved == (lib_tree / "loose.mkv").resolve()


def test_resolve_missing_path_raises_not_found():
    with pytest.raises(NotFoundError):
        library_service.resolve_library_path(settings.LIBRARY_DIR / "nope_xyz.mkv")


def test_download_dir_is_also_allowed():
    """下载目录同样可操作 —— 用户要看的正是"本地下载情况"。"""
    roots = {str(p) for p in library_service.library_roots()}
    assert str(settings.DOWNLOAD_DIR.resolve()) in roots
    assert str(settings.LIBRARY_DIR.resolve()) in roots


# ---------------- 浏览 ----------------
def test_browse_without_path_lists_roots():
    data = library_service.browse_library(None)
    assert data["path"] is None
    names = {item["path"] for item in data["items"]}
    assert str(settings.LIBRARY_DIR.resolve()) in names


def test_browse_lists_entries_with_kind(lib_tree):
    data = library_service.browse_library(lib_tree)
    kinds = {item["name"]: item["kind"] for item in data["items"]}
    assert kinds["Show S01"] == "dir"
    assert kinds["loose.mkv"] == "media"


def test_browse_marks_subtitle_kind(lib_tree):
    data = library_service.browse_library(lib_tree / "Show S01")
    kinds = {item["name"]: item["kind"] for item in data["items"]}
    assert kinds["ep1.srt"] == "subtitle"
    assert kinds["ep1.mkv"] == "media"


def test_browse_dir_lists_before_files(lib_tree):
    data = library_service.browse_library(lib_tree)
    order = [item["kind"] for item in data["items"]]
    assert order[0] == "dir"


def test_browse_file_falls_back_to_parent(lib_tree):
    """点到文件时退回它所在目录，而不是报错。"""
    data = library_service.browse_library(lib_tree / "loose.mkv")
    assert data["path"] == str(lib_tree.resolve())


def test_browse_outside_root_is_rejected(tmp_path):
    with pytest.raises(ConfigError):
        library_service.browse_library(tmp_path)


# ---------------- 重命名 ----------------
def test_rename_file_moves_on_disk(lib_tree):
    result = library_service.rename_entry(lib_tree / "loose.mkv", "renamed.mkv")
    assert not (lib_tree / "loose.mkv").exists()
    assert (lib_tree / "renamed.mkv").is_file()
    assert result["target"].endswith("renamed.mkv")


def test_rename_syncs_library_index(lib_tree):
    """重命名单文件后索引里的 path 必须跟着变，否则媒体库页会指向死路径。"""
    old = lib_tree / "loose.mkv"
    _index(old)
    result = library_service.rename_entry(old, "moved.mkv")
    assert result["reindexed"] == 1
    paths = _indexed_paths()
    assert str(old) not in paths
    assert str(lib_tree / "moved.mkv") in paths


def test_rename_dir_reindexes_children(lib_tree):
    """重命名目录要按前缀批量改子条目，不能只改等值那一行。"""
    child = lib_tree / "Show S01" / "ep1.mkv"
    _index(child)
    result = library_service.rename_entry(lib_tree / "Show S01", "Show Season 1")
    assert result["reindexed"] == 1
    assert str(lib_tree / "Show Season 1" / "ep1.mkv") in _indexed_paths()


def test_rename_rejects_path_separator(lib_tree):
    """新名字里塞路径分隔符会被挡下，否则等于任意位置写入。"""
    with pytest.raises(ConfigError) as exc:
        library_service.rename_entry(lib_tree / "loose.mkv", "../../pwned.mkv")
    assert "非法字符" in str(exc.value)
    assert (lib_tree / "loose.mkv").exists()


def test_rename_rejects_empty_name(lib_tree):
    with pytest.raises(ConfigError):
        library_service.rename_entry(lib_tree / "loose.mkv", "   ")


def test_rename_rejects_existing_target(lib_tree):
    with pytest.raises(ConfigError) as exc:
        library_service.rename_entry(lib_tree / "loose.mkv", "Show S01")
    assert "已存在" in str(exc.value)
    assert (lib_tree / "loose.mkv").exists()


def test_rename_same_name_is_noop(lib_tree):
    result = library_service.rename_entry(lib_tree / "loose.mkv", "loose.mkv")
    assert result["reindexed"] == 0
    assert (lib_tree / "loose.mkv").exists()


# ---------------- 移动 ----------------
def test_move_file_into_dir(lib_tree):
    result = library_service.move_entry(lib_tree / "loose.mkv", lib_tree / "Show S01")
    assert (lib_tree / "Show S01" / "loose.mkv").is_file()
    assert not (lib_tree / "loose.mkv").exists()
    assert result["target"].endswith("loose.mkv")


def test_move_creates_missing_destination(lib_tree):
    dest = lib_tree / "movies" / "2024"
    library_service.move_entry(lib_tree / "loose.mkv", dest)
    assert (dest / "loose.mkv").is_file()


def test_move_syncs_index(lib_tree):
    old = lib_tree / "loose.mkv"
    _index(old)
    result = library_service.move_entry(old, lib_tree / "Show S01")
    assert result["reindexed"] == 1
    assert str(lib_tree / "Show S01" / "loose.mkv") in _indexed_paths()


def test_move_rejects_destination_outside_roots(lib_tree, tmp_path):
    with pytest.raises(ConfigError):
        library_service.move_entry(lib_tree / "loose.mkv", tmp_path / "out")
    assert (lib_tree / "loose.mkv").exists()


def test_move_rejects_existing_target_without_overwrite(lib_tree):
    (lib_tree / "Show S01" / "loose.mkv").write_bytes(b"x")
    with pytest.raises(ConfigError) as exc:
        library_service.move_entry(lib_tree / "loose.mkv", lib_tree / "Show S01")
    assert "已存在" in str(exc.value)
    assert (lib_tree / "loose.mkv").exists()


def test_move_overwrite_replaces_target(lib_tree):
    victim = lib_tree / "Show S01" / "loose.mkv"
    victim.write_bytes(b"x")
    library_service.move_entry(
        lib_tree / "loose.mkv", lib_tree / "Show S01", overwrite=True
    )
    assert victim.stat().st_size == 1024


def test_move_dir_into_itself_is_rejected(lib_tree):
    """把目录移进自己的子目录会造成无限递归，必须提前拦。"""
    with pytest.raises(ConfigError) as exc:
        library_service.move_entry(lib_tree / "Show S01", lib_tree / "Show S01" / "sub")
    assert "自己" in str(exc.value)
    assert (lib_tree / "Show S01").is_dir()


# ---------------- 删除 ----------------
def test_delete_file(lib_tree):
    library_service.delete_entry(lib_tree / "loose.mkv")
    assert not (lib_tree / "loose.mkv").exists()


def test_delete_reports_size(lib_tree):
    result = library_service.delete_entry(lib_tree / "loose.mkv")
    assert result["size"] == 1024
    assert result["files"] == 1


def test_delete_clears_index(lib_tree):
    target = lib_tree / "loose.mkv"
    _index(target)
    result = library_service.delete_entry(target)
    assert result["forgotten"] == 1
    assert str(target) not in _indexed_paths()


def test_delete_nonempty_dir_needs_recursive(lib_tree):
    """非空目录不给一键删，必须显式确认递归。"""
    with pytest.raises(ConfigError) as exc:
        library_service.delete_entry(lib_tree / "Show S01")
    assert "非空" in str(exc.value)
    assert (lib_tree / "Show S01" / "ep1.mkv").exists()


def test_delete_recursive_dir_removes_children_and_index(lib_tree):
    child = lib_tree / "Show S01" / "ep1.mkv"
    _index(child)
    result = library_service.delete_entry(lib_tree / "Show S01", recursive=True)
    assert not (lib_tree / "Show S01").exists()
    assert result["forgotten"] == 1
    assert str(child) not in _indexed_paths()


def test_delete_empty_dir_without_recursive(lib_tree):
    empty = lib_tree / "empty"
    empty.mkdir()
    library_service.delete_entry(empty)
    assert not empty.exists()


def test_delete_root_is_rejected():
    """删根目录等于清库，任何情况下都不放行。"""
    with pytest.raises(ConfigError) as exc:
        library_service.delete_entry(settings.LIBRARY_DIR, recursive=True)
    assert "根目录" in str(exc.value)
    assert settings.LIBRARY_DIR.exists()


def test_delete_outside_root_is_rejected(tmp_path):
    victim = tmp_path / "keepme.mkv"
    victim.write_bytes(b"0")
    with pytest.raises(ConfigError):
        library_service.delete_entry(victim)
    assert victim.exists()


# ---------------- HTTP 层 ----------------
def test_api_browse_returns_roots(client, auth_headers):
    response = client.get("/api/v1/library/browse", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True
    assert response.json()["roots"]


def test_api_rename_round_trip(client, auth_headers, lib_tree):
    response = client.post(
        "/api/v1/library/rename",
        headers=auth_headers,
        json={"path": str(lib_tree / "loose.mkv"), "new_name": "api.mkv"},
    )
    assert response.status_code == 200, response.text
    assert (lib_tree / "api.mkv").is_file()


def test_api_move_round_trip(client, auth_headers, lib_tree):
    response = client.post(
        "/api/v1/library/move",
        headers=auth_headers,
        json={"path": str(lib_tree / "loose.mkv"), "dest_dir": str(lib_tree / "Show S01")},
    )
    assert response.status_code == 200, response.text
    assert (lib_tree / "Show S01" / "loose.mkv").is_file()


def test_api_delete_round_trip(client, auth_headers, lib_tree):
    response = client.post(
        "/api/v1/library/delete",
        headers=auth_headers,
        json={"path": str(lib_tree / "loose.mkv")},
    )
    assert response.status_code == 200, response.text
    assert not (lib_tree / "loose.mkv").exists()


def test_api_rejects_outside_path(client, auth_headers, tmp_path):
    """越界请求要返回 4xx 且带可读原因，不能 200 静默失败。"""
    victim = tmp_path / "outside.mkv"
    victim.write_bytes(b"0")
    response = client.post(
        "/api/v1/library/delete", headers=auth_headers, json={"path": str(victim)}
    )
    assert response.status_code == 400, response.text
    assert "超出允许范围" in response.text
    assert victim.exists()


def test_api_scrape_endpoint_is_gone(client, auth_headers):
    """刮削已移除（ADR-96）：旧端点必须 404，避免文档与实现脱节。"""
    response = client.post(
        "/api/v1/library/scrape", headers=auth_headers, json={"limit": 1}
    )
    assert response.status_code == 404


# ---------------- 扫描会清理僵尸索引 ----------------
#: v1.24.0 前 scan_library 只加行不删行。实测后果：把 CF_LIBRARY_DIR 从
#: D:\...\library 改到 K:\media\library 之后，旧库那一行永远留在索引里 ——
#: 文件早不存在，路径又在允许根目录之外，连 delete_entry 都清不掉。
#: 那是一条任何操作都碰不到的僵尸行，会污染统计与缺集判断（静默错）。
def test_scan_removes_index_rows_whose_file_is_gone(lib_tree):
    """文件已删除的索引行必须在扫描时被清掉。"""
    ghost = lib_tree / "vanished.mkv"
    _index(ghost)
    assert str(ghost) in _indexed_paths()
    stats = library_service.scan_library(lib_tree)
    assert stats["removed"] >= 1
    assert str(ghost) not in _indexed_paths()


def test_scan_removes_rows_outside_allowed_roots_when_file_gone(lib_tree):
    """僵尸行即使指向允许范围之外，也要能被清掉。

    这正是实测遇到的那条：路径在 D: 老库，delete_entry 会（正确地）拒绝它，
    所以扫描是唯一能收拾它的入口。
    """
    orphan = Path("D:/definitely-not-here-xyz/old library/show/ep1.mkv")
    _index(orphan, title="旧库残留")
    stats = library_service.scan_library(lib_tree)
    assert stats["removed"] >= 1
    assert str(orphan) not in _indexed_paths()


def test_scan_keeps_rows_whose_file_still_exists(lib_tree):
    """清理口径是"文件不存在"，不是"不在当前 LIBRARY_DIR 下"。

    用户可能临时把 LIBRARY_DIR 指到别处，不该因此抹掉还在盘上的旧库记录。
    """
    keeper = lib_tree / "Show S01" / "ep1.mkv"
    _index(keeper)
    # 扫一个**不含** keeper 的子目录，keeper 仍在盘上 → 必须保留
    library_service.scan_library(lib_tree / "Show S01")
    assert str(keeper) in _indexed_paths()


def test_scan_reports_three_counters(lib_tree):
    stats = library_service.scan_library(lib_tree)
    assert set(stats) == {"scanned", "added", "removed"}


def test_scan_missing_root_still_prunes(lib_tree):
    """根目录不存在时也要清僵尸行 —— 换盘/盘符掉线正是最需要清的时候。"""
    ghost = lib_tree / "gone.mkv"
    _index(ghost)
    stats = library_service.scan_library(lib_tree / "no-such-subdir")
    assert stats["scanned"] == 0
    assert stats["removed"] >= 1
    assert str(ghost) not in _indexed_paths()


def test_api_scan_exposes_removed(client, auth_headers, lib_tree):
    _index(lib_tree / "ghost-api.mkv")
    response = client.post("/api/v1/library/scan", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert "removed" in response.json()


# ---------------- 兄弟目录同前缀边界 ----------------
#: 注入验证抓出的缺口：把 `_is_within` 的 `+ os.sep` 去掉后，上面所有越界用例
#: **依然全绿** —— 因为它们用的 tmp_path 跟根目录不共享前缀，压根走不到这条分支。
#: 真正会误判的是**兄弟目录**：`<root>X` 以 `<root>` 开头，不补分隔符就会被
#: 当成库内路径（实测对应 `K:\mediaX` vs `K:\media`）。ADR-96 里点名了这条边界，
#: 那就必须真有测试钉住它，否则文档在说谎。
def test_sibling_dir_with_shared_prefix_is_outside(lib_tree):
    """`<LIBRARY_DIR>X` 是兄弟目录，不是库内路径。"""
    root = Path(settings.LIBRARY_DIR).resolve()
    sibling = root.parent / (root.name + "X")
    sibling.mkdir(exist_ok=True)
    victim = sibling / "keepme.mkv"
    victim.write_bytes(b"0" * 16)
    try:
        with pytest.raises(ConfigError) as exc:
            library_service.resolve_library_path(victim)
        assert "超出允许范围" in str(exc.value)
        # 而且真的没被删掉 —— 只断异常不够，要证明文件安全
        with pytest.raises(ConfigError):
            library_service.delete_entry(victim)
        assert victim.exists()
    finally:
        victim.unlink(missing_ok=True)
        sibling.rmdir()


def test_is_within_requires_separator_boundary(tmp_path):
    """直接钉住比对函数本身，免得将来有人"优化"掉 os.sep。"""
    root = tmp_path / "media"
    assert library_service._is_within(root, root) is True
    assert library_service._is_within(root / "movies", root) is True
    assert library_service._is_within(tmp_path / "mediaX", root) is False
    assert library_service._is_within(tmp_path / "mediaX" / "a.mkv", root) is False


def test_download_dir_sibling_prefix_is_outside():
    """下载目录侧同理（两个根都走同一个比对函数，但两边都钉一次更保险）。"""
    root = Path(settings.DOWNLOAD_DIR).resolve()
    intruder = root.parent / (root.name + "-evil") / "x.mkv"
    with pytest.raises(ConfigError):
        library_service.resolve_library_path(intruder, must_exist=False)

# ---------------------------------------------------------------------------
# 入库跳过必须可见 + content_path 指向非媒体文件时回退到目录（v1.24.0 实测）
# ---------------------------------------------------------------------------


def _make_task(*, content_path: str, title: str, status: str | None = None):
    """建一条 completed 下载任务，meta.content_path 可控。"""
    from app.db.models import DownloadTask
    from app.schemas.enums import TaskStatus

    with session_scope() as session:
        task = DownloadTask(
            title=title,
            kind="torrent",
            link="magnet:?xt=urn:btih:" + "0" * 40,  # link 非空约束
            status=status or TaskStatus.COMPLETED.value,
            save_path=str(Path(content_path).parent),
            meta={"content_path": content_path},
        )
        session.add(task)
        session.flush()
        return task.id


def _drop_task(task_id: int) -> None:
    from app.db.models import DownloadTask

    with session_scope() as session:
        row = session.get(DownloadTask, task_id)
        if row:
            session.delete(row)


def _clear_completed_tasks() -> list[int]:
    """把库里已完成但未整理的任务先挪走，避免串扰本文件的断言。"""
    from app.db.models import DownloadTask
    from app.schemas.enums import TaskStatus

    moved = []
    with session_scope() as session:
        for row in session.execute(
            select(DownloadTask).where(
                DownloadTask.status == TaskStatus.COMPLETED.value
            )
        ).scalars().all():
            row.status = "paused"
            moved.append(row.id)
    return moved


def test_transfer_falls_back_to_directory_when_content_path_is_subtitle(
    tmp_path, monkeypatch
) -> None:
    """content_path 指向字幕时，要回退到它所在目录把正片找出来。

    实测（v1.24.0）：aria2 按 torrent 内文件序返回 `files`，
    `content_path` 拿到的是 **140 字节的 `Big Buck Bunny.en.srt`**，
    而正片 `.mp4`（276MB）在同一目录里。
    不回退的话整个任务静默停在 `completed`，媒体库里什么都没有。
    """
    import asyncio

    _clear_completed_tasks()
    src = tmp_path / "downloads" / "Big Buck Bunny"
    src.mkdir(parents=True)
    (src / "Big Buck Bunny.en.srt").write_text("1\n00:00:01,000\n字幕\n", encoding="utf-8")
    video = src / "Big Buck Bunny.mp4"
    video.write_bytes(b"\x00" * (200 * 1024 * 1024 + 1))  # 要过 MIN_FILE_SIZE_MB

    lib = tmp_path / "library"
    lib.mkdir()
    monkeypatch.setattr(settings, "LIBRARY_DIR", lib)
    monkeypatch.setattr(settings, "DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setattr(settings, "TRANSFER_MODE", "copy")

    task_id = _make_task(
        content_path=str(src / "Big Buck Bunny.en.srt"),
        title="Big Buck Bunny (2008)",
    )
    try:
        stats = asyncio.run(library_service.transfer_completed_tasks())
    finally:
        _drop_task(task_id)

    assert stats["files"] >= 1, f"正片没被入库：{stats}"
    assert stats["skipped"] == 0, f"回退没生效，任务被跳过：{stats}"
    mp4s = list(lib.rglob("*.mp4"))
    assert mp4s, f"库里没有 mp4：{list(lib.rglob('*'))}"
    # 任务标题里的年份必须落到入库路径上。只断言"有文件入库"是不够的 ——
    # 注入验证证明：删掉 year 传递后那种断言照样绿（假绿）。
    assert any("(2008)" in str(p) for p in mp4s), (
        f"任务标题的年份没进入库路径：{[str(p) for p in mp4s]}"
    )
    assert not any("unknown" in p.name for p in mp4s), (
        f"文件名里出现字面量 unknown：{[p.name for p in mp4s]}"
    )


def test_transfer_applies_task_year_on_direct_path(tmp_path, monkeypatch) -> None:
    """content_path 直接指向正片时（单文件种子），任务标题的年份也要落到入库路径。

    这条和上面那条走的是 `transfer_completed_tasks` 里**两个不同的调用点**：
    上面那条 content_path 是字幕，走目录回退分支；这条不回退，走首个调用点。
    注入验证发现：只有回退分支那条测试时，删掉首个调用点的 `year=` 照样绿。
    """
    import asyncio

    _clear_completed_tasks()
    src = tmp_path / "downloads" / "Big Buck Bunny"
    src.mkdir(parents=True)
    video = src / "Big Buck Bunny.mp4"
    video.write_bytes(b"\x00" * (200 * 1024 * 1024 + 1))  # 要过 MIN_FILE_SIZE_MB

    lib = tmp_path / "library"
    lib.mkdir()
    monkeypatch.setattr(settings, "LIBRARY_DIR", lib)
    monkeypatch.setattr(settings, "DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setattr(settings, "TRANSFER_MODE", "copy")

    task_id = _make_task(content_path=str(video), title="Big Buck Bunny (2008)")
    try:
        stats = asyncio.run(library_service.transfer_completed_tasks())
    finally:
        _drop_task(task_id)

    assert stats["files"] >= 1, f"正片没被入库：{stats}"
    mp4s = list(lib.rglob("*.mp4"))
    assert mp4s, f"库里没有 mp4：{list(lib.rglob('*'))}"
    assert any("(2008)" in str(q) for q in mp4s), (
        f"任务标题的年份没进入库路径：{[str(q) for q in mp4s]}"
    )


def test_transfer_counts_skipped_tasks_instead_of_silent_log(tmp_path, monkeypatch) -> None:
    """真的没有媒体文件时，要计入 skipped，而不是只写一行日志。

    只 logger.info 的话返回值是 {"tasks": 0, "files": 0, "failed": 0} ——
    和"根本没有待整理任务"**一模一样**，调用方无法区分（ADR-82）。
    """
    import asyncio

    _clear_completed_tasks()
    src = tmp_path / "downloads" / "只有字幕的种子"
    src.mkdir(parents=True)
    (src / "readme.txt").write_text("no video here", encoding="utf-8")

    lib = tmp_path / "library"
    lib.mkdir()
    monkeypatch.setattr(settings, "LIBRARY_DIR", lib)
    monkeypatch.setattr(settings, "DOWNLOAD_DIR", str(tmp_path / "downloads"))

    task_id = _make_task(content_path=str(src), title="只有字幕的种子")
    try:
        stats = asyncio.run(library_service.transfer_completed_tasks())
    finally:
        _drop_task(task_id)

    assert stats["skipped"] == 1, f"跳过没被计数：{stats}"
    assert stats["files"] == 0
    # 反向担保：不能把跳过算成失败——失败是"试了没成"，跳过是"没东西可试"
    assert stats["failed"] == 0, f"跳过被错算成失败：{stats}"


def test_transfer_with_nothing_to_do_is_distinguishable_from_skipping(monkeypatch) -> None:
    """反向担保：真的没有待整理任务时 skipped 必须是 0。

    否则 skipped 就成了恒为正的噪声，用户学会忽略它，等于没加。
    """
    import asyncio

    _clear_completed_tasks()
    stats = asyncio.run(library_service.transfer_completed_tasks())
    assert stats == {"tasks": 0, "files": 0, "failed": 0, "skipped": 0}, stats
