"""HTTP 客户端封装。"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger(__name__)


def build_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    """默认请求头。"""
    headers = {"User-Agent": settings.USER_AGENT, "Accept": "*/*"}
    if extra:
        headers.update({k: v for k, v in extra.items() if v})
    return headers


def async_client(
    timeout: float | None = None,
    headers: dict[str, str] | None = None,
    follow_redirects: bool = True,
) -> httpx.AsyncClient:
    """创建异步客户端（含代理与默认头）。"""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout or settings.SEARCH_TIMEOUT),
        headers=build_headers(headers),
        follow_redirects=follow_redirects,
        proxy=settings.HTTP_PROXY or None,
        verify=False,
    )


async def fetch_text(
    url: str,
    *,
    method: str = "GET",
    params: dict[str, Any] | None = None,
    data: Any = None,
    json_body: Any = None,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
) -> str | None:
    """请求并返回文本，失败返回 ``None``。"""
    try:
        async with async_client(timeout=timeout, headers=headers) as client:
            response = await client.request(
                method, url, params=params, data=data, json=json_body
            )
            response.raise_for_status()
            return response.text
    except Exception as exc:
        logger.warning("HTTP 文本请求失败 %s: %s", url, exc)
        return None


async def fetch_json(
    url: str,
    *,
    method: str = "GET",
    params: dict[str, Any] | None = None,
    data: Any = None,
    json_body: Any = None,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
) -> Any | None:
    """请求并返回 JSON，失败返回 ``None``。"""
    try:
        async with async_client(timeout=timeout, headers=headers) as client:
            response = await client.request(
                method, url, params=params, data=data, json=json_body
            )
            response.raise_for_status()
            return response.json()
    except Exception as exc:
        logger.warning("HTTP JSON 请求失败 %s: %s", url, exc)
        return None
