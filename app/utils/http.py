"""HTTP 客户端封装。"""

from __future__ import annotations

import ssl
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


class FetchError(Exception):
    """HTTP 取回失败，带上能直接展示给用户的原因。

    **为什么需要它**：``fetch_text``/``fetch_json`` 失败一律返回 ``None``，
    调用方普遍写成 ``if not text: return []``。于是"服务已经死了"和
    "站点确实没有这个片"在上层完全同形，搜索诊断只能笼统报
    「连通正常，但没有匹配结果」。

    实测坑（v1.19.0）：Jackett 进程挂掉后端口返回 **502**，
    界面上却显示「Jackett 聚合：连通正常，但没有匹配结果」——
    用户会去反复换关键词，而真正该做的是把 Jackett 拉起来。
    这正是 ADR-20 那类"结果变少但没人知道为什么"的静默故障。

    因此对**需要区分**的调用点（索引器搜索）提供
    :func:`fetch_text_result`，把状态码/异常类型如实带上来。
    """

    def __init__(self, message: str, *, status: int | None = None, kind: str = "error") -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        #: ``error`` 网络/服务异常 · ``http`` 服务返回了非 2xx
        self.kind = kind


def describe_http_error(exc: Exception) -> tuple[str, int | None, str]:
    """把 httpx 异常翻译成"用户能照着做下一步"的中文原因。

    分类刻意做细：连不上、超时、TLS、DNS、非 2xx 的下一步动作完全不同，
    统一报"请求失败"等于让用户瞎试。
    """
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        hint = {
            401: "需要认证（检查 API Key / Cookie）",
            403: "被站点拒绝（可能触发反爬或需要 Cookie）",
            404: "地址不存在（检查 URL 路径是否写对）",
            429: "请求过于频繁，被限流",
            500: "站点内部错误",
            502: "网关错误——服务通常没有运行",
            503: "服务不可用（可能正在重启或过载）",
            504: "网关超时",
        }.get(code, "")
        text = f"HTTP {code}" + (f"：{hint}" if hint else "")
        return text, code, "http"
    if isinstance(exc, httpx.ConnectTimeout):
        return "连接超时（地址不可达或被墙）", None, "error"
    if isinstance(exc, httpx.ReadTimeout):
        return "读取超时（站点响应过慢）", None, "error"
    if isinstance(exc, httpx.ConnectError):
        detail = str(exc)
        if "Name or service not known" in detail or "getaddrinfo" in detail:
            return "域名解析失败（站点可能已下线或换域名）", None, "error"
        return "无法建立连接（服务未运行 / 端口不通）", None, "error"
    if isinstance(exc, ssl.SSLError) or "SSL" in type(exc).__name__:
        return f"TLS 握手失败（{type(exc).__name__}）", None, "error"
    if isinstance(exc, httpx.TooManyRedirects):
        return "重定向过多（站点可能要求登录）", None, "error"
    return f"{type(exc).__name__}: {str(exc)[:120]}", None, "error"


async def fetch_text_result(
    url: str,
    *,
    method: str = "GET",
    params: dict[str, Any] | None = None,
    data: Any = None,
    json_body: Any = None,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
    encoding: str | None = None,
) -> str:
    """与 :func:`fetch_text` 相同，但**失败时抛** :class:`FetchError`。

    给「必须区分坏了 / 没有」的调用方用（见 :class:`FetchError` 文档）。
    ``fetch_text`` 保持原样返回 ``None``，避免改动 19 处既有调用点。
    """
    try:
        async with async_client(timeout=timeout, headers=headers) as client:
            response = await client.request(
                method, url, params=params, data=data, json=json_body
            )
            response.raise_for_status()
            if encoding:
                return response.content.decode(encoding, errors="replace")
            return response.text
    except Exception as exc:
        message, status, kind = describe_http_error(exc)
        logger.warning("HTTP 文本请求失败 %s: %s", url, message)
        raise FetchError(message, status=status, kind=kind) from exc


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
