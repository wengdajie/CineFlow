"""MacCMS（苹果 CMS）在线影视站通用适配器。

**这是什么站**：`bzzdyy.com` / `cz4k.com` 这类"在线影视站"绝大多数是
MacCMS 搭的，URL 结构完全固定，一份代码适配一大批站：

- 搜索 ``/index.php/vod/search.html?wd=<关键词>``
- 详情 ``/index.php/vod/detail/id/<vid>.html``
- 播放 ``/index.php/vod/play/id/<vid>/sid/<源>/nid/<集>.html``，
  页面里有 ``var player_aaaa={...}``，其中 ``from`` 是播放源、``url`` 是真地址。

**关键实测结论（决定了本 Provider 该怎么做）**：这类站自己**不存片**，
播放页的 ``url`` 指向的就是各平台官方地址。抓 bzzdyy 首页 30 部片、
73 个播放源实测分布：

    qq 21 / qiyi 12 / youku 10 / mgtv 6 / bilibili 3 / rrmj 1

即 **49/53 ≈ 92% 是长视频平台的会员正片**。这些站唯一的"能力"是把
播放交给一个 VIP 解析网关（bzzdyy 的 ``playerconfig.js`` 里写着
``"parse": "https://hls.xiguadh.com/?url="``，全部播放源共用）——
而这正是 [ADR-24](../../docs/04-决策记录.md) 明确拒绝的东西：
解析网关依赖盗取的会员票据，接进来等于把项目和灰产绑在一起。

**所以本 Provider 的定位**：做**诚实的那一部分**——
把这类站当"索引"用，产出播放源指向的**原始平台地址**，
再交给既有的 yt-dlp 链路。能不能下由 ``is_blocked()`` 统一裁决：
公开内容（B 站/UP 主自制/官方免费片）正常下，会员正片如实拒绝。
**不接入任何解析网关，不伪造会员票据。**

因此用户会看到"搜到很多、能下的少"，这是**如实反映**而非缺陷：
这类站的绝大部分内容本就在会员墙后。宁可给出可信的少数，
也不做一个靠灰产接口撑起来的"什么都能下"。
"""

from __future__ import annotations

import asyncio
import html
import json
import re
from typing import Any
from urllib.parse import urljoin

from app.core.logger import get_logger
from app.providers.base import Resource, SearchProvider
from app.providers.registry import register
from app.schemas.enums import ProviderKind, ResourceKind
from app.utils.http import fetch_text
from app.utils.strings import match_keywords

logger = get_logger(__name__)

_TAG = re.compile(r"<[^>]+>")
#: 搜索/列表页里的详情链接，同时兼容 ``/vod/detail/id/123.html`` 与伪静态变体
_DETAIL_ID = re.compile(r"/(?:index\.php/)?vod/detail/id/(\d+)", re.I)
#: 详情页里的播放链接（取每个播放源的第 1 集）
_PLAY_HREF = re.compile(
    r'href="((?:/index\.php)?/vod/play/id/\d+/sid/(\d+)/nid/1(?:\.html)?)"', re.I
)
#: 播放页里的播放器配置
_PLAYER_CFG = re.compile(r"var\s+player_aaaa\s*=\s*(\{.*?\})\s*</script>", re.S | re.I)
#: 详情页标题
_TITLE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)
#: 详情页封面
_PIC = re.compile(
    r'<img[^>]+class="[^"]*(?:lazyload|thumb)[^"]*"[^>]+(?:data-original|src)="([^"]+)"',
    re.I,
)

#: 播放源代号 → 展示名。未列出的代号原样显示，方便用户自己判断。
SOURCE_LABELS = {
    "qq": "腾讯视频",
    "qiyi": "爱奇艺",
    "iqiyi": "爱奇艺",
    "youku": "优酷",
    "mgtv": "芒果TV",
    "bilibili": "哔哩哔哩",
    "bilibili1": "哔哩哔哩",
    "rrmj": "人人美剧",
    "letv": "乐视",
    "sohu": "搜狐视频",
    "pptv": "PPTV",
    "1905": "1905电影网",
}


def strip_tags(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", str(text or "")))).strip()


def source_label(code: str) -> str:
    key = str(code or "").strip().lower()
    return SOURCE_LABELS.get(key, key or "未知源")


def parse_player_config(page: str) -> dict[str, Any]:
    """从播放页抠出 ``player_aaaa`` 配置。

    抠不出来就返回空 dict——站点换模板是常态，不能让整站崩掉。
    """
    found = _PLAYER_CFG.search(str(page or ""))
    if not found:
        return {}
    try:
        data = json.loads(found.group(1))
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


@register
class MacCmsIndexer(SearchProvider):
    """MacCMS 在线影视站（产出播放源的原始平台地址）。"""

    name = "maccms"
    kind = ProviderKind.INDEXER.value
    display_name = "MacCMS 在线影视站（在线解析）"

    @property
    def base_url(self) -> str:
        return str(self.config.get("url") or "").rstrip("/")

    def _headers(self) -> dict[str, str]:
        # 这类站普遍校验 Referer，缺了会被跳回首页
        return {"Referer": f"{self.base_url}/"} if self.base_url else {}

    async def _get(self, path: str) -> str:
        text = await fetch_text(
            urljoin(f"{self.base_url}/", path.lstrip("/")), headers=self._headers()
        )
        return text or ""

    async def search(
        self,
        keyword: str,
        *,
        media_type: str | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[Resource]:
        if not self.base_url:
            logger.warning("%s 未配置站点地址", self.site_name)
            return []

        page = await self._get(f"/index.php/vod/search.html?wd={keyword}")
        if not page:
            return []
        # 去重保序：搜索结果里同一部片的封面与标题会各出一个链接
        vids = list(dict.fromkeys(_DETAIL_ID.findall(page)))
        if not vids:
            return []

        limit = int(self.option("max_items", 6) or 6)
        tasks = [self._detail(vid, keyword) for vid in vids[:limit]]
        groups = await asyncio.gather(*tasks, return_exceptions=True)

        results: list[Resource] = []
        for group in groups:
            if isinstance(group, Exception):
                logger.warning("%s 详情抓取异常: %s", self.site_name, group)
                continue
            results.extend(group)
        return results

    async def _detail(self, vid: str, keyword: str) -> list[Resource]:
        """抓一部片的所有播放源，产出 ``webvideo`` 资源。"""
        page = await self._get(f"/index.php/vod/detail/id/{vid}.html")
        if not page:
            return []

        found = _TITLE.search(page)
        title = strip_tags(found.group(1)) if found else ""
        if not title:
            return []
        # 站内搜索经常把弱相关结果也返回，这里按关键词过滤，和其它 Provider 一致
        if not match_keywords(title, [keyword]):
            return []

        poster = ""
        pic = _PIC.search(page)
        if pic:
            poster = urljoin(f"{self.base_url}/", pic.group(1))

        # 每个播放源取第 1 集：本 Provider 的产物是"这部片在哪个平台能看"，
        # 具体某一集由 yt-dlp 拿到平台地址后自行处理
        hrefs = list(dict.fromkeys(_PLAY_HREF.findall(page)))
        if not hrefs:
            return []

        pages = await asyncio.gather(
            *[self._get(href) for href, _ in hrefs], return_exceptions=True
        )

        results: list[Resource] = []
        seen: set[str] = set()
        for play_page in pages:
            if isinstance(play_page, Exception) or not play_page:
                continue
            config = parse_player_config(play_page)
            url = str(config.get("url") or "").strip()
            if not url or not url.lower().startswith("http") or url in seen:
                continue
            seen.add(url)
            source = str(config.get("from") or "")
            results.append(
                Resource(
                    title=f"{title}（{source_label(source)}）",
                    link=url,
                    site=self.site_name,
                    # 交给 yt-dlp 链路；会员正片会在 is_blocked() 处如实拒绝
                    kind=ResourceKind.WEBVIDEO.value,
                    page_url=urljoin(f"{self.base_url}/", f"/index.php/vod/detail/id/{vid}.html"),
                    priority=self.priority,
                    extra={
                        "poster": poster or None,
                        "play_source": source,
                        "play_source_label": source_label(source),
                    },
                )
            )
        return results

    async def health_check(self) -> tuple[bool, str]:
        if not self.base_url:
            return False, "未配置站点地址"
        page = await self._get("/")
        if not page:
            return False, "无法访问站点首页（可能被 WAF 拦截或域名已失效）"
        if not _DETAIL_ID.search(page):
            return False, "首页结构不像 MacCMS（找不到 /vod/detail/ 链接）"
        return True, "连接正常，站点结构匹配 MacCMS"
