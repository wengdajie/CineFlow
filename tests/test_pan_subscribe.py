"""网盘分享追更测试：增量转存、正则过滤/重命名、失效熔断、执行窗口。

用假网盘 Provider，全程离线。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app.core.config import settings
from app.db.models import PanSubscribe
from app.db.session import session_scope
from app.providers.panstorage.base import BasePanStorage, PanFile, SaveResult
from app.services import pan_subscribe as service


def run(coro):
    return asyncio.run(coro)


def _files(*names: str) -> list[PanFile]:
    return [PanFile(name, "/" + name, size=1024) for name in names]


class FakeShare(BasePanStorage):
    """能列举分享内容的假网盘（对标夸克）。"""

    name = "fake_share_pan"
    display_name = "假分享盘"
    #: 真实 Provider 实现了 rename 就会声明这个能力位，假盘也必须如实声明。
    #: 服务层按**能力位**决定要不要改名（基类恒有 rename 方法，
    #: 所以 hasattr 判断永远为真、等于没判断）。
    supports_rename = True

    def __init__(self, config=None, items=None, ok=True):
        super().__init__(config or {})
        self.items = items if items is not None else []
        self.ok = ok
        #: 每次 save_share_files 收到的文件名，用来断言「只转存新增」
        self.batches: list[list[str]] = []
        self.whole_share_calls = 0
        self.renames: list[tuple[str, str]] = []

    async def list_dir(self, path: str = "/") -> list[PanFile]:
        return []

    async def list_share(self, share_url, *, password=None):
        return list(self.items)

    async def save_share(self, share_url, *, password=None, target_dir=None):
        self.whole_share_calls += 1
        return SaveResult(self.ok, "整体转存" if self.ok else "链接失效", file_count=9)

    async def save_share_files(self, share_url, files, *, password=None, target_dir=None):
        self.batches.append([item.name for item in files])
        if not self.ok:
            return SaveResult(False, "链接失效")
        return SaveResult(True, f"转存 {len(files)} 个文件", file_count=len(files))

    async def rename(self, path, new_name):
        self.renames.append((path, new_name))
        return True


class BlindShare(FakeShare):
    """看不清分享内部的假网盘（``list_share`` 返回空），走整体转存退化路径。"""

    name = "fake_blind_pan"

    async def list_share(self, share_url, *, password=None):
        return []


@pytest.fixture
def inject(monkeypatch):
    """把指定的假网盘注入 pan_subscribe。"""

    def _inject(storage):
        monkeypatch.setattr(service, "_storage_for", lambda record: storage)
        return storage

    return _inject


# --------------------------------------------------------------- 纯函数部分
def test_match_files_filters_and_skips_saved():
    """include/exclude 正则 + 已转存清单共同决定「这次要转存谁」。"""
    files = _files("庆余年.第01集.mp4", "庆余年.第02集.mp4", "预告片.mp4", "说明.txt")
    picked = service.match_files(
        files, include=r"\.mp4$", exclude="预告", saved=["庆余年.第01集.mp4"]
    )
    assert [item.name for item in picked] == ["庆余年.第02集.mp4"]


def test_saved_key_uses_full_path_not_bare_name():
    """去重身份必须是**分享内完整路径**，否则不同目录的同名集数会互相顶掉。

    实测场景：递归列举后 ``/第一季/EP01.mp4`` 与 ``/第二季/EP01.mp4``
    都叫 ``EP01.mp4``。按名字去重的话，第一季转存过之后，
    第二季整季都被判定「已转存」——一个不报错、永不更新的订阅。
    """
    s1 = PanFile("EP01.mp4", "/第一季/EP01.mp4", size=1)
    s2 = PanFile("EP01.mp4", "/第二季/EP01.mp4", size=1)
    assert service.saved_key(s1) != service.saved_key(s2)

    # 第一季存过之后，第二季那一集仍然必须被认成「新增」
    picked = service.match_files([s1, s2], saved=[service.saved_key(s1)])
    assert [item.path for item in picked] == ["/第二季/EP01.mp4"]


def test_saved_key_falls_back_to_name_when_path_missing():
    """path 为空时退回文件名，保证不填 path 的 Provider 仍能去重。"""
    assert service.saved_key(PanFile("a.mp4", "")) == "a.mp4"


def test_match_files_honours_legacy_name_only_history():
    """v1.28.0 前的历史记录存的是纯文件名，升级后不能重转一遍。

    只比路径的话，所有老订阅的 saved_files（纯名字）都对不上，
    下轮巡检会把整季重新转存，网盘里堆出一串 ``xxx(1).mp4``。
    """
    item = PanFile("EP01.mp4", "/第一季/EP01.mp4", size=1)
    assert service.match_files([item], saved=["EP01.mp4"]) == []


def test_match_files_regex_matches_filename_not_path():
    """正则匹配文件名，父目录名不该意外参与判断。"""
    item = PanFile("EP01.mp4", "/预告片合集/EP01.mp4", size=1)
    # 排除「预告」应当只看文件名 —— 这一集不该被父目录名连带排除
    assert len(service.match_files([item], exclude="预告")) == 1


def test_match_files_ignores_directories():
    """分享里的子目录不能当文件转存。"""
    items = [*_files("a.mp4"), PanFile("子目录", "/子目录", is_dir=True)]
    assert [item.name for item in service.match_files(items)] == ["a.mp4"]


def test_invalid_regex_is_treated_as_absent():
    """用户填错正则不能搞崩巡检——当没填处理。"""
    files = _files("a.mp4", "b.mkv")
    assert len(service.match_files(files, include="([")) == 2
    assert len(service.match_files(files, exclude="(?P<")) == 2
    assert service.apply_rename("a.mp4", "([", "x") == "a.mp4"


def test_apply_rename_supports_backreference():
    """重命名要支持反向引用，这是「第01集 → S01E01」的关键。"""
    assert service.apply_rename("庆余年.第03集.1080p.mp4", r".*第(\d+)集.*", r"S01E\1.mp4") == "S01E03.mp4"
    assert service.apply_rename("x.mp4", None, None) == "x.mp4"
    # 没匹配上时原样返回，不能变成空文件名
    assert service.apply_rename("x.mp4", r"^不匹配$", "y") == "x.mp4"


# --------------------------------------------------------------- 增量巡检
def test_check_one_saves_only_new_files(client, inject):
    """核心行为：首次转存全部命中文件，之后只转存新增。"""
    storage = inject(
        FakeShare(items=_files("剧.第01集.mp4", "剧.第02集.mp4", "预告片.mp4"))
    )
    record = service.create(
        {
            "name": "增量测试",
            "share_url": "https://pan.quark.cn/s/incremental",
            "exclude_regex": "预告",
        }
    )

    first = run(service.check_one(record["id"], notify=False))
    assert first["success"] is True
    assert first["saved"] == 2
    assert storage.batches[-1] == ["剧.第01集.mp4", "剧.第02集.mp4"]

    second = run(service.check_one(record["id"], notify=False))
    assert second["saved"] == 0
    assert "没有新增" in second["message"]

    storage.items.append(PanFile("剧.第03集.mp4", "/剧.第03集.mp4", size=1))
    third = run(service.check_one(record["id"], notify=False))
    assert third["saved"] == 1
    assert storage.batches[-1] == ["剧.第03集.mp4"]

    row = next(item for item in service.list_all() if item["id"] == record["id"])
    assert row["total_saved"] == 3
    assert row["saved_count"] == 3


def test_rename_applied_after_save(client, inject):
    """转存成功后按规则在网盘侧改名。"""
    storage = inject(FakeShare(items=_files("剧.第05集.1080p.mp4")))
    record = service.create(
        {
            "name": "重命名测试",
            "share_url": "https://pan.quark.cn/s/rename",
            "rename_search": r".*第(\d+)集.*",
            "rename_replace": r"S01E\1.mp4",
        }
    )
    run(service.check_one(record["id"], notify=False))
    assert storage.renames and storage.renames[0][1] == "S01E05.mp4"


def test_blind_storage_falls_back_to_whole_share_once(client, inject):
    """网盘看不清分享内部时整体转存，但**只做一次**（哨兵防重复）。"""
    storage = inject(BlindShare())
    record = service.create(
        {"name": "退化测试", "share_url": "https://pan.baidu.com/s/blind"}
    )
    first = run(service.check_one(record["id"], notify=False))
    assert first["success"] is True
    assert storage.whole_share_calls == 1

    second = run(service.check_one(record["id"], notify=False))
    assert storage.whole_share_calls == 1, "不能每小时重复整体转存一次"
    assert "整体转存已完成过" in second["message"]


def test_missing_storage_reports_reason(client, monkeypatch):
    """没有可用网盘时给出可操作的提示，而不是静默失败。"""
    monkeypatch.setattr(service, "_storage_for", lambda record: None)
    record = service.create({"name": "无盘", "share_url": "https://x/s/none"})
    result = run(service.check_one(record["id"], notify=False))
    assert result["success"] is False
    assert "网盘" in result["message"]


def test_check_one_missing_subscribe(client):
    result = run(service.check_one(99999999, notify=False))
    assert result["success"] is False
    assert "不存在" in result["message"]


# --------------------------------------------------------------- 失效熔断
def test_consecutive_failures_mark_invalid_and_reset_recovers(client, inject, monkeypatch):
    """连续失败到阈值就标记失效停手；用户重置后可恢复。"""
    monkeypatch.setattr(settings, "PAN_SUBSCRIBE_MAX_FAILURES", 3)
    inject(FakeShare(items=_files("x.mp4"), ok=False))
    record = service.create({"name": "熔断测试", "share_url": "https://x/s/dead"})

    for expected in (1, 2, 3):
        run(service.check_one(record["id"], notify=False))
        row = next(item for item in service.list_all() if item["id"] == record["id"])
        assert row["failure_count"] == expected

    row = next(item for item in service.list_all() if item["id"] == record["id"])
    assert row["invalid"] is True
    assert row["status"] == "failed"

    # 失效后直接跳过，不再浪费请求
    skipped = run(service.check_one(record["id"], notify=False))
    assert skipped["skipped"] is True

    service.update(record["id"], {"reset_invalid": True})
    row = next(item for item in service.list_all() if item["id"] == record["id"])
    assert row["invalid"] is False
    assert row["failure_count"] == 0
    assert row["status"] == "active"


# --------------------------------------------------------------- 执行窗口
def test_should_run_respects_weekdays_and_expiry(client):
    """周更剧只在更新日巡检；过期任务不再执行。"""
    record = service.create({"name": "窗口测试", "share_url": "https://x/s/win"})
    monday = datetime(2026, 8, 31, tzinfo=timezone.utc)
    tuesday = datetime(2026, 9, 1, tzinfo=timezone.utc)
    with session_scope() as session:
        row = session.get(PanSubscribe, record["id"])
        row.weekdays = [0]  # 只周一
        session.flush()
        assert service._should_run(row, now=monday)[0] is True
        runnable, reason = service._should_run(row, now=tuesday)
        assert runnable is False and "星期" in reason

        row.weekdays = []
        row.expire_at = datetime(2020, 1, 1)
        runnable, reason = service._should_run(row)
        assert runnable is False and "期限" in reason


def test_paused_subscribe_is_skipped(client):
    record = service.create({"name": "暂停测试", "share_url": "https://x/s/pause"})
    service.update(record["id"], {"status": "paused"})
    result = run(service.check_one(record["id"], notify=False))
    assert result["skipped"] is True


# --------------------------------------------------------------- CRUD
def test_crud_roundtrip(client):
    record = service.create(
        {
            "name": "CRUD",
            "share_url": "https://x/s/crud",
            "include_regex": r"\.mkv$",
            "weekdays": [1, 3],
        }
    )
    assert record["weekdays"] == [1, 3]

    updated = service.update(record["id"], {"name": "CRUD 改名", "include_regex": None})
    assert updated["name"] == "CRUD 改名"
    assert updated["include_regex"] is None

    assert service.update(99999999, {"name": "x"}) is None
    assert service.delete(record["id"]) is True
    assert service.delete(record["id"]) is False


def test_reset_history_forces_full_resave(client, inject):
    """清空历史后应重新转存全部文件（用户换了落地目录时会用到）。"""
    storage = inject(FakeShare(items=_files("a.mp4", "b.mp4")))
    record = service.create({"name": "重置历史", "share_url": "https://x/s/reset"})
    assert run(service.check_one(record["id"], notify=False))["saved"] == 2
    assert run(service.check_one(record["id"], notify=False))["saved"] == 0
    service.update(record["id"], {"reset_history": True})
    assert run(service.check_one(record["id"], notify=False))["saved"] == 2
    assert len(storage.batches) == 2


def test_check_all_aggregates_stats(client, inject):
    """批量巡检要汇总成功/失败/跳过，供定时任务与界面展示。"""
    inject(FakeShare(items=_files("z.mp4")))
    service.create({"name": "批量1", "share_url": "https://x/s/b1"})
    service.create({"name": "批量2", "share_url": "https://x/s/b2"})
    result = run(service.check_all(limit=50, notify=False))
    assert result["checked"] >= 2
    assert "巡检" in result["message"]


# ------------------------------------------------- v1.28.0 新增回归
def test_invalid_weekdays_are_filtered_not_stored(client):
    """超范围的星期几必须被过滤掉，否则订阅**永远不会执行**。

    ``_should_run()`` 的判断是 ``moment.weekday() not in weekdays``；
    存进 ``[99]`` 之后这个条件恒为真，巡检每次都跳过，
    而且不报错、不提示 —— 用户只会看到「怎么一直没更新」。
    """
    record = service.create(
        {
            "name": "非法星期",
            "share_url": "https://pan.quark.cn/s/weekday",
            "weekdays": [0, 3, 99, -1],
        }
    )
    assert record["weekdays"] == [0, 3]


def test_expire_at_is_normalised_to_utc(client):
    """带时区的到期时间要换算成 UTC 再落库，不能按字面值存。

    ``expire_at`` 列不带时区，而 ``_should_run()`` 会把读出来的值
    当 UTC 解释。直接存 ``+08:00`` 的字面时间，期限会凭空多出 8 小时。
    """
    record = service.create(
        {
            "name": "期限归一",
            "share_url": "https://pan.quark.cn/s/expire",
            "expire_at": "2027-01-01T00:00:00+08:00",
        }
    )
    # 2027-01-01 00:00 +08:00 == 2026-12-31 16:00 UTC
    assert record["expire_at"].startswith("2026-12-31T16:00")


def test_expired_subscribe_is_skipped(client, inject):
    """过了期限就不再执行（而且要说清是因为期限）。"""
    inject(FakeShare(items=_files("EP01.mp4")))
    record = service.create(
        {
            "name": "已过期",
            "share_url": "https://pan.quark.cn/s/gone",
            "expire_at": "2020-01-01T00:00:00+00:00",
        }
    )
    outcome = run(service.check_one(record["id"], notify=False))
    assert outcome["skipped"] is True
    assert "期限" in outcome["message"]


def test_saved_files_are_capped(client, inject, monkeypatch):
    """已转存清单不能无限增长（JSON 字段会随每次查询被整份读出）。"""
    monkeypatch.setattr(service, "_MAX_SAVED_FILES", 5)
    storage = inject(FakeShare(items=_files(*[f"EP{i:03d}.mp4" for i in range(20)])))
    record = service.create(
        {"name": "截断", "share_url": "https://pan.quark.cn/s/cap"}
    )
    run(service.check_one(record["id"], notify=False))
    with session_scope() as session:
        row = session.get(PanSubscribe, record["id"])
        assert len(row.saved_files) <= 5, f"没有截断：{len(row.saved_files)}"
    assert storage.batches, "本轮应当确实转存过文件"


def test_invalidation_notifies_user(client, inject, monkeypatch):
    """连续失败到阈值判失效时必须推一条通知。

    以前只改了状态位、写了日志 —— 用户不看日志就永远不知道追更停了。
    """
    monkeypatch.setattr(settings, "PAN_SUBSCRIBE_MAX_FAILURES", 1)
    inject(FakeShare(items=_files("EP01.mp4"), ok=False))

    sent: list[tuple[str, str]] = []

    async def fake_send(title, body, **kwargs):
        sent.append((title, body))

    from app.services import notify as notify_service

    monkeypatch.setattr(notify_service, "send", fake_send)

    record = service.create(
        {"name": "会失效的任务", "share_url": "https://pan.quark.cn/s/dead"}
    )
    run(service.check_one(record["id"], notify=True))

    assert sent, "判定失效却没有推送任何通知"
    assert "失效" in sent[0][0]
    assert "会失效的任务" in sent[0][0]
