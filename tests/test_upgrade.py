"""洗版（best version）测试：评分决策与旧文件替换。"""

from __future__ import annotations

import asyncio

import pytest

from app.core.config import settings
from app.services import upgrade


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ 评分决策
@pytest.mark.parametrize(
    "current, candidate, count, expected, keyword",
    [
        (1000, 1200, 0, True, "提升"),
        (1000, 1005, 0, False, "未达阈值"),
        (1000, 1200, 2, False, "上限"),
        (1200, 1000, 0, False, "未达阈值"),
        (1000, 1015, 0, True, "提升"),
    ],
    ids=["明显更优", "提升太小防横跳", "已达次数上限", "候选更差", "刚好等于阈值"],
)
def test_evaluate_boundaries(current, candidate, count, expected, keyword):
    """洗版决策的全部边界：默认阈值 15 分、每文件最多 2 次。"""
    should, reason = upgrade.evaluate(current, candidate, upgrade_count=count)
    assert should is expected
    assert keyword in reason


def test_evaluate_respects_custom_thresholds():
    """阈值与次数上限可按调用覆盖，方便不同订阅用不同策略。"""
    assert upgrade.evaluate(100, 105, delta=3)[0] is True
    assert upgrade.evaluate(100, 105, delta=50)[0] is False
    assert upgrade.evaluate(100, 200, upgrade_count=5, max_times=10)[0] is True
    assert upgrade.evaluate(100, 200, upgrade_count=5, max_times=5)[0] is False


def test_evaluate_with_zero_max_times_disables_upgrade():
    """max_times=0 等于关掉洗版，不能出现「第 0 次也允许」的漏洞。"""
    assert upgrade.evaluate(100, 999, upgrade_count=0, max_times=0)[0] is False


# ------------------------------------------------------------------ 默认配置
def test_default_config_is_conservative():
    """洗版默认必须关闭：它会删除用户已入库的文件，不能默认开启。"""
    assert settings.UPGRADE_ENABLED is False
    assert settings.UPGRADE_SCORE_DELTA >= 10
    assert settings.UPGRADE_MAX_TIMES >= 1


def test_run_is_noop_when_disabled(client):
    """总开关关闭时 run() 直接返回，不去搜任何站点。"""
    result = run(upgrade.run(notify=False))
    assert result["checked"] == 0
    assert "未启用" in result["message"]


def test_check_subscribe_missing_returns_message(client):
    """订阅不存在时给出明确信息而不是抛异常（API 层据此回 404）。"""
    result = run(upgrade.check_subscribe(999999))
    assert result["message"] == "订阅不存在"
    assert result["upgraded"] == 0


# ------------------------------------------------------------ 替换已入库文件
def test_replace_library_file_deletes_old_only_when_new_exists(client, tmp_path):
    """旧文件确实存在且与新文件不同才删除，避免删出空洞。"""
    old = tmp_path / "old.mkv"
    new = tmp_path / "new.mkv"
    old.write_bytes(b"0" * 1024)
    new.write_bytes(b"1" * 2048)

    outcome = upgrade.replace_library_file(str(old), str(new), new_score=88.0)
    assert outcome["deleted"] is True
    assert not old.exists()
    assert new.exists(), "新文件绝不能被删"


def test_replace_library_file_skips_when_same_path(client, tmp_path):
    """新旧路径相同（原地替换）时不能把唯一的文件删掉。"""
    same = tmp_path / "same.mkv"
    same.write_bytes(b"0" * 512)
    outcome = upgrade.replace_library_file(str(same), str(same))
    assert outcome["deleted"] is False
    assert same.exists()


def test_replace_library_file_tolerates_missing_old(client, tmp_path):
    """旧文件已被用户手动删掉时，不能抛异常打断入库流程。"""
    outcome = upgrade.replace_library_file(
        str(tmp_path / "gone.mkv"), str(tmp_path / "new.mkv")
    )
    assert outcome["deleted"] is False
    assert "未删除" in outcome["message"]


def test_replace_merges_existing_new_index(client, tmp_path):
    from sqlalchemy import select

    from app.db.models import LibraryFile
    from app.db.session import session_scope
    old, new = tmp_path / "indexed-old.mkv", tmp_path / "indexed-new.mkv"
    old.write_bytes(b"old")
    new.write_bytes(b"new")
    with session_scope() as session:
        session.add_all([LibraryFile(path=str(old), title="merge", upgrade_count=2),
                         LibraryFile(path=str(new), title="merge")])
    result = upgrade.replace_library_file(str(old), str(new), new_score=99)
    assert result["completed"] and not old.exists() and new.exists()
    with session_scope() as session:
        assert session.scalar(select(LibraryFile).where(LibraryFile.path == str(old))) is None
        current = session.scalar(select(LibraryFile).where(LibraryFile.path == str(new)))
        assert current.upgrade_count == 3 and current.quality_score == 99


def test_missing_new_preserves_old_index(client, tmp_path):
    from app.db.models import LibraryFile
    from app.db.session import session_scope
    old = tmp_path / "keep.mkv"
    old.write_bytes(b"original")
    with session_scope() as session:
        session.add(LibraryFile(path=str(old), title="keep"))
    outcome = upgrade.replace_library_file(str(old), str(tmp_path / "missing.mkv"))
    assert old.exists() and not outcome["completed"]
    with session_scope() as session:
        assert session.query(LibraryFile).filter_by(path=str(old)).count() == 1


def test_failed_delete_keeps_retryable_task(client, tmp_path, monkeypatch):
    from pathlib import Path

    from app.db.models import DownloadTask, LibraryFile
    from app.db.session import session_scope
    from app.services.library import _finish_upgrade_task
    old, new = tmp_path / "retry-old.mkv", tmp_path / "retry-new.mkv"
    old.write_bytes(b"old")
    new.write_bytes(b"new")
    with session_scope() as session:
        session.add_all([LibraryFile(path=str(old), title="retry"), LibraryFile(path=str(new), title="retry")])
        task = DownloadTask(title="retry", link="audit", status="completed", meta={"upgrade_for": str(old)})
        session.add(task)
        session.flush()
        tid = task.id
    snapshot = {"id": tid, "upgrade_for": str(old), "score": 90}
    original = Path.unlink
    def failed(path, *args, **kwargs):
        if path == old:
            raise PermissionError("busy")
        return original(path, *args, **kwargs)
    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "unlink", failed)
        assert not _finish_upgrade_task(snapshot, str(new))["completed"]
    with session_scope() as session:
        task = session.get(DownloadTask, tid)
        assert task.status == "completed" and task.meta["upgrade_target"] == str(new)
        assert session.query(LibraryFile).filter_by(path=str(old)).count() == 1
    assert _finish_upgrade_task(snapshot, str(new))["completed"]
    with session_scope() as session:
        assert session.get(DownloadTask, tid).status == "transferred"
