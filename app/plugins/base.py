"""插件基类。

插件放在 ``plugins/<plugin_id>/`` 目录下，需包含：

- ``plugin.json``：清单（id/name/version/description/author/config_schema）
- ``__init__.py``：导出一个继承 ``PluginBase`` 的类

插件可以：订阅系统事件、注册定时任务、暴露自定义动作、扩展 Provider。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar


class PluginBase:
    """插件基类。"""

    #: 插件唯一 ID（需与目录名一致）
    plugin_id: ClassVar[str] = ""
    plugin_name: ClassVar[str] = ""
    plugin_version: ClassVar[str] = "1.0.0"
    plugin_desc: ClassVar[str] = ""
    plugin_author: ClassVar[str] = ""
    #: 配置项声明，供前端渲染表单
    config_schema: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self._events: dict[str, Callable[..., Any]] = {}

    # ---------------- 生命周期 ----------------
    async def on_load(self) -> None:
        """插件启用时调用。"""

    async def on_unload(self) -> None:
        """插件停用时调用。"""

    async def on_config_change(self, config: dict[str, Any]) -> None:
        """配置更新时调用。"""
        self.config = config

    # ---------------- 能力声明 ----------------
    def event_handlers(self) -> dict[str, Callable[..., Any]]:
        """返回 ``{事件名: 处理函数}``。"""
        return {}

    def scheduled_jobs(self) -> list[dict[str, Any]]:
        """返回定时任务声明。

        每项形如 ``{"id": "sync", "func": self.sync, "trigger": "interval",
        "minutes": 30}``，``trigger`` 支持 ``interval`` 与 ``cron``。
        """
        return []

    def actions(self) -> dict[str, Callable[..., Any]]:
        """返回可由 API 手动触发的动作 ``{动作名: 函数}``。"""
        return {}

    # ---------------- 便捷方法 ----------------
    def get_config(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", True))

    def manifest(self) -> dict[str, Any]:
        """插件清单信息。"""
        return {
            "id": self.plugin_id,
            "name": self.plugin_name or self.plugin_id,
            "version": self.plugin_version,
            "description": self.plugin_desc,
            "author": self.plugin_author,
            "config_schema": self.config_schema,
            "actions": list(self.actions().keys()),
            "events": list(self.event_handlers().keys()),
        }
