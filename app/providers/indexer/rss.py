"""通用 RSS 索引器：适配各类 BT 站点的 RSS/种子订阅源。"""

from __future__ import annotations

import feedparser

from app.core.logger import get_logger
from app.providers.base import Resource, SearchProvider
from app.providers.registry import register
from app.schemas.enums import ProviderKind, ResourceKind
from app.utils.http import fetch_text
from app.utils.strings import match_keywords, parse_datetime, parse_size

logger = get_logger(__name__)


@register
class RssIndexer(SearchProvider):
    """RSS 索引器。

    搜索时若站点支持关键词占位符（``{keyword}``）则直接查询，
    否则拉取整个 RSS 再本地过滤，这样任何 RSS 源都能用于追新。
    """

    name = "rss"
    kind = ProviderKind.INDEXER.value
    display_name = "通用 RSS 订阅源"

    async def _load(self, url: str) -> list[Resource]:
        text = await fetch_text(
            url,
            headers={"Cookie": self.config.get("cookie") or ""},
            timeout=self.config.get("timeout"),
        )
        if not text:
            return []
        try:
            parsed = feedparser.parse(text)
        except Exception as exc:
            logger.warning("RSS 解析失败 %s: %s", self.site_name, exc)
            return []

        resources: list[Resource] = []
        for entry in parsed.entries:
            title = str(getattr(entry, "title", "") or "").strip()
            if not title:
                continue

            link = ""
            for enclosure in getattr(entry, "enclosures", []) or []:
                href = enclosure.get("href") or enclosure.get("url")
                if href:
                    link = href
                    break
            link = link or str(getattr(entry, "link", "") or "")
            if not link:
                continue

            size = 0
            for enclosure in getattr(entry, "enclosures", []) or []:
                size = parse_size(enclosure.get("length") or 0)
                if size:
                    break
            if not size:
                size = parse_size(getattr(entry, "size", 0))

            kind = (
                ResourceKind.MAGNET.value
                if link.startswith("magnet:")
                else ResourceKind.TORRENT.value
            )
            published = getattr(entry, "published", None) or getattr(
                entry, "updated", None
            )

            resources.append(
                Resource(
                    title=title,
                    link=link,
                    site=self.site_name,
                    kind=kind,
                    page_url=str(getattr(entry, "link", "") or "") or None,
                    description=str(getattr(entry, "summary", "") or "")[:500] or None,
                    size=size,
                    publish_at=parse_datetime(published),
                    priority=self.priority,
                )
            )
        return resources

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
        if not url:
            return []

        if "{keyword}" in url:
            from urllib.parse import quote

            resources = await self._load(url.replace("{keyword}", quote(keyword)))
        else:
            resources = await self._load(url)
            if keyword:
                resources = [
                    item
                    for item in resources
                    if match_keywords(item.title, keyword.split(), mode="all")
                ]
        return resources

    async def fetch_latest(self, limit: int = 100) -> list[Resource]:
        url = str(self.config.get("url") or "")
        if not url:
            return []
        resources = await self._load(url.replace("{keyword}", ""))
        return resources[:limit]

    async def health_check(self) -> tuple[bool, str]:
        url = str(self.config.get("url") or "")
        if not url:
            return False, "未配置 url"
        resources = await self._load(url.replace("{keyword}", ""))
        if not resources:
            return False, "RSS 无有效条目"
        return True, f"连接正常，获取 {len(resources)} 条"
