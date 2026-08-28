"""插件管理器：发现、加载、启停、执行插件动作。"""

from __future__ import annotations

import importlib.util
import inspect
import json
import sys
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.logger import get_logger
from app.db.base import utcnow
from app.db.models import PluginState
from app.db.session import session_scope
from app.plugins.base import PluginBase
from app.services import notify as notify_service

logger = get_logger(__name__)


class PluginManager:
    """插件管理器（单例）。"""

    def __init__(self) -> None:
        self._instances: dict[str, PluginBase] = {}
        self._classes: dict[str, type[PluginBase]] = {}
        self._manifests: dict[str, dict[str, Any]] = {}

    # ---------------- 发现与加载 ----------------
    def discover(self) -> dict[str, dict[str, Any]]:
        """扫描插件目录，读取清单。"""
        root = Path(settings.PLUGIN_DIR)
        root.mkdir(parents=True, exist_ok=True)
        found: dict[str, dict[str, Any]] = {}

        for entry in sorted(root.iterdir()):
            if not entry.is_dir() or entry.name.startswith((".", "_")):
                continue
            manifest_file = entry / "plugin.json"
            manifest: dict[str, Any] = {"id": entry.name}
            if manifest_file.exists():
                try:
                    manifest.update(json.loads(manifest_file.read_text(encoding="utf-8")))
                except Exception as exc:
                    logger.warning("插件 %s 清单解析失败: %s", entry.name, exc)
            manifest.setdefault("name", entry.name)
            manifest.setdefault("version", "1.0.0")
            manifest["path"] = str(entry)
            found[str(manifest.get("id") or entry.name)] = manifest

        self._manifests = found
        return found

    def _load_class(self, plugin_id: str) -> type[PluginBase] | None:
        """从插件目录导入插件类。"""
        if plugin_id in self._classes:
            return self._classes[plugin_id]

        manifest = self._manifests.get(plugin_id) or {}
        directory = Path(manifest.get("path") or (Path(settings.PLUGIN_DIR) / plugin_id))
        entry_file = directory / "__init__.py"
        if not entry_file.exists():
            entry_file = directory / f"{plugin_id}.py"
        if not entry_file.exists():
            logger.warning("插件 %s 缺少入口文件", plugin_id)
            return None

        module_name = f"cineflow_plugin_{plugin_id}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, entry_file)
            if not spec or not spec.loader:
                return None
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        except Exception as exc:
            logger.error("插件 %s 导入失败: %s", plugin_id, exc)
            return None

        for _, member in inspect.getmembers(module, inspect.isclass):
            if issubclass(member, PluginBase) and member is not PluginBase:
                self._classes[plugin_id] = member
                return member
        logger.warning("插件 %s 未找到 PluginBase 子类", plugin_id)
        return None

    def sync_to_db(self) -> None:
        """把发现的插件登记进数据库。"""
        manifests = self.discover()
        with session_scope() as session:
            for plugin_id, manifest in manifests.items():
                record = (
                    session.query(PluginState)
                    .filter(PluginState.plugin_id == plugin_id)
                    .one_or_none()
                )
                if record:
                    record.name = manifest.get("name") or plugin_id
                    record.version = str(manifest.get("version") or "1.0.0")
                    record.description = manifest.get("description")
                    record.author = manifest.get("author")
                else:
                    session.add(
                        PluginState(
                            plugin_id=plugin_id,
                            name=manifest.get("name") or plugin_id,
                            version=str(manifest.get("version") or "1.0.0"),
                            description=manifest.get("description"),
                            author=manifest.get("author"),
                            enabled=bool(manifest.get("enabled_by_default", False)),
                            config=manifest.get("default_config") or {},
                        )
                    )

    async def load_enabled(self) -> int:
        """加载所有已启用插件。"""
        self.sync_to_db()
        with session_scope() as session:
            records = [
                {"plugin_id": item.plugin_id, "config": item.config or {}}
                for item in session.query(PluginState)
                .filter(PluginState.enabled.is_(True))
                .all()
            ]

        loaded = 0
        for record in records:
            if await self.enable(record["plugin_id"], record["config"], persist=False):
                loaded += 1
        if loaded:
            logger.info("已加载 %d 个插件", loaded)
        return loaded

    # ---------------- 启停 ----------------
    async def enable(
        self,
        plugin_id: str,
        config: dict[str, Any] | None = None,
        *,
        persist: bool = True,
    ) -> bool:
        """启用插件。"""
        if plugin_id in self._instances:
            await self.disable(plugin_id, persist=False)

        if not self._manifests:
            self.discover()
        plugin_cls = self._load_class(plugin_id)
        if not plugin_cls:
            return False

        merged_config = dict(config or {})
        if not merged_config:
            with session_scope() as session:
                record = (
                    session.query(PluginState)
                    .filter(PluginState.plugin_id == plugin_id)
                    .one_or_none()
                )
                merged_config = dict(record.config or {}) if record else {}

        try:
            instance = plugin_cls(merged_config)
            await instance.on_load()
        except Exception as exc:
            logger.error("插件 %s 启用失败: %s", plugin_id, exc)
            with session_scope() as session:
                record = (
                    session.query(PluginState)
                    .filter(PluginState.plugin_id == plugin_id)
                    .one_or_none()
                )
                if record:
                    record.last_error = str(exc)[:500]
            return False

        self._instances[plugin_id] = instance

        # 注册事件与定时任务
        for event, handler in (instance.event_handlers() or {}).items():
            notify_service.subscribe_event(event, handler)
        self._register_jobs(plugin_id, instance)

        if persist:
            with session_scope() as session:
                record = (
                    session.query(PluginState)
                    .filter(PluginState.plugin_id == plugin_id)
                    .one_or_none()
                )
                if record:
                    record.enabled = True
                    record.config = merged_config
                    record.last_error = None
        logger.info("插件已启用：%s", plugin_id)
        return True

    def _register_jobs(self, plugin_id: str, instance: PluginBase) -> None:
        """注册插件定时任务。"""
        from app.services.scheduler import scheduler_service

        for job in instance.scheduled_jobs() or []:
            try:
                scheduler_service.add_plugin_job(plugin_id, job)
            except Exception as exc:
                logger.warning("插件 %s 定时任务注册失败: %s", plugin_id, exc)

    async def disable(self, plugin_id: str, *, persist: bool = True) -> bool:
        """停用插件。"""
        instance = self._instances.pop(plugin_id, None)
        if instance:
            for event, handler in (instance.event_handlers() or {}).items():
                notify_service.unsubscribe_event(event, handler)
            try:
                await instance.on_unload()
            except Exception as exc:
                logger.warning("插件 %s 卸载回调异常: %s", plugin_id, exc)

        from app.services.scheduler import scheduler_service

        scheduler_service.remove_plugin_jobs(plugin_id)
        self._classes.pop(plugin_id, None)

        if persist:
            with session_scope() as session:
                record = (
                    session.query(PluginState)
                    .filter(PluginState.plugin_id == plugin_id)
                    .one_or_none()
                )
                if record:
                    record.enabled = False
        logger.info("插件已停用：%s", plugin_id)
        return True

    async def update_config(self, plugin_id: str, config: dict[str, Any]) -> bool:
        """更新插件配置。"""
        with session_scope() as session:
            record = (
                session.query(PluginState)
                .filter(PluginState.plugin_id == plugin_id)
                .one_or_none()
            )
            if not record:
                return False
            record.config = config
            enabled = record.enabled

        instance = self._instances.get(plugin_id)
        if instance:
            await instance.on_config_change(config)
        elif enabled:
            await self.enable(plugin_id, config, persist=False)
        return True

    # ---------------- 运行 ----------------
    async def run_action(
        self, plugin_id: str, action: str, params: dict[str, Any] | None = None
    ) -> Any:
        """执行插件动作。"""
        instance = self._instances.get(plugin_id)
        if not instance:
            raise ValueError(f"插件未启用：{plugin_id}")
        handler = (instance.actions() or {}).get(action)
        if not handler:
            raise ValueError(f"插件 {plugin_id} 不支持动作 {action}")

        result = handler(**(params or {})) if params else handler()
        if inspect.isawaitable(result):
            result = await result


        with session_scope() as session:
            record = (
                session.query(PluginState)
                .filter(PluginState.plugin_id == plugin_id)
                .one_or_none()
            )
            if record:
                record.last_run_at = utcnow()
        return result

    def get(self, plugin_id: str) -> PluginBase | None:
        return self._instances.get(plugin_id)

    def list_plugins(self) -> list[dict[str, Any]]:
        """列出插件（合并清单与数据库状态）。"""
        manifests = self.discover()
        with session_scope() as session:
            records = {
                item.plugin_id: item
                for item in session.query(PluginState).all()
            }
            items = []
            for plugin_id, manifest in manifests.items():
                record = records.get(plugin_id)
                instance = self._instances.get(plugin_id)
                items.append(
                    {
                        "id": plugin_id,
                        "name": manifest.get("name"),
                        "version": manifest.get("version"),
                        "description": manifest.get("description"),
                        "author": manifest.get("author"),
                        "enabled": bool(record.enabled) if record else False,
                        "config": (record.config if record else {}) or {},
                        "config_schema": manifest.get("config_schema")
                        or (instance.config_schema if instance else []),
                        "actions": list(instance.actions().keys()) if instance else [],
                        "loaded": instance is not None,
                        "last_run_at": record.last_run_at.isoformat()
                        if record and record.last_run_at
                        else None,
                        "last_error": record.last_error if record else None,
                    }
                )
        return items


#: 全局插件管理器
plugin_manager = PluginManager()
