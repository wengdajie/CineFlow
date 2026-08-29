"""WordPress 影视站通用适配器（RSS 搜索 + 详情页抓链）。

**为什么需要它**：很多中文影视资源站都是 WordPress 搭的，共同特征是
``/?s=关键词&feed=rss2`` 能直接搜索并返回标准 RSS，而真正的磁力/网盘链接
写在文章详情页里。这个 Provider 把这套「RSS 找文章 → 进详情页抓链接」的
固定套路抽象出来，一份代码适配多站（bdflixs / dygod / mjf2020 …）。

**实测**（bdflixs.com）：搜「阿凡达」RSS 返回 3 篇文章，进第一篇详情页
抓到 160 条磁力链接。

**降级**：RSS 拿不到就返回空；详情页抓不到链接就跳过该文章，绝不抛异常。
"""

from __future__ import annotations

import asyncio
import html
import re
from typing import Any
from urllib.parse import quote, urljoin

from app.core.logger import get_logger
from app.providers.base import Resource, SearchProvider
from app.providers.registry import register
from app.schemas.enums import ProviderKind, ResourceKind
from app.utils.http import fetch_text
from app.utils.strings import match_keywords, parse_datetime

logger = get_logger(__name__)

_ITEM = re.compile(r"<item>(.*?)</item>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_MAGNET = re.compile(r"magnet:\?xt=urn:btih:[0-9a-zA-Z]{32,40}[^\s\"'<>\\]*", re.I)
_ED2K = re.compile(r"ed2k://\|file\|[^\s\"'<>|]+\|\d+\|[0-9A-Fa-f]{32}\|[^\s\"'<>]*", re.I)
#: 常见网盘分享域名（用于从详情页里挑出网盘链接）
_PAN_HOSTS = (
    "pan.quark.cn",
    "pan.baidu.com",
    "www.alipan.com",
    "www.aliyundrive.com",
    "aliyundrive.com",
    "alipan.com",
    "115.com",
    "115cdn.com",
    "cloud.189.cn",
    "pan.xunlei.com",
    "caiyun.139.com",
    "drive.uc.cn",
    "mypikpak.com",
)
_PAN_HOST_ALT = "|".join(h.replace(".", r"\.") for h in _PAN_HOSTS)
_PAN_LINK = re.compile(
    rf"https?://(?:{_PAN_HOST_ALT})/[^\s\"'<>\\]+",
    re.I,
)
_PASSWORD = re.compile(r"(?:密码|提取码|访问码|pwd|code)\s*[:：]?\s*([A-Za-z0-9]{4,8})")


def _tag_text(block: str, tag: str) -> str:
    """从 RSS item 里取某个标签的文本（兼容 CDATA）。"""
    found = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", block, re.S | re.I)
    if not found:
        return ""
    raw = found.group(1).strip()
    cdata = re.match(r"<!\[CDATA\[(.*?)\]\]>", raw, re.S)
    if cdata:
        raw = cdata.group(1)
    return html.unescape(_TAG.sub("", raw)).strip()


@register
class WordPressFilmProvider(SearchProvider):
    """WordPress 影视站（RSS 搜索 + 详情页抓链）。"""

    name = "wp_film"
    kind = ProviderKind.INDEXER.value
    display_name = "WordPress 影视站（RSS）"

    @property
    def base(self) -> str:
        return str(self.config.get("url") or self.option("site_url") or "").rstrip("/")

    @property
    def article_limit(self) -> int:
        """最多深入几篇文章抓链接。"""
        try:
            return max(1, min(int(self.option("article_limit") or 5), 20))
        except (TypeError, ValueError):
            return 5

    @property
    def per_article_limit(self) -> int:
        """单篇文章最多取多少条链接。有的合集页有上百条，全收会淹没搜索结果。"""
        try:
            return max(1, min(int(self.option("per_article_limit") or 12), 100))
        except (TypeError, ValueError):
            return 12

    def _headers(self) -> dict[str, str]:
        headers = {"Referer": f"{self.base}/"}
        if self.config.get("cookie"):
            headers["Cookie"] = str(self.config["cookie"])
        headers.update(dict(self.option("headers", {}) or {}))
        return headers

    def _search_url(self, keyword: str) -> str:
        template = str(self.option("search_url") or "")
        if template:
            return template.replace("{keyword}", quote(keyword))
        # WordPress 默认搜索 RSS
        return f"{self.base}/?s={quote(keyword)}&feed=rss2"

    async def _fetch(self, url: str) -> str:
        text = await fetch_text(
            url,
            headers=self._headers(),
            timeout=self.config.get("timeout"),
            encoding=self.option("encoding") or None,
        )
        return text or ""

    async def _article_links(self, keyword: str) -> list[tuple[str, str, Any]]:
        """从搜索 RSS 里取出 ``(标题, 链接, 发布时间)`` 列表。"""
        feed = await self._fetch(self._search_url(keyword))
        if not feed:
            return []
        rows: list[tuple[str, str, Any]] = []
        for block in _ITEM.finditer(feed):
            item = block.group(1)
            title = _tag_text(item, "title")
            link = _tag_text(item, "link")
            if not title or not link:
                continue
            published = parse_datetime(_tag_text(item, "pubDate"))
            rows.append((title, urljoin(f"{self.base}/", link), published))
        return rows

    def _extract(
        self, page: str, *, title: str, page_url: str, published: Any
    ) -> list[Resource]:
        """从文章正文里抽出磁力 / 电驴 / 网盘链接。"""
        resources: list[Resource] = []
        seen: set[str] = set()
        password_hit = _PASSWORD.search(page)
        shared_password = password_hit.group(1) if password_hit else None

        buckets: list[tuple[re.Pattern[str], str]] = [
            (_MAGNET, ResourceKind.MAGNET.value),
            (_PAN_LINK, ResourceKind.PAN.value),
            (_ED2K, ResourceKind.DIRECT.value),
        ]
        for pattern, kind in buckets:
            for found in pattern.finditer(page):
                link = html.unescape(found.group(0)).rstrip("\"'&,;)")
                if not link or link in seen:
                    continue
                seen.add(link)
                resources.append(
                    Resource(
                        title=title,
                        link=link,
                        site=self.site_name,
                        kind=kind,
                        page_url=page_url,
                        publish_at=published,
                        priority=self.priority,
                        password=(
                            shared_password if kind == ResourceKind.PAN.value else None
                        ),
                        extra={"provider": self.name, "article": page_url},
                    )
                )
                if len(resources) >= self.per_article_limit:
                    return resources
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
        word = str(keyword or "").strip()
        if not word or not self.base:
            return []

        articles = await self._article_links(word)
        if not articles:
            return []

        # 站点搜索有时会把不相关的文章也带回来，这里再按关键词过滤一次
        filtered = [
            row for row in articles if match_keywords(row[0], word)
        ] or articles

        targets = filtered[: self.article_limit]
        pages = await asyncio.gather(
            *(self._fetch(url) for _, url, _ in targets), return_exceptions=True
        )

        resources: list[Resource] = []
        for (title, url, published), body in zip(targets, pages, strict=False):
            if isinstance(body, Exception) or not body:
                continue
            resources.extend(
                self._extract(body, title=title, page_url=url, published=published)
            )
        return resources

    async def fetch_latest(self, limit: int = 100) -> list[Resource]:
        """用站点主 RSS 做追新雷达。"""
        if not self.base:
            return []
        feed_url = str(self.option("latest_url") or f"{self.base}/feed")
        feed = await self._fetch(feed_url)
        if not feed:
            return []
        rows: list[tuple[str, str, Any]] = []
        for block in _ITEM.finditer(feed):
            item = block.group(1)
            title = _tag_text(item, "title")
            link = _tag_text(item, "link")
            if not title or not link:
                continue
            rows.append((title, urljoin(f"{self.base}/", link), parse_datetime(_tag_text(item, "pubDate"))))

        targets = rows[: self.article_limit]
        pages = await asyncio.gather(
            *(self._fetch(url) for _, url, _ in targets), return_exceptions=True
        )
        resources: list[Resource] = []
        for (title, url, published), body in zip(targets, pages, strict=False):
            if isinstance(body, Exception) or not body:
                continue
            resources.extend(
                self._extract(body, title=title, page_url=url, published=published)
            )
            if len(resources) >= limit:
                break
        return resources[:limit]

    async def health_check(self) -> tuple[bool, str]:
        if not self.base:
            return False, "未配置站点地址"
        probe = str(self.option("health_keyword") or "2024")
        articles = await self._article_links(probe)
        if not articles:
            feed = await self._fetch(f"{self.base}/feed")
            if feed:
                return True, "站点可访问，但搜索无结果（换个关键词试试）"
            return False, "无法访问站点 RSS（可能被墙/换域名/需要 Cookie）"
        return True, f"连接正常，搜索返回 {len(articles)} 篇文章"
