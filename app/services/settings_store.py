"""运行期设置仓库：把可变配置持久化到 ``settings`` 表。

静态配置（``.env`` / ``config.yaml``）负责部署期的默认值，本模块负责
**运行期由用户在界面上改动**的配置（例如定时任务的间隔与 cron），
让改动重启后依然生效。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.core.logger import get_logger
from app.db.models import SystemSetting
from app.db.session import session_scope

logger = get_logger(__name__)

#: 定时任务覆盖配置的存储键
KEY_SCHEDULES = "schedules"


def get_setting(key: str, default: Any = None) -> Any:
    """读取一条运行期设置。"""
    try:
        with session_scope() as session:
            record = session.execute(
                select(SystemSetting).where(SystemSetting.key == key)
            ).scalar_one_or_none()
            if record is None or record.value is None:
                return default
            return record.value
    except Exception as exc:  # pragma: no cover - 读取失败时退回默认值
        logger.warning("读取设置 %s 失败: %s", key, exc)
        return default


def set_setting(key: str, value: Any) -> None:
    """写入一条运行期设置（存在则覆盖）。"""
    with session_scope() as session:
        record = session.execute(
            select(SystemSetting).where(SystemSetting.key == key)
        ).scalar_one_or_none()
        if record is None:
            session.add(SystemSetting(key=key, value=value))
        else:
            record.value = value


def delete_setting(key: str) -> bool:
    """删除一条运行期设置。"""
    with session_scope() as session:
        record = session.execute(
            select(SystemSetting).where(SystemSetting.key == key)
        ).scalar_one_or_none()
        if record is None:
            return False
        session.delete(record)
        return True
