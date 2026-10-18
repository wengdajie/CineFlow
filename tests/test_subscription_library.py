"""订阅名称稳定决定保存和入库目录，不随字幕组标题改变。"""

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.db.models import DownloadTask, LibraryFile, Subscribe
from app.db.session import session_scope
from app.services import download, download_routing, library


def test_subscription_releases_share_one_library_folder(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LIBRARY_DIR", tmp_path / "library")
    with session_scope() as session:
        subscription = Subscribe(title="Canonical Show", media_type="tv", season=1)
        session.add(subscription)
        session.flush()
        subscription_id = subscription.id
        for episode, prefix in [(1, "OtherGroup"), (7, "7月新番")]:
            source = tmp_path / f"[{prefix}] Wrong Show {2000+episode} S02E{episode:02d}.mkv"
            source.write_bytes(b"media" * 200)
            session.add(DownloadTask(
                title=f"[{prefix}] Wrong Show S01E{episode:02d}",
                link=f"https://example.test/canonical/{episode}",
                kind="direct", status="completed", subscribe_id=subscription_id,
                season=None, meta={"content_path": str(source)},
            ))
    asyncio.run(library.transfer_completed_tasks())
    with session_scope() as session:
        files = list(session.execute(select(LibraryFile).where(LibraryFile.title == "Canonical Show")).scalars())
        tasks = list(session.execute(select(DownloadTask).where(DownloadTask.subscribe_id == subscription_id)).scalars())
        assert {f.episode for f in files} == {1, 7}
        assert len({Path(f.path).parent for f in files}) == 1
        assert all(Path(f.path).parent == tmp_path / "library" / "Canonical Show" / "Season 01" for f in files)
        assert all(Path(f.path).exists() for f in files)
        assert all(t.status == "transferred" for t in tasks)
        for row in files + tasks:
            session.delete(row)
        session.delete(session.get(Subscribe, subscription_id))


@pytest.mark.parametrize("custom", [None, "chosen-path"])
def test_subscription_download_path_is_stable(client, tmp_path, monkeypatch, custom):
    monkeypatch.setattr(settings, "DOWNLOAD_DIR", tmp_path / "downloads")
    calls = []

    class Downloader:
        site_name = "test aria2"

        async def add(self, link, **kwargs):
            calls.append(kwargs["save_path"])
            return "gid-test"

    monkeypatch.setattr(download_routing, "candidates_for", lambda *args: [Downloader()])
    with session_scope() as session:
        subscription = Subscribe(title="Stable Show", media_type="tv", season=2)
        session.add(subscription)
        session.flush()
        subscription_id = subscription.id
    task = asyncio.run(download.add_download(
        {"title": "7月新番 Different Title S01E07", "kind": "direct", "link": "https://example.test/stable.mkv"},
        subscribe_id=subscription_id, save_path=custom, notify=False,
    ))
    expected = Path(custom) if custom else tmp_path / "downloads" / "tv" / "Stable Show" / "Season 02"
    assert calls == [str(expected)]
    assert task.season == 2
    with session_scope() as session:
        session.delete(session.get(DownloadTask, task.id))
        session.delete(session.get(Subscribe, subscription_id))
