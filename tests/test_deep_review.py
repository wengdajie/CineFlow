"""深度审查中复现的凭据、任务归属和文件安全问题。"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.core import organizer
from app.core.config import settings
from app.core.exceptions import ConfigError, ProviderError
from app.core.meta import parse
from app.db.models import DownloadTask, SiteConfig, User
from app.db.session import session_scope
from app.providers.panstorage.local_dir import LocalDirStorage
from app.services import download, library, sites


@pytest.mark.parametrize("missing", ["deleted aria2", "aria2"])
def test_missing_named_downloader_does_not_use_another_instance(monkeypatch, missing):
    other = SimpleNamespace(site_name="another aria2", name="aria2")
    monkeypatch.setattr(sites, "healthy_downloaders", lambda: [other])
    assert sites.default_downloader(missing) is None


def test_sync_does_not_read_foreign_downloader_task(monkeypatch, client):
    calls = []

    async def get(gid):
        calls.append(gid)

    async def transfer():
        return {}

    monkeypatch.setattr(
        sites, "downloaders", lambda: [SimpleNamespace(site_name="unrelated", get=get)]
    )
    monkeypatch.setattr(library, "transfer_completed_tasks", transfer)
    with session_scope() as session:
        task = DownloadTask(
            title="review foreign task",
            link="review://foreign",
            kind="direct",
            status="downloading",
            external_id="review-original-gid",
            downloader="deleted-instance",
        )
        session.add(task)
    asyncio.run(download.sync_tasks())
    assert "review-original-gid" not in calls


def test_site_options_hide_secrets_and_round_trip_without_losing_them(client, auth_headers):
    with session_scope() as session:
        site = SiteConfig(
            name="review-secret-options",
            provider="quark",
            kind="panstorage",
            enabled=False,
            options={
                "refresh_token": "review-private-token",
                "headers": {"Authorization": "review-private-auth", "Accept": "application/json"},
                "root_path": "/media",
            },
        )
        session.add(site)
        session.flush()
        site_id = site.id
    exposed = next(
        item
        for item in client.get("/api/v1/sites", headers=auth_headers).json()
        if item["id"] == site_id
    )
    assert "review-private" not in json.dumps(exposed)
    options = exposed["options"]
    options["root_path"] = "/new"
    response = client.patch(
        f"/api/v1/sites/{site_id}", headers=auth_headers, json={"options": options}
    )
    assert response.status_code == 200
    with session_scope() as session:
        updated = session.get(SiteConfig, site_id)
        assert updated.options["refresh_token"] == "review-private-token"
        assert updated.options["headers"]["Authorization"] == "review-private-auth"
        assert updated.options["root_path"] == "/new"


def test_api_token_cannot_authenticate_as_a_disabled_administrator(monkeypatch, client):
    monkeypatch.setattr(settings, "API_TOKEN", "review-only-api-token")
    with session_scope() as session:
        admins = list(session.query(User).filter(User.is_superuser.is_(True)))
        previous = {user.id: user.is_active for user in admins}
        for user in admins:
            user.is_active = False
    try:
        response = client.get("/api/v1/auth/me", headers={"X-API-Token": "review-only-api-token"})
        assert response.status_code == 401
    finally:
        with session_scope() as session:
            for user_id, active in previous.items():
                session.get(User, user_id).is_active = active


def test_overwrite_never_unlinks_the_source_itself(tmp_path):
    source = tmp_path / "Show.mkv"
    source.write_bytes(b"existing media")
    result = organizer.transfer_file(
        source, library_dir=tmp_path, template="Show.mkv", mode="copy", overwrite=True
    )
    assert source.exists()
    assert source.read_bytes() == b"existing media"
    assert result.success


def test_failed_overwrite_preserves_existing_media(monkeypatch, tmp_path):
    source = tmp_path / "input.mkv"
    source.write_bytes(b"new media")
    root = tmp_path / "library"
    root.mkdir()
    target = root / "target.mkv"
    target.write_bytes(b"old media")

    def fail(*args):
        raise OSError("disk full fixture")

    monkeypatch.setattr(organizer, "_link_or_copy", fail)
    result = organizer.transfer_file(
        source, library_dir=root, template="target.mkv", overwrite=True
    )
    assert not result.success
    assert target.exists()
    assert target.read_bytes() == b"old media"
    assert source.read_bytes() == b"new media"


def test_local_storage_root_cannot_be_deleted(tmp_path):
    root = tmp_path / "mounted"
    root.mkdir()
    (root / "video.mkv").write_bytes(b"media")
    storage = LocalDirStorage({"options": {"base_dir": str(root)}})
    assert not asyncio.run(storage.delete("/"))
    assert (root / "video.mkv").exists()


def test_absolute_organizer_template_cannot_escape_library(tmp_path):
    source = tmp_path / "source.mkv"
    source.write_bytes(b"media")
    outside = tmp_path / "outside.mkv"
    result = organizer.transfer_file(
        source, library_dir=tmp_path / "library", template=str(outside), meta=parse("Movie.2024")
    )
    assert not result.success
    assert not outside.exists()


def test_manual_download_is_recorded_before_external_submission(monkeypatch, client):
    seen = []

    async def add(link, **kwargs):
        with session_scope() as session:
            claim = session.query(DownloadTask).filter_by(link=link, status="submitting").first()
            seen.append(claim is not None)
        return "review-manual-gid"

    monkeypatch.setattr(
        download.download_routing,
        "candidates_for",
        lambda *args: [SimpleNamespace(site_name="review", add=add)],
    )
    task = asyncio.run(
        download.add_download(
            {
                "title": "Manual Review",
                "link": "https://example.test/review-manual.mp4",
                "kind": "direct",
            },
            notify=False,
        )
    )
    assert task.status == "downloading"
    assert seen == [True]


def test_deleted_pan_task_withdraws_just_submitted_files(monkeypatch, client):
    removed = []
    with session_scope() as session:
        task = DownloadTask(
            title="review delete during I/O", link="review://delete", kind="pan", status="pending"
        )
        session.add(task)
        session.flush()
        task_id = task.id

    async def remove(gid, *, delete_files):
        assert not delete_files
        removed.append(gid)
        return True

    async def land(*args):
        with session_scope() as session:
            session.delete(session.get(DownloadTask, task_id))
        return {
            "success": True,
            "external_id": "gid-1",
            "external_ids": ["gid-1", "gid-2"],
            "downloader": SimpleNamespace(site_name="review", remove=remove),
        }

    monkeypatch.setattr(download, "_land_pan_to_local", land)
    result = asyncio.run(
        download.queue_saved_pan_task(task_id, {"site_id": 1, "saved_path": "/file"})
    )
    assert not result["success"]
    assert removed == ["gid-1", "gid-2"]


def test_failed_downloader_removal_keeps_task_for_retry(monkeypatch, client):
    async def remove(*args, **kwargs):
        return False

    monkeypatch.setattr(sites, "default_downloader", lambda *args: SimpleNamespace(remove=remove))
    with session_scope() as session:
        task = DownloadTask(
            title="review retain task",
            link="review://retain",
            kind="direct",
            status="downloading",
            external_id="gid",
            downloader="review",
        )
        session.add(task)
        session.flush()
        task_id = task.id
    with pytest.raises(ProviderError):
        asyncio.run(download.remove_task(task_id))
    with session_scope() as session:
        assert session.get(DownloadTask, task_id) is not None


def test_partial_library_transfer_remains_retryable(monkeypatch, client, tmp_path):
    monkeypatch.setattr(settings, "LIBRARY_DIR", tmp_path / "library")
    source = tmp_path / "pack"
    source.mkdir()
    for episode in (1, 2):
        (source / f"Review.Show.S01E{episode:02d}.mkv").write_bytes(b"video" * 40)
    with session_scope() as session:
        task = DownloadTask(
            title="Review Show S01",
            kind="direct",
            link="review://partial",
            status="completed",
            meta={"content_path": str(source)},
        )
        session.add(task)
        session.flush()
        task_id = task.id
    real_transfer = organizer._link_or_copy
    fail_second = True

    def transfer(path, *args):
        if fail_second and "E02" in path.name:
            raise OSError("disk fixture")
        return real_transfer(path, *args)

    monkeypatch.setattr(organizer, "_link_or_copy", transfer)
    asyncio.run(library.transfer_completed_tasks())
    with session_scope() as session:
        assert session.get(DownloadTask, task_id).status == "completed"
    fail_second = False
    asyncio.run(library.transfer_completed_tasks())
    with session_scope() as session:
        assert session.get(DownloadTask, task_id).status == "transferred"
    assert len(list((tmp_path / "library").rglob("*.mkv"))) == 2


def test_failed_library_move_keeps_overwritten_file(monkeypatch, tmp_path):
    root = tmp_path / "library"
    source_dir, dest = root / "source", root / "destination"
    source_dir.mkdir(parents=True)
    dest.mkdir()
    source, target = source_dir / "video.mkv", dest / "video.mkv"
    source.write_bytes(b"new")
    target.write_bytes(b"old")
    monkeypatch.setattr(settings, "LIBRARY_DIR", root)

    def fail(*args, **kwargs):
        raise OSError("disk fixture")

    monkeypatch.setattr(library.shutil, "move", fail)
    monkeypatch.setattr(organizer, "_link_or_copy", fail)
    with pytest.raises(ConfigError):
        library.move_entry(source, dest, overwrite=True)
    assert target.exists() and target.read_bytes() == b"old"
    assert source.exists() and source.read_bytes() == b"new"


def test_library_move_cannot_overwrite_its_source_ancestor(monkeypatch, tmp_path):
    root = tmp_path / "library"
    source = root / "parent" / "parent"
    source.mkdir(parents=True)
    media = source / "video.mkv"
    media.write_bytes(b"keep")
    monkeypatch.setattr(settings, "LIBRARY_DIR", root)
    with pytest.raises(ConfigError):
        library.move_entry(source, root, overwrite=True)
    assert media.exists() and media.read_bytes() == b"keep"


@pytest.mark.parametrize("field", ["name", "url", "enabled", "priority", "timeout", "options"])
def test_site_update_rejects_null_required_fields(client, auth_headers, field):
    with session_scope() as session:
        site = SiteConfig(
            name=f"review-null-{field}", provider="quark", kind="panstorage", enabled=False
        )
        session.add(site)
        session.flush()
        site_id = site.id
    response = client.patch(f"/api/v1/sites/{site_id}", headers=auth_headers, json={field: None})
    assert response.status_code == 422


def test_sync_does_not_regress_an_already_transferred_task(monkeypatch, client):
    from app.providers.downloader.base import TorrentState

    with session_scope() as session:
        task = DownloadTask(
            title="review stale snapshot",
            link="review://stale",
            kind="direct",
            status="downloading",
            external_id="stale-gid",
            downloader="review-sync",
        )
        session.add(task)
        session.flush()
        task_id = task.id

    async def get(gid):
        with session_scope() as session:
            session.get(DownloadTask, task_id).status = "transferred"
        return TorrentState(gid, status="completed", progress=1)

    async def transfer():
        return {}

    monkeypatch.setattr(
        sites, "downloaders", lambda: [SimpleNamespace(site_name="review-sync", get=get)]
    )
    monkeypatch.setattr(library, "transfer_completed_tasks", transfer)
    asyncio.run(download.sync_tasks())
    with session_scope() as session:
        assert session.get(DownloadTask, task_id).status == "transferred"


def test_subtitles_from_another_episode_are_not_attached(tmp_path):
    source = tmp_path / "Show.S01E01.mkv"
    source.write_bytes(b"video")
    (tmp_path / "Show.S01E02.zh.srt").write_text("wrong episode", encoding="utf-8")
    (tmp_path / "show.s01e01.zh.srt").write_text("correct episode", encoding="utf-8")
    result = organizer.transfer_file(source, library_dir=tmp_path / "library", mode="copy")
    subtitles = list(result.target.parent.glob("*.srt"))
    assert len(subtitles) == 1
    assert subtitles[0].read_text(encoding="utf-8") == "correct episode"
    assert subtitles[0].name.endswith(".zh.srt")


def test_failed_strm_overwrite_keeps_previous_contents(monkeypatch, tmp_path):
    from pathlib import Path

    source = tmp_path / "video.mkv"
    source.write_bytes(b"video")
    root = tmp_path / "strm"
    root.mkdir()
    target = root / "video.strm"
    target.write_text("previous source", encoding="utf-8")
    monkeypatch.setattr(settings, "STRM_DIR", root)
    original_write = Path.write_text

    def fail(path, *args, **kwargs):
        original_write(path, "partial", encoding="utf-8")
        raise OSError("disk fixture")

    monkeypatch.setattr(Path, "write_text", fail)
    result = organizer.transfer_file(source, mode="strm", template="video.mkv", overwrite=True)
    assert not result.success
    assert target.read_text(encoding="utf-8") == "previous source"


def test_video_subscription_does_not_count_failed_tasks_as_downloaded(monkeypatch, client):
    from app.db.models import VideoSubscribe
    from app.services import video_subscribe

    with session_scope() as session:
        row = VideoSubscribe(
            name="review failed video", url="https://www.youtube.com/@review", skip_existing=False
        )
        session.add(row)
        session.flush()
        row_id = row.id

    async def listing(*args, **kwargs):
        return [
            {
                "id": "review-video",
                "title": "Review",
                "url": "https://www.youtube.com/watch?v=review",
            }
        ], None

    async def failed(*args, **kwargs):
        return SimpleNamespace(status="failed")

    monkeypatch.setattr(video_subscribe, "list_entries", listing)
    monkeypatch.setattr(download, "add_download", failed)
    result = asyncio.run(video_subscribe.check_one(row_id, notify=False))
    assert result["downloaded"] == 0
    assert "失败" in result["message"]


@pytest.mark.parametrize("kind", ["video", "pan"])
def test_subscription_checks_cannot_overlap(monkeypatch, client, kind):
    from app.db.models import PanSubscribe, VideoSubscribe
    from app.providers.panstorage.base import SaveResult
    from app.services import pan_subscribe, video_subscribe

    service = video_subscribe if kind == "video" else pan_subscribe
    with session_scope() as session:
        row = (
            VideoSubscribe(
                name="review concurrent video", url="https://www.youtube.com/@review-concurrent"
            )
            if kind == "video"
            else PanSubscribe(
                name="review concurrent share", share_url="https://pan.quark.cn/s/review-concurrent"
            )
        )
        session.add(row)
        session.flush()
        row_id = row.id

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def listing(*args, **kwargs):
            calls.append(True)
            entered.set()
            await release.wait()
            return ([], None) if kind == "video" else []

        async def save(*args, **kwargs):
            return SaveResult(True, "fixture", file_count=1)

        if kind == "video":
            monkeypatch.setattr(service, "list_entries", listing)
        else:
            monkeypatch.setattr(
                service,
                "_storage_for",
                lambda row: SimpleNamespace(list_share=listing, save_share=save),
            )
        first = asyncio.create_task(service.check_one(row_id, notify=False))
        await entered.wait()
        second = asyncio.create_task(service.check_one(row_id, notify=False))
        await asyncio.sleep(0)
        release.set()
        results = await asyncio.gather(first, second)
        assert len(calls) == 1
        assert any(result.get("skipped") for result in results)

    asyncio.run(run())


@pytest.mark.parametrize("status", ["failed", "canceled", "downloading", "paused"])
def test_full_progress_does_not_override_explicit_downloader_state(status):
    from app.providers.downloader.base import TorrentState

    assert not TorrentState("review", status=status, progress=1).finished


def test_renaming_downloader_preserves_task_ownership(client, auth_headers):
    with session_scope() as session:
        site = SiteConfig(
            name="review-original-instance", provider="aria2", kind="downloader", enabled=False
        )
        task = DownloadTask(
            title="review ownership", link="review://ownership", kind="direct", downloader=site.name
        )
        session.add_all([site, task])
        session.flush()
        site_id, task_id = site.id, task.id
    response = client.patch(
        f"/api/v1/sites/{site_id}", headers=auth_headers, json={"name": "review-renamed-instance"}
    )
    assert response.status_code == 200
    with session_scope() as session:
        assert session.get(DownloadTask, task_id).downloader == "review-renamed-instance"


def test_site_rename_conflict_is_a_validation_error(client, auth_headers):
    with session_scope() as session:
        first = SiteConfig(
            name="review-rename-a", provider="quark", kind="panstorage", enabled=False
        )
        second = SiteConfig(
            name="review-rename-b", provider="quark", kind="panstorage", enabled=False
        )
        session.add_all([first, second])
        session.flush()
        site_id = first.id
    response = client.patch(
        f"/api/v1/sites/{site_id}", headers=auth_headers, json={"name": "review-rename-b"}
    )
    assert response.status_code == 409


def test_overwriting_indexed_media_does_not_leave_duplicate_metadata(monkeypatch, client, tmp_path):
    from app.db.models import LibraryFile

    root = tmp_path / "library"
    source, target = root / "input" / "video.mkv", root / "dest" / "video.mkv"
    source.parent.mkdir(parents=True)
    target.parent.mkdir()
    source.write_bytes(b"new")
    target.write_bytes(b"old")
    monkeypatch.setattr(settings, "LIBRARY_DIR", root)
    with session_scope() as session:
        session.add_all(
            [
                LibraryFile(path=str(source), title="Source", size=3),
                LibraryFile(path=str(target), title="Old", size=3),
            ]
        )
    library.move_entry(source, target.parent, overwrite=True)
    with session_scope() as session:
        entries = list(session.query(LibraryFile).filter_by(path=str(target)))
        assert len(entries) == 1
        assert entries[0].title == "Source"


@pytest.mark.parametrize("operation", ["move", "rename"])
def test_configured_library_root_cannot_be_relocated(monkeypatch, tmp_path, operation):
    root = tmp_path / "library"
    root.mkdir()
    (root / "video.mkv").write_bytes(b"keep")
    monkeypatch.setattr(settings, "LIBRARY_DIR", root)
    monkeypatch.setattr(settings, "DOWNLOAD_DIR", tmp_path)
    with pytest.raises(ConfigError):
        if operation == "move":
            library.move_entry(root, tmp_path / "destination")
        else:
            library.rename_entry(root, "new-library")
    assert (root / "video.mkv").exists()


def test_credentials_inside_urls_are_masked_without_breaking_templates(client, auth_headers):
    url = "https://review:review-private-password@example.test/api?api_key=review-private-key&q={keyword}"
    with session_scope() as session:
        row = SiteConfig(
            name="review-url-secret",
            provider="quark",
            kind="panstorage",
            enabled=False,
            url=url,
            options={"search_url": url},
        )
        session.add(row)
        session.flush()
        site_id = row.id
    exposed = next(
        item
        for item in client.get("/api/v1/sites", headers=auth_headers).json()
        if item["id"] == site_id
    )
    assert "review-private" not in json.dumps(exposed)
    assert "{keyword}" in exposed["options"]["search_url"]
    response = client.patch(
        f"/api/v1/sites/{site_id}",
        headers=auth_headers,
        json={"url": exposed["url"], "options": exposed["options"]},
    )
    assert response.status_code == 200
    with session_scope() as session:
        row = session.get(SiteConfig, site_id)
        assert row.url == url and row.options["search_url"] == url


@pytest.mark.parametrize("probe", ["missing", "active", "offline"])
def test_aria2_missing_task_removal_is_idempotent_but_not_assumed(monkeypatch, probe):
    from app.providers.downloader import aria2

    gid = "0000000000000001"
    missing = {"error": {"code": 1, "message": f"GID {gid} is not found"}}

    async def fetch(url, **kwargs):
        method = kwargs["json_body"]["method"]
        if method == "aria2.tellStatus":
            if probe == "offline":
                return None
            if probe == "active":
                return {"result": {"gid": gid, "status": "active"}}
        return missing

    monkeypatch.setattr(aria2, "fetch_json", fetch)
    result = asyncio.run(aria2.Aria2Downloader({}).remove(gid))
    assert result == (probe == "missing")


def test_identical_passwords_get_independent_salts():
    from app.core.security import hash_password, verify_password

    first, second = hash_password("review password"), hash_password("review password")
    assert first != second
    assert verify_password("review password", first)
    assert verify_password("review password", second)


@pytest.mark.parametrize(
    "encoded",
    [
        "pbkdf2_sha256$bad$salt$digest",
        "pbkdf2_sha256$0$salt$digest",
        "pbkdf2_sha256$-1$salt$digest",
        None,
    ],
)
def test_invalid_password_hash_is_rejected_without_crashing(encoded):
    from app.core.security import verify_password

    assert not verify_password("review password", encoded)


def test_http_error_logs_do_not_reveal_url_credentials(monkeypatch, caplog):
    import httpx

    from app.utils import http

    transport = httpx.MockTransport(lambda request: httpx.Response(403, json={"error": "denied"}))
    monkeypatch.setattr(
        http, "async_client", lambda **kwargs: httpx.AsyncClient(transport=transport)
    )
    asyncio.run(http.fetch_json("https://example.test/api?api_key=review-private-key"))
    assert "review-private-key" not in caplog.text


def test_moving_hardlink_alias_removes_only_the_source_alias(tmp_path):
    import os

    source, target = tmp_path / "source.mkv", tmp_path / "target.mkv"
    source.write_bytes(b"keep")
    os.link(source, target)
    organizer.transfer_path(source, target, "move", overwrite=True)
    assert not source.exists()
    assert target.read_bytes() == b"keep"


def test_default_http_client_rejects_untrusted_tls(tmp_path):
    import datetime
    import ipaddress
    import ssl
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    import httpx
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    from app.utils.http import async_client

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    async def run():
        url = f"https://127.0.0.1:{server.server_port}/"
        async with async_client(timeout=3) as client:
            with pytest.raises(httpx.ConnectError, match="CERTIFICATE_VERIFY_FAILED"):
                await client.get(url)
        async with async_client(timeout=3, verify=False) as client:
            assert (await client.get(url)).status_code == 200

    try:
        asyncio.run(run())
    finally:
        server.shutdown()
        server.server_close()
