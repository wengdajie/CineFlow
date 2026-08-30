"""插件系统测试：发现、启用、动作、事件、定时任务。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.core.config import settings
from app.db.models import DownloadTask, LibraryFile, NotificationRecord
from app.db.session import session_scope
from app.plugins.manager import PluginManager, plugin_manager
from app.schemas.enums import ResourceKind, TaskStatus
from app.services import notify as notify_service
from app.services import sites as site_service

REPO_PLUGINS = Path(__file__).resolve().parents[1] / "plugins"
BUILTIN_IDS = {"auto_cleanup", "pan_transfer", "daily_digest"}


@pytest.fixture
def plugin_env(tmp_path, monkeypatch, client):
    """把内置示例插件复制到隔离的插件目录。"""
    import shutil

    root = tmp_path / "plugins"
    root.mkdir()
    for plugin_id in BUILTIN_IDS:
        source = REPO_PLUGINS / plugin_id
        if source.exists():
            shutil.copytree(source, root / plugin_id)

    monkeypatch.setattr(settings, "PLUGIN_DIR", root)
    monkeypatch.setattr(site_service, "notifiers", lambda: [])
    monkeypatch.setattr(site_service, "downloaders", lambda: [])
    monkeypatch.setattr(site_service, "default_downloader", lambda prefer=None: None)
    manager = PluginManager()
    yield manager
    notify_service.clear_handlers()


def test_discover_builtin_plugins(plugin_env):
    """三个示例插件都能被发现且清单完整。"""
    manifests = plugin_env.discover()
    assert set(manifests) >= BUILTIN_IDS
    for plugin_id in BUILTIN_IDS:
        manifest = manifests[plugin_id]
        assert manifest["name"]
        assert manifest["version"]
        assert manifest["description"]
        assert manifest["config_schema"]


def test_plugin_json_is_valid(plugin_env):
    """plugin.json 必须是合法 JSON 且 id 与目录名一致。"""
    for plugin_id in BUILTIN_IDS:
        path = Path(settings.PLUGIN_DIR) / plugin_id / "plugin.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["id"] == plugin_id
        assert isinstance(data.get("default_config"), dict)


def test_enable_registers_capabilities(plugin_env):
    """启用插件后应注册事件处理器与定时任务。"""
    ok = asyncio.run(plugin_env.enable("auto_cleanup", {"enabled": True}))
    assert ok

    instance = plugin_env.get("auto_cleanup")
    assert instance is not None
    assert "cleanup" in instance.actions()
    assert instance.scheduled_jobs()[0]["trigger"] == "interval"
    assert "transfer.completed" in instance.event_handlers()

    asyncio.run(plugin_env.disable("auto_cleanup"))
    assert plugin_env.get("auto_cleanup") is None


def test_auto_cleanup_removes_old_records(plugin_env):
    """自动清理动作应删除过期通知与已入库任务。"""
    from datetime import timedelta

    from app.db.base import utcnow

    old = utcnow() - timedelta(days=90)
    with session_scope() as session:
        session.add(
            NotificationRecord(title="旧通知", body="应被清理", created_at=old, updated_at=old)
        )
        session.add(
            DownloadTask(
                title="已入库任务.S01E01.1080p",
                kind=ResourceKind.TORRENT.value,
                link="magnet:?xt=urn:btih:CLEANUP01",
                status=TaskStatus.TRANSFERRED.value,
                created_at=old,
                updated_at=old,
            )
        )

    asyncio.run(plugin_env.enable("auto_cleanup", {"enabled": True}))
    stats = asyncio.run(
        plugin_env.run_action(
            "auto_cleanup",
            "cleanup",
            {},
        )
    )
    assert stats["notifications"] >= 1
    assert stats["transferred"] >= 1

    with session_scope() as session:
        assert (
            session.query(DownloadTask)
            .filter(DownloadTask.link == "magnet:?xt=urn:btih:CLEANUP01")
            .count()
            == 0
        )
    asyncio.run(plugin_env.disable("auto_cleanup"))


def test_auto_cleanup_prunes_missing_library_files(plugin_env, tmp_path):
    """媒体库索引里已删除的文件应被剪除，存在的文件保留。"""
    alive = tmp_path / "alive.mkv"
    alive.write_bytes(b"0" * 1024)
    with session_scope() as session:
        session.add(LibraryFile(path=str(alive), title="存活影片", size=1024))
        session.add(
            LibraryFile(path=str(tmp_path / "gone.mkv"), title="失效影片", size=1024)
        )

    asyncio.run(plugin_env.enable("auto_cleanup", {"enabled": True}))
    result = asyncio.run(
        plugin_env.run_action("auto_cleanup", "prune_missing_library_files", {})
    )
    assert result["removed"] >= 1

    with session_scope() as session:
        assert session.query(LibraryFile).filter(LibraryFile.path == str(alive)).count() == 1
        assert (
            session.query(LibraryFile)
            .filter(LibraryFile.path == str(tmp_path / "gone.mkv"))
            .count()
            == 0
        )
    asyncio.run(plugin_env.disable("auto_cleanup"))


def test_pan_transfer_lists_pending(plugin_env):
    """网盘转存插件应能列出待转存任务，未配 Webhook 时只提醒。"""
    with session_scope() as session:
        session.add(
            DownloadTask(
                title="网盘剧集.S01.全集.4K",
                kind=ResourceKind.PAN.value,
                link="https://pan.quark.cn/s/abcdef123456",
                status=TaskStatus.PENDING.value,
                meta={"password": "1a2b", "pan_type": "quark"},
            )
        )

    asyncio.run(plugin_env.enable("pan_transfer", {"enabled": True, "webhook_url": ""}))
    pending = asyncio.run(plugin_env.run_action("pan_transfer", "list_pending", {}))
    assert any(item["pan_type"] == "quark" for item in pending)
    assert any(item["password"] == "1a2b" for item in pending)

    stats = asyncio.run(plugin_env.run_action("pan_transfer", "process_pending", {}))
    assert stats["pending"] >= 1
    assert stats["saved"] == 0
    assert stats["notified"] == 1
    asyncio.run(plugin_env.disable("pan_transfer"))


def test_pan_transfer_respects_pan_type_filter(plugin_env):
    """allow_pan_types 过滤掉不匹配的网盘。"""
    asyncio.run(
        plugin_env.enable(
            "pan_transfer", {"enabled": True, "allow_pan_types": "aliyun"}
        )
    )
    stats = asyncio.run(plugin_env.run_action("pan_transfer", "process_pending", {}))
    assert stats["pending"] == 0
    asyncio.run(plugin_env.disable("pan_transfer"))


def test_daily_digest_builds_report(plugin_env):
    """日报插件生成的文本应包含三段内容。"""
    asyncio.run(plugin_env.enable("daily_digest", {"enabled": True}))
    instance = plugin_env.get("daily_digest")
    assert instance.scheduled_jobs()[0]["trigger"] == "cron"

    digest = asyncio.run(plugin_env.run_action("daily_digest", "preview", {}))
    assert "媒体库" in digest["body"]
    assert "近 24 小时新入库" in digest["body"]
    assert "追新中的订阅" in digest["body"]
    assert "进行中的下载" in digest["body"]
    asyncio.run(plugin_env.disable("daily_digest"))


def test_run_action_emits_plugin_action_event(plugin_env):
    """执行插件动作必须广播 ``plugin.action``。

    开发指南把它列为可订阅事件，但之前**零触发点** —— 插件想靠它联动
    另一个插件的动作，订阅后永远收不到回调，而且没有任何报错。
    只走事件总线，不发用户通知（点一下按钮就弹条推送太吵）。
    """
    from app.schemas.enums import EventType

    received: list[dict] = []

    async def _handler(payload):
        received.append(payload)

    notify_service.subscribe_event(EventType.PLUGIN_ACTION.value, _handler)
    asyncio.run(plugin_env.enable("auto_cleanup", {"enabled": True}))
    try:
        asyncio.run(
            plugin_env.run_action("auto_cleanup", "prune_missing_library_files", {})
        )
        assert len(received) == 1, "plugin.action 必须真的被广播出来"
        assert received[0]["plugin_id"] == "auto_cleanup"
        assert received[0]["action"] == "prune_missing_library_files"
    finally:
        notify_service.unsubscribe_event(EventType.PLUGIN_ACTION.value, _handler)
        asyncio.run(plugin_env.disable("auto_cleanup"))


def test_failed_action_does_not_emit_event(plugin_env):
    """动作抛错时不该广播成功事件。"""
    from app.schemas.enums import EventType

    received: list[dict] = []

    async def _handler(payload):
        received.append(payload)

    notify_service.subscribe_event(EventType.PLUGIN_ACTION.value, _handler)
    asyncio.run(plugin_env.enable("auto_cleanup", {"enabled": True}))
    try:
        with pytest.raises(ValueError):
            asyncio.run(plugin_env.run_action("auto_cleanup", "not-exists", {}))
        assert received == []
    finally:
        notify_service.unsubscribe_event(EventType.PLUGIN_ACTION.value, _handler)
        asyncio.run(plugin_env.disable("auto_cleanup"))


def test_unknown_action_raises(plugin_env):
    """请求不存在的动作应抛出明确错误。"""
    asyncio.run(plugin_env.enable("auto_cleanup", {"enabled": True}))
    with pytest.raises(ValueError):
        asyncio.run(plugin_env.run_action("auto_cleanup", "not-exists", {}))
    with pytest.raises(ValueError):
        asyncio.run(plugin_env.run_action("not-a-plugin", "cleanup", {}))
    asyncio.run(plugin_env.disable("auto_cleanup"))


def test_plugin_api_lists_builtin(plugin_env, client, auth_headers):
    """插件列表接口能返回示例插件。"""
    plugin_manager.discover()
    response = client.get("/api/v1/plugins", headers=auth_headers)
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert ids >= BUILTIN_IDS
