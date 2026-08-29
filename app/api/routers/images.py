"""图片代理：解决第三方图床防盗链导致的封面加载失败。

**为什么需要**：豆瓣图床对没有 ``Referer: movie.douban.com`` 的请求返回
**HTTP 418**（实测：裸请求 418，带 Referer 200）。浏览器出于隐私会剥离
跨站 Referer，前端即便加 ``referrerpolicy`` 也拿不到图。所以由后端代拉：
服务端带上正确的 Referer 请求，再把图片字节转发给浏览器。

**安全**：这类"代拉任意 URL"的接口天生有 SSRF 风险，因此这里做了三道约束——

1. **域名白名单**：只允许已知图床，杜绝内网探测（127.0.0.1 / 169.254 等）
2. **只允许 http/https**：挡掉 file:// gopher:// 之类协议
3. **响应必须是图片**：content-type 不是 image/* 直接拒绝，避免被当通用代理

命中的图片带长缓存头，浏览器与 CDN 都不会反复回源。
"""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query, Response

from app.core.logger import get_logger
from app.utils.http import async_client

logger = get_logger(__name__)

router = APIRouter(prefix="/images", tags=["图片代理"])

#: 允许代理的图床域名后缀 -> 请求时使用的 Referer
ALLOWED_HOSTS: dict[str, str] = {
    "doubanio.com": "https://movie.douban.com/",
    "douban.com": "https://movie.douban.com/",
    "hdslb.com": "https://www.bilibili.com/",
    "biliimg.com": "https://www.bilibili.com/",
    "ytimg.com": "https://www.youtube.com/",
    "themoviedb.org": "https://www.themoviedb.org/",
    "tmdb.org": "https://www.themoviedb.org/",
}

#: 单张图上限 8MB：封面不可能更大，超了说明地址不对
MAX_BYTES = 8 * 1024 * 1024

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def resolve_referer(url: str) -> str | None:
    """校验 URL 并返回应使用的 Referer；不在白名单内返回 ``None``。"""
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    host = (parsed.hostname or "").lower()
    if not host:
        return None
    for suffix, referer in ALLOWED_HOSTS.items():
        # 用 == 或 .endswith("." + suffix) 而不是 in：
        # 否则 evil-doubanio.com.attacker.net 也会被放行
        if host == suffix or host.endswith("." + suffix):
            return referer
    return None


@router.get("/proxy", summary="代理拉取封面图（绕过图床防盗链）")
async def proxy(
    url: str = Query(description="图片地址，必须属于白名单图床"),
) -> Response:
    """代拉第三方封面图。

    刻意**不加登录校验**：``<img src>`` 不会带 Authorization 头，
    加了认证图就永远加载不出来。安全性由域名白名单 + 图片类型校验保证，
    该接口不能读取内网也不能当通用代理。
    """
    referer = resolve_referer(url)
    if not referer:
        raise HTTPException(status_code=400, detail="该图片地址不在允许代理的图床白名单内")

    try:
        async with async_client(timeout=15) as client:
            response = await client.get(
                url, headers={"User-Agent": UA, "Referer": referer}
            )
    except Exception as exc:
        logger.warning("代理图片失败 %s: %s", url, exc)
        raise HTTPException(status_code=502, detail="图片拉取失败") from exc

    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=f"图床返回 {response.status_code}")

    content_type = str(response.headers.get("content-type") or "")
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=415, detail="目标地址不是图片")

    content = response.content
    if len(content) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="图片过大")

    return Response(
        content=content,
        media_type=content_type,
        headers={
            # 封面基本不变，缓存 7 天，避免反复回源打扰图床
            "Cache-Control": "public, max-age=604800",
        },
    )
