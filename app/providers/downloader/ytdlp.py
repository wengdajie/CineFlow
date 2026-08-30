"""yt-dlp 下载器：抓取公开视频页面（B 站 / YouTube / 抖音 / TikTok 等 1700+ 站点）。

与 qBittorrent/aria2 的区别：那些接收 magnet/torrent，本 Provider 接收
**网页地址**，由 yt-dlp 解析出可下载的音视频流。适用场景是把公开的
纪录片、公开课、UP 主更新、自己发布的内容收进媒体库。

为什么用 yt-dlp：它是该领域事实标准（GitHub 最高星的同类项目），
维护活跃、抽取器覆盖 1700+ 站点，比自己写各站解析靠谱得多。

明确的边界（见 ADR-24）：
- 只下载**可公开访问**的内容。不实现任何会员/付费内容的解密与绕过，
  不集成"VIP 解析"接口。需要登录才能看的内容，只支持用户提供**自己账号**
  的 cookie 去下载其**有权访问**的内容。
- 默认开启限速与并发上限，避免对站点造成压力。

实现要点：yt-dlp 是同步阻塞库，全部调用放进线程池，避免卡住事件循环。
"""

from __future__ import annotations

import asyncio
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, ClassVar

from app.core.config import settings
from app.core.logger import get_logger
from app.providers.downloader.base import BaseDownloader, TorrentState
from app.providers.registry import register
from app.schemas.enums import TaskStatus

logger = get_logger(__name__)

#: 支持的站点关键字 → 展示名。仅用于界面提示与链接识别，
#: 实际支持范围由 yt-dlp 的抽取器决定（远大于此表）。
KNOWN_SITES = {
    "bilibili": "哔哩哔哩",
    "b23.tv": "哔哩哔哩",
    "youtube": "YouTube",
    "youtu.be": "YouTube",
    "douyin": "抖音",
    "iesdouyin": "抖音",
    "tiktok": "TikTok",
    "acfun": "AcFun",
    "xiaohongshu": "小红书",
    "twitter": "X / Twitter",
    "x.com": "X / Twitter",
    "weibo": "微博",
}

#: 明确拒绝的地址特征：这些是长视频平台的**正片**播放页，
#: 内容基本都需要会员或有区域授权，抓取属于规避付费墙。
#: 与"平台首页/预告/UP 主自制内容"区分开，避免一刀切封掉整个域名。
BLOCKED_PATTERNS = (
    re.compile(r"v\.qq\.com/x/(cover|page)/", re.I),
    re.compile(r"iqiyi\.com/v_", re.I),
    re.compile(r"youku\.com/v_show/", re.I),
    re.compile(r"mgtv\.com/b/", re.I),
    re.compile(r"(netflix|disneyplus|hulu|hbomax|primevideo)\.com", re.I),
)


#: 伪装成常规浏览器的请求头。
#: 不加这些，B 站会对"看起来像脚本"的请求回 HTTP 412 Precondition Failed，
#: 抖音/小红书也有类似风控。这是让**公开**内容能正常取到，
#: 不涉及任何鉴权绕过。
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

#: 各站点需要的额外 Referer（部分站点校验来源页）
SITE_REFERERS = (
    ("bilibili", "https://www.bilibili.com/"),
    ("b23.tv", "https://www.bilibili.com/"),
    ("douyin", "https://www.douyin.com/"),
    ("xiaohongshu", "https://www.xiaohongshu.com/"),
)


def build_headers(url: str) -> dict[str, str]:
    """按目标站点补齐请求头。"""
    headers = dict(DEFAULT_HEADERS)
    lowered = str(url or "").lower()
    for key, referer in SITE_REFERERS:
        if key in lowered:
            headers["Referer"] = referer
            break
    return headers


def guess_site(url: str) -> str:
    """从链接猜测站点展示名（仅用于界面提示）。"""
    lowered = str(url or "").lower()
    for key, label in KNOWN_SITES.items():
        if key in lowered:
            return label
    return "其他站点"


def is_blocked(url: str) -> tuple[bool, str]:
    """判断链接是否指向付费墙内容。

    返回 ``(是否拒绝, 原因)``。这不是"技术上能不能下"，而是**产品上要不要做**：
    正片播放页需要会员，抓取就等于绕过付费，因此在入口直接拒绝。
    """
    for pattern in BLOCKED_PATTERNS:
        if pattern.search(str(url or "")):
            return True, (
                "该地址是长视频平台的正片播放页，内容通常需要会员或有区域授权，"
                "本工具不提供此类抓取。可改用平台官方客户端的离线缓存功能。"
            )
    return False, ""


#: 探测结果缓存 ``{url: (到期时间戳, 结果)}``。
#: 用户在界面上反复点「解析」是常态（改画质、犹豫、手滑），
#: 每次都真去请求既慢又会撞上风控。缓存 5 分钟对用户无感，却能显著减少请求。
_PROBE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_PROBE_TTL = 300.0


def _cache_get(url: str) -> dict[str, Any] | None:
    entry = _PROBE_CACHE.get(url)
    if not entry:
        return None
    expire_at, payload = entry
    if expire_at < time.time():
        _PROBE_CACHE.pop(url, None)
        return None
    return payload


def _cache_put(url: str, payload: dict[str, Any]) -> None:
    # 只缓存成功结果：失败往往是临时风控，缓存下来会让用户以为"永久坏了"
    if payload.get("success"):
        _PROBE_CACHE[url] = (time.time() + _PROBE_TTL, payload)
        if len(_PROBE_CACHE) > 200:
            # 简单清理：丢掉已过期项，避免长跑进程无限增长
            now = time.time()
            for key in [k for k, (exp, _) in _PROBE_CACHE.items() if exp < now]:
                _PROBE_CACHE.pop(key, None)


def is_rate_limited(error: str) -> bool:
    """判断是否是站点风控/限流（而非"视频不存在"这类确定性失败）。

    412/429/403 都属于"现在别来，过会儿再试"，重试有意义；
    404/私有视频重试一万次也没用，不该浪费时间和请求。
    """
    lowered = str(error).lower()
    return any(code in lowered for code in ("412", "429", "too many requests", "precondition failed"))


class _Progress:
    """把 yt-dlp 的进度回调收敛成任务状态快照。"""

    def __init__(self, task_id: str, url: str) -> None:
        self.task_id = task_id
        self.url = url
        self.title = ""
        self.status = TaskStatus.PENDING.value
        self.progress = 0.0
        self.size = 0
        self.downloaded = 0
        self.speed = 0
        self.eta = 0
        self.file_path = ""
        self.error: str | None = None
        self.updated_at = time.time()

    def hook(self, payload: dict[str, Any]) -> None:
        """yt-dlp progress_hooks 回调（在工作线程里执行）。"""
        state = payload.get("status")
        if state == "downloading":
            self.status = TaskStatus.DOWNLOADING.value
            total = payload.get("total_bytes") or payload.get("total_bytes_estimate") or 0
            done = payload.get("downloaded_bytes") or 0
            self.size = int(total or 0)
            self.downloaded = int(done)
            self.speed = int(payload.get("speed") or 0)
            self.eta = int(payload.get("eta") or 0)
            if total:
                self.progress = min(done / float(total), 0.999)
        elif state == "finished":
            # finished 表示"这一路流下完了"，合并/转码可能还在后面
            self.progress = 0.999
            self.downloaded = self.size or self.downloaded
            self.file_path = str(payload.get("filename") or self.file_path)
        self.updated_at = time.time()

    def to_state(self) -> TorrentState:
        return TorrentState(
            external_id=self.task_id,
            name=self.title or self.url,
            status=self.status,
            progress=round(self.progress, 4),
            size=self.size,
            downloaded=self.downloaded,
            speed=self.speed,
            eta=self.eta,
            save_path=str(Path(self.file_path).parent) if self.file_path else "",
            content_path=self.file_path,
            files=[self.file_path] if self.file_path else [],
            error=self.error,
        )


@register
class YtDlpDownloader(BaseDownloader):
    """yt-dlp（公开视频页面下载：B 站 / YouTube / 抖音 / TikTok 等）。"""

    name = "ytdlp"
    display_name = "yt-dlp 视频下载"

    #: 进程内任务表。yt-dlp 没有常驻服务，任务状态只能自己记。
    #: 重启即丢失，因此上层（download 服务）落库保存关键信息。
    #: 刻意做成类级共享：每次请求都会 new 一个 Provider 实例，
    #: 若挂在实例上，下一次查询就找不到上一次创建的任务了。
    _tasks: ClassVar[dict[str, _Progress]] = {}
    _futures: ClassVar[dict[str, asyncio.Task[Any]]] = {}

    @property
    def available(self) -> bool:
        """依赖是否就绪。缺依赖时给出可操作提示，而不是抛栈。"""
        try:
            import yt_dlp  # noqa: F401
        except ImportError:
            return False
        return True

    def _ydl_options(
        self,
        save_path: str,
        cookie: str | None = None,
        url: str = "",
        video_format: str | None = None,
    ) -> dict[str, Any]:
        """构造 yt-dlp 参数。

        画质上限与限速可在站点 options 里配；默认给保守值，
        既避免把 NAS 带宽占满，也降低对站点的压力。

        ``video_format`` 是**界面上用户点选的那一档画质**（probe 返回的
        ``format_id``），优先级最高——用户明确挑了 1080p 就不该被站点
        配置的 ``max_height`` 再压一次。它后面接 ``+bestaudio`` 是因为
        YouTube 的高清流是视频/音频分离的，只给视频 id 会下到无声画面。
        """
        picked = str(video_format or "").strip()
        if picked:
            # 分离流需要合并音轨；万一该 id 本身就是合并流，
            # yt-dlp 会因 "+bestaudio" 找不到而回退到后面的备选。
            fmt = f"{picked}+bestaudio/{picked}/best"
        else:
            fmt = str(self.option("format", "") or "").strip()
        if not fmt:
            height = int(self.option("max_height", 1080) or 1080)
            # 优先取"视频+音频分离流再合并"，退化到单文件流
            fmt = f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best"

        options: dict[str, Any] = {
            "format": fmt,
            "outtmpl": str(Path(save_path) / "%(title).150B [%(id)s].%(ext)s"),
            "noprogress": True,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": bool(self.option("no_playlist", True)),
            "ignoreerrors": False,
            "retries": int(self.option("retries", 3) or 3),
            "socket_timeout": int(self.option("socket_timeout", 20) or 20),
            "concurrent_fragment_downloads": int(self.option("fragments", 4) or 4),
            "http_headers": build_headers(url),
            # 写入元数据与缩略图，方便媒体库刮削识别
            "writethumbnail": bool(self.option("write_thumbnail", True)),
            "postprocessors": [
                {"key": "FFmpegMetadata", "add_metadata": True},
            ],
        }

        rate = self.option("rate_limit")
        if rate:
            # 单位 KB/s，避免把上游带宽吃满
            options["ratelimit"] = int(rate) * 1024

        if self.option("write_subtitles", True):
            options["writesubtitles"] = True
            options["subtitleslangs"] = list(
                self.option("subtitle_langs", ["zh-Hans", "zh-CN", "zh", "en"])
            )
            options["ignoreerrors"] = False

        proxy = self.option("proxy") or settings.HTTP_PROXY or None
        if proxy:
            options["proxy"] = str(proxy)

        cookie_file = self.option("cookie_file")
        if cookie_file:
            options["cookiefile"] = str(cookie_file)

        if not shutil.which("ffmpeg"):
            # 没有 ffmpeg 就无法合并分离流，退回单文件格式，
            # 否则会下到一半失败——宁可画质低一档也要能下完。
            # 用户在界面选的画质同样受此限制，这里记一条日志说明为什么没生效。
            if picked:
                logger.warning(
                    "未安装 ffmpeg，无法合并分离流，忽略所选画质 %s 改用单文件最佳",
                    picked,
                )
            options["format"] = "best"
            options["postprocessors"] = []
            options.pop("writethumbnail", None)

        options.update(dict(self.option("ydl_options", {}) or {}))
        return options

    async def probe(self, url: str) -> dict[str, Any]:
        """只解析不下载：拿标题/时长/可用画质，供界面确认。"""
        blocked, reason = is_blocked(url)
        if blocked:
            return {"success": False, "message": reason}
        if not self.available:
            return {"success": False, "message": "未安装 yt-dlp，请执行 pip install yt-dlp"}

        cached = _cache_get(url)
        if cached is not None:
            return cached

        def _extract() -> dict[str, Any]:
            import yt_dlp

            options = {
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "http_headers": build_headers(url),
            }
            proxy = self.option("proxy") or settings.HTTP_PROXY or None
            if proxy:
                options["proxy"] = str(proxy)
            with yt_dlp.YoutubeDL(options) as ydl:
                return ydl.extract_info(url, download=False) or {}

        # B 站等站点对连续请求会回 412 Precondition Failed（实测第 1 次成功、
        # 紧接着的第 2 次就被拒）。这属于"过会儿再试就好"，做几次退避重试；
        # 而"视频不存在/已私有"这类确定性失败不重试，免得白等。
        attempts = int(self.option("probe_retries", 3) or 3)
        last_error = ""
        for attempt in range(max(attempts, 1)):
            try:
                info = await asyncio.to_thread(_extract)
                break
            except Exception as exc:
                last_error = str(exc)
                if not is_rate_limited(last_error) or attempt == attempts - 1:
                    if is_rate_limited(last_error):
                        return {
                            "success": False,
                            "message": "站点限流（HTTP 412/429），请稍等几秒再试；"
                                       "频繁解析同一站点会触发风控",
                        }
                    return {"success": False, "message": f"解析失败：{last_error[:200]}"}
                await asyncio.sleep(1.5 * (attempt + 1))
        else:  # pragma: no cover - 循环必然 break 或 return
            return {"success": False, "message": f"解析失败：{last_error[:200]}"}

        formats = [
            {
                "format_id": item.get("format_id"),
                "ext": item.get("ext"),
                "height": item.get("height"),
                "filesize": item.get("filesize") or item.get("filesize_approx"),
                "note": item.get("format_note"),
            }
            for item in (info.get("formats") or [])
            if item.get("vcodec") not in (None, "none")
        ]
        result = {
            "success": True,
            "title": info.get("title"),
            "uploader": info.get("uploader") or info.get("channel"),
            "duration": info.get("duration"),
            "thumbnail": info.get("thumbnail"),
            "site": guess_site(url) if not info.get("extractor_key") else info["extractor_key"],
            "heights": sorted({f["height"] for f in formats if f.get("height")}, reverse=True),
            "formats": formats[-12:],
        }
        _cache_put(url, result)
        return result

    async def add(
        self,
        link: str,
        *,
        save_path: str | None = None,
        category: str | None = None,
        paused: bool = False,
        cookie: str | None = None,
        video_format: str | None = None,
    ) -> str | None:
        """开始一个下载任务，立即返回任务 ID（后台异步下载）。

        ``video_format`` 供界面「选画质再下载」使用，留空表示按配置自动挑最佳。
        """
        blocked, block_reason = is_blocked(link)
        if blocked:
            logger.warning("拒绝下载付费墙内容：%s（%s）", link, block_reason)
            return None
        if not self.available:
            logger.error("未安装 yt-dlp，无法下载 %s", link)
            return None

        target = Path(save_path or self.default_save_path() or settings.DOWNLOAD_DIR)
        if category:
            target = target / category
        target.mkdir(parents=True, exist_ok=True)

        task_id = f"ytdlp-{uuid.uuid4().hex[:12]}"
        progress = _Progress(task_id, link)
        self._tasks[task_id] = progress
        options = self._ydl_options(
            str(target), cookie, url=link, video_format=video_format
        )

        def _download() -> None:
            import yt_dlp

            options["progress_hooks"] = [progress.hook]
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(link, download=True)
                progress.title = str((info or {}).get("title") or "")
                path = (info or {}).get("requested_downloads") or []
                if path and path[0].get("filepath"):
                    progress.file_path = str(path[0]["filepath"])

        async def _run() -> None:
            try:
                progress.status = TaskStatus.DOWNLOADING.value
                await asyncio.to_thread(_download)
                progress.status = TaskStatus.COMPLETED.value
                progress.progress = 1.0
                logger.info("yt-dlp 下载完成：%s -> %s", link, progress.file_path)
            except asyncio.CancelledError:
                progress.status = TaskStatus.CANCELED.value
                raise
            except Exception as exc:
                progress.status = TaskStatus.FAILED.value
                progress.error = str(exc)[:400]
                logger.error("yt-dlp 下载失败 %s: %s", link, exc)

        self._futures[task_id] = asyncio.create_task(_run())
        return task_id

    async def get(self, external_id: str) -> TorrentState | None:
        progress = self._tasks.get(external_id)
        return progress.to_state() if progress else None

    async def list_tasks(self, category: str | None = None) -> list[TorrentState]:
        return [progress.to_state() for progress in self._tasks.values()]

    async def remove(self, external_id: str, *, delete_files: bool = False) -> bool:
        future = self._futures.pop(external_id, None)
        if future and not future.done():
            future.cancel()
        progress = self._tasks.pop(external_id, None)
        if progress and delete_files and progress.file_path:
            try:
                Path(progress.file_path).unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("删除文件失败 %s: %s", progress.file_path, exc)
        return progress is not None

    async def health_check(self) -> tuple[bool, str]:
        if not self.available:
            return False, "未安装 yt-dlp（pip install yt-dlp）"
        import yt_dlp

        note = "" if shutil.which("ffmpeg") else "；未检测到 ffmpeg，将只下单文件流（画质受限）"
        return True, f"yt-dlp {yt_dlp.version.__version__} 就绪{note}"
