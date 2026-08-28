"""Nyaa 风格站点索引器（动漫资源，走 RSS 搜索接口）。"""

from __future__ import annotations

from urllib.parse import quote

from app.providers.base import Resource
from app.providers.indexer.rss import RssIndexer
from app.providers.registry import register
from app.schemas.enums import ProviderKind

DEFAULT_BASE = "https://nyaa.si"


@register
class NyaaIndexer(RssIndexer):
    """Nyaa/Sukebei 等兼容站点。"""

    name = "nyaa"
    kind = ProviderKind.INDEXER.value
    display_name = "Nyaa（动漫）"

    def _base(self) -> str:
        return str(self.config.get("url") or DEFAULT_BASE).rstrip("/")

    async def search(
        self,
        keyword: str,
        *,
        media_type: str | None = None,
        season: int | None = None,
        episode: int | None = None,
        page: int = 0,
    ) -> list[Resource]:
        # 动漫单集通常直接以集号命名，把集号并入关键词可显著提高命中率
        query = keyword
        if episode is not None:
            query = f"{keyword} {episode:02d}"

        category = self.option("category", "1_2")  # 1_2 = Anime English-translated
        url = (
            f"{self._base()}/?page=rss&q={quote(query)}"
            f"&c={category}&f={self.option('filter', 0)}"
        )
        resources = await self._load(url)
        for item in resources:
            item.extra.setdefault("category", category)
        return resources

    async def fetch_latest(self, limit: int = 100) -> list[Resource]:
        url = f"{self._base()}/?page=rss&c={self.option('category', '1_2')}"
        return (await self._load(url))[:limit]

    async def health_check(self) -> tuple[bool, str]:
        resources = await self._load(f"{self._base()}/?page=rss")
        if not resources:
            return False, "无法获取 Nyaa RSS"
        return True, f"连接正常，获取 {len(resources)} 条"
