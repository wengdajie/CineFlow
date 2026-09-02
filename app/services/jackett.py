"""Jackett / Prowlarr 索引器接入：连一次，把站点清单**整批拉进来**。

**为什么单独做这个而不是让用户填 Torznab 地址**：`torznab` Provider 早就有了，
但用户要用它必须自己拼出这种地址：

```
http://127.0.0.1:9117/api/v2.0/indexers/<indexer_id>/results/torznab
```

`<indexer_id>` 只能去 Jackett 界面上一个个复制，一个站一条，
20 个站就得手工填 20 次 —— 这与「日常使用」的直觉完全不符：
用户心里的操作是「我 Jackett 里已经配好一堆站了，把它们拿过来」。

本模块做的就是这件事：给一个 Jackett 地址 + API Key，
调 `/api/v2.0/indexers` 列出**已配置好的**索引器，让用户勾选后批量落库，
每条都指向自己的 torznab 端点（复用现成的 `torznab` Provider，零新增解析逻辑）。

同时支持 Jackett 的聚合端点（`indexers/all`）—— 一条站点吃掉全部索引器。
它省事，但**不是默认推荐**：聚合端点里任何一个站慢/挂都会拖慢整体，
而且诊断只能看到「Jackett 聚合」一行，出问题无法定位到具体站点。
拆成一条条反而更符合本项目的站点诊断与熔断设计（ADR-20 / v1.15.0 熔断）。

Prowlarr 也兼容：它提供同构的 `/api/v2.0/indexers` Torznab 兼容层。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from app.core.logger import get_logger
from app.providers.indexer.torznab import TorznabIndexer
from app.schemas.enums import ProviderKind
from app.utils.http import fetch_json, fetch_text, normalize_endpoint

logger = get_logger(__name__)

#: Jackett/Prowlarr 的索引器清单接口（两者同构）
INDEXERS_PATH = "/api/v2.0/indexers"

#: 聚合端点的索引器 ID —— Jackett 用它表示「所有已配置的索引器」
ALL_INDEXER = "all"


def torznab_url(base: str, indexer_id: str) -> str:
    """拼出某个索引器的 Torznab 端点（**不含**结尾的 ``/api``）。

    这段拼接是用户手工接 Jackett 时最容易出错的地方（漏 `/results/`、
    indexer id 用了显示名而不是 id），所以收敛到一处。

    ⚠️ 刻意不带 ``/api``：``TorznabIndexer._endpoint()`` 会自己补上。
    落库的站点地址必须是这个「不带 /api」的形式，否则 Provider 会拼成
    ``/torznab/api/api`` 而 404 —— 这是本次实测踩到的坑，
    用 :func:`caps_url` 区分「给 Provider 存的地址」与「直接请求用的地址」。
    """
    root = normalize_endpoint(base)
    return f"{root}{INDEXERS_PATH}/{indexer_id}/results/torznab"


def caps_url(base: str, indexer_id: str) -> str:
    """直接请求 Torznab 时用的地址（带 ``/api``）。

    真实 Jackett 的 Torznab 端点是 ``.../results/torznab/api``；
    界面上展示的 feed 地址省略了 ``/api``，客户端负责补。
    """
    return f"{torznab_url(base, indexer_id)}/api"


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes"}


def _caps_categories(item: dict[str, Any]) -> list[str]:
    """提取索引器支持的顶层分类名，用于界面上展示「这站有什么」。"""
    names: list[str] = []
    caps = item.get("caps")
    if isinstance(caps, list):
        for cap in caps:
            if isinstance(cap, dict):
                name = str(cap.get("Name") or cap.get("name") or "").strip()
                # 只留顶层大类（Movies / TV / Anime…），子类太碎没有展示价值
                if name and "/" not in name and name not in names:
                    names.append(name)
    return names[:6]


async def list_indexers(
    base_url: str, api_key: str, *, timeout: float | None = None
) -> dict[str, Any]:
    """列出 Jackett/Prowlarr 上**已配置**的索引器。

    返回 ``{"ok", "message", "items", "endpoint"}``。失败时 ``ok=False`` 且
    ``message`` 是能直接给用户看的原因 —— 这里的失败原因必须分得细，
    因为「连不上」「Key 错了」「Key 对但一个站都没配」三种情况的下一步动作
    完全不同，笼统报「获取失败」等于让用户瞎试（ADR-73 的教训）。
    """
    root = normalize_endpoint(base_url)
    if not root:
        return {"ok": False, "message": "请填写 Jackett 地址", "items": [], "endpoint": ""}

    endpoint = f"{root}{INDEXERS_PATH}"
    key = str(api_key or "").strip()
    if not key:
        return {
            "ok": False,
            "message": "请填写 API Key（Jackett 界面右上角 API Key 一栏可复制）",
            "items": [],
            "endpoint": endpoint,
        }

    data = await fetch_json(
        endpoint,
        params={"apikey": key, "configured": "true"},
        timeout=timeout,
    )
    if data is None:
        return {
            "ok": False,
            "message": (
                f"连不上 {root}。请确认 Jackett 正在运行、地址与端口正确；"
                "若 CineFlow 跑在 Docker 里，127.0.0.1 指的是容器自己，"
                "要填宿主机 IP 或用 host 网络"
            ),
            "items": [],
            "endpoint": endpoint,
        }
    if not isinstance(data, list):
        # Jackett 对错误的 apikey 返回的是 JSON 对象而不是数组
        detail = ""
        if isinstance(data, dict):
            detail = str(data.get("error") or data.get("Error") or "")[:120]
        return {
            "ok": False,
            "message": f"返回内容不是索引器列表，通常是 API Key 不正确。{detail}".strip(),
            "items": [],
            "endpoint": endpoint,
        }

    items: list[dict[str, Any]] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        indexer_id = str(raw.get("id") or raw.get("ID") or "").strip()
        if not indexer_id:
            continue
        # configured=true 已在服务端过滤，但 Prowlarr 不认这个参数，
        # 所以这里再按字段兜一次；字段缺失时**默认当作已配置**，
        # 不能把用户真实存在的站点悄悄藏掉
        configured = raw.get("configured")
        if configured is not None and not _as_bool(configured):
            continue
        items.append(
            {
                "id": indexer_id,
                "name": str(raw.get("name") or raw.get("title") or indexer_id).strip(),
                "description": str(raw.get("description") or "")[:200],
                "language": str(raw.get("language") or ""),
                "type": str(raw.get("type") or ""),
                "site_link": str(raw.get("site_link") or raw.get("siteLink") or ""),
                "categories": _caps_categories(raw),
                "torznab_url": torznab_url(root, indexer_id),
            }
        )

    items.sort(key=lambda item: item["name"].lower())
    if not items:
        return {
            "ok": False,
            "message": (
                "连接成功，但 Jackett 上还没有配置任何索引器。"
                "请先在 Jackett 里「Add Indexer」添加并保存站点，再回来导入"
            ),
            "items": [],
            "endpoint": endpoint,
        }
    return {
        "ok": True,
        "message": f"发现 {len(items)} 个已配置的索引器",
        "items": items,
        "endpoint": endpoint,
    }


async def test_indexer(
    base_url: str, api_key: str, indexer_id: str, *, timeout: float | None = None
) -> tuple[bool, str]:
    """对单个索引器做一次 Torznab ``t=caps`` 探测。

    用 caps 而不是真搜一次：caps 不消耗站点的搜索配额，
    也不会因为「这个关键词在该站确实没有」而误判成不可用
    （ADR-75 的反面教训：把「搜不到」当成「站点坏了」）。
    """
    url = caps_url(base_url, indexer_id)
    text = await fetch_text(
        url, params={"apikey": str(api_key or "").strip(), "t": "caps"}, timeout=timeout
    )
    if not text:
        return False, "无法连接（检查 Jackett 是否运行、地址是否可达）"
    if "<error" in text:
        code = ""
        try:
            root = ET.fromstring(text)
            code = str(root.attrib.get("description") or root.attrib.get("code") or "")
        except ET.ParseError:
            pass
        return False, f"Jackett 返回错误：{code or '检查 API Key'}"
    if "<caps" not in text:
        return False, "返回内容不是 Torznab caps（地址可能不对）"
    return True, "连接正常"


def build_site_payload(
    base_url: str,
    api_key: str,
    indexer: dict[str, Any],
    *,
    name_prefix: str = "Jackett",
    enabled: bool = True,
    priority: int = 15,
) -> dict[str, Any]:
    """把一个索引器转成可直接落库的站点配置。

    站点名加前缀是刻意的：Jackett 里的站名（如 "1337x"）和用户可能已经
    手工添加的同名站点会撞 unique 约束，加前缀后既不冲突，
    也让用户在站点列表里一眼看出「这批是从 Jackett 导进来的」。
    """
    indexer_id = str(indexer.get("id") or "").strip()
    label = str(indexer.get("name") or indexer_id).strip()
    prefix = str(name_prefix or "").strip()
    return {
        "name": f"{prefix} · {label}" if prefix else label,
        "kind": ProviderKind.INDEXER.value,
        "provider": TorznabIndexer.name,
        "url": torznab_url(base_url, indexer_id),
        "api_key": str(api_key or "").strip(),
        "enabled": bool(enabled),
        "priority": int(priority),
        "options": {
            "jackett_indexer_id": indexer_id,
            "jackett_base": normalize_endpoint(base_url),
            "site_link": str(indexer.get("site_link") or ""),
            "note": (
                f"由 Jackett 导入（索引器 {indexer_id}）。"
                "地址已自动拼好，改动 Jackett 侧配置后无需在这里重填"
            ),
        },
    }
