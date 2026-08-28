"""通用盘搜 Provider：通过 JSON 字段映射适配任意第三方盘搜 API。

无需写代码即可接入新盘搜站点，只要在站点 ``options`` 中描述字段映射：

```json
{
  "method": "GET",
  "query_key": "kw",
  "list_path": "data.list",
  "field_map": {"title": "name", "link": "url", "password": "pwd"}
}
```
"""

from __future__ import annotations

from typing import Any

from app.core.logger import get_logger
from app.providers.base import Resource, SearchProvider
from app.providers.pan.pansou import detect_pan_type
from app.providers.registry import register
from app.schemas.enums import ProviderKind, ResourceKind
from app.utils.http import fetch_json
from app.utils.strings import parse_datetime, parse_size

logger = get_logger(__name__)

DEFAULT_FIELD_MAP = {
    "title": "title",
    "link": "url",
    "password": "password",
    "size": "size",
    "description": "content",
    "publish_at": "datetime",
}


def dig(data: Any, path: str) -> Any:
    """按 ``a.b.c`` 路径取值，支持列表下标。"""
    current = data
    for segment in str(path or "").split("."):
        if not segment:
            continue
        if isinstance(current, dict):
            current = current.get(segment)
        elif isinstance(current, list) and segment.isdigit():
            index = int(segment)
            current = current[index] if index < len(current) else None
        else:
            return None
        if current is None:
            return None
    return current


@register
class GenericPanProvider(SearchProvider):
    """字段映射式盘搜。"""

    name = "pan_generic"
    kind = ProviderKind.PAN.value
    display_name = "通用盘搜（字段映射）"

    async def search(
        self,
        keyword: str,
        *,
        media_type: str | None = None,
        season: int | None = None,
        episode: int | None = None,
        page: int = 0,
    ) -> list[Resource]:
        url = str(self.config.get("url") or "")
        if not url or not keyword:
            return []

        method = str(self.option("method", "GET")).upper()
        query_key = str(self.option("query_key", "kw"))
        extra_params = dict(self.option("params", {}) or {})
        headers = dict(self.option("headers", {}) or {})
        if self.config.get("api_key"):
            headers.setdefault("Authorization", f"Bearer {self.config['api_key']}")
        if self.config.get("cookie"):
            headers.setdefault("Cookie", self.config["cookie"])

        if "{keyword}" in url:
            from urllib.parse import quote

            target, params, body = url.replace("{keyword}", quote(keyword)), extra_params, None
        elif method == "POST":
            target, params, body = url, None, {**extra_params, query_key: keyword}
        else:
            target, params, body = url, {**extra_params, query_key: keyword}, None

        payload = await fetch_json(
            target,
            method=method,
            params=params,
            json_body=body,
            headers=headers,
            timeout=self.config.get("timeout"),
        )
        if not payload:
            return []

        items = dig(payload, str(self.option("list_path", "data")))
        if isinstance(items, dict):
            collected: list[Any] = []
            for value in items.values():
                if isinstance(value, list):
                    collected.extend(value)
            items = collected
        if not isinstance(items, list):
            return []

        field_map = {**DEFAULT_FIELD_MAP, **(self.option("field_map", {}) or {})}
        resources: list[Resource] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            link = str(dig(item, field_map["link"]) or "").strip()
            if not link:
                continue
            title = str(dig(item, field_map["title"]) or keyword).strip()
            pan_label = detect_pan_type(link)
            published = dig(item, field_map["publish_at"])
            resources.append(
                Resource(
                    title=title,
                    link=link,
                    site=f"{self.site_name}·{pan_label}",
                    kind=ResourceKind.PAN.value,
                    page_url=link,
                    description=str(dig(item, field_map["description"]) or "")[:500] or None,
                    size=parse_size(dig(item, field_map["size"])),
                    publish_at=parse_datetime(str(published) if published else None),
                    priority=self.priority,
                    password=str(dig(item, field_map["password"]) or "") or None,
                    extra={"pan_type": pan_label},
                )
            )
        return resources

    async def health_check(self) -> tuple[bool, str]:
        if not self.config.get("url"):
            return False, "未配置 url"
        results = await self.search("测试")
        return True, f"连接正常，返回 {len(results)} 条"
