"""公开视频站搜索（YouTube / Bilibili）。

**为什么单独做一个 Provider**：项目原有的索引器都产出「种子/网盘链接」，
而 YouTube / B 站产出的是**视频网页地址**，要交给 yt-dlp 下载。所以这里统一
产出 ``ResourceKind.WEBVIDEO``，前端只渲染「下载」按钮，下载路由自动选中
``ytdlp`` 下载器。

**为什么不用 yt-dlp 的搜索**：yt-dlp 的 ``bilisearch:`` 目前会被 B 站以
HTTP 412 拦截（缺少 buvid Cookie）。因此 B 站走官方 web 搜索 API，并**先访问
一次首页拿 Cookie**（buvid3）再搜索——这是 412 的根因。YouTube 侧 yt-dlp 的
``ytsearch:`` 稳定可用，直接复用，避免自己解析 YouTube 那套混淆 JSON。

两者都做了缓存 + 限流退避，任何失败都返回空列表而不抛异常。
"""

from __future__ import annotations

import asyncio
import html
import re
import time
from typing import Any

from app.core.logger import get_logger
from app.providers.base import Resource, SearchProvider
from app.providers.registry import register
from app.schemas.enums import ProviderKind, ResourceKind
from app.utils.http import async_client

logger = get_logger(__name__)

#: 搜索结果缓存 10 分钟：热门关键词会被订阅任务反复搜
_CACHE_TTL = 600
_BACKOFF_SECONDS = 300

_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_BACKOFF: dict[str, float] = {}

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _cache_get(key: str) -> list[dict[str, Any]] | None:
    item = _CACHE.get(key)
    if not item:
        return None
    expire_at, value = item
    if expire_at < time.time():
        _CACHE.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: list[dict[str, Any]]) -> None:
    _CACHE[key] = (time.time() + _CACHE_TTL, value)


def _backed_off(scope: str) -> bool:
    return time.time() < _BACKOFF.get(scope, 0.0)


def _mark_backoff(scope: str) -> None:
    _BACKOFF[scope] = time.time() + _BACKOFF_SECONDS
    logger.warning("%s 疑似限流，静默 %s 秒", scope, _BACKOFF_SECONDS)


def reset_state() -> None:
    """清空缓存与退避（测试用）。"""
    _CACHE.clear()
    _BACKOFF.clear()


def strip_tags(text: str) -> str:
    """去掉 B 站搜索结果标题里的 ``<em class="keyword">`` 高亮标签。"""
    return html.unescape(re.sub(r"<[^>]+>", "", str(text or ""))).strip()


def parse_duration(raw: str) -> int:
    """把 ``166:39`` / ``1:02:03`` 这类时长转成秒。"""
    parts = [p for p in str(raw or "").strip().split(":") if p.strip().isdigit()]
    if not parts:
        return 0
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds


class _WebVideoProvider(SearchProvider):
    """公开视频站搜索的公共逻辑。"""

    kind = ProviderKind.INDEXER.value

    @property
    def limit(self) -> int:
        """单次搜索返回条数上限。"""
        try:
            return max(1, min(int(self.option("limit") or 20), 50))
        except (TypeError, ValueError):
            return 20

    def _cache_key(self, keyword: str) -> str:
        return f"{self.name}:{keyword.strip().lower()}:{self.limit}"

    async def _do_search(self, keyword: str) -> list[dict[str, Any]]:
        """子类实现：返回归一化的 dict 列表。"""
        raise NotImplementedError

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
        if not word:
            return []

        cache_key = self._cache_key(word)
        rows = _cache_get(cache_key)
        if rows is None:
            if _backed_off(self.name):
                return []
            try:
                rows = await self._do_search(word)
            except Exception as exc:
                logger.warning("%s 搜索失败 %s: %s", self.display_name, word, exc)
                _mark_backoff(self.name)
                return []
            if rows is None:
                _mark_backoff(self.name)
                return []
            _cache_set(cache_key, rows)

        resources: list[Resource] = []
        for row in rows[: self.limit]:
            link = str(row.get("link") or "").strip()
            title = strip_tags(row.get("title") or "")
            if not link or not title:
                continue
            resources.append(
                Resource(
                    title=title,
                    link=link,
                    site=self.site_name,
                    kind=ResourceKind.WEBVIDEO.value,
                    page_url=link,
                    description=str(row.get("description") or "")[:500] or None,
                    # 视频网页没有"体积"概念，用播放量当热度投影到 seeders，
                    # 这样能直接参与既有的热度排序，无需给榜单加特例
                    seeders=int(row.get("play_count") or 0),
                    publish_at=row.get("publish_at"),
                    priority=self.priority,
                    extra={
                        "poster": row.get("poster"),
                        "uploader": row.get("uploader"),
                        "duration": row.get("duration") or 0,
                        "play_count": row.get("play_count") or 0,
                        "platform": self.display_name,
                        "webvideo": True,
                    },
                )
            )
        return resources


@register
class BilibiliSearchProvider(_WebVideoProvider):
    """Bilibili 视频搜索。"""

    name = "bilibili"
    display_name = "Bilibili"

    SEARCH_URL = "https://api.bilibili.com/x/web-interface/search/type"
    HOME_URL = "https://www.bilibili.com/"

    def _headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": UA,
            "Referer": "https://www.bilibili.com/",
            "Accept": "application/json, text/plain, */*",
        }
        # 用户填了自己的 Cookie 能提高配额与稳定性，但非必需
        cookie = str(self.option("cookie") or self.config.get("cookie") or "").strip()
        if cookie:
            headers["Cookie"] = cookie
        return headers

    async def _do_search(self, keyword: str) -> list[dict[str, Any]] | None:
        from datetime import UTC, datetime

        rows: list[dict[str, Any]] = []
        async with async_client(timeout=self.config.get("timeout") or 20) as client:
            # 关键一步：先摸首页换 buvid3 Cookie，否则搜索接口直接 412
            try:
                await client.get(self.HOME_URL, headers={"User-Agent": UA})
            except Exception as exc:
                logger.debug("B 站首页预热失败（继续尝试搜索）: %s", exc)

            response = await client.get(
                self.SEARCH_URL,
                params={
                    "search_type": "video",
                    "keyword": keyword,
                    "page": 1,
                    "page_size": self.limit,
                },
                headers=self._headers(),
            )
            if response.status_code != 200:
                logger.warning("B 站搜索 HTTP %s", response.status_code)
                return None
            payload = response.json()

        if not isinstance(payload, dict) or payload.get("code") != 0:
            logger.warning(
                "B 站搜索返回 code=%s msg=%s",
                (payload or {}).get("code"),
                (payload or {}).get("message"),
            )
            return None

        for item in ((payload.get("data") or {}).get("result") or []):
            if not isinstance(item, dict):
                continue
            bvid = str(item.get("bvid") or "").strip()
            arcurl = str(item.get("arcurl") or "").strip()
            # 优先用 bvid 拼规范地址：arcurl 有时是 av 号或课程页，yt-dlp 支持度较差
            link = f"https://www.bilibili.com/video/{bvid}" if bvid else arcurl
            if not link:
                continue
            poster = str(item.get("pic") or "").strip()
            if poster.startswith("//"):
                poster = "https:" + poster
            stamp = int(item.get("pubdate") or 0)
            rows.append(
                {
                    "title": item.get("title"),
                    "link": link,
                    "description": strip_tags(item.get("description") or ""),
                    "poster": poster or None,
                    "uploader": item.get("author"),
                    "duration": parse_duration(item.get("duration") or ""),
                    "play_count": int(item.get("play") or 0),
                    "publish_at": (
                        datetime.fromtimestamp(stamp, tz=UTC) if stamp > 0 else None
                    ),
                }
            )
        return rows

    async def health_check(self) -> tuple[bool, str]:
        rows = await self._do_search("测试")
        if rows is None:
            return False, "B 站搜索接口不可用（可能被限流，稍后重试）"
        return True, f"连接正常，返回 {len(rows)} 条"


@register
class YouTubeSearchProvider(_WebVideoProvider):
    """YouTube 视频搜索（复用 yt-dlp 的 ytsearch）。"""

    name = "youtube"
    display_name = "YouTube"

    async def _do_search(self, keyword: str) -> list[dict[str, Any]] | None:
        try:
            import yt_dlp
        except ImportError:
            logger.warning("未安装 yt-dlp，YouTube 搜索不可用")
            return None

        options: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            # extract_flat 只取列表元信息，不解析每个视频的播放地址——快得多
            "extract_flat": True,
            "skip_download": True,
            "socket_timeout": int(self.config.get("timeout") or 20),
            "noprogress": True,
        }
        proxy = str(self.option("proxy") or "").strip()
        if proxy:
            options["proxy"] = proxy
        cookie_file = str(self.option("cookie_file") or "").strip()
        if cookie_file:
            options["cookiefile"] = cookie_file

        query = f"ytsearch{self.limit}:{keyword}"

        def _extract() -> dict[str, Any]:
            with yt_dlp.YoutubeDL(options) as ydl:
                return ydl.extract_info(query, download=False) or {}

        # yt-dlp 是同步阻塞库，丢到线程里避免卡住事件循环
        info = await asyncio.to_thread(_extract)
        entries = info.get("entries") or []
        rows: list[dict[str, Any]] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            link = str(item.get("url") or item.get("webpage_url") or "").strip()
            video_id = str(item.get("id") or "").strip()
            if not link and video_id:
                link = f"https://www.youtube.com/watch?v={video_id}"
            if not link:
                continue
            thumbs = item.get("thumbnails") or []
            poster = str(item.get("thumbnail") or "").strip()
            if not poster and isinstance(thumbs, list) and thumbs:
                poster = str((thumbs[-1] or {}).get("url") or "").strip()
            if not poster and video_id:
                # 兜底用 YouTube 固定缩略图地址规则，保证画板有图
                poster = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
            rows.append(
                {
                    "title": item.get("title"),
                    "link": link,
                    "description": item.get("description"),
                    "poster": poster or None,
                    "uploader": item.get("uploader") or item.get("channel"),
                    "duration": int(item.get("duration") or 0),
                    "play_count": int(item.get("view_count") or 0),
                    "publish_at": None,
                }
            )
        return rows

    async def health_check(self) -> tuple[bool, str]:
        try:
            import yt_dlp  # noqa: F401
        except ImportError:
            return False, "未安装 yt-dlp，无法搜索 YouTube"
        rows = await self._do_search("test")
        if rows is None:
            return False, "YouTube 搜索失败（检查网络或代理设置）"
        return True, f"连接正常，返回 {len(rows)} 条"
