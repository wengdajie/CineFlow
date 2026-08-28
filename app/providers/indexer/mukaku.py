"""Mukaku 影视站 Provider（内置预设，开箱即用）。

该站点提供公开 JSON API，一次详情请求即可拿到某部影视的**全部**
BT 种子磁力与网盘分享链接，非常适合追剧：

- ``getVideoList?sb=<关键词>`` 搜索影视条目（返回豆瓣 ID）
- ``getVideoDetail?id=<豆瓣ID>`` 详情，含 ``all_seeds``（磁力）与
  ``movies_online_seed``（夸克/百度/迅雷等网盘分享）
- ``getTList?sc=1|2`` 全站最新种子流，用于定时追新雷达

实现上直接复用 :class:`GenericApiIndexer` 的字段映射能力，
本类只是把已验证的映射固化为默认值，用户无需手工填写。
注意：该站为中文站，搜索需使用**中文片名**（英文原名命中率极低）。
"""

from __future__ import annotations

from typing import Any

from app.providers.indexer.generic_api import GenericApiIndexer
from app.providers.registry import register
from app.schemas.enums import ProviderKind

DEFAULT_SITE = "https://web5.mukaku.com"
DEFAULT_API_BASE = f"{DEFAULT_SITE}/prod/api/v1"

#: 站点公开鉴权参数（前端硬编码，非用户凭据）
DEFAULT_APP_ID = "83768d9ad4"
DEFAULT_IDENTITY = "23734adac0301bccdcb107c4aa21f96c"

#: 已验证的字段映射预设
PRESET: dict[str, Any] = {
    "success_key": "success",
    "success_value": True,
    "message_key": "message",
    "search_path": "getVideoList",
    "query_key": "sb",
    "page_key": "page",
    "limit_key": "limit",
    "limit": 8,
    "list_path": "data.data",
    "item_map": {
        "title": "title",
        "alias": "alias",
        "detail_id": "idcode",
        # 列表项本身没有下载链接，链接一律来自详情接口
        "link": "__absent__",
    },
    "detail_path": "getVideoDetail",
    "detail_query_key": "id",
    "max_detail_items": 3,
    "detail_extract": [
        {
            "list_path": "data.all_seeds",
            "kind": "magnet",
            "label": "BT",
            "map": {
                "title": "zname",
                "link": "zlink",
                "size": "zsize",
                "publish_at": "ezt",
            },
        },
        {
            "list_path": "data.movies_online_seed",
            "kind": "pan",
            "label": "网盘",
            "map": {
                "title": "seed_name",
                "link": "link",
                "password": "code",
                "publish_at": "created_at",
            },
        },
    ],
    "latest_path": "getTList",
    "latest_list_path": "data.list",
    "latest_params": [{"sc": 1}, {"sc": 2}],
    "latest_pages": 1,
    "latest_map": {
        "title": "zname",
        "link": "zlink",
        "size": "zsize",
        "publish_at": "eztime",
        "page_url": "aurl",
    },
}


@register
class MukakuIndexer(GenericApiIndexer):
    """Mukaku 影视站（磁力 + 网盘，内置字段映射）。"""

    name = "mukaku"
    kind = ProviderKind.INDEXER.value
    display_name = "Mukaku 影视站（磁力+网盘）"

    def option(self, key: str, default: Any = None) -> Any:
        """用户配置优先，其次内置预设，最后是调用方默认值。"""
        options = self.config.get("options") or {}
        if key in options:
            return options[key]
        if key in self.config:
            return self.config[key]
        if key == "api_base":
            return self._preset_api_base()
        if key in PRESET:
            return PRESET[key]
        return default

    def _preset_api_base(self) -> str:
        """站点地址可换域名，API 路径固定在其下。"""
        root = str(self.config.get("url") or DEFAULT_SITE).rstrip("/")
        if "/api/" in root:
            return root
        return f"{root}/prod/api/v1"

    def _fixed_params(self) -> dict[str, Any]:
        """注入站点鉴权参数（允许用户在 options 中覆盖）。"""
        params = dict(PRESET.get("fixed_params", {}) or {})
        params.setdefault("app_id", str(self.option("app_id", DEFAULT_APP_ID)))
        params.setdefault("identity", str(self.option("identity", DEFAULT_IDENTITY)))
        params.update(dict(self.config.get("options", {}).get("fixed_params", {}) or {}))
        if self.config.get("api_key"):
            params["access_token"] = str(self.config["api_key"])
        return params

    def _headers(self) -> dict[str, str]:
        headers = super()._headers()
        headers.setdefault("Referer", f"{str(self.config.get('url') or DEFAULT_SITE).rstrip('/')}/")
        return headers

    async def health_check(self) -> tuple[bool, str]:
        """用最新流做连通性探测（不依赖关键词）。"""
        latest = await self.fetch_latest(limit=5)
        if latest:
            return True, f"连接正常，最新流返回 {len(latest)} 条资源"
        return False, "无法获取站点最新资源，请检查网络或站点域名"
