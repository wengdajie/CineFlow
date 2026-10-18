"""HTTP 客户端封装。"""

from __future__ import annotations

import ssl
from typing import Any

import httpx

from app.core.config import settings
from app.core.logger import get_logger
from app.core.secrets import redact_url

logger = get_logger(__name__)

#: 伪装成普通浏览器的 UA。部分站点（BT4G/Bitsearch）对脚本 UA 会 403 或
#: 上人机验证，对公开 JSON API（apibay/therarbg/knaben）带上也无害。
#: 与 ``settings.USER_AGENT``（带 CineFlow 标识）分开：标识版用于友好站，
#: 匿名浏览器版用于会按 UA 卡人的站。
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)



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
    verify: bool = True,
) -> httpx.AsyncClient:
    """创建异步客户端（含代理与默认头）。"""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout or settings.SEARCH_TIMEOUT),
        headers=build_headers(headers),
        follow_redirects=follow_redirects,
        proxy=settings.HTTP_PROXY or None,
        verify=verify,
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


#: Cloudflare 的「浏览器校验」拦截页特征。
#:
#: **为什么需要单独识别**：这种页面回的是 **HTTP 200**，内容是一段 JS，
#: 靠状态码完全看不出问题。下游拿它去解析只会得到「0 条」或
#: 「不是合法 RSS」，两者都会把「站点上了人机验证」误报成
#: 「这个站没有这部片」或「站点改版了」—— 处置动作完全不同（ADR-82）：
#: 改版要改解析代码，人机验证只能换镜像/等它撤下，改代码没有用。
#:
#: 实测（2026-09-04，BT4G 全部 5 个镜像域名）：
#: ``<title>Security Check</title>`` + ``cf_chl_out`` cookie + rocket-loader，
#: 长度稳定在 2441~2444 字节。
#:
#: 按 ADR-34：**只识别、不绕过**。伪造 ``cf_chl_out`` 属于绕验证码，不做。
_CF_CHALLENGE_MARKERS = (
    "cf_chl_out",
    "<title>Security Check</title>",
    "Just a moment",
    "cf-browser-verification",
    "/cdn-cgi/challenge-platform/",
)


def is_cf_challenge(text: str | None) -> bool:
    """响应体是否是 Cloudflare/类似的人机校验拦截页。

    只在**短文档**里判定：真实的搜索结果页也可能顺带提到
    ``/cdn-cgi/`` 之类的静态资源路径，而拦截页实测都在 3KB 以内。
    不设这道长度闸门会把正常的大页面误判成被拦（假阳性比漏判更糟：
    会把好站点标成坏的）。
    """
    body = str(text or "")
    if not body or len(body) > 8192:
        return False
    return any(marker in body for marker in _CF_CHALLENGE_MARKERS)


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
        logger.warning("HTTP 文本请求失败 %s: %s", redact_url(url), message)
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
        logger.warning("HTTP 文本请求失败 %s: %s", redact_url(url), describe_http_error(exc)[0])
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
    verify: bool = True,
    allow_http_error: bool = False,
) -> Any | None:
    """请求并返回 JSON，失败返回 ``None``。"""
    try:
        async with async_client(timeout=timeout, headers=headers, verify=verify) as client:
            response = await client.request(
                method, url, params=params, data=data, json=json_body
            )
            if not allow_http_error:
                response.raise_for_status()
            return response.json()
    except Exception as exc:
        logger.warning("HTTP JSON 请求失败 %s: %s", redact_url(url), describe_http_error(exc)[0])
        return None
