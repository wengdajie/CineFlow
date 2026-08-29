"""yt-dlp 公开视频下载器（v1.6.0 任务 1 回归）。

覆盖三类关键行为：
1. 合规边界（ADR-24）——付费墙正片播放页必须在入口被拒，且不发任何网络请求；
2. 站点风控——B 站实测会对连续解析回 HTTP 412，需要能识别限流并缓存成功结果；
3. 环境降级——缺 ffmpeg 时不能生成需要合并的 format，否则下载到一半失败。

这些用例全部离线，不依赖网络，也不依赖真的装了 ffmpeg。
"""

import asyncio
import time

import pytest

from app.providers.downloader import ytdlp as mod
from app.providers.downloader.ytdlp import (
    YtDlpDownloader,
    build_headers,
    guess_site,
    is_blocked,
    is_rate_limited,
)


def _dl(**options):
    return YtDlpDownloader({"name": "yt-dlp", "options": options})


class TestBlockedPaywall:
    """付费墙判定：正片播放页拒绝，公开内容放行。"""

    @pytest.mark.parametrize(
        "url",
        [
            "https://v.qq.com/x/cover/mzc00200abc/n0045xyz.html",
            "https://v.qq.com/x/page/abc123.html",
            "https://www.iqiyi.com/v_19rr7f0m0k.html",
            "https://v.youku.com/v_show/id_XNTkzMzQ1.html",
            "https://www.mgtv.com/b/335313/12345678.html",
            "https://www.netflix.com/watch/80100172",
            "https://www.primevideo.com/detail/0ABC",
        ],
    )
    def test_长视频平台正片页被拒绝(self, url):
        blocked, reason = is_blocked(url)
        assert blocked is True
        # 原因必须是给人看的中文，而不是正则或异常
        assert "会员" in reason and "官方客户端" in reason

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.bilibili.com/video/BV1GJ411x7h7",
            "https://b23.tv/abcdefg",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://www.douyin.com/video/7300000000000000000",
            "https://www.tiktok.com/@user/video/7300000000000000000",
            # 平台首页/非正片路径不该被一刀切封掉
            "https://v.qq.com/",
            "https://www.iqiyi.com/",
        ],
    )
    def test_公开内容与平台首页放行(self, url):
        blocked, reason = is_blocked(url)
        assert blocked is False
        assert reason == ""

    def test_空地址不崩(self):
        assert is_blocked("") == (False, "")
        assert is_blocked(None) == (False, "")


class TestGuessSite:
    """站点识别只用于界面提示，识别不出要有兜底而不是报错。"""

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://www.bilibili.com/video/BV1xx", "哔哩哔哩"),
            ("https://b23.tv/xyz", "哔哩哔哩"),
            ("https://youtu.be/abc", "YouTube"),
            ("https://www.youtube.com/watch?v=abc", "YouTube"),
            ("https://www.douyin.com/video/1", "抖音"),
            ("https://www.tiktok.com/@a/video/1", "TikTok"),
            ("https://www.acfun.cn/v/ac1", "AcFun"),
            ("https://example.org/video/1", "其他站点"),
            ("", "其他站点"),
        ],
    )
    def test_识别站点展示名(self, url, expected):
        assert guess_site(url) == expected


class TestBuildHeaders:
    """没有浏览器请求头，B 站会直接回 412，所以头必须带上。"""

    def test_默认带UA和语言(self):
        headers = build_headers("https://example.org/v/1")
        assert "Chrome" in headers["User-Agent"]
        assert headers["Accept-Language"].startswith("zh-CN")
        # 非特殊站点不该乱加 Referer
        assert "Referer" not in headers

    def test_B站补Referer(self):
        assert build_headers("https://www.bilibili.com/video/BV1xx")["Referer"] == (
            "https://www.bilibili.com/"
        )
        assert build_headers("https://b23.tv/xyz")["Referer"] == "https://www.bilibili.com/"

    def test_抖音补Referer(self):
        assert build_headers("https://www.douyin.com/video/1")["Referer"] == (
            "https://www.douyin.com/"
        )

    def test_返回的是副本不污染全局(self):
        headers = build_headers("https://www.bilibili.com/video/BV1xx")
        headers["User-Agent"] = "changed"
        assert mod.DEFAULT_HEADERS["User-Agent"] != "changed"
        assert "Referer" not in mod.DEFAULT_HEADERS


class TestRateLimited:
    """区分"过会儿再试有用"与"重试一万次也没用"。"""

    @pytest.mark.parametrize(
        "message",
        [
            "HTTP Error 412: Precondition Failed",
            "HTTP Error 429: Too Many Requests",
            "unable to download: too many requests",
        ],
    )
    def test_限流类错误应重试(self, message):
        assert is_rate_limited(message) is True

    @pytest.mark.parametrize(
        "message",
        [
            "HTTP Error 404: Not Found",
            "Video unavailable: private video",
            "Unsupported URL",
        ],
    )
    def test_确定性失败不该重试(self, message):
        assert is_rate_limited(message) is False


class TestProbeCache:
    """反复点「解析」是常态，缓存既提速也避开风控。"""

    def setup_method(self):
        mod._PROBE_CACHE.clear()

    teardown_method = setup_method

    def test_成功结果会被缓存(self):
        payload = {"success": True, "title": "t"}
        mod._cache_put("u1", payload)
        assert mod._cache_get("u1") == payload

    def test_失败结果不缓存(self):
        # 失败大多是临时风控，缓存下来会让用户误以为永久坏了
        mod._cache_put("u2", {"success": False, "message": "限流"})
        assert mod._cache_get("u2") is None

    def test_过期即失效(self):
        mod._PROBE_CACHE["u3"] = (time.time() - 1, {"success": True})
        assert mod._cache_get("u3") is None
        assert "u3" not in mod._PROBE_CACHE

    def test_未命中返回None(self):
        assert mod._cache_get("never-seen") is None


class TestProbeGuards:
    """probe 的前置校验不应该联网。"""

    def test_付费墙地址直接返回原因(self, monkeypatch):
        def _boom(*args, **kwargs):  # pragma: no cover - 不该被调用
            raise AssertionError("付费墙地址不应该发起解析")

        monkeypatch.setattr(asyncio, "to_thread", _boom)
        result = asyncio.run(_dl().probe("https://www.iqiyi.com/v_19rr7f0m0k.html"))
        assert result["success"] is False
        assert "会员" in result["message"]

    def test_缓存命中不再联网(self, monkeypatch):
        mod._PROBE_CACHE.clear()
        cached = {"success": True, "title": "缓存命中"}
        mod._cache_put("https://www.bilibili.com/video/BV1xx", cached)

        def _boom(*args, **kwargs):  # pragma: no cover - 不该被调用
            raise AssertionError("命中缓存时不应该联网")

        monkeypatch.setattr(asyncio, "to_thread", _boom)
        result = asyncio.run(_dl().probe("https://www.bilibili.com/video/BV1xx"))
        assert result == cached
        mod._PROBE_CACHE.clear()

    def test_限流耗尽重试给可操作提示(self, monkeypatch):
        mod._PROBE_CACHE.clear()
        calls = {"n": 0}

        async def _fake_to_thread(func, *args, **kwargs):
            calls["n"] += 1
            raise RuntimeError("HTTP Error 412: Precondition Failed")

        async def _no_sleep(_seconds):
            return None

        monkeypatch.setattr(asyncio, "to_thread", _fake_to_thread)
        monkeypatch.setattr(asyncio, "sleep", _no_sleep)
        result = asyncio.run(
            _dl(probe_retries=3).probe("https://www.bilibili.com/video/BV1xx")
        )
        assert result["success"] is False
        assert "限流" in result["message"]
        assert calls["n"] == 3, "限流应重试到配置次数"

    def test_确定性失败只试一次(self, monkeypatch):
        mod._PROBE_CACHE.clear()
        calls = {"n": 0}

        async def _fake_to_thread(func, *args, **kwargs):
            calls["n"] += 1
            raise RuntimeError("HTTP Error 404: Not Found")

        monkeypatch.setattr(asyncio, "to_thread", _fake_to_thread)
        result = asyncio.run(
            _dl(probe_retries=3).probe("https://www.bilibili.com/video/BV1xx")
        )
        assert result["success"] is False
        assert "404" in result["message"]
        assert calls["n"] == 1, "确定性失败不该浪费重试"


class TestYdlOptions:
    """参数构造：画质上限、限速、无 ffmpeg 降级。"""

    def test_默认限制1080并优先合并流(self):
        options = _dl()._ydl_options("/tmp/dl")
        assert "bestvideo[height<=1080]" in options["format"]
        assert options["noplaylist"] is True

    def test_可自定义画质上限(self):
        options = _dl(max_height=720)._ydl_options("/tmp/dl")
        assert "height<=720" in options["format"]

    def test_显式format优先(self):
        options = _dl(format="worst")._ydl_options("/tmp/dl")
        assert options["format"] == "worst"

    def test_限速单位为KB每秒(self):
        options = _dl(rate_limit=500)._ydl_options("/tmp/dl")
        assert options["ratelimit"] == 500 * 1024

    def test_不限速时不写ratelimit(self):
        assert "ratelimit" not in _dl()._ydl_options("/tmp/dl")

    def test_请求头随目标站点变化(self):
        options = _dl()._ydl_options(
            "/tmp/dl", url="https://www.bilibili.com/video/BV1xx"
        )
        assert options["http_headers"]["Referer"] == "https://www.bilibili.com/"

    def test_无ffmpeg时退回单文件流(self, monkeypatch):
        # 需要合并的 format 在没有 ffmpeg 的机器上会下到一半失败，
        # 宁可画质低一档也要能拿到完整文件
        monkeypatch.setattr(mod.shutil, "which", lambda _name: None)
        options = _dl()._ydl_options("/tmp/dl")
        assert "+" not in options["format"]
        assert not options.get("postprocessors")


class TestTaskRegistry:
    """任务表必须跨实例共享：每次 API 请求都会 new 一个 Provider。"""

    def test_任务表是类级共享(self):
        assert _dl()._tasks is _dl()._tasks

    def test_进度回调收敛下载状态(self):
        progress = mod._Progress("t1", "https://example.org/v/1")
        progress.hook(
            {
                "status": "downloading",
                "total_bytes": 1000,
                "downloaded_bytes": 250,
                "speed": 1024,
                "eta": 30,
            }
        )
        assert progress.progress == pytest.approx(0.25)
        assert progress.speed == 1024
        assert progress.to_state().downloaded == 250

    def test_下载完成不提前报100(self):
        # finished 只代表"这一路流下完了"，合并/写元数据还在后面
        progress = mod._Progress("t2", "https://example.org/v/1")
        progress.hook({"status": "finished", "filename": "/data/a.mp4"})
        assert progress.progress < 1.0
        assert progress.to_state().content_path == "/data/a.mp4"

    def test_总大小未知时不算出错误进度(self):
        progress = mod._Progress("t3", "https://example.org/v/1")
        progress.hook({"status": "downloading", "downloaded_bytes": 100})
        assert progress.progress == 0.0
