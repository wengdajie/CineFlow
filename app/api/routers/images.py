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

**豆瓣坏镜像**：豆瓣把封面随机分发到 ``img1/img2/img3/img9.doubanio.com``
四个镜像，但实测 ``img9`` 已长期损坏——**即便带正确 Referer 也返回
HTTP 200 + text/html 的反爬脚本页**（不是图片），而同一张图换 img1/2/3
就正常返回 image/jpeg。约 1/4 的封面会被分到 img9，导致前端大面积裂图。
因此这里对 doubanio 域做**镜像轮换重试**：img9 直接跳过，且任一镜像返回
非图片内容时自动换下一个，全部失败才报错。

**连接层抖动重试**：实测 ``lain.bgm.tv`` 的 TLS 连接会被**间歇性掐断**
（同一 URL 三连的结果是 ``EXC / EXC / 200``，报错为
``SSL: UNEXPECTED_EOF_WHILE_READING``）。镜像轮换只救得了豆瓣，
其它图床只有一个候选，一次抖动就 502、前端退占位 —— 表现为封面随机裂图。
所以每个候选地址会重试 :data:`MAX_ATTEMPTS_PER_CANDIDATE` 次，
但**只针对连接层异常**：HTTP 403/404 这类重试多少次结果都一样。
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
    # Bangumi 番剧封面图床（放送日历用）。注意它返回的原始地址是 http://，
    # 已在 metadata/bangumi.py 升级成 https；这里的白名单按主机名匹配，与协议无关。
    "bgm.tv": "https://bangumi.tv/",
    "themoviedb.org": "https://www.themoviedb.org/",
    "tmdb.org": "https://www.themoviedb.org/",
    # 网盘扫码登录的二维码图：这些接口同样校验 Referer，且二维码是一次性的，
    # 让浏览器直连会拿到 403/空图，所以一并走代理。
    "115.com": "https://115.com/",
    "baidu.com": "https://pan.baidu.com/",
}

#: 单张图上限 8MB：封面不可能更大，超了说明地址不对
MAX_BYTES = 8 * 1024 * 1024

#: 豆瓣可用图床镜像，按实测可靠性排序。
#: 刻意**不含 img9**：该镜像对任何请求都返回 200 + text/html 反爬页，属于坏节点。
DOUBAN_MIRRORS = ("img3", "img1", "img2")

#: 被判定为坏节点的豆瓣镜像主机名前缀
DOUBAN_BAD_MIRRORS = ("img9",)

#: 单个候选地址的最大尝试次数（含首次）。
#:
#: **为什么必须重试**：实测 ``lain.bgm.tv``（Bangumi 图床）的 TLS 连接会
#: **间歇性被掐断** —— 同一个 URL 连续请求三次，结果是
#: ``EXC / EXC / 200``、``200 / EXC / 200``，报错固定为
#: ``SSL: UNEXPECTED_EOF_WHILE_READING``（握手中途对端直接断开，
#: 典型的链路干扰而非图床故障，因为紧接着重试就成功了）。
#:
#: 而这里的镜像轮换只对豆瓣有效（其它图床只有 1 个候选），于是**一次网络抖动
#: 就直接 502**，前端退占位 → 表现为「新番日历随机几张裂图，刷新一下换成
#: 另外几张裂」。加上重试后，抖动被吸收在后端，用户看不见。
#:
#: 只重试**连接层异常**，不重试 HTTP 状态码错误：403/404 重试多少次都一样，
#: 白等而已。
MAX_ATTEMPTS_PER_CANDIDATE = 3


def douban_candidates(url: str) -> list[str]:
    """把一个豆瓣图片地址展开成「按优先级排队的候选镜像列表」。

    非豆瓣地址原样返回单元素列表（其它图床没有镜像可换）。
    豆瓣地址则把主机名替换成 :data:`DOUBAN_MIRRORS` 里的各个镜像：
    这样即使原始地址落在坏节点 img9 上，也能靠 img3/img1/img2 取到同一张图
    （豆瓣各镜像共享同一套路径，路径不变只换主机名即可）。
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host.endswith("doubanio.com"):
        return [url]
    # 只替换形如 imgN.doubanio.com 的主机名，其它形态（如自定义子域）不动
    parts = host.split(".")
    if len(parts) < 3:
        return [url]
    candidates: list[str] = []
    for mirror in DOUBAN_MIRRORS:
        new_host = ".".join([mirror, *parts[1:]])
        candidates.append(parsed._replace(netloc=new_host).geturl())
    # 原地址若本身就是好镜像，它已在候选里；若是坏镜像则被彻底排除。
    # 兜底：候选为空时退回原地址，保证不会把功能整个弄没。
    return candidates or [url]

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

    # 豆瓣会把封面随机分到 4 个镜像，其中 img9 是坏节点（返回反爬 HTML 而非图片），
    # 所以这里按候选镜像依次尝试，任一成功即返回。非豆瓣地址只有 1 个候选，行为不变。
    candidates = douban_candidates(url)
    content = b""
    content_type = ""
    last_error = ""
    for candidate in candidates:
        response = None
        # 连接层抖动（实测 bgm.tv 的 TLS 会被间歇掐断）重试几次；
        # 拿到 HTTP 响应就跳出——状态码错误重试没有意义。
        for attempt in range(MAX_ATTEMPTS_PER_CANDIDATE):
            try:
                async with async_client(timeout=15) as client:
                    response = await client.get(
                        candidate, headers={"User-Agent": UA, "Referer": referer}
                    )
                break
            except Exception as exc:
                # 异常信息里带上第几次，便于从日志判断是"一直连不上"还是"抖了一下"
                last_error = f"请求异常（第 {attempt + 1} 次）{exc}"
                response = None
        if response is None:
            continue

        if response.status_code != 200:
            last_error = f"图床返回 {response.status_code}"
            continue

        candidate_type = str(response.headers.get("content-type") or "")
        # 仍然坚持「必须是图片」——这是 SSRF 三道防线之一，不能因为要凑成功率而放宽。
        # 只是把「这一个镜像不是图片」从直接失败改成换下一个镜像重试。
        if not candidate_type.startswith("image/"):
            last_error = "目标地址不是图片"
            continue

        if len(response.content) > MAX_BYTES:
            raise HTTPException(status_code=413, detail="图片过大")

        content = response.content
        content_type = candidate_type
        break

    if not content:
        # 全部候选都失败：沿用原来的语义化状态码，前端 onerror 会退占位色块
        logger.warning("代理图片失败 %s: %s", url, last_error)
        if last_error == "目标地址不是图片":
            raise HTTPException(status_code=415, detail="目标地址不是图片")
        raise HTTPException(status_code=502, detail=last_error or "图片拉取失败")

    return Response(
        content=content,
        media_type=content_type,
        headers={
            # 封面基本不变，缓存 7 天，避免反复回源打扰图床
            "Cache-Control": "public, max-age=604800",
        },
    )
