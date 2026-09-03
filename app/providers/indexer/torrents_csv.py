"""torrents-csv 公开 BT 搜索 API：**零配置、免 API Key、支持中文关键词**。

## 为什么加这个 Provider

用户反馈「大部分内容搜不到 BT 资源」。实测根因之一：v1.19.0 的内置 BT 站
只有 3 个 HTML 站（dmhy 偏动漫、bdflixs/cilixiong 偏电影），而覆盖面最广的
`torznab` **必须先自己部署 Jackett/Prowlarr** —— 对「装完就想用」的用户等于零覆盖。

torrents-csv 是一份公开的 BT 元数据集合（自 DHT 与主流公开站聚合而来），
提供**免费无鉴权**的 JSON 搜索接口，因此它是唯一能做到「开箱即用」的 BT 源。

## 实测凭据（2026-09-03，收录标准见 site_catalog.py）

| 关键词 | 结果 | 说明 |
|---|---|---|
| `dune` | 25 条/页，可翻页累计 100+ | 带 seeders/leechers/体积/发布时间，元数据最完整 |
| `凡人修仙传` | 25 条 | **中文可直接搜**，命中 GM-Team 国漫组 |
| `庆余年` | 4 条 | 含 4K 全集包 61.9GB |
| `流浪地球` | 3 条 | 含 DBD-Raws 蓝光 |
| `沙丘` | 3 条 | 含阳光电影国英双语 |
| `zzzzqqqq…`(乱码) | **0 条** | 无匹配时如实返回空数组，不返回兜底榜 |

最后一行是**这个源能用的关键理由**，也是它与 apibay(海盗湾 JSON) 的分水岭：
apibay 对任何非 ASCII 关键词会**静默忽略查询并返回全站 top-100 热门**
（实测「沙丘」与「凡人修仙传」返回的前 5 条完全相同），
那种源接进来会让每次中文搜索都塞满无关热门种子，比搜不到更糟 —— 故不收录。

## 分页

游标式：响应带 `next`，把它作为下次请求的 `after` 参数。实测 4 页各 25 条、
去重后 100 条无重复。单次请求的 `size` 上限被服务端钳到 25，因此要更多结果
必须翻页（`max_pages`，默认 3 页 = 75 条）。
"""

from __future__ import annotations

import re
from typing import Any

from app.core.logger import get_logger
from app.providers.base import Resource, SearchProvider
from app.providers.registry import register
from app.schemas.enums import ProviderKind, ResourceKind
from app.utils.http import FetchError, fetch_json
from app.utils.strings import match_keywords

logger = get_logger(__name__)

#: 官方公开接口。允许用户覆盖（自建镜像/反代）。
DEFAULT_BASE = "https://torrents-csv.com/service"

#: 服务端把 size 钳在 25，写死是为了让翻页数算得准
PAGE_SIZE = 25

#: 附加 tracker：接口只给 infohash，裸磁力（无 tr=）在部分下载器上
#: 要靠 DHT 慢慢找 peer。补几个公开 tracker 能显著加快起步速度。
DEFAULT_TRACKERS = (
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://exodus.desync.com:6969/announce",
)


def build_magnet(infohash: str, title: str, trackers: tuple[str, ...] = DEFAULT_TRACKERS) -> str:
    """由 infohash 拼出磁力链。

    ``dn=`` 必须带上：下游 ``guess_kind``/入库命名都靠标题，
    而详情页二段抓取的经验（见 generic_html 文档）是「不带 dn 的磁力
    会让标题退化」。这里数据源本来就给了 name，没有理由丢掉。
    """
    from urllib.parse import quote

    parts = [f"magnet:?xt=urn:btih:{infohash.lower()}"]
    if title:
        parts.append(f"dn={quote(title)}")
    parts.extend(f"tr={quote(tracker, safe='')}" for tracker in trackers)
    return "&".join(parts)


@register
class TorrentsCsvIndexer(SearchProvider):
    """torrents-csv 公开 BT 搜索（零配置、支持中文）。"""

    name = "torrents_csv"
    kind = ProviderKind.INDEXER.value
    display_name = "Torrents-CSV 公开搜索"

    def _base(self) -> str:
        return str(
            self.option("base_url", "") or self.config.get("url") or DEFAULT_BASE
        ).rstrip("/")

    def _parse(self, payload: Any) -> list[tuple[Resource, Any]]:
        """把一页响应解析成资源列表；同时返回游标。"""
        items = payload.get("torrents") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            return []
        collected: list[tuple[Resource, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            infohash = str(item.get("infohash") or "").strip()
            title = str(item.get("name") or "").strip()
            # infohash 是**唯一**不能缺的字段：没有它就拼不出磁力，
            # 留下一条点了没反应的结果比不给这条更糟
            if not re.fullmatch(r"[0-9a-fA-F]{40}", infohash):
                continue
            if not title:
                continue
            publish_at = None
            created = item.get("created_unix")
            if isinstance(created, (int, float)) and created > 0:
                from datetime import datetime, timezone

                publish_at = datetime.fromtimestamp(float(created), tz=timezone.utc).replace(
                    tzinfo=None
                )
            collected.append((
                Resource(
                    title=title,
                    link=build_magnet(infohash, title),
                    site=self.site_name,
                    kind=ResourceKind.MAGNET.value,
                    size=int(item.get("size_bytes") or 0),
                    seeders=int(item.get("seeders") or 0),
                    leechers=int(item.get("leechers") or 0),
                    grabs=int(item.get("completed") or 0),
                    publish_at=publish_at,
                    priority=self.priority,
                    extra={"infohash": infohash, "source": "torrents-csv"},
                ),
                item,
            ))
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
        max_pages = max(int(self.option("max_pages", 3)), 1)
        collected: list[Resource] = []
        seen: set[str] = set()
        cursor: Any = None
        for _ in range(max_pages):
            params: dict[str, Any] = {"q": query, "size": PAGE_SIZE}
            if cursor:
                params["after"] = cursor
            payload = await fetch_json(f"{self._base()}/search", params=params)
            if payload is None:
                # 首页就失败 → 让上层区分"坏了"与"没有"（ADR-82）；
                # 翻页途中失败则保留已拿到的结果，不因为第 3 页挂了丢掉前 2 页
                if not collected:
                    raise FetchError("torrents-csv 接口无响应（可能被墙或服务暂时不可用）")
                break
            for resource, _raw in self._parse(payload):
                infohash = str(resource.extra.get("infohash") or "")
                if infohash in seen:
                    continue
                seen.add(infohash)
                collected.append(resource)
            cursor = payload.get("next") if isinstance(payload, dict) else None
            if not cursor:
                break

        # 本地相关性过滤：接口是全文模糊匹配，搜「沙丘」会带回
        # 名字里含「沙」的无关片。命中不到就**保留原样**而不是清空 ——
        # 中文站的标题常把片名写成别名/罗马音，全清等于把可用结果扔了。
        if self.option("local_filter", True):
            filtered = [
                item
                for item in collected
                if match_keywords(item.title, [re.escape(query)], mode="any")
            ]
            if filtered:
                collected = filtered
        logger.info(
            "torrents-csv 搜索「%s」：%d 条（翻 %d 页）", query, len(collected), max_pages
        )
        return collected

    async def fetch_latest(self, limit: int = 100) -> list[Resource]:
        """接口没有「最新」端点，只有搜索。如实返回空而不是伪造一批。"""
        return []

    async def health_check(self) -> tuple[bool, str]:
        payload = await fetch_json(
            f"{self._base()}/search", params={"q": "dune", "size": PAGE_SIZE}
        )
        if payload is None:
            return False, "无法连接 torrents-csv（检查网络/代理，或改用自建镜像）"
        parsed = self._parse(payload)
        if not parsed:
            return False, "接口连通但未解析到资源（字段可能已变更）"
        return True, f"连通正常，搜索「dune」返回 {len(parsed)} 条"
