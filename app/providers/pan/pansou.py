"""PanSou 盘搜索（对接 pansou 系 API，聚合夸克/阿里/百度/迅雷等网盘资源）。

兼容两类返回结构：

- ``{"data": {"merged_by_type": {"quark": [...], "aliyun": [...]}}}``
- ``{"data": {"results": [...]}}`` / ``{"data": [...]}`` / ``{"list": [...]}``
"""

from __future__ import annotations

import re
from typing import Any

from app.core.logger import get_logger
from app.providers.base import Resource, SearchProvider
from app.providers.registry import register
from app.schemas.enums import ProviderKind, ResourceKind
from app.utils.http import fetch_json
from app.utils.strings import parse_datetime, parse_size

logger = get_logger(__name__)

# 网盘域名 -> 展示名
PAN_TYPES = {
    "pan.quark.cn": "夸克网盘",
    "www.alipan.com": "阿里云盘",
    "www.aliyundrive.com": "阿里云盘",
    "pan.baidu.com": "百度网盘",
    "pan.xunlei.com": "迅雷网盘",
    "cloud.189.cn": "天翼云盘",
    "115.com": "115网盘",
    "115cdn.com": "115网盘",
    "caiyun.139.com": "移动云盘",
    "drive.uc.cn": "UC网盘",
    "mypikpak.com": "PikPak",
}
_PASSWORD_RE = re.compile(r"(?:密码|提取码|pwd|code)\s*[:：]?\s*([A-Za-z0-9]{4,8})")


def detect_pan_type(url: str) -> str:
    """根据链接判断网盘类型。"""
    lowered = str(url or "").lower()
    for domain, label in PAN_TYPES.items():
        if domain in lowered:
            return label
    return "未知网盘"


@register
class PanSouProvider(SearchProvider):
    """PanSou 网盘搜索。"""

    name = "pansou"
    kind = ProviderKind.PAN.value
    display_name = "PanSou 网盘搜索"

    def _endpoint(self) -> str:
        url = str(self.config.get("url") or "").rstrip("/")
        if not url:
            return ""
        if "/search" in url:
            return url
        return f"{url}/api/search"

    async def search(
        self,
        keyword: str,
        *,
        media_type: str | None = None,
        season: int | None = None,
        episode: int | None = None,
        page: int = 0,
    ) -> list[Resource]:
        endpoint = self._endpoint()
        if not endpoint or not keyword:
            return []

        params: dict[str, Any] = {
            "kw": keyword,
            "keyword": keyword,
            "refresh": "false",
            "res": "merge",
        }
        cloud_types = self.option("cloud_types")
        if cloud_types:
            params["cloud_types"] = (
                ",".join(cloud_types) if isinstance(cloud_types, list) else cloud_types
            )

        headers = {}
        if self.config.get("api_key"):
            headers["Authorization"] = f"Bearer {self.config['api_key']}"

        payload = await fetch_json(
            endpoint,
            params=params,
            headers=headers,
            timeout=self.config.get("timeout"),
        )
        if payload is None:
            # 部分部署只接受 POST
            payload = await fetch_json(
                endpoint,
                method="POST",
                json_body={"kw": keyword, "res": "merge"},
                headers=headers,
                timeout=self.config.get("timeout"),
            )
        if not payload:
            return []
        return self._parse(payload, keyword)

    def _parse(self, payload: Any, keyword: str) -> list[Resource]:
        """把各种 pansou 返回结构拍平成 Resource 列表。"""
        raw_items: list[dict[str, Any]] = []
        data = payload.get("data", payload) if isinstance(payload, dict) else payload

        if isinstance(data, dict):
            merged = data.get("merged_by_type")
            if isinstance(merged, dict):
                for pan_type, items in merged.items():
                    for item in items or []:
                        if isinstance(item, dict):
                            entry = dict(item)
                            entry.setdefault("_pan_type", pan_type)
                            raw_items.append(entry)
            for key in ("results", "list", "items", "data"):
                value = data.get(key)
                if isinstance(value, list):
                    raw_items.extend(item for item in value if isinstance(item, dict))
        elif isinstance(data, list):
            raw_items.extend(item for item in data if isinstance(item, dict))

        resources: list[Resource] = []
        seen: set[str] = set()
        for item in raw_items:
            link = str(
                item.get("url")
                or item.get("link")
                or item.get("share_url")
                or item.get("shareUrl")
                or ""
            ).strip()
            if not link or link in seen:
                continue
            seen.add(link)

            title = str(
                item.get("title")
                or item.get("name")
                or item.get("note")
                or keyword
            ).strip()
            title = re.sub(r"<[^>]+>", "", title)

            content = str(item.get("content") or item.get("desc") or "")
            password = str(
                item.get("password") or item.get("pwd") or item.get("code") or ""
            ).strip()
            if not password:
                found = _PASSWORD_RE.search(f"{title} {content}")
                if found:
                    password = found.group(1)

            pan_label = item.get("_pan_type") or detect_pan_type(link)
            published = (
                item.get("datetime")
                or item.get("time")
                or item.get("gmt_create")
                or item.get("created_at")
            )

            resources.append(
                Resource(
                    title=title,
                    link=link,
                    site=f"{self.site_name}·{pan_label}",
                    kind=ResourceKind.PAN.value,
                    page_url=link,
                    description=content[:500] or None,
                    size=parse_size(item.get("size") or 0),
                    publish_at=parse_datetime(str(published) if published else None),
                    priority=self.priority,
                    password=password or None,
                    extra={
                        "pan_type": pan_label,
                        "source": item.get("channel") or item.get("source"),
                    },
                )
            )
        return resources

    async def health_check(self) -> tuple[bool, str]:
        endpoint = self._endpoint()
        if not endpoint:
            return False, "未配置 url"
        payload = await fetch_json(
            endpoint, params={"kw": "测试", "res": "merge"}, timeout=10
        )
        if payload is None:
            return False, "无法连接 PanSou 服务"
        return True, "连接正常"
