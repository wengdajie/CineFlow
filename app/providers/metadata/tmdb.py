"""TMDB 元数据（识别、海报、剧集集数）。

未配置 API Key 时所有方法安全降级为空结果，不影响下载与整理主流程。
"""

from __future__ import annotations

import time
from typing import Any

from app.core.config import settings
from app.core.logger import get_logger
from app.schemas.enums import MediaType
from app.utils.http import fetch_json

logger = get_logger(__name__)

_CACHE: dict[str, tuple[float, Any]] = {}


def _cache_get(key: str) -> Any | None:
    item = _CACHE.get(key)
    if not item:
        return None
    expire_at, value = item
    if expire_at < time.time():
        _CACHE.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: Any) -> None:
    _CACHE[key] = (time.time() + settings.METADATA_CACHE_TTL, value)


class TmdbClient:
    """TMDB v3 API 轻封装。"""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.TMDB_API_KEY
        self.base = settings.TMDB_API_HOST.rstrip("/") + "/3"

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    async def _get(self, path: str, **params: Any) -> Any | None:
        if not self.available:
            return None
        query = {
            "api_key": self.api_key,
            "language": settings.TMDB_LANGUAGE,
            **{k: v for k, v in params.items() if v not in (None, "")},
        }
        cache_key = f"{path}:{sorted(query.items())}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
        payload = await fetch_json(f"{self.base}{path}", params=query, timeout=15)
        if payload is not None:
            _cache_set(cache_key, payload)
        return payload

    def _image(self, path: str | None, size: str = "w500") -> str | None:
        if not path:
            return None
        return f"{settings.TMDB_IMAGE_HOST.rstrip('/')}/t/p/{size}{path}"

    def _normalize(self, item: dict[str, Any], media_type: str | None = None) -> dict[str, Any]:
        """把 TMDB 条目转成内部结构。"""
        kind = media_type or item.get("media_type") or (
            MediaType.TV.value if item.get("first_air_date") else MediaType.MOVIE.value
        )
        date = str(item.get("release_date") or item.get("first_air_date") or "")
        year = int(date[:4]) if date[:4].isdigit() else None
        return {
            "tmdb_id": item.get("id"),
            "title": item.get("title") or item.get("name") or "",
            "original_title": item.get("original_title") or item.get("original_name"),
            "year": year,
            "media_type": kind,
            "overview": item.get("overview"),
            "poster": self._image(item.get("poster_path")),
            "backdrop": self._image(item.get("backdrop_path"), "w1280"),
            "vote_average": item.get("vote_average"),
            "genres": [g["name"] for g in item.get("genres", []) if isinstance(g, dict)],
        }

    async def search(
        self, keyword: str, *, media_type: str | None = None, year: int | None = None
    ) -> list[dict[str, Any]]:
        """搜索影视条目。"""
        if not keyword:
            return []
        if media_type == MediaType.MOVIE.value:
            path, extra = "/search/movie", {"year": year}
        elif media_type in (MediaType.TV.value, MediaType.ANIME.value):
            path, extra = "/search/tv", {"first_air_date_year": year}
        else:
            path, extra = "/search/multi", {}

        payload = await self._get(path, query=keyword, **extra)
        if not payload:
            return []
        results = []
        for item in payload.get("results", [])[:20]:
            if item.get("media_type") == "person":
                continue
            results.append(self._normalize(item, media_type))
        return results

    async def recognize(
        self, title: str, *, media_type: str | None = None, year: int | None = None
    ) -> dict[str, Any] | None:
        """识别单个条目（取最匹配的第一条）。"""
        candidates = await self.search(title, media_type=media_type, year=year)
        if not candidates:
            return None
        if year:
            for item in candidates:
                if item.get("year") == year:
                    return item
        return candidates[0]

    async def detail(self, tmdb_id: int, media_type: str) -> dict[str, Any] | None:
        """获取详情（含季信息）。"""
        path = (
            f"/movie/{tmdb_id}"
            if media_type == MediaType.MOVIE.value
            else f"/tv/{tmdb_id}"
        )
        payload = await self._get(path, append_to_response="credits")
        if not payload:
            return None
        data = self._normalize(payload, media_type)
        data["imdb_id"] = payload.get("imdb_id")
        # NFO 刮削需要制作公司与演职员，顺手带出来（append_to_response 省一次请求）
        data["studios"] = [
            company["name"]
            for company in payload.get("production_companies", [])
            if isinstance(company, dict) and company.get("name")
        ]
        data["status"] = payload.get("status")
        data["number_of_episodes"] = payload.get("number_of_episodes")
        credits = payload.get("credits") or {}
        data["actors"] = [
            {
                "name": person.get("name"),
                "role": person.get("character"),
                "thumb": self._image(person.get("profile_path"), "w185"),
            }
            for person in (credits.get("cast") or [])[:15]
            if isinstance(person, dict) and person.get("name")
        ]
        data["directors"] = [
            person["name"]
            for person in (credits.get("crew") or [])
            if isinstance(person, dict)
            and person.get("job") in ("Director", "Series Director")
            and person.get("name")
        ]
        if media_type != MediaType.MOVIE.value:
            data["total_seasons"] = payload.get("number_of_seasons")
            data["seasons"] = [
                {
                    "season_number": season.get("season_number"),
                    "episode_count": season.get("episode_count"),
                    "air_date": season.get("air_date"),
                    "name": season.get("name"),
                }
                for season in payload.get("seasons", [])
            ]
        return data

    async def season_episodes(self, tmdb_id: int, season: int) -> list[dict[str, Any]]:
        """获取某季的分集信息（用于计算缺集与追新）。"""
        payload = await self._get(f"/tv/{tmdb_id}/season/{season}")
        if not payload:
            return []
        return [
            {
                "episode_number": episode.get("episode_number"),
                "name": episode.get("name"),
                "air_date": episode.get("air_date"),
                "overview": episode.get("overview"),
                "still": self._image(episode.get("still_path")),
            }
            for episode in payload.get("episodes", [])
        ]

    async def trending(self, media_type: str = "all", window: str = "week") -> list[dict[str, Any]]:
        """热门榜单（用于发现页）。"""
        payload = await self._get(f"/trending/{media_type}/{window}")
        if not payload:
            return []
        items = []
        for item in payload.get("results", [])[:30]:
            if item.get("media_type") == "person":
                continue
            items.append(self._normalize(item))
        return items

    async def discover(
        self, media_type: str = MediaType.TV.value, **filters: Any
    ) -> list[dict[str, Any]]:
        """按条件发现内容。"""
        path = "/discover/movie" if media_type == MediaType.MOVIE.value else "/discover/tv"
        payload = await self._get(path, sort_by="popularity.desc", **filters)
        if not payload:
            return []
        return [self._normalize(item, media_type) for item in payload.get("results", [])[:30]]

    async def ranking(
        self, source: str, media_type: str = MediaType.TV.value, limit: int = 20
    ) -> list[dict[str, Any]]:
        """榜单数据（供「榜单自动订阅」使用）。

        ``source`` 取值：``tmdb_trending`` / ``tmdb_popular`` / ``tmdb_top_rated``。
        未配置 API Key 时返回空列表（全量降级，不抛错）。
        """
        kind = "movie" if media_type == MediaType.MOVIE.value else "tv"
        if source == "tmdb_popular":
            payload = await self._get(f"/{kind}/popular")
        elif source == "tmdb_top_rated":
            payload = await self._get(f"/{kind}/top_rated")
        else:
            payload = await self._get(f"/trending/{kind}/week")
        if not payload:
            return []
        items = []
        for item in payload.get("results", []):
            if item.get("media_type") == "person":
                continue
            items.append(self._normalize(item, media_type))
            if len(items) >= max(1, limit):
                break
        return items

    async def health_check(self) -> tuple[bool, str]:
        if not self.available:
            return False, "未配置 TMDB_API_KEY"
        payload = await self._get("/configuration")
        if not payload:
            return False, "无法连接 TMDB（检查网络或代理）"
        return True, "连接正常"


#: 全局共享实例
tmdb = TmdbClient()
