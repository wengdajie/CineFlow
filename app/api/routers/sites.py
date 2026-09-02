"""站点与 Provider 管理接口。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import AdminUser, CurrentUser, DbSession
from app.db.base import utcnow
from app.db.models import SiteConfig
from app.providers.registry import list_providers
from app.schemas.enums import ProviderKind
from app.schemas.models import (
    JackettConnect,
    JackettImport,
    JackettTest,
    Message,
    SiteCreate,
    SiteOut,
    SiteUpdate,
)
from app.services import discovery as discovery_service
from app.services import jackett as jackett_service
from app.services import presets as preset_service
from app.services import sites as site_service
from app.services import zhuiju as zhuiju_service

router = APIRouter(prefix="/sites", tags=["站点管理"])


def _to_out(site: SiteConfig) -> SiteOut:
    data = SiteOut.model_validate(site)
    data.has_credentials = bool(site.api_key or site.password or site.cookie)
    return data


@router.get("/providers", summary="可用 Provider 列表")
def providers(user: CurrentUser, kind: ProviderKind | None = None) -> list[dict[str, Any]]:
    return list_providers(kind.value if kind else None)


@router.get("", response_model=list[SiteOut], summary="站点列表")
def list_sites(
    session: DbSession,
    user: CurrentUser,
    kind: ProviderKind | None = None,
    enabled_only: bool = False,
) -> list[SiteOut]:
    records = site_service.list_sites(
        session, kind=kind.value if kind else None, enabled_only=enabled_only
    )
    return [_to_out(item) for item in records]


@router.post("", response_model=SiteOut, summary="新增站点")
def create_site(payload: SiteCreate, session: DbSession, user: AdminUser) -> SiteOut:
    exists = session.execute(
        select(SiteConfig).where(SiteConfig.name == payload.name)
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail="站点名称已存在")

    from app.providers.registry import get_provider_class

    if not get_provider_class(payload.provider):
        raise HTTPException(
            status_code=400, detail=f"未知 provider：{payload.provider}"
        )

    site = SiteConfig(**payload.model_dump(exclude={"kind"}), kind=payload.kind.value)
    session.add(site)
    session.commit()
    session.refresh(site)
    return _to_out(site)


# ---------------- Jackett / Prowlarr 批量接入 ----------------
@router.post("/jackett/indexers", summary="列出 Jackett 上已配置的索引器")
async def jackett_indexers(
    payload: JackettConnect,
    session: DbSession,
    user: AdminUser,
) -> dict[str, Any]:
    """连一次 Jackett，把它上面**已经配好**的站点列出来供勾选导入。

    这是「站点添加更符合日常使用」的关键一步：用户心里的操作是
    「我 Jackett 里配好一堆站了，拿过来」，而不是逐个手工拼
    `indexers/<id>/results/torznab` 地址（20 个站要填 20 次）。
    """
    result = await jackett_service.list_indexers(
        payload.url, payload.api_key, timeout=payload.timeout
    )
    # 标注哪些已经导入过，避免重复添加（同「站点发现 / 社区清单」的做法）
    existing = {
        str((row.options or {}).get("jackett_indexer_id") or "")
        for row in session.execute(select(SiteConfig)).scalars()
    }
    existing.discard("")
    for item in result.get("items") or []:
        item["already_added"] = item["id"] in existing
    return {"success": result.get("ok", False), "data": result}


@router.post("/jackett/import", summary="批量导入 Jackett 索引器为站点")
async def jackett_import(
    payload: JackettImport,
    session: DbSession,
    user: AdminUser,
) -> dict[str, Any]:
    """把勾选的索引器批量落库。

    **逐条落库而不是整批失败**：一次导入十几个站，其中一个撞了重名
    就把整批回滚，用户得自己找出是哪个再重来 —— 所以这里对每条单独处理，
    最后如实汇报「成功 N 个、跳过 M 个（原因）」。
    """
    listing = await jackett_service.list_indexers(
        payload.url, payload.api_key, timeout=payload.timeout
    )
    if not listing.get("ok"):
        raise HTTPException(status_code=400, detail=str(listing.get("message") or "无法读取索引器列表"))

    available = {item["id"]: item for item in listing.get("items") or []}
    wanted = payload.indexer_ids or list(available)
    if payload.aggregate:
        # 聚合端点不在 indexers 列表里（它是 Jackett 的虚拟索引器），单独构造
        available[jackett_service.ALL_INDEXER] = {
            "id": jackett_service.ALL_INDEXER,
            "name": "全部索引器（聚合）",
        }
        wanted = [jackett_service.ALL_INDEXER]

    created: list[SiteOut] = []
    skipped: list[dict[str, str]] = []
    for indexer_id in wanted:
        indexer = available.get(indexer_id)
        if not indexer:
            skipped.append({"id": indexer_id, "reason": "Jackett 上没有这个索引器"})
            continue
        data = jackett_service.build_site_payload(
            payload.url,
            payload.api_key,
            indexer,
            name_prefix=payload.name_prefix,
            enabled=payload.enabled,
            priority=payload.priority,
        )
        dup = session.execute(
            select(SiteConfig).where(SiteConfig.name == data["name"])
        ).scalar_one_or_none()
        if dup is not None:
            # 已存在就**更新地址与 Key**：用户重装 Jackett 或换了 Key 时，
            # 报「已存在」让他先删再加是没必要的折腾
            dup.url = data["url"]
            dup.api_key = data["api_key"]
            dup.options = data["options"]
            session.commit()
            session.refresh(dup)
            skipped.append({"id": indexer_id, "reason": "已存在，已更新地址与 API Key"})
            continue
        site = SiteConfig(**data)
        session.add(site)
        session.commit()
        session.refresh(site)
        created.append(_to_out(site))

    return {
        "success": True,
        "created": [item.model_dump() for item in created],
        "created_count": len(created),
        "skipped": skipped,
        "message": f"已导入 {len(created)} 个站点"
        + (f"，跳过 {len(skipped)} 个" if skipped else ""),
    }


@router.post("/jackett/test", summary="测试单个 Jackett 索引器")
async def jackett_test(
    payload: JackettTest,
    user: AdminUser,
) -> dict[str, Any]:
    """用 Torznab ``t=caps`` 探测，不消耗站点搜索配额。"""
    ok, message = await jackett_service.test_indexer(
        payload.url, payload.api_key, payload.indexer_id, timeout=payload.timeout
    )
    return {"success": ok, "message": message}


@router.patch("/{site_id}", response_model=SiteOut, summary="更新站点")
def update_site(
    site_id: int, payload: SiteUpdate, session: DbSession, user: AdminUser
) -> SiteOut:
    site = session.get(SiteConfig, site_id)
    if not site:
        raise HTTPException(status_code=404, detail="站点不存在")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(site, key, value)
    session.commit()
    session.refresh(site)
    return _to_out(site)


@router.delete("/{site_id}", response_model=Message, summary="删除站点")
def delete_site(site_id: int, session: DbSession, user: AdminUser) -> Message:
    site = session.get(SiteConfig, site_id)
    if not site:
        raise HTTPException(status_code=404, detail="站点不存在")
    session.delete(site)
    session.commit()
    return Message(message="站点已删除")


@router.post("/{site_id}/test", summary="测试站点连通性")
async def test_site(site_id: int, session: DbSession, user: CurrentUser) -> dict[str, Any]:
    site = session.get(SiteConfig, site_id)
    if not site:
        raise HTTPException(status_code=404, detail="站点不存在")

    provider = site_service.get_provider_by_site_id(site_id)
    if not provider:
        raise HTTPException(status_code=400, detail="Provider 初始化失败")

    ok, message = await provider.health_check()
    site.last_status = f"{'正常' if ok else '异常'}: {message}"[:255]
    site.last_check_at = utcnow()
    session.commit()
    return {"success": ok, "message": message}


@router.get("/presets", summary="自定义站点预设模板")
def site_presets(user: CurrentUser, kind: ProviderKind | None = None) -> list[dict[str, Any]]:
    """列出可一键套用的站点配置模板。"""
    return preset_service.list_presets(kind.value if kind else None)


@router.post("/presets/{preset_id}/apply", response_model=SiteOut, summary="套用预设新增站点")
def apply_preset(
    preset_id: str,
    session: DbSession,
    user: AdminUser,
    name: str | None = None,
    url: str | None = None,
    enabled: bool = False,
) -> SiteOut:
    """按预设创建站点（默认不启用，便于先测试连通性）。"""
    preset = preset_service.get_preset(preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail=f"预设不存在：{preset_id}")

    site_name = (name or preset["name"]).strip()
    if session.execute(
        select(SiteConfig).where(SiteConfig.name == site_name)
    ).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="站点名称已存在")

    site = SiteConfig(
        name=site_name,
        kind=preset["kind"],
        provider=preset["provider"],
        url=(url or preset["url"]).strip(),
        enabled=enabled,
        priority=int(preset.get("priority", 50)),
        options=dict(preset.get("options") or {}),
    )
    session.add(site)
    session.commit()
    session.refresh(site)
    return _to_out(site)


@router.get("/discover", summary="从导航站发现资源站点")
async def discover_sites(
    session: DbSession,
    user: CurrentUser,
    url: str | None = None,
    media_only: bool = True,
    limit: int = 200,
) -> dict[str, Any]:
    """抓取导航站，列出其收录的候选资源站点。

    导航站本身不提供磁力/网盘链接，只是资源站入口的集合；
    发现结果需用户选择并配置为自定义站点后才能参与搜索与追新。
    """
    data = await discovery_service.discover(url, media_only=media_only, limit=limit)

    # 标记哪些站点已经添加过，避免重复配置
    existing = [
        (row.url or "").lower()
        for row in session.execute(select(SiteConfig)).scalars()
    ]
    for item in data["sites"]:
        domain = str(item.get("domain") or "")
        item["already_added"] = bool(
            domain and any(domain in configured for configured in existing)
        )

    data["directories_builtin"] = discovery_service.DEFAULT_DIRECTORIES
    return {"success": True, "data": data}
@router.get("/catalog", summary="社区站点清单（awesome-zhuiju-free）")
async def zhuiju_catalog(
    session: DbSession,
    user: CurrentUser,
    refresh: bool = False,
    probe: str | None = None,
) -> dict[str, Any]:
    """列出社区维护的追剧站点候选清单。

    数据来自 [awesome-zhuiju-free](https://github.com/laoma2053/awesome-zhuiju-free)
    （CC-BY-4.0）。⚠️ `probe` 字段是**我们自己真搜一次**的结论，与上游的
    `reachability`（只探首页状态码）不是一回事：实测 20 个候选里上游标
    `reachable` 的有 14 个，而真能搜到可下载链接的只有 4 个（ADR-70）。
    """
    data = await zhuiju_service.refresh() if refresh else zhuiju_service.load()
    entries = list(data.get("entries") or [])
    if probe:
        wanted = {p.strip() for p in probe.split(",") if p.strip()}
        entries = [e for e in entries if str(e.get("probe") or "unknown") in wanted]

    # 标注已添加过的站点，避免重复配置（同「站点发现」的做法）
    existing = [
        (row.url or "").lower() for row in session.execute(select(SiteConfig)).scalars()
    ]
    for item in entries:
        domain = str(item.get("domain") or "")
        item["already_added"] = bool(
            domain and any(domain in configured for configured in existing)
        )

    return {
        "success": True,
        "data": {
            "entries": entries,
            "stats": zhuiju_service.stats(list(data.get("entries") or [])),
            "updated_at": data.get("updated_at") or "",
            "upstream_updated_at": data.get("upstream_updated_at") or "",
            "probed_at": data.get("probed_at") or "",
            "stale": bool(data.get("stale")),
            "error": data.get("error"),
            "upstream": {
                "repo": zhuiju_service.UPSTREAM_REPO,
                "url": zhuiju_service.UPSTREAM_URL,
                "license": zhuiju_service.UPSTREAM_LICENSE,
                "site": zhuiju_service.UPSTREAM_SITE,
            },
        },
    }


@router.post("/catalog/probe", summary="探测社区清单站点是否真能搜到资源")
async def zhuiju_probe(
    user: AdminUser,
    limit: int = 20,
    only_unknown: bool = False,
) -> dict[str, Any]:
    """对候选站点逐个「真搜一次」，判定能否拿到可下载链接。

    串行执行且留间隔（别人的站，且实测个别站有搜索限流），20 个站约需 1~3 分钟。
    """
    result = await zhuiju_service.probe_all(limit=limit, only_unknown=only_unknown)
    return {"success": True, "data": result}


@router.post("/catalog/{entry_id}/apply", response_model=SiteOut, summary="从社区清单添加站点")
def zhuiju_apply(
    entry_id: str,
    session: DbSession,
    user: AdminUser,
    enabled: bool = False,
) -> SiteOut:
    """把一个**通过本地探测**的候选站点落库为可用站点。

    只允许 `searchable` 档：`reachable_only` 的站加进来也搜不到东西，
    等于给用户一个「加了但没用」的坑（沿用 v1.13.0 AI 接站的立场）。
    默认 `enabled=False`，由用户确认后再启用。
    """
    suggestion = zhuiju_service.suggest_site(entry_id)
    if not suggestion:
        raise HTTPException(status_code=404, detail="清单中没有该条目")
    if not suggestion.get("ok"):
        raise HTTPException(
            status_code=400,
            detail=str(suggestion.get("reason") or "该站点未通过本地搜索探测"),
        )

    url = str(suggestion.get("url") or "")
    exists = session.execute(
        select(SiteConfig).where(SiteConfig.url == url)
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=400, detail=f"站点已存在：{exists.name}")

    site = SiteConfig(
        name=str(suggestion.get("name") or entry_id),
        kind=str(suggestion.get("kind") or ProviderKind.INDEXER.value),
        provider=str(suggestion.get("provider") or "html_generic"),
        url=url,
        enabled=bool(enabled),
        priority=40,
        options={
            **(suggestion.get("options") or {}),
            "_from_zhuiju": entry_id,
            "note": f"由社区清单 {zhuiju_service.UPSTREAM_REPO} 添加，已通过本地搜索探测",
        },
        updated_at=utcnow(),
    )
    session.add(site)
    session.commit()
    session.refresh(site)
    return _to_out(site)
