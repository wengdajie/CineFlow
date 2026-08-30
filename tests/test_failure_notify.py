"""失败通知与事件总线接线测试。

对应 v1.12.1 的查缺补漏轮。这一组测试守的是三条**很容易静默失效**的线：

* 下载被下载器标成失败 → 必须推一条 ``download.failed``；
* 下载成功但整理入库失败 → 必须推一条 ``transfer.failed``；
* ``EventType`` 里声明的事件必须真有触发点，文档里写的事件名必须真实存在。

前两条在 v1.12.0 之前**一条都发不出来**：失败的唯一下场是
``stats["failed"] += 1``，然后这个数字被丢掉。而「只把失败推到手机」
恰恰是通知过滤功能最主要的用例，等于头号场景是空的。
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from app.core.organizer import TransferResult
from app.db.init_db import create_tables
from app.db.models import DownloadTask
from app.db.session import session_scope
from app.schemas.enums import EventType, NotifyLevel, TaskStatus
from app.services import download as download_service
from app.services import library as library_service


@pytest.fixture(autouse=True)
def _tables():
    """保证表已建好，便于单独运行本文件（不依赖 client fixture 的 lifespan）。"""
    create_tables()


class _FakeState:
    """下载器返回的任务状态。"""

    def __init__(self, *, status: str, finished: bool = False, error: str = ""):
        self.status = status
        self.finished = finished
        self.error = error
        self.progress = 1.0 if finished else 0.4
        self.speed = 0
        self.eta = 0
        self.size = 2048
        self.content_path = "/downloads/Fake.Show.S01E01"
        self.save_path = "/downloads"


class _FakeDownloader:
    site_name = "fake-qb"

    def __init__(self, state: _FakeState):
        self._state = state

    async def get(self, external_id: str):
        return self._state


@pytest.fixture
def capture_notify(monkeypatch):
    """拦截 notify.send，返回收集到的通知列表。"""
    sent: list[dict] = []

    async def _send(title, body="", **kwargs):
        sent.append(
            {
                "title": title,
                "body": body,
                "event": kwargs.get("event"),
                "level": kwargs.get("level"),
                "payload": kwargs.get("payload") or {},
            }
        )
        return 1

    monkeypatch.setattr(download_service.notify_service, "send", _send)
    monkeypatch.setattr(library_service.notify_service, "send", _send)
    return sent


def _make_task(**kwargs) -> int:
    """建一个下载任务，返回 id。``link`` 是 NOT NULL 列，必须给。"""
    defaults = {
        "title": "Fake.Show.S01E01.1080p.WEB-DL",
        "link": "magnet:?xt=urn:btih:0123456789abcdef",
        "status": TaskStatus.DOWNLOADING.value,
        "external_id": "hash-0001",
        "downloader": "fake-qb",
        "kind": "torrent",
    }
    defaults.update(kwargs)
    with session_scope() as session:
        task = DownloadTask(**defaults)
        session.add(task)
        session.flush()
        return task.id


def _cleanup(task_id: int) -> None:
    with session_scope() as session:
        task = session.get(DownloadTask, task_id)
        if task:
            session.delete(task)


# ---------------- 下载失败 ----------------
def test_download_failure_sends_notification(monkeypatch, capture_notify):
    """任务翻转成失败时必须推一条 error 级 ``download.failed``。"""
    task_id = _make_task(external_id="hash-fail-1")
    state = _FakeState(status=TaskStatus.FAILED.value, error="种子无做种者")
    monkeypatch.setattr(
        download_service.site_service, "downloaders", lambda: [_FakeDownloader(state)]
    )

    try:
        stats = asyncio.run(download_service.sync_tasks())
        assert stats["failed"] >= 1

        failed = [m for m in capture_notify if m["event"] == EventType.DOWNLOAD_FAILED.value]
        assert len(failed) == 1, "下载失败必须发通知，而不是只把失败数记进 stats"
        message = failed[0]
        assert message["level"] == NotifyLevel.ERROR.value
        # 失败原因要带上，否则用户还得自己去下载器里翻
        assert "种子无做种者" in message["body"]
        assert "Fake.Show.S01E01" in message["body"]
        assert task_id in message["payload"].get("task_ids", [])
    finally:
        _cleanup(task_id)


def test_download_failure_does_not_repeat_each_round(monkeypatch, capture_notify):
    """同一个死种不能每轮巡检推一次。

    ``sync_tasks`` 每 5 分钟跑一轮，若不按**状态翻转**去抖，一个卡住的
    种子一天能推 288 条 —— 用户会直接关掉通知，真正重要的告警一起没了。
    """
    task_id = _make_task(external_id="hash-fail-2")
    state = _FakeState(status=TaskStatus.FAILED.value, error="磁盘空间不足")
    monkeypatch.setattr(
        download_service.site_service, "downloaders", lambda: [_FakeDownloader(state)]
    )

    try:
        asyncio.run(download_service.sync_tasks())
        first = len([m for m in capture_notify if m["event"] == EventType.DOWNLOAD_FAILED.value])
        assert first == 1

        capture_notify.clear()
        asyncio.run(download_service.sync_tasks())
        assert capture_notify == [], "任务已是失败态，第二轮不该再推"
    finally:
        _cleanup(task_id)


def test_multiple_failures_collapse_into_one_message(monkeypatch, capture_notify):
    """一次批量失败合成一条通知，不是 N 条。"""
    ids = [
        _make_task(title=f"Batch.Show.S01E{index:02d}", external_id=f"hash-b{index}")
        for index in range(1, 4)
    ]
    state = _FakeState(status=TaskStatus.FAILED.value, error="tracker 无响应")
    monkeypatch.setattr(
        download_service.site_service, "downloaders", lambda: [_FakeDownloader(state)]
    )

    try:
        asyncio.run(download_service.sync_tasks())
        failed = [m for m in capture_notify if m["event"] == EventType.DOWNLOAD_FAILED.value]
        assert len(failed) == 1
        assert failed[0]["payload"]["failed"] == 3
        assert "3" in failed[0]["title"]
    finally:
        for task_id in ids:
            _cleanup(task_id)


# ---------------- 下载完成事件 ----------------
def test_download_completed_emits_event_without_user_notification(
    monkeypatch, capture_notify
):
    """``download.completed`` 走事件总线，但**不**额外推用户通知。

    开发指南把它列为可订阅事件，之前零触发点 —— 插件订阅后永远收不到。
    同时它不该发用户通知：紧接着的「入库完成」已经会推一条，
    两条挨着发就是刷屏。
    """
    task_id = _make_task(external_id="hash-done-1")
    state = _FakeState(status=TaskStatus.DOWNLOADING.value, finished=True)
    monkeypatch.setattr(
        download_service.site_service, "downloaders", lambda: [_FakeDownloader(state)]
    )

    received: list[dict] = []

    async def _handler(payload):
        received.append(payload)

    async def _noop_transfer():
        return {}

    # sync_tasks 内部是 `from app.services import library as library_service`
    # 的函数内延迟导入，所以打在模块对象上就够了
    monkeypatch.setattr(library_service, "transfer_completed_tasks", _noop_transfer)

    from app.services import notify

    notify.subscribe_event(EventType.DOWNLOAD_COMPLETED.value, _handler)
    try:
        asyncio.run(download_service.sync_tasks())
        assert len(received) == 1, "download.completed 必须真的被广播出来"
        assert received[0]["task_id"] == task_id
        assert not [
            m for m in capture_notify if m["event"] == EventType.DOWNLOAD_COMPLETED.value
        ], "下载完成只走事件总线，不该再推一条用户通知"

        received.clear()
        asyncio.run(download_service.sync_tasks())
        assert received == [], "已完成的任务第二轮不该重复广播"
    finally:
        notify.unsubscribe_event(EventType.DOWNLOAD_COMPLETED.value, _handler)
        _cleanup(task_id)


# ---------------- 入库失败 ----------------
def test_transfer_failure_sends_notification(monkeypatch, capture_notify, tmp_path):
    """下载成功但整理失败必须推 ``transfer.failed``。

    这是最容易被忽略的中间态：「下载任务」页显示已完成，媒体库里却没有。
    硬链接跨盘（Invalid cross-device link）和权限问题都会走到这里。
    """
    source = tmp_path / "Cross.Device.S01E01.1080p"
    source.mkdir(parents=True, exist_ok=True)
    media = source / "Cross.Device.S01E01.1080p.mkv"
    media.write_bytes(b"0" * 1024)

    task_id = _make_task(
        title="Cross.Device.S01E01.1080p",
        status=TaskStatus.COMPLETED.value,
        external_id="hash-tr-1",
        save_path=str(source),
    )

    def _fail_transfer(src, title=None, season=None):
        return [
            TransferResult(
                success=False,
                source=Path(media),
                target=None,
                message="Invalid cross-device link",
            )
        ]

    monkeypatch.setattr(library_service, "transfer_directory", _fail_transfer)

    try:
        stats = asyncio.run(library_service.transfer_completed_tasks())
        assert stats["failed"] >= 1

        failed = [m for m in capture_notify if m["event"] == EventType.TRANSFER_FAILED.value]
        assert len(failed) == 1, "入库失败必须发通知"
        assert failed[0]["level"] == NotifyLevel.ERROR.value
        # 原因必须带上：跨盘和权限是两种完全不同的处理方式
        assert "cross-device" in failed[0]["body"]
    finally:
        _cleanup(task_id)


# ---------------- 元测试：防止再次编造事件名 ----------------
def test_every_event_type_has_a_trigger_point():
    """``EventType`` 里的每个成员都必须至少有一处真实触发。

    声明了却永不触发 = 用户在渠道白名单里配上它，然后永远收不到任何
    通知，而且没有任何报错可查。历史上 ``transfer.failed``、
    ``download.completed``、``plugin.action`` 都是这种状态。
    """
    root = Path(__file__).resolve().parent.parent
    sources = list((root / "app").rglob("*.py")) + list((root / "plugins").rglob("*.py"))
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sources
        if path.name != "enums.py"
    )

    orphans = [
        member.name
        for member in EventType
        if not re.search(rf"EventType\.{member.name}\b", text)
    ]
    assert not orphans, f"这些事件声明了却没有任何触发点：{orphans}"


def test_documented_event_names_all_exist():
    """文档里出现的事件名必须都在 ``EventType`` 里。

    v1.12.0 的文档凭空写了 ``download.failed``（当时不存在）和
    ``site.down``（真名是 ``site.unhealthy``）。用户照抄配好之后该渠道
    彻底静默 —— 这类错误不会报错，只会让人以为功能坏了。
    """
    root = Path(__file__).resolve().parent.parent
    valid = {member.value for member in EventType}
    # 文档里以反引号包裹、形如 a.b 的小写点号标识符
    pattern = re.compile(r"`([a-z_]+\.[a-z_]+)`")
    # 这些是 JSON 字段路径 / 文件名 / 配置键，不是事件名
    allow = {
        "data.list",
        "data.data",
        "data.all_seeds",
        "docker.io",
        "example.com",
        "plugin.json",  # 插件清单文件名，不是事件
    }

    # ``"events": ["a.b", ...]`` 这种 JSON 片段是用户**直接复制粘贴**的地方，
    # 错在这里代价最大，必须单独扫（反引号那条正则覆盖不到双引号）
    json_pattern = re.compile(r'"events(?:_exclude)?"\s*:\s*\[([^\]]*)\]')
    quoted = re.compile(r'"([a-z_]+\.[a-z_*]+)"')

    def _suspicious(name: str) -> bool:
        """与已知事件同族（download.* / site.* 等）却不在枚举里 = 写错了。"""
        if name in valid or name in allow:
            return False
        bare = name[:-2] if name.endswith(".*") else name
        if bare in {item.split(".")[0] for item in valid}:
            return False  # "site.*" 这类通配写法是合法的
        prefix = bare.split(".")[0]
        return any(item.startswith(prefix + ".") for item in valid)

    # 变更日志与决策记录会**刻意引用写错过的名字**来说明当时错在哪
    # （「`site.down` 纯属笔误，真名是 `site.unhealthy`」）。这类行要放过，
    # 否则「记录踩过的坑」和「不许出现错名字」两个目标直接冲突。
    # 判据收得很窄：必须带明确的否定性措辞，纯配置示例不会命中。
    disclaimers = ("笔误", "真名", "不存在", "写错", "编造", "抓到", "错的")

    bad: list[str] = []
    for doc in sorted((root / "docs").rglob("*.md")):
        body = doc.read_text(encoding="utf-8")
        for block in json_pattern.findall(body):
            for name in quoted.findall(block):
                if _suspicious(name):
                    bad.append(f"{doc.name}: {name}（events 配置示例）")
        for line in body.splitlines():
            # 只检查明显在讲事件的行，避免把满篇的点号标识符都拖进来
            if "事件" not in line and "event" not in line.lower():
                continue
            if any(word in line for word in disclaimers):
                continue
            for name in pattern.findall(line) + quoted.findall(line):
                if _suspicious(name):
                    bad.append(f"{doc.name}: {name}")

    assert not bad, "文档写了不存在的事件名：" + "; ".join(sorted(set(bad)))
