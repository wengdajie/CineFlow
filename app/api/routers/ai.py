"""内置 AI 站点分析接口。

流程刻意分成三步，而不是"一键分析并添加"：

    analyze（AI 出建议） → verify（本地真跑一次搜索） → apply（用户确认后落库）

**为什么不合并**：模型会编造字段。直接自动建站等于把一堆搜不到东西的
配置塞进用户的搜索链路，之后每次搜索都白等它一次超时。让用户看到
「置信度 + 依据 + 试搜命中几条」再决定，才是可信的自动化。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import AdminUser, CurrentUser, DbSession
from app.db.models import SiteConfig
from app.schemas.models import SiteOut
from app.services import ai_site

router = APIRouter(prefix="/ai", tags=["内置 AI"])


class AnalyzeRequest(BaseModel):
    url: str = Field(min_length=4, description="要分析的站点首页地址")
    keyword: str = Field("流浪地球", min_length=1, max_length=50, description="试探搜索用的关键词")


class SuggestionModel(BaseModel):
    """AI 给出的接入建议（analyze 的返回可直接回传）。"""

    url: str = Field(min_length=4)
    provider: str = Field(min_length=1)
    kind: str = "indexer"
    options: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    reason: str = ""
    notes: str = ""


class VerifyRequest(BaseModel):
    suggestion: SuggestionModel
    keyword: str = Field("流浪地球", min_length=1, max_length=50)


class ApplyRequest(BaseModel):
    suggestion: SuggestionModel
    name: str = Field(min_length=1, max_length=128, description="站点显示名")
    #: 默认不启用：先让用户自己测一次连通性再放进搜索链路
    enabled: bool = False
    priority: int = Field(50, ge=1, le=999)


@router.get("/config", summary="内置 AI 配置状态与可选接入方案")
def ai_config(user: CurrentUser) -> dict[str, Any]:
    """返回当前 AI 是否可用（密钥只回显长度，不回显内容）。"""
    return {"success": True, "data": ai_site.describe()}


@router.post("/analyze", summary="AI 分析站点该用哪种接入方式")
async def analyze(payload: AnalyzeRequest, user: AdminUser) -> dict[str, Any]:
    """抓站点页面交给 AI，返回接入建议。

    仅管理员可用：它会消耗用户自己的 API 额度，且要把页面正文外发。
    """
    try:
        data = await ai_site.analyze_site(payload.url, keyword=payload.keyword)
    except ValueError as exc:
        # 未配置 / 站点打不开 / 模型返回不可解析，都是用户能处理的问题
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "data": data}


@router.post("/verify", summary="按 AI 建议真跑一次搜索做验证")
async def verify(payload: VerifyRequest, user: AdminUser) -> dict[str, Any]:
    """「模型说能用」和「真能搜到」是两件事，这里做后者。"""
    result = await ai_site.verify(
        payload.suggestion.model_dump(), keyword=payload.keyword
    )
    return {"success": True, "data": result}


@router.post("/apply", response_model=SiteOut, summary="确认后按建议添加站点")
def apply(payload: ApplyRequest, session: DbSession, user: AdminUser) -> SiteOut:
    """把建议落库成一个真实站点。沿用站点创建的既有校验。"""
    from app.providers.registry import get_provider_class

    suggestion = payload.suggestion
    if suggestion.provider not in ai_site.PROVIDER_CHOICES:
        raise HTTPException(
            status_code=400, detail=f"不支持的接入方式：{suggestion.provider}"
        )
    if not get_provider_class(suggestion.provider):
        raise HTTPException(status_code=400, detail=f"未知 provider：{suggestion.provider}")

    name = payload.name.strip()
    if session.execute(
        select(SiteConfig).where(SiteConfig.name == name)
    ).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="站点名称已存在")

    options = dict(suggestion.options or {})
    # 留一份来源痕迹：日后排查"这个奇怪的正则哪来的"能立刻定位
    options["_ai_generated"] = True
    options["_ai_reason"] = suggestion.reason[:300]

    site = SiteConfig(
        name=name,
        kind="pan" if suggestion.kind == "pan" else "indexer",
        provider=suggestion.provider,
        url=suggestion.url.strip(),
        enabled=payload.enabled,
        priority=payload.priority,
        options=options,
    )
    session.add(site)
    session.commit()
    session.refresh(site)
    data = SiteOut.model_validate(site)
    data.has_credentials = bool(site.api_key or site.password or site.cookie)
    return data
