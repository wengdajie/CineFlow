"""Torznab 索引器（兼容 Jackett / Prowlarr，覆盖绝大多数 BT/PT 站点）。"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from app.core.logger import get_logger
from app.providers.base import Resource, SearchProvider
from app.providers.registry import register
from app.schemas.enums import MediaType, ProviderKind, ResourceKind
from app.utils.http import FetchError, fetch_text, fetch_text_result
from app.utils.strings import parse_datetime, parse_size

logger = get_logger(__name__)

_NS = {"torznab": "http://torznab.com/schemas/2015/feed"}
# Torznab 标准分类号
CATEGORY_MAP = {
    MediaType.MOVIE.value: "2000",
    MediaType.TV.value: "5000",
    MediaType.ANIME.value: "5070",
}


@register
class TorznabIndexer(SearchProvider):
    """Torznab 协议索引器。"""

    name = "torznab"
    kind = ProviderKind.INDEXER.value
    display_name = "Torznab / Jackett / Prowlarr"

    def _endpoint(self) -> str:
        url = str(self.config.get("url") or "").rstrip("/")
        if not url:
            return ""
        # 允许直接填写 /api 结尾的完整地址
        if url.endswith("/api"):
            return url
        return f"{url}/api"

    def _params(self, **extra: Any) -> dict[str, Any]:
        params: dict[str, Any] = {"apikey": self.config.get("api_key") or ""}
        params.update({k: v for k, v in extra.items() if v not in (None, "")})
        return params

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
        if not endpoint:
            logger.warning("Torznab 站点 %s 缺少 url", self.site_name)
            return []

        # tvsearch/movie 专用接口可携带季集，命中率更高
        search_type = "search"
        params: dict[str, Any] = {"q": keyword}
        if media_type in (MediaType.TV.value, MediaType.ANIME.value):
            search_type = "tvsearch"
            if season is not None:
                params["season"] = season
            if episode is not None:
                params["ep"] = episode
        elif media_type == MediaType.MOVIE.value:
            search_type = "movie"

        category = self.option("category") or CATEGORY_MAP.get(media_type or "")
        if category:
            params["cat"] = category
        if page:
            params["offset"] = page * int(self.option("page_size", 100))

        # 刻意用 fetch_text_result（会抛）而不是 fetch_text（返回 None）：
        # Jackett 挂掉时端口返回 502，返回 None 会让上层把"服务已死"
        # 显示成「连通正常，但没有匹配结果」，用户只会去反复换关键词。
        # 详见 app.utils.http.FetchError 的文档与 ADR-82。
        text = await fetch_text_result(
            endpoint,
            params=self._params(t=search_type, **params),
            headers={"Cookie": self.config.get("cookie") or ""},
            timeout=self.config.get("timeout"),
        )
        if not text:
            return []
        return self._parse(text)

    async def fetch_latest(self, limit: int = 100) -> list[Resource]:
        """Torznab 空查询即返回站点最新资源。"""
        endpoint = self._endpoint()
        if not endpoint:
            return []
        category = self.option("category")
        text = await fetch_text(
            endpoint,
            params=self._params(t="search", cat=category, limit=limit),
            headers={"Cookie": self.config.get("cookie") or ""},
            timeout=self.config.get("timeout"),
        )
        if not text:
            return []
        return self._parse(text)[:limit]

    def _parse(self, xml_text: str) -> list[Resource]:
        """解析 Torznab XML。"""
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            logger.warning("站点 %s 返回非法 XML: %s", self.site_name, exc)
            return []

        resources: list[Resource] = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            if not title:
                continue

            attrs: dict[str, str] = {}
            for attr in item.findall("torznab:attr", _NS) or []:
                key = attr.get("name")
                if key:
                    attrs[key] = attr.get("value") or ""

            link = ""
            enclosure = item.find("enclosure")
            if enclosure is not None:
                link = enclosure.get("url") or ""
            link = link or attrs.get("magneturl") or (item.findtext("link") or "")
            if not link:
                continue

            size = parse_size(item.findtext("size") or attrs.get("size") or 0)
            if enclosure is not None and not size:
                size = parse_size(enclosure.get("length") or 0)

            kind = (
                ResourceKind.MAGNET.value
                if link.startswith("magnet:")
                else ResourceKind.TORRENT.value
            )

            resources.append(
                Resource(
                    title=title,
                    link=link,
                    site=self.site_name,
                    kind=kind,
                    page_url=item.findtext("comments") or item.findtext("guid"),
                    description=item.findtext("description"),
                    size=size,
                    seeders=int(attrs.get("seeders") or 0),
                    leechers=int(attrs.get("peers") or attrs.get("leechers") or 0),
                    grabs=int(attrs.get("grabs") or 0),
                    publish_at=parse_datetime(item.findtext("pubDate")),
                    priority=self.priority,
                    extra={
                        "downloadvolumefactor": attrs.get("downloadvolumefactor"),
                        "uploadvolumefactor": attrs.get("uploadvolumefactor"),
                        "imdbid": attrs.get("imdbid"),
                        "tmdbid": attrs.get("tmdbid"),
                    },
                )
            )
        return resources

    async def health_check(self) -> tuple[bool, str]:
        endpoint = self._endpoint()
        if not endpoint:
            return False, "未配置 url"
        try:
            text = await fetch_text_result(
                endpoint, params=self._params(t="caps"), timeout=self.config.get("timeout")
            )
        except FetchError as exc:
            # 如实回报 502/超时/TLS，而不是一律"无法连接站点"
            return False, exc.message
        if not text:
            return False, "站点返回空内容"
        if "<caps" not in text and "<error" not in text:
            return False, "返回内容不是 Torznab caps"
        if "<error" in text:
            return False, "站点返回错误（检查 apikey）"
        return True, "连接正常"
