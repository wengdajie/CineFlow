"""人人影视资源站（yyets.click 系镜像）。

**为什么单独写 Provider**：该站有干净的 JSON API，两阶段取资源——
``/api/resource?keyword=`` 拿剧集条目，再 ``/api/resource?id=`` 拿该剧
全部下载地址（电驴 / 磁力 / 网盘三种 ``way``）。用通用 JSON 适配器描述不了
「按季 → 按清晰度 → 按文件 → 按下载方式」这样的四层嵌套，故内置解析。

**实测**：搜「阿凡达」返回 3 条剧集，详情含 10 个电驴 + 9 个磁力 + 3 个诚通网盘。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from app.core.logger import get_logger
from app.providers.base import Resource, SearchProvider
from app.providers.registry import register
from app.schemas.enums import ProviderKind, ResourceKind
from app.utils.http import fetch_json
from app.utils.strings import parse_size

logger = get_logger(__name__)

DEFAULT_SITE = "https://yyets.click"

#: 下载方式 -> 资源类型。站点用中文标注 way_cn
_WAY_KIND = {
    "磁力": ResourceKind.MAGNET.value,
    "电驴": ResourceKind.DIRECT.value,
    "网盘": ResourceKind.PAN.value,
}


def _kind_of(address: str, way_cn: str) -> str:
    """按链接前缀判断类型（比站点标注更可靠）。"""
    lowered = str(address or "").lower()
    if lowered.startswith("magnet:"):
        return ResourceKind.MAGNET.value
    if lowered.startswith("ed2k:"):
        return ResourceKind.DIRECT.value
    if lowered.startswith("http") and ("pan" in lowered or "ctfile" in lowered):
        return ResourceKind.PAN.value
    for label, kind in _WAY_KIND.items():
        if label in str(way_cn or ""):
            return kind
    return ResourceKind.DIRECT.value


@register
class YyetsProvider(SearchProvider):
    """人人影视（yyets）资源站。"""

    name = "yyets"
    kind = ProviderKind.INDEXER.value
    display_name = "人人影视 YYeTs"

    @property
    def base(self) -> str:
        return str(self.config.get("url") or DEFAULT_SITE).rstrip("/")

    @property
    def detail_limit(self) -> int:
        """最多深入几个剧集详情。详情请求较慢，默认只取前若干条。"""
        try:
            return max(1, min(int(self.option("detail_limit") or 5), 20))
        except (TypeError, ValueError):
            return 5

    def _headers(self) -> dict[str, str]:
        return {"Referer": f"{self.base}/", "Accept": "application/json"}

    async def _detail(self, resource_id: Any) -> dict[str, Any] | None:
        payload = await fetch_json(
            f"{self.base}/api/resource",
            params={"id": resource_id},
            headers=self._headers(),
            timeout=self.config.get("timeout"),
        )
        if not isinstance(payload, dict):
            return None
        data = payload.get("data")
        return data if isinstance(data, dict) else None

    def _flatten(self, data: dict[str, Any], fallback_title: str) -> list[Resource]:
        """把「季 → 清晰度 → 文件 → 下载方式」四层结构拍平成资源列表。"""
        info = data.get("info") or {}
        show_name = str(info.get("cnname") or fallback_title or "").strip()
        en_name = str(info.get("enname") or "").strip()
        page_url = (
            f"{self.base}/resource/{info.get('id')}" if info.get("id") else None
        )
        resources: list[Resource] = []
        seen: set[str] = set()

        for season in data.get("list") or []:
            if not isinstance(season, dict):
                continue
            season_no = season.get("season_num")
            try:
                season_num = int(season_no) if season_no not in (None, "") else None
            except (TypeError, ValueError):
                season_num = None
            for quality, entries in (season.get("items") or {}).items():
                for entry in entries or []:
                    if not isinstance(entry, dict):
                        continue
                    file_name = str(entry.get("name") or "").strip()
                    size = parse_size(entry.get("size") or 0)
                    stamp = str(entry.get("dateline") or "").strip()
                    published = None
                    if stamp.isdigit():
                        try:
                            published = datetime.fromtimestamp(int(stamp), tz=UTC)
                        except (OSError, ValueError, OverflowError):
                            published = None
                    for item in entry.get("files") or []:
                        if not isinstance(item, dict):
                            continue
                        address = str(item.get("address") or "").strip()
                        if not address or address in seen:
                            continue
                        seen.add(address)
                        way_cn = str(item.get("way_cn") or "")
                        # 标题优先用文件名：里面已含清晰度/组名，便于识别与过滤
                        title = file_name or f"{show_name} {quality}".strip()
                        resources.append(
                            Resource(
                                title=title,
                                link=address,
                                site=self.site_name,
                                kind=_kind_of(address, way_cn),
                                page_url=page_url,
                                size=size,
                                publish_at=published,
                                priority=self.priority,
                                password=str(item.get("passwd") or "").strip() or None,
                                extra={
                                    "provider": self.name,
                                    "show_name": show_name,
                                    "en_name": en_name or None,
                                    "quality": quality,
                                    "season": season_num,
                                    "way": way_cn or None,
                                    "area": info.get("area"),
                                    "channel": info.get("channel_cn"),
                                },
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
        word = str(keyword or "").strip()
        if not word:
            return []

        payload = await fetch_json(
            f"{self.base}/api/resource",
            params={"keyword": word},
            headers=self._headers(),
            timeout=self.config.get("timeout"),
        )
        if not isinstance(payload, dict):
            return []
        entries = payload.get("resource")
        if not isinstance(entries, list) or not entries:
            return []

        targets = [e for e in entries[: self.detail_limit] if isinstance(e, dict)]
        # 并发取详情：站点响应不快，串行会拖垮整体搜索耗时
        details = await asyncio.gather(
            *(self._detail(e.get("id")) for e in targets),
            return_exceptions=True,
        )

        resources: list[Resource] = []
        for entry, detail in zip(targets, details, strict=False):
            if isinstance(detail, Exception) or not detail:
                continue
            resources.extend(
                self._flatten(detail, str(entry.get("cnname") or word))
            )
        return resources

    async def health_check(self) -> tuple[bool, str]:
        payload = await fetch_json(
            f"{self.base}/api/resource",
            params={"keyword": "生活"},
            headers=self._headers(),
            timeout=15,
        )
        if not isinstance(payload, dict):
            return False, "无法连接人人影视 API（站点常换域名，可在站点地址里改镜像）"
        count = len(payload.get("resource") or [])
        return True, f"连接正常，测试关键词返回 {count} 条剧集"
