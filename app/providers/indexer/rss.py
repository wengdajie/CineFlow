"""通用 RSS 索引器：适配各类 BT 站点的 RSS/种子订阅源。

v1.18.0 起字段提取交给 :mod:`app.core.rss_dialects`（各站点方言差异见该模块），
本类只负责"取 URL → 请求 → 转成 :class:`Resource`"。

**聚合 RSS**（``aggregate``）：一条 RSS 里混着多部作品（Mikan 的「我的番组」、
dmhy 的分类流都是这种）。它与"单番 RSS"的区别不在解析，而在**用法**：

* 单番 RSS：整条流都是同一部作品，可以直接全量下载；
* 聚合 RSS：必须先识别每条属于哪部作品，再交给订阅匹配，
  否则会把整站新番都下回来。

所以这里只把 ``aggregate`` 如实标进 ``extra``，真正的分流在
:mod:`app.services.rss_feeds` 做（那里能看到订阅表）。
"""

from __future__ import annotations

from urllib.parse import quote

from app.core.logger import get_logger
from app.core.rss_dialects import RssEntry, parse_feed
from app.providers.base import Resource, SearchProvider
from app.providers.registry import register
from app.schemas.enums import ProviderKind, ResourceKind
from app.utils.http import fetch_text
from app.utils.strings import match_keywords

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

    def _to_resource(self, entry: RssEntry) -> Resource:
        """把方言层的条目转成统一资源。"""
        return Resource(
            title=entry.title,
            link=entry.link,
            site=self.site_name,
            kind=(
                ResourceKind.MAGNET.value
                if entry.is_magnet
                else ResourceKind.TORRENT.value
            ),
            page_url=entry.homepage,
            description=entry.description,
            size=entry.size,
            seeders=entry.seeders,
            leechers=entry.leechers,
            grabs=entry.grabs,
            publish_at=entry.publish_at,
            priority=self.priority,
            extra=dict(entry.extra),
        )

    async def _entries(self, url: str) -> list[RssEntry]:
        """请求并解析成方言层条目（失败返回空列表）。"""
        text = await fetch_text(
            url,
            headers={"Cookie": self.config.get("cookie") or ""},
            timeout=self.config.get("timeout"),
        )
        if not text:
            return []
        feed_title, dialect, entries = parse_feed(text, url=url)
        if entries:
            logger.debug(
                "RSS %s：方言 %s，%d 条（%s）",
                self.site_name, dialect, len(entries), feed_title,
            )
        return entries

    async def _load(self, url: str) -> list[Resource]:
        return [self._to_resource(entry) for entry in await self._entries(url)]

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
        entries = await self._entries(url.replace("{keyword}", ""))
        if not entries:
            return False, "RSS 无有效条目"
        return True, f"连接正常，获取 {len(entries)} 条"
