"""Backoff 共享退避状态机的契约测试。"""

from __future__ import annotations

from app.utils.backoff import Backoff


def test_初始不静默() -> None:
    assert Backoff(300).limited() is False


def test_mark后进入静默期() -> None:
    backoff = Backoff(300)
    backoff.mark()
    assert backoff.limited() is True


def test_reset恢复() -> None:
    backoff = Backoff(300)
    backoff.mark()
    backoff.reset()
    assert backoff.limited() is False


def test_静默期到期自动恢复() -> None:
    backoff = Backoff(0)
    backoff.mark()
    assert backoff.limited() is False
