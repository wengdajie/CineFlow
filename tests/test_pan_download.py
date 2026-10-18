"""网盘目录下载必须逐文件交给 aria2，并跟踪全部文件。"""

import asyncio
from pathlib import Path

import pytest

from app.providers.downloader.aria2 import Aria2Downloader
from app.providers.downloader.base import TorrentState
from app.providers.panstorage.base import IncompleteDirectory, PanFile
from app.services import download, download_routing, pan_download, pan_storage


class Storage:
    site_name = "Test cloud"
    direct_link_user_agent = "netdisk-test"

    async def download_url(self, path, *, file_id=None):
        return None if path in ("/show", "/show/sub") else "https://files.test/" + Path(path).name

    async def list_dir_for_sync(self, path):
        if path == "/show":
            return [PanFile("ep.mkv", "/show/ep.mkv"), PanFile("sub", "/show/sub", is_dir=True)]
        return [PanFile("ep.mkv", "/show/sub/ep.mkv")]


class Downloader:
    name = "aria2"
    site_name = "Test aria2"

    def __init__(self, fail_after=None):
        self.calls = []
        self.removed = []
        self.fail_after = fail_after

    async def add(self, link, **options):
        self.calls.append((link, options))
        if len(self.calls) == self.fail_after:
            return None
        return f"gid-{len(self.calls)}"

    async def remove(self, gid, *, delete_files):
        self.removed.append((gid, delete_files))
        return True


@pytest.fixture
def storage(monkeypatch):
    storage = Storage()
    monkeypatch.setattr(pan_storage, "get_storage", lambda site_id: storage)
    return storage


def test_cloud_directory_download_preserves_subfolders_and_all_ids(monkeypatch, tmp_path, storage):
    downloader = Downloader()
    monkeypatch.setattr(download_routing, "candidates_for", lambda *args: [downloader])
    result = asyncio.run(download._land_pan_to_local({"site_id": 1, "saved_path": "/show", "is_dir": True}, str(tmp_path), "aria2"))
    assert result["success"]
    assert result["external_ids"] == ["gid-1", "gid-2"]
    assert [Path(options["save_path"]) for _, options in downloader.calls] == [tmp_path, tmp_path / "sub"]
    assert all(options["filename"] == "ep.mkv" for _, options in downloader.calls)
    assert all(options["user_agent"] == "netdisk-test" for _, options in downloader.calls)


def test_partial_submission_cancels_queued_files_without_deleting_data(monkeypatch, tmp_path, storage):
    downloader = Downloader(fail_after=2)
    monkeypatch.setattr(download_routing, "candidates_for", lambda *args: [downloader])
    result = asyncio.run(download._land_pan_to_local({"site_id": 1, "saved_path": "/show", "is_dir": True}, str(tmp_path), None))
    assert result["success"] is False
    assert downloader.removed == [("gid-1", False)]


def test_root_returned_by_share_does_not_download_entire_account(storage):
    with pytest.raises(ValueError, match="具体文件"):
        asyncio.run(pan_download.resolve_files({"site_id": 1, "saved_path": "/"}))
    files = asyncio.run(pan_download.resolve_files({"site_id": 1, "saved_path": "/", "saved_files": ["/chosen.mkv"]}))
    assert [f["path"] for f in files] == ["/chosen.mkv"]


def test_incomplete_directory_never_submits_partial_downloads(monkeypatch, storage, tmp_path):
    async def incomplete(path):
        raise IncompleteDirectory([PanFile("ep.mkv", "/show/ep.mkv")])

    storage.list_dir_for_sync = incomplete
    downloader = Downloader()
    monkeypatch.setattr(download_routing, "candidates_for", lambda *args: [downloader])
    result = asyncio.run(download._land_pan_to_local({"site_id": 1, "saved_path": "/show", "is_dir": True}, str(tmp_path), None))
    assert result["success"] is False
    assert downloader.calls == []


@pytest.mark.parametrize("bad_path", ["/elsewhere/ep.mkv", "/show/../escape.mkv", "/show/..\\escape.mkv"])
def test_cloud_paths_cannot_escape_selected_directory(storage, bad_path):
    async def listing(path):
        return [PanFile("ep.mkv", bad_path)]

    storage.list_dir_for_sync = listing
    with pytest.raises(ValueError):
        asyncio.run(pan_download.resolve_files({"site_id": 1, "saved_path": "/show", "is_dir": True}))


@pytest.mark.parametrize("second", [None, "downloading", "failed", "completed"])
def test_multi_file_task_waits_for_every_file(second):
    class States:
        async def get(self, gid):
            if gid == "1":
                return TorrentState("1", status="completed", progress=1, size=10, downloaded=10)
            if second is None:
                return None
            return TorrentState("2", status=second, progress=1 if second == "completed" else .5, size=10, downloaded=10 if second == "completed" else 5)

    state = asyncio.run(pan_download.combined_state(States(), ["1", "2"], "/task"))
    if second is None:
        assert state is None
    else:
        assert state.finished == (second == "completed")
        assert state.status == second
        assert state.content_path == "/task"


def test_aria2_sets_original_filename_and_required_user_agent(monkeypatch):
    downloader = Aria2Downloader({"name": "aria2"})
    calls = []

    async def call(method, params=None):
        calls.append((method, params))
        return "gid"

    monkeypatch.setattr(downloader, "_call", call)
    asyncio.run(downloader.add("https://files.test/id", save_path="/download", filename="Episode 01.mkv", user_agent="netdisk-test"))
    options = calls[0][1][1]
    assert options["out"] == "Episode 01.mkv"
    assert options["user-agent"] == "netdisk-test"


def test_baidu_transfer_result_tracks_only_transferred_paths(monkeypatch):
    from app.providers.panstorage import baidu

    async def transfer(*args, **kwargs):
        return {"errno": 0, "extra": {"list": [{"to": "/chosen/episode.mkv"}]}}

    monkeypatch.setattr(baidu, "fetch_json", transfer)
    storage = baidu.BaiduPanStorage({"name": "cloud"})
    result = asyncio.run(storage._submit_transfer({"shareid": "1", "uk": "2", "bdstoken": "test", "headers": {}}, ["1"], "/chosen"))
    assert result.saved_files == ["/chosen/episode.mkv"]
    assert result.to_dict()["saved_files"] == result.saved_files


def test_all_files_are_paused_resumed_and_removed(monkeypatch, client):
    from app.db.models import DownloadTask
    from app.db.session import session_scope

    calls = []

    class Controls:
        async def pause(self, gid):
            calls.append(("pause", gid))
            return True

        async def resume(self, gid):
            calls.append(("resume", gid))
            return True

        async def remove(self, gid, *, delete_files):
            assert delete_files is False
            calls.append(("remove", gid))
            return True

    monkeypatch.setattr(download.site_service, "default_downloader", lambda *args: Controls())
    with session_scope() as session:
        task = DownloadTask(title="multi", kind="pan", link="pan://test/control", status="downloading", external_id="gid-1",
                            meta={"pan_download_ids": ["gid-1", "gid-2"]})
        session.add(task)
        session.flush()
        task_id = task.id
    assert asyncio.run(download.control_task(task_id, "pause"))
    assert asyncio.run(download.control_task(task_id, "resume"))
    assert asyncio.run(download.remove_task(task_id, delete_files=False))
    assert calls == [(action, gid) for action in ["pause", "resume", "remove"] for gid in ["gid-1", "gid-2"]]


def test_concurrent_pending_pan_retry_submits_once(monkeypatch, client, tmp_path):
    from app.db.models import DownloadTask
    from app.db.session import session_scope

    calls = []

    async def land(*args):
        calls.append(args)
        await asyncio.sleep(.01)
        return {"success": True, "downloader": Downloader(), "external_id": "gid-1", "external_ids": ["gid-1"]}

    monkeypatch.setattr(download, "_land_pan_to_local", land)
    with session_scope() as session:
        task = DownloadTask(title="retry", kind="pan", link="pan://test/retry", status="pending", save_path=str(tmp_path))
        session.add(task)
        session.flush()
        task_id = task.id
    async def retry():
        result = {"site_id": 1, "storage": "cloud", "saved_path": "/episode.mkv"}
        return await asyncio.gather(download.queue_saved_pan_task(task_id, result), download.queue_saved_pan_task(task_id, result))

    results = asyncio.run(retry())
    assert sum(bool(r.get("success")) for r in results) == 1
    assert len(calls) == 1
    with session_scope() as session:
        task = session.get(DownloadTask, task_id)
        assert task.status == "downloading"
        assert task.meta["saved_path"] == "/episode.mkv"
        session.delete(task)
