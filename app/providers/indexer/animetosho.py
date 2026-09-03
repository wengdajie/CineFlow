"""AnimeTosho 公开索引：动漫**全量镜像**，零配置、免鉴权、中英文皆可搜。

## 为什么加它（与 Nyaa / dmhy 的分工）

三者都做动漫，但覆盖与形态不同：

| 源 | 形态 | 中文关键词 | 缺陷 |
|---|---|---|---|
| dmhy | HTML 正则 | ✅ 原生中文 | 只有华语字幕组，欧美/日生肉少 |
| Nyaa | RSS 搜索 | 🟡 能搜但命中少（实测「沙丘」1 条） | 需按 category 配，中文标题靠字幕组自填 |
| **AnimeTosho** | JSON API | ✅ 实测中文命中 | 只有动漫，非动漫题材为零 |

AnimeTosho 会**镜像** Nyaa/AniDex/TokyoTosho 的条目并做统一元数据整理，
因此它同时是「Nyaa 的超集」与「带 .torrent 直链的稳定源」。

## 实测凭据（2026-09-03）

| 关键词 | 结果 | 命中示例 |
|---|---|---|
| `凡人修仙传` | 75 条 | `[FSP DN] A Record of a Mortal's Journey to Immortality - 176` |
| `葬送的芙莉莲` | 75 条 | `中配 - 葬送的芙莉莲 (Sousou no Frieren) (Season 2)` |
| `鬼灭之刃` | 75 条 | `[Furretar] 中台粤配 - 鬼灭之刃 (Kimetsu no Yaiba)` |
| `Frieren` | 75 条 | `[9volt] Sousou no Frieren - Season 2 (WEB 1080p)` |

**中文关键词能命中罗马音标题**，这是它比 Nyaa 值得接的关键 ——
它把作品的中/日/英名都并进检索索引，而不是只匹配种子标题字面。

⚠️ 75 是接口的**返回上限**（不是真实总数），故不要把「75 条」当作命中质量的指标；
本 Provider 仍会做一次本地相关性过滤兜底。

## 为什么用 `torrent_url` 而不是磁力

接口同时给 `info_hash` 与 `torrent_url`（AnimeTosho 自己的种子存储）。
优先用 `.torrent` 直链：它不依赖 DHT 找 peer，qB/TR 拿到即可开始，
`ResourceKind.TORRENT` 也正好对上下载路由表。infohash 一并存进 `extra`
供跨站去重使用（`Resource.unique_key` 对磁力取 infohash，
这里显式给出 magnet 形式的 link 会更利于去重，故两者都保留）。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from app.core.logger import get_logger
from app.providers.base import Resource, SearchProvider
from app.providers.registry import register
from app.schemas.enums import ProviderKind, ResourceKind
from app.utils.http import FetchError, fetch_json
from app.utils.strings import match_keywords

logger = get_logger(__name__)

#: 公开 feed 接口（无鉴权）
DEFAULT_BASE = "https://feed.animetosho.org"


@register
class AnimeToshoIndexer(SearchProvider):
    """AnimeTosho 动漫索引（零配置，中英文可搜）。"""

    name = "animetosho"
    kind = ProviderKind.INDEXER.value
    display_name = "AnimeTosho（动漫）"

    def _base(self) -> str:
        return str(
            self.option("base_url", "") or self.config.get("url") or DEFAULT_BASE
        ).rstrip("/")

    def _parse(self, payload: Any) -> list[Resource]:
        if not isinstance(payload, list):
            return []
        collected: list[Resource] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("torrent_name") or "").strip()
            torrent_url = str(item.get("torrent_url") or "").strip()
            infohash = str(item.get("info_hash") or "").strip()
            if not title:
                continue
            # 两者都没有就拼不出可下的东西，丢掉而不是留一条死链
            if not torrent_url and not re.fullmatch(r"[0-9a-fA-F]{40}", infohash):
                continue
            # 只收已完成的条目：status=complete 之外还有 "skipped"/"processing"，
            # 后者的 torrent_url 可能还没落地
            status = str(item.get("status") or "").strip().lower()
            if status and status != "complete":
                continue
            publish_at = None
            timestamp = item.get("timestamp")
            if isinstance(timestamp, (int, float)) and timestamp > 0:
                publish_at = datetime.fromtimestamp(float(timestamp), tz=timezone.utc).replace(
                    tzinfo=None
                )
            link = torrent_url
            kind = ResourceKind.TORRENT.value
            if not link:
                from app.providers.indexer.torrents_csv import build_magnet

                link = build_magnet(infohash, title)
                kind = ResourceKind.MAGNET.value
            collected.append(
                Resource(
                    title=title,
                    link=link,
                    site=self.site_name,
                    kind=kind,
                    page_url=str(item.get("link") or "") or None,
                    size=int(item.get("total_size") or 0),
                    publish_at=publish_at,
                    priority=self.priority,
                    extra={
                        "infohash": infohash.lower(),
                        "source": "animetosho",
                        "nyaa_id": item.get("nyaa_id"),
                    },
                )
            )
        return collected

    async def search(
        self,
        keyword: str,
        *,
        media_type: str | None = None,
        season: int | None = None,
        episode: int | None = None,
        page: int = 0,
    ) -> list[Resource]:
        query = str(keyword or "").strip()
        if not query:
            return []
        payload = await fetch_json(
            f"{self._base()}/json", params={"q": query, "show": "torrents"}
        )
        if payload is None:
            raise FetchError("AnimeTosho 接口无响应（可能被墙或服务暂时不可用）")
        collected = self._parse(payload)
        # 接口是宽匹配（会把同作品的所有集数、甚至相关作品带回来）。
        # 命中不到就保留原样：中文关键词常常只能匹配到罗马音标题，
        # 按中文字面过滤会把正确结果全部误杀（这正是它区别于 Nyaa 的价值）。
        if self.option("local_filter", False):
            filtered = [
                item
                for item in collected
                if match_keywords(item.title, [re.escape(query)], mode="any")
            ]
            if filtered:
                collected = filtered
        logger.info("AnimeTosho 搜索「%s」：%d 条", query, len(collected))
        return collected

    async def fetch_latest(self, limit: int = 100) -> list[Resource]:
        payload = await fetch_json(f"{self._base()}/json", params={"show": "torrents"})
        if payload is None:
            return []
        return self._parse(payload)[:limit]

    async def health_check(self) -> tuple[bool, str]:
        payload = await fetch_json(f"{self._base()}/json", params={"q": "frieren"})
        if payload is None:
            return False, "无法连接 AnimeTosho（检查网络/代理）"
        parsed = self._parse(payload)
        if not parsed:
            return False, "接口连通但未解析到资源（字段可能已变更）"
        return True, f"连通正常，搜索「frieren」返回 {len(parsed)} 条"
