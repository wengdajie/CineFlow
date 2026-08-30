"""通知按事件分渠道 + 告警去抖测试。

对应 v1.12.0 的 M39。两条向后兼容底线必须守住：

* 渠道**不配任何过滤**时收全部通知（升级上来的用户不能突然收不到）；
* 白名单模式下，**无事件名**的通知（界面手点「测试推送」）一律放行，
  否则用户会以为渠道坏了。
"""

from __future__ import annotations

import pytest

from app.schemas.enums import EventType, NotifyLevel
from app.services import notify


@pytest.fixture(autouse=True)
def _clean_suppression():
    notify.reset_suppression()
    yield
    notify.reset_suppression()


def accepts(options, *, event=None, level=NotifyLevel.INFO.value):
    return notify.channel_accepts(options, event=event, level=level)


# ---------------- 不配即全收（向后兼容） ----------------
@pytest.mark.parametrize("options", [None, {}, {"other_key": 1}])
def test_no_filter_accepts_everything(options):
    """没配过滤的渠道收全部——这是 v1.11.0 的行为，升级不能变。"""
    assert accepts(options, event=EventType.DOWNLOAD_ADDED.value) is True
    assert accepts(options, event=None) is True


@pytest.mark.parametrize("options", ["not-a-dict", 123, [], 3.5])
def test_dirty_options_do_not_crash(options):
    """脏配置（非 dict）不能让通知整个挂掉，按"全收"处理。"""
    assert accepts(options, event="anything") is True


# ---------------- 白名单 ----------------
def test_allow_list_only_passes_listed():
    options = {"events": [EventType.DOWNLOAD_ADDED.value]}
    assert accepts(options, event=EventType.DOWNLOAD_ADDED.value) is True
    assert accepts(options, event=EventType.SITE_UNHEALTHY.value) is False


def test_allow_list_accepts_plain_string():
    """只配一个事件时允许写字符串而不是单元素列表。"""
    options = {"events": EventType.DOWNLOAD_ADDED.value}
    assert accepts(options, event=EventType.DOWNLOAD_ADDED.value) is True
    assert accepts(options, event="other.event") is False


@pytest.mark.parametrize("pattern", ["site.*", "site.", "site"])
def test_allow_list_prefix_wildcard(pattern):
    """前缀通配的三种写法必须**完全等价**。

    这条测试原先只用了 ``"site."``（尾点），于是它一直是绿的，
    而文档教用户写的 ``"site.*"`` 实际上一条都匹配不上 —— 白名单配了
    等于这个渠道彻底静默，且没有任何报错。教训：测试要锁**文档承诺的
    契约**，不能只锁实现恰好支持的那一种写法。
    """
    options = {"events": [pattern]}
    assert accepts(options, event="site.unhealthy") is True
    assert accepts(options, event="site.recovered") is True
    assert accepts(options, event="download.added") is False


@pytest.mark.parametrize("pattern", ["download.*", "download.", "download"])
def test_prefix_wildcard_does_not_leak_across_names(pattern):
    """``download.*`` 不能顺手匹配 ``downloader.xxx``。

    纯字符串 startswith 会把 "downloader.speed_limit" 也算进 "download"，
    通配必须以**点**为边界。
    """
    options = {"events": [pattern]}
    assert accepts(options, event="download.added") is True
    assert accepts(options, event="downloader.speed_limit") is False


def test_exact_event_name_stays_exact():
    """不带通配的精确名不能被当成前缀用。"""
    options = {"events": ["site.unhealthy"]}
    assert accepts(options, event="site.unhealthy") is True
    assert accepts(options, event="site.recovered") is False


def test_allow_list_passes_eventless_notification():
    """白名单模式下无 event 的通知必须放行。

    界面「测试推送」不带事件名；如果被吞掉，用户会以为这个渠道配坏了，
    然后开始怀疑整个通知功能。
    """
    options = {"events": ["site.unhealthy"]}
    assert accepts(options, event=None) is True


def test_empty_allow_list_is_not_a_filter():
    """events=[] 等于没配，别把它当成"什么都不收"。"""
    assert accepts({"events": []}, event="download.added") is True


# ---------------- 黑名单 ----------------
def test_deny_list_blocks_listed():
    options = {"events_exclude": ["download.added"]}
    assert accepts(options, event="download.added") is False
    assert accepts(options, event="site.unhealthy") is True


@pytest.mark.parametrize("pattern", ["site.*", "site.", "site"])
def test_deny_list_prefix_wildcard(pattern):
    """黑名单三种写法同样等价。

    这个方向的失效更阴险：以为屏蔽了，其实照收，用户只会觉得
    「过滤功能没用」而不会怀疑是语法问题。
    """
    options = {"events_exclude": [pattern]}
    assert accepts(options, event="site.unhealthy") is False
    assert accepts(options, event="download.added") is True


def test_deny_list_passes_eventless():
    """黑名单模式下无 event 的通知也放行。"""
    assert accepts({"events_exclude": ["site."]}, event=None) is True


def test_allow_list_wins_over_deny_list():
    """两者同时配置时白名单优先（更明确的意图）。"""
    options = {"events": ["download.added"], "events_exclude": ["download.added"]}
    assert accepts(options, event="download.added") is True


# ---------------- min_level ----------------
def test_min_level_filters_lower_levels():
    options = {"min_level": "warning"}
    assert accepts(options, event="x", level=NotifyLevel.INFO.value) is False
    assert accepts(options, event="x", level=NotifyLevel.SUCCESS.value) is False
    assert accepts(options, event="x", level=NotifyLevel.WARNING.value) is True
    assert accepts(options, event="x", level=NotifyLevel.ERROR.value) is True


def test_min_level_garbage_ignored():
    """写错级别名当没配，不要把所有通知都拦掉。"""
    assert accepts({"min_level": "loud"}, event="x", level=NotifyLevel.INFO.value) is True


def test_min_level_combines_with_allow_list():
    """级别下限与白名单是**与**关系，两个都得过。"""
    options = {"events": ["site."], "min_level": "warning"}
    assert accepts(options, event="site.unhealthy", level=NotifyLevel.WARNING.value) is True
    assert accepts(options, event="site.unhealthy", level=NotifyLevel.INFO.value) is False
    assert accepts(options, event="download.added", level=NotifyLevel.ERROR.value) is False


# ---------------- 去抖 ----------------
def test_suppress_within_window():
    """窗口内同一 key 只放过第一次。"""
    assert notify.should_suppress("site.unhealthy:A", window_seconds=600) is False
    assert notify.should_suppress("site.unhealthy:A", window_seconds=600) is True
    assert notify.should_suppress("site.unhealthy:A", window_seconds=600) is True


def test_suppress_keys_are_independent():
    """不同站点各自计时，一个站坏了不该让另一个站的告警被吞。"""
    assert notify.should_suppress("site.unhealthy:A", window_seconds=600) is False
    assert notify.should_suppress("site.unhealthy:B", window_seconds=600) is False


def test_zero_window_disables_suppression():
    """window<=0 表示不去抖，永远放行。"""
    assert notify.should_suppress("k", window_seconds=0) is False
    assert notify.should_suppress("k", window_seconds=0) is False
    assert notify.should_suppress("k", window_seconds=-1) is False


def test_clear_suppression_allows_next_alert():
    """恢复通知清掉抑制后，「又坏了」必须能再发出来。

    这是去抖最容易出的 bug：不清抑制的话，"坏→好→又坏"的第二次异常
    还落在上次冷却窗口里会被静默吞掉，这个站从此再也不告警。
    """
    key = "site.unhealthy:A"
    assert notify.should_suppress(key, window_seconds=3600) is False
    assert notify.should_suppress(key, window_seconds=3600) is True
    notify.clear_suppression(key)
    assert notify.should_suppress(key, window_seconds=3600) is False


def test_clear_unknown_key_is_noop():
    notify.clear_suppression("never-seen")  # 不该抛异常


def test_reset_suppression_clears_all():
    notify.should_suppress("a", window_seconds=600)
    notify.should_suppress("b", window_seconds=600)
    notify.reset_suppression()
    assert notify.should_suppress("a", window_seconds=600) is False
    assert notify.should_suppress("b", window_seconds=600) is False


# ---------------- send() 与去抖联动 ----------------
def test_send_respects_suppression(monkeypatch):
    """带 suppress_key 的通知在窗口内第二次应直接返回 0，且不去碰渠道。"""
    calls = []

    def _notifiers():
        calls.append(1)
        return []

    monkeypatch.setattr("app.services.sites.notifiers", _notifiers)

    import asyncio

    first = asyncio.run(
        notify.send("t", "b", suppress_key="k1", suppress_seconds=600)
    )
    before = len(calls)
    second = asyncio.run(
        notify.send("t", "b", suppress_key="k1", suppress_seconds=600)
    )
    assert first == 0  # 没有渠道，成功数自然是 0
    assert second == 0
    assert len(calls) == before, "被抑制的通知不该再去枚举渠道"
