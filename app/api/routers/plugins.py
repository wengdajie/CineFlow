"""插件管理接口。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.deps import AdminUser, CurrentUser, OperatorUser
from app.plugins.manager import plugin_manager
from app.schemas.models import Message, PluginActionRequest, PluginConfigUpdate

router = APIRouter(prefix="/plugins", tags=["插件"])


@router.get("", summary="插件列表")
def list_plugins(user: CurrentUser) -> dict[str, Any]:
    items = plugin_manager.list_plugins()
    return {"success": True, "total": len(items), "items": items}


@router.post("/{plugin_id}/enable", response_model=Message, summary="启用插件")
async def enable(plugin_id: str, user: AdminUser) -> Message:
    if not await plugin_manager.enable(plugin_id):
        raise HTTPException(status_code=400, detail="插件启用失败，请查看日志")
    return Message(message=f"插件 {plugin_id} 已启用")


@router.post("/{plugin_id}/disable", response_model=Message, summary="停用插件")
async def disable(plugin_id: str, user: AdminUser) -> Message:
    await plugin_manager.disable(plugin_id)
    return Message(message=f"插件 {plugin_id} 已停用")


@router.put("/{plugin_id}/config", response_model=Message, summary="更新插件配置")
async def update_config(
    plugin_id: str, payload: PluginConfigUpdate, user: AdminUser
) -> Message:
    if not await plugin_manager.update_config(plugin_id, payload.config):
        raise HTTPException(status_code=404, detail="插件不存在")
    return Message(message="配置已保存")


@router.post("/{plugin_id}/run", summary="执行插件动作")
async def run_action(
    plugin_id: str, payload: PluginActionRequest, user: OperatorUser
) -> dict[str, Any]:
    try:
        result = await plugin_manager.run_action(
            plugin_id, payload.action, payload.params
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "result": result}
