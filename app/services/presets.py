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
        # v1.13.0：MacCMS 在线影视站。放进预设是因为这类站数量极大且 URL 结构
        # 完全固定（`/index.php/vod/...`），用户只要填个域名就能接一批站，
        # 是「从模板添加」最划算的一项。
        #
        # description 必须把能力边界写清楚：实测 bzzdyy 首页 53 个播放源里
        # 约 92% 是会员正片（腾讯/爱奇艺/优酷/芒果），它们会在下载入口被如实
        # 拒绝（ADR-24：不接 VIP 解析网关）。不写清楚的话，用户会以为「加了
        # 站却什么都下不了」是 bug，然后去反复折腾配置。
        "id": "maccms",
        "name": "在线影视站（MacCMS）",
        "kind": ProviderKind.INDEXER.value,
        "provider": "maccms",
        "url": "https://example.com",
        "priority": 60,
        "description": "适配 MacCMS（苹果 CMS）搭建的在线影视站：只填域名即可，"
                       "搜索/详情/播放路径内置。产出的是播放源指向的【平台原始地址】，"
                       "交给 yt-dlp 下载。⚠️ 实测这类站约 92% 的内容是腾讯/爱奇艺/"
                       "优酷/芒果的会员正片，会被如实拒绝（本项目不接入 VIP 解析网关），"
                       "能下的主要是 B 站与官方免费内容。",
        "requires": ["url"],
        "options": {"max_items": 6},
        "verified": True,
    },
    {
        # v1.14.0：kkso / zhuiju.us 同模板的网盘搜索站。放进预设的理由同 maccms——
        # 只要填域名就能接一批同模板站点，且这两站是 awesome-zhuiju-free 清单里
        # 唯二通过「真搜一次」探测的网盘搜索站（20 个候选里仅 4 个过关）。
        "id": "kkso",
        "name": "KK 系网盘搜索（kkso / zhuiju.us）",
        "kind": ProviderKind.PAN.value,
        "provider": "kkso",
        "url": "https://kkso.net",
        "priority": 25,
        "description": "开箱可用：只填域名即可，搜索路径 /s/{关键词} 内置。"
                       "产出夸克/百度/迅雷分享链接，提取码会从链接的 ?pwd= 自动提取。"
                       "已知同模板站点：kkso.net、www.zhuiju.us。",
        "requires": ["url"],
        "options": {},
        "verified": True,
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
