"""HTTP 客户端封装。"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger(__name__)


def normalize_endpoint(raw: str | None, *, default: str = "", scheme: str = "http") -> str:
    """把用户填的下载器/服务地址整理成 httpx 能用的绝对 URL。

    **为什么需要它**：实测三个下载器（qB/TR/aria2）都直接把
    ``config["url"]`` 拼进请求，于是用户两种极常见的输入会让调用必然失败：

    * 漏掉协议 —— ``127.0.0.1:8080`` → httpx 抛
      "Request URL is missing an 'http://' or 'https://' protocol"，
      日志里只留一句"连接失败"，用户完全看不出是自己少打了 7 个字符；
    * 从别处复制带上首尾空格 —— ``" http://x:8080 "`` 同样报缺协议
      （空格让 httpx 认不出 scheme），这个更隐蔽，因为界面上"看着是对的"。

    统一在这里兜住：去空白（含中文全角空格）、补协议、去尾部斜杠。
    ``//host:port`` 这种省略协议的写法也一并补全。
    """
    text = str(raw or "").strip().strip("\u3000")
    if not text:
        return default
    if text.startswith("//"):  # 协议相对写法，补成 http
        text = f"{scheme}:{text}"
    if "://" not in text:
        text = f"{scheme}://{text}"
    return text.rstrip("/")


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
    encoding: str | None = None,
) -> str | None:
    """请求并返回文本，失败返回 ``None``。

    ``encoding``：强制指定响应编码。很多老牌中文资源站是 GB2312/GBK 却
    不在响应头里声明，httpx 会按 UTF-8 解出乱码，这时必须显式指定。
    """
    try:
        async with async_client(timeout=timeout, headers=headers) as client:
            response = await client.request(
                method, url, params=params, data=data, json=json_body
            )
            response.raise_for_status()
            if encoding:
                # 用 errors="replace" 而不是抛异常：个别字符解不出来
                # 也不该让整页解析失败
                return response.content.decode(encoding, errors="replace")
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
