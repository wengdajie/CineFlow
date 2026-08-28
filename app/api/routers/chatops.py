"""ChatOps 接口：入站 Webhook + 配置管理 + 审计查询。

**注意**：``/chatops/webhook/{platform}`` 是给聊天平台回调的，
因此**不能要求 JWT**，安全性由各平台的验签机制保证（见 adapters）。
其余管理类端点仍然要求登录。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.api.deps import CurrentUser, SuperUser
from app.core.logger import get_logger
from app.schemas.models import ChatOpsConfigUpdate, ChatOpsTestRequest
from app.services.chatops import adapters as chat_adapters
from app.services.chatops import commands as command_parser
from app.services.chatops import service as chat_service

logger = get_logger(__name__)

router = APIRouter(prefix="/chatops", tags=["ChatOps 机器人"])


@router.post("/webhook/{platform}", summary="入站 Webhook（供聊天平台回调，无需登录）")
async def webhook(platform: str, request: Request) -> JSONResponse:
    """接收飞书/钉钉/Telegram 的消息回调并执行指令。

    安全性由平台验签保证：飞书校验 verification token、
    钉钉校验 HMAC-SHA256 签名与时间戳、Telegram 校验 secret token。
    验签失败返回 401。
    """
    body = await request.body()
    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        # 部分平台会用 form 编码，尽力解析
        try:
            form = await request.form()
            payload = dict(form)
        except Exception:
            payload = {}
    if not isinstance(payload, dict):
        payload = {}

    result = await chat_service.process_webhook(
        platform,
        headers=dict(request.headers),
        body=body,
        payload=payload,
    )
    return JSONResponse(status_code=int(result.get("status", 200)), content=result.get("response") or {})


@router.get("/platforms", summary="支持的聊天平台")
def platforms(user: CurrentUser) -> dict[str, Any]:
    """列出支持的平台及其回调地址模板。"""
    items = []
    for item in chat_adapters.list_platforms():
        items.append(
            {
                **item,
                "webhook_path": f"/api/v1/chatops/webhook/{item['platform']}",
                "configured": bool(chat_service.platform_config(item["platform"])),
            }
        )
    return {"success": True, "total": len(items), "items": items}


@router.get("/config", summary="ChatOps 配置")
def get_config(user: CurrentUser) -> dict[str, Any]:
    """当前 ChatOps 配置（密钥字段做脱敏）。"""
    config = chat_service.get_config()
    safe_platforms: dict[str, Any] = {}
    for platform, values in (config.get("platforms") or {}).items():
        if not isinstance(values, dict):
            continue
        safe_platforms[platform] = {
            key: ("******" if _is_secret(key) and value else value)
            for key, value in values.items()
        }
    return {"success": True, "data": {**config, "platforms": safe_platforms}}


def _is_secret(key: str) -> bool:
    """判断某个配置项是否敏感（需脱敏展示）。"""
    lowered = str(key).lower()
    return any(
        word in lowered
        for word in ("secret", "token", "password", "key")
    )


@router.put("/config", summary="更新 ChatOps 配置")
def update_config(payload: ChatOpsConfigUpdate, user: SuperUser) -> dict[str, Any]:
    """更新配置。平台密钥提交 ``******`` 表示保持原值不变。"""
    data = payload.model_dump(exclude_unset=True)

    # 合并平台配置：脱敏占位符不覆盖原值
    if "platforms" in data and isinstance(data["platforms"], dict):
        current = chat_service.get_config().get("platforms") or {}
        merged: dict[str, Any] = {
            key: dict(value) for key, value in current.items() if isinstance(value, dict)
        }
        for platform, values in data["platforms"].items():
            if not isinstance(values, dict):
                continue
            target = merged.setdefault(platform, {})
            for key, value in values.items():
                if value == "******":
                    continue
                target[key] = value
        data["platforms"] = merged

    config = chat_service.save_config(data)
    return {"success": True, "data": config}


@router.post("/test", summary="本地模拟一条指令（不经过平台）")
async def test_command(payload: ChatOpsTestRequest, user: SuperUser) -> dict[str, Any]:
    """在界面上直接试指令，便于验证解析与执行是否符合预期。"""
    message = chat_adapters.InboundMessage(
        platform=payload.platform or "console",
        text=payload.text,
        user_id=payload.user_id or "console",
        user_name="控制台",
        chat_id="console",
        message_id=f"console-{payload.text}-{id(payload)}",
    )
    result = await chat_service.handle_message(message)
    return {"success": True, **result}


@router.get("/commands", summary="指令帮助与别名")
def command_help(user: CurrentUser) -> dict[str, Any]:
    """返回指令说明，供前端展示。"""
    grouped: dict[str, list[str]] = {}
    for alias, name in command_parser.ALIASES.items():
        grouped.setdefault(name, []).append(alias)
    return {
        "success": True,
        "help_text": command_parser.HELP_TEXT,
        "commands": [
            {"name": name, "aliases": sorted(aliases)}
            for name, aliases in sorted(grouped.items())
        ],
    }


@router.get("/audit", summary="指令审计日志")
def audit(
    user: CurrentUser,
    limit: int = Query(100, ge=1, le=1000),
    source: str | None = None,
) -> dict[str, Any]:
    """查询谁通过哪个渠道下了什么指令。"""
    items = chat_service.list_audit(limit=limit, source=source)
    return {"success": True, "total": len(items), "items": items}


@router.post("/parse", summary="只解析不执行（调试用）")
def parse_only(payload: ChatOpsTestRequest, user: CurrentUser) -> dict[str, Any]:
    """把一条文本解析成结构化指令，用于排查"为什么没识别"。"""
    command = command_parser.parse(payload.text)
    if not command.ok:
        raise HTTPException(status_code=400, detail="无法识别的指令")
    return {
        "success": True,
        "data": {
            "name": command.name,
            "argument": command.argument,
            "index": command.index,
            "season": command.season,
            "episode": command.episode,
        },
    }
