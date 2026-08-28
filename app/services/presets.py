"""自定义站点预设：把已验证的站点配置固化为「一键添加」模板。

每个预设描述一个可直接使用的站点配置，前端「添加自定义站点」时
可先选模板再微调，降低字段映射的上手门槛。
"""

from __future__ import annotations

from typing import Any

from app.providers.indexer.mukaku import DEFAULT_SITE as MUKAKU_SITE
from app.schemas.enums import ProviderKind

#: 站点预设清单
SITE_PRESETS: list[dict[str, Any]] = [
    {
        "id": "mukaku",
        "name": "Mukaku 影视站",
        "kind": ProviderKind.INDEXER.value,
        "provider": "mukaku",
        "url": MUKAKU_SITE,
        "priority": 20,
        "description": "公开 JSON API，一次请求即可取到某片全部磁力与网盘分享，"
                       "支持最新流追新。搜索请用中文片名。",
        "requires": [],
        "options": {},
        "verified": True,
    },
    {
        "id": "api_generic",
        "name": "自定义 JSON API 站点",
        "kind": ProviderKind.INDEXER.value,
        "provider": "api_generic",
        "url": "https://example.com",
        "priority": 40,
        "description": "适配任意返回 JSON 的资源站：填写接口路径与字段映射即可，"
                       "支持「列表直出链接」与「列表+详情两阶段」两种形态。",
        "requires": ["api_base", "search_path", "query_key", "list_path"],
        "options": {
            "api_base": "https://example.com/api/v1",
            "fixed_params": {},
            "success_key": "code",
            "success_value": 200,
            "search_path": "search",
            "query_key": "keyword",
            "page_key": "page",
            "page_base": 1,
            "limit_key": "limit",
            "limit": 20,
            "list_path": "data.list",
            "item_map": {
                "title": "name",
                "link": "magnet",
                "size": "size",
                "seeders": "seeders",
                "publish_at": "created_at",
            },
            "latest_path": "latest",
            "latest_list_path": "data.list",
        },
        "verified": False,
    },
    {
        "id": "html_generic",
        "name": "自定义网页站点（正则）",
        "kind": ProviderKind.INDEXER.value,
        "provider": "html_generic",
        "url": "https://example.com",
        "priority": 45,
        "description": "适配没有 API 的网页站：用正则描述「行 → 字段」。"
                       "也可只开 magnet_only 直接抓取页面内所有磁力链。",
        "requires": ["search_url"],
        "options": {
            "search_url": "https://example.com/search?q={keyword}&page={page}",
            "latest_url": "https://example.com/latest",
            "row_pattern": "<tr[^>]*>(.*?)</tr>",
            "field_patterns": {
                "title": "title=\"([^\"]+)\"",
                "link": "href=\"(magnet:[^\"]+)\"",
                "size": "<td[^>]*>([\\d.]+\\s*[KMGT]B)</td>",
                "seeders": "<td[^>]*>(\\d+)</td>",
            },
            "magnet_only": False,
            "max_rows": 100,
        },
        "verified": False,
    },
    {
        "id": "pan_generic",
        "name": "自定义盘搜接口",
        "kind": ProviderKind.PAN.value,
        "provider": "pan_generic",
        "url": "https://example.com/api/search",
        "priority": 30,
        "description": "适配任意第三方网盘搜索 API（字段映射式）。",
        "requires": ["url"],
        "options": {
            "method": "GET",
            "query_key": "kw",
            "list_path": "data.list",
            "field_map": {"title": "name", "link": "url", "password": "pwd"},
        },
        "verified": False,
    },
]


def list_presets(kind: str | None = None) -> list[dict[str, Any]]:
    """列出站点预设（可按类别过滤）。"""
    if not kind:
        return SITE_PRESETS
    return [item for item in SITE_PRESETS if item["kind"] == kind]


def get_preset(preset_id: str) -> dict[str, Any] | None:
    """按 ID 取预设。"""
    for item in SITE_PRESETS:
        if item["id"] == str(preset_id):
            return item
    return None
