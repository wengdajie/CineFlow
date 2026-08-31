"""内置 AI 站点分析（v1.13.0 需求 4）。

这一组测试钉住的是 **"AI 参与配置"最容易出事的三个地方**：

1. **默认不许外发数据**：开启 AI 意味着把站点页面正文发给第三方模型，
   所以 `AI_ENABLED` 默认 False，未开启时任何入口都必须直接拒绝，
   并且拒绝理由要能指导操作（去哪个页面打开哪个开关）。
2. **模型的输出一律当不可信输入**：它会编造 provider 名、把 options
   给成字符串、confidence 给 3.7、在 JSON 外面裹一层废话。全部要在
   `_normalize` / `extract_json` 拦住——坏配置一旦落库，用户之后每次
   搜索都要为这个搜不到东西的站白等一次超时。
3. **建议 ≠ 可用**：`verify` 必须拿建议真跑一次搜索。"模型说能用"和
   "真能搜到"是两件事。

另外还钉了一条本轮特意修的行为：**敏感项提交空值 = 不修改**。
界面出于脱敏不回显已存的密钥，用户改隔壁的"超时"时这一格天然是空的，
若直接落库就会把 `AI_API_KEY` 洗成空串，表现为"我只改了个超时，
AI 就不工作了"，极难排查。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from app.core.config import settings
from app.db.models import SiteConfig
from app.db.session import session_scope
from app.providers.base import Resource
from app.services import ai_site, config_store, settings_store


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def ai_on(monkeypatch):
    """把 AI 配成"可用"。默认关闭是刻意的，所以要用时必须显式打开。"""
    monkeypatch.setattr(settings, "AI_ENABLED", True)
    monkeypatch.setattr(settings, "AI_BASE_URL", "https://ai.example.com/v1")
    monkeypatch.setattr(settings, "AI_API_KEY", "sk-test-123456")
    monkeypatch.setattr(settings, "AI_MODEL", "gpt-4o-mini")
    monkeypatch.setattr(settings, "AI_TIMEOUT", 5)
    monkeypatch.setattr(settings, "AI_MAX_PAGE_CHARS", 16000)
    monkeypatch.setattr(settings, "AI_TEMPERATURE", 0.0)


def _fake_chat(monkeypatch, handler):
    """替掉 ai_site 里的 async_client：用 MockTransport 在传输层仿真模型接口。

    不 monkeypatch `chat` 本身——那样就把"请求怎么发、错误怎么翻译"
    这段真正容易出错的代码全绕过去了，测试会变成自说自话。
    """
    seen: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(
            {
                "url": str(request.url),
                "auth": request.headers.get("authorization"),
                "body": json.loads(request.content.decode("utf-8")),
            }
        )
        return handler(request)

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("timeout", None)
        kwargs.pop("follow_redirects", None)
        headers = kwargs.pop("headers", None)
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handle), headers=headers
        )

    monkeypatch.setattr(ai_site, "async_client", factory)
    return seen


def _ok_body(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]},
    )


# ============================================================ is_configured
class Test配置校验:
    """不可用时的理由必须能指导操作，而不是只说"未配置"。"""

    def test_默认关闭(self):
        """默认必须是关的：开启等于把站点正文发给第三方模型。"""
        assert settings.AI_ENABLED is False
        ready, reason = ai_site.is_configured()
        assert ready is False
        assert "启用" in reason and "AI" in reason

    def test_缺接口地址(self, ai_on, monkeypatch):
        monkeypatch.setattr(settings, "AI_BASE_URL", "   ")
        ready, reason = ai_site.is_configured()
        assert ready is False
        assert "接口地址" in reason

    def test_缺模型名(self, ai_on, monkeypatch):
        monkeypatch.setattr(settings, "AI_MODEL", "")
        ready, reason = ai_site.is_configured()
        assert ready is False
        assert "模型名" in reason

    def test_本地模型无密钥也算可用(self, ai_on, monkeypatch):
        """Ollama/LM Studio 这类本地模型不校验 key，不能因为空 key 就拦。"""
        monkeypatch.setattr(settings, "AI_BASE_URL", "http://127.0.0.1:11434/v1")
        monkeypatch.setattr(settings, "AI_API_KEY", "")
        ready, reason = ai_site.is_configured()
        assert ready is True
        assert reason == ""


# ==================================================================== describe
class Test配置回显:
    def test_密钥只回显长度不回显内容(self, ai_on):
        data = ai_site.describe()
        text = json.dumps(data, ensure_ascii=False)
        assert "sk-test-123456" not in text, "密钥不能出现在回显里"
        assert data["api_key_set"] is True
        assert str(len("sk-test-123456")) in data["api_key_hint"]

    def test_未配置密钥时提示未配置(self, ai_on, monkeypatch):
        monkeypatch.setattr(settings, "AI_API_KEY", "")
        data = ai_site.describe()
        assert data["api_key_set"] is False
        assert data["api_key_hint"] == "未配置"

    def test_回显方案清单供界面渲染(self, ai_on):
        data = ai_site.describe()
        names = {item["provider"] for item in data["providers"]}
        assert names == set(ai_site.PROVIDER_CHOICES)
        for item in data["providers"]:
            assert item["label"] and item["when"]
            assert isinstance(item["fields"], list)

    def test_方案清单只含真实注册的provider(self):
        """菜单里出现一个没实现的 provider，用户按建议添加就必然搜不到。"""
        from app.providers.registry import get_provider_class, load_builtin_providers

        load_builtin_providers()
        for name in ai_site.PROVIDER_CHOICES:
            assert get_provider_class(name) is not None, name


# ==================================================================== condense
class Test页面压缩:
    def test_去掉script与style(self):
        html = (
            "<html><head><style>.a{color:red}</style></head>"
            "<body><script>var x=1;</script><div class='row'>片名</div></body></html>"
        )
        out = ai_site.condense(html)
        assert "var x=1" not in out and "color:red" not in out
        assert "片名" in out
        assert "<div class='row'>" in out, "结构标签必须留着，模型靠它判断套路"

    def test_大小写与多行的script也要去掉(self):
        html = "<SCRIPT type='text/javascript'>\nvar a=1;\nvar b=2;\n</SCRIPT><p>ok</p>"
        out = ai_site.condense(html)
        assert "var a=1" not in out and "var b=2" not in out
        assert "ok" in out

    def test_超长时头尾都保留(self):
        """只留头部会丢掉分页/脚本线索，只留尾部会丢掉 generator/meta。"""
        html = "<head>GENERATOR_MARK</head>" + ("x" * 5000) + "<foot>TAIL_MARK</foot>"
        out = ai_site.condense(html, limit=600)
        assert "GENERATOR_MARK" in out
        assert "TAIL_MARK" in out
        assert "已截断" in out
        assert len(out) < len(html)

    def test_不超长时原样返回(self):
        out = ai_site.condense("<div>短页面</div>", limit=4000)
        assert out == "<div>短页面</div>"
        assert "已截断" not in out

    def test_默认上限取配置项(self, ai_on, monkeypatch):
        monkeypatch.setattr(settings, "AI_MAX_PAGE_CHARS", 200)
        out = ai_site.condense("y" * 2000)
        assert "已截断" in out
        assert len(out) < 400

    def test_空输入不炸(self):
        assert ai_site.condense(None) == ""
        assert ai_site.condense("") == ""


# ================================================================= extract_json
class Test模型输出解析:
    """模型常无视"不要代码块"的指令，容错是必需项而非锦上添花。"""

    def test_裸JSON(self):
        assert ai_site.extract_json('{"provider":"maccms"}') == {"provider": "maccms"}

    def test_markdown围栏(self):
        """围栏外也带花括号——否则"第一个 { 到最后一个 }"的退化路径会顺手
        截对，测试就管不住"到底剥没剥围栏"（实测过这个假绿）。"""
        text = (
            "按 {配置} 的格式给你：\n"
            '```json\n{"provider": "wp_film", "confidence": 0.8}\n```\n'
            "注意 {} 只是占位符。"
        )
        data = ai_site.extract_json(text)
        assert data["provider"] == "wp_film"
        assert data["confidence"] == 0.8

    def test_无语言标记的围栏(self):
        data = ai_site.extract_json('```\n{"provider":"rss"}\n```')
        assert data["provider"] == "rss"

    def test_前后有废话也能抠出来(self):
        text = '好的，我分析如下。{"provider": "html_generic", "kind": "indexer"} 以上。'
        assert ai_site.extract_json(text)["provider"] == "html_generic"

    def test_完全不是JSON要抛错(self):
        with pytest.raises(ValueError, match="JSON"):
            ai_site.extract_json("我不知道这个站点是什么类型。")

    def test_坏JSON要抛错(self):
        with pytest.raises(ValueError, match="JSON"):
            ai_site.extract_json('{"provider": "maccms", }}}{{')

    def test_返回数组要抛错(self):
        """必须是对象：返回数组时下游取 .get 会 AttributeError。"""
        with pytest.raises(ValueError, match="JSON 对象"):
            ai_site.extract_json('[{"provider":"rss"}]')

    def test_空回复要抛错(self):
        with pytest.raises(ValueError):
            ai_site.extract_json("")


# =================================================================== _normalize
class Test建议规整:
    """模型的输出一律当不可信输入。"""

    def test_编造的provider被拒(self):
        with pytest.raises(ValueError) as err:
            ai_site._normalize({"provider": "super_magic_parser"}, "https://x.com")
        # 报错要把可选清单列出来，否则用户不知道该怎么改
        assert "super_magic_parser" in str(err.value)
        for name in ai_site.PROVIDER_CHOICES:
            assert name in str(err.value)

    def test_provider为空也被拒(self):
        with pytest.raises(ValueError, match="空"):
            ai_site._normalize({"reason": "看不出来"}, "https://x.com")

    def test_provider大小写与空格容错(self):
        data = ai_site._normalize({"provider": "  MacCMS "}, "https://x.com")
        assert data["provider"] == "maccms"
        assert data["provider_label"] == ai_site.PROVIDER_CHOICES["maccms"]["label"]

    def test_options不是字典时兜成空字典(self):
        """模型偶尔把 options 给成字符串，直接塞进 SiteConfig.options 会炸库。"""
        data = ai_site._normalize(
            {"provider": "rss", "options": "rss_url=https://x.com/feed"}, "https://x.com"
        )
        assert data["options"] == {}

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [(3.7, 1.0), (-2, 0.0), ("0.85", 0.85), (None, 0.0), ("很高", 0.0), (0.5, 0.5)],
    )
    def test_置信度裁剪到0到1(self, raw, expected):
        data = ai_site._normalize({"provider": "rss", "confidence": raw}, "https://x.com")
        assert data["confidence"] == pytest.approx(expected)

    def test_kind非法时按provider兜底(self):
        """pan_generic 归 pan，其余归 indexer——kind 错了站点会被搜索链路漏掉。"""
        assert ai_site._normalize({"provider": "pan_generic", "kind": "网盘"}, "u")["kind"] == "pan"
        assert ai_site._normalize({"provider": "rss", "kind": ""}, "u")["kind"] == "indexer"
        assert ai_site._normalize({"provider": "rss", "kind": "PAN"}, "u")["kind"] == "pan"

    def test_长文本被截断(self):
        data = ai_site._normalize(
            {"provider": "rss", "reason": "很" * 900, "notes": "长" * 900}, "u"
        )
        assert len(data["reason"]) == 500
        assert len(data["notes"]) == 500

    def test_保留传入的url(self):
        data = ai_site._normalize({"provider": "rss"}, "https://site.example")
        assert data["url"] == "https://site.example"


# ========================================================================= chat
class Test模型调用:
    def test_未配置时直接拒绝不发请求(self, monkeypatch):
        """AI 没开就不能有任何外发流量——这是"默认不外发数据"的实际保证。"""
        called: list[int] = []

        def factory(*args: Any, **kwargs: Any):
            called.append(1)
            raise AssertionError("未配置时不该创建 HTTP 客户端")

        monkeypatch.setattr(ai_site, "async_client", factory)
        with pytest.raises(ValueError, match="启用"):
            run(ai_site.chat([{"role": "user", "content": "hi"}]))
        assert called == []

    def test_正常调用走chat_completions并带鉴权头(self, ai_on, monkeypatch):
        seen = _fake_chat(monkeypatch, lambda req: _ok_body({"provider": "rss"}))
        content = run(ai_site.chat([{"role": "user", "content": "hi"}]))
        assert json.loads(content)["provider"] == "rss"
        assert seen[0]["url"] == "https://ai.example.com/v1/chat/completions"
        assert seen[0]["auth"] == "Bearer sk-test-123456"
        assert seen[0]["body"]["model"] == "gpt-4o-mini"
        assert seen[0]["body"]["temperature"] == 0.0

    def test_地址末尾多斜杠不会拼出双斜杠(self, ai_on, monkeypatch):
        monkeypatch.setattr(settings, "AI_BASE_URL", "https://ai.example.com/v1///")
        seen = _fake_chat(monkeypatch, lambda req: _ok_body({"provider": "rss"}))
        run(ai_site.chat([{"role": "user", "content": "hi"}]))
        assert seen[0]["url"] == "https://ai.example.com/v1/chat/completions"

    def test_无密钥时不带鉴权头(self, ai_on, monkeypatch):
        """本地模型带一个 "Bearer " 空头，有些实现会直接 401。"""
        monkeypatch.setattr(settings, "AI_API_KEY", "")
        seen = _fake_chat(monkeypatch, lambda req: _ok_body({"provider": "rss"}))
        run(ai_site.chat([{"role": "user", "content": "hi"}]))
        assert seen[0]["auth"] is None

    def test_上游报错要把原文带出来(self, ai_on, monkeypatch):
        """401/模型名写错全靠上游原文定位，吞掉它等于让用户瞎猜。"""
        _fake_chat(
            monkeypatch,
            lambda req: httpx.Response(401, text='{"error":{"message":"Invalid API key"}}'),
        )
        with pytest.raises(ValueError) as err:
            run(ai_site.chat([{"role": "user", "content": "hi"}]))
        assert "401" in str(err.value)
        assert "Invalid API key" in str(err.value)

    def test_没有choices时报错(self, ai_on, monkeypatch):
        _fake_chat(monkeypatch, lambda req: httpx.Response(200, json={"choices": []}))
        with pytest.raises(ValueError, match="没有返回"):
            run(ai_site.chat([{"role": "user", "content": "hi"}]))

    def test_内容为空时报错(self, ai_on, monkeypatch):
        _fake_chat(
            monkeypatch,
            lambda req: httpx.Response(200, json={"choices": [{"message": {"content": "  "}}]}),
        )
        with pytest.raises(ValueError, match="空内容"):
            run(ai_site.chat([{"role": "user", "content": "hi"}]))


# ================================================================= analyze_site
class Test站点分析:
    def test_未启用时不抓页面也不调模型(self, monkeypatch):
        touched: list[str] = []

        async def fake_fetch(url, **kwargs):
            touched.append(url)
            return "<html></html>"

        monkeypatch.setattr(ai_site, "fetch_text", fake_fetch)
        with pytest.raises(ValueError, match="启用"):
            run(ai_site.analyze_site("https://site.example"))
        assert touched == [], "未启用时连页面都不该抓"

    @pytest.mark.parametrize("bad", ["site.example", "ftp://x.com", "  ", "javascript:alert(1)"])
    def test_地址必须是http开头(self, ai_on, bad):
        with pytest.raises(ValueError, match="http"):
            run(ai_site.analyze_site(bad))

    def test_站点打不开时给可行动的理由(self, ai_on, monkeypatch):
        async def fake_fetch(url, **kwargs):
            return None

        monkeypatch.setattr(ai_site, "fetch_text", fake_fetch)
        with pytest.raises(ValueError) as err:
            run(ai_site.analyze_site("https://dead.example"))
        message = str(err.value)
        assert "WAF" in message or "代理" in message

    def test_完整链路并把探测命中一起交给模型(self, ai_on, monkeypatch):
        """探测到的搜索页必须真的进 prompt：只看首页极易判错套路。"""

        async def fake_fetch(url, **kwargs):
            if "vod/search" in url:
                return "<div class='module-search-item'>" + "流浪地球" * 100 + "</div>"
            if "feed=rss2" in url:
                return None  # 不是 WordPress
            return "<html><head><meta name='generator' content='maccms'></head></html>"

        monkeypatch.setattr(ai_site, "fetch_text", fake_fetch)
        seen = _fake_chat(
            monkeypatch,
            lambda req: _ok_body(
                {
                    "provider": "maccms",
                    "kind": "indexer",
                    "confidence": 0.92,
                    "reason": "首页 generator 是 maccms，搜索页有 module-search-item",
                    "options": {"max_items": 8},
                }
            ),
        )
        data = run(ai_site.analyze_site("https://site.example/", keyword="流浪地球"))

        assert data["provider"] == "maccms"
        assert data["confidence"] == pytest.approx(0.92)
        assert data["options"] == {"max_items": 8}
        assert data["probes_hit"] == ["maccms"], "只有 maccms 探测命中"
        assert data["url"] == "https://site.example", "尾斜杠要去掉，否则拼路径出双斜杠"

        prompt = seen[0]["body"]["messages"][-1]["content"]
        assert "module-search-item" in prompt, "命中的搜索页必须进 prompt"
        assert "https://site.example" in prompt
        # 菜单必须给模型看，否则它一定会编造 provider
        for name in ai_site.PROVIDER_CHOICES:
            assert name in prompt

    def test_太短的探测响应不算命中(self, ai_on, monkeypatch):
        """WAF 的 403 短页面/空壳页当成"命中"会把模型带偏。"""

        async def fake_fetch(url, **kwargs):
            if "vod/search" in url or "feed=rss2" in url:
                return "<html>404</html>"
            return "<html>" + "首页" * 200 + "</html>"

        monkeypatch.setattr(ai_site, "fetch_text", fake_fetch)
        _fake_chat(monkeypatch, lambda req: _ok_body({"provider": "html_generic"}))
        data = run(ai_site.analyze_site("https://site.example"))
        assert data["probes_hit"] == []

    def test_模型编造provider时整体失败(self, ai_on, monkeypatch):
        async def fake_fetch(url, **kwargs):
            return "<html>" + "内容" * 200 + "</html>"

        monkeypatch.setattr(ai_site, "fetch_text", fake_fetch)
        _fake_chat(monkeypatch, lambda req: _ok_body({"provider": "ai_universal_v2"}))
        with pytest.raises(ValueError, match="不支持"):
            run(ai_site.analyze_site("https://site.example"))


# ======================================================================= verify
class Test试跑验证:
    """"模型说能用"和"真能搜到"是两件事，verify 做的是后者。"""

    def test_不支持的provider直接返回失败(self):
        result = run(ai_site.verify({"provider": "nope", "url": "https://x.com"}))
        assert result["success"] is False
        assert "nope" in result["message"]

    def test_命中时算通过并回样例(self, monkeypatch):
        async def fake_search(self, keyword, **kwargs):
            return [
                Resource(
                    title=f"{keyword} 2019 1080p",
                    link="magnet:?xt=urn:btih:abc123",
                    site="试跑",
                ),
                Resource(title="第二条", link="magnet:?xt=urn:btih:def456", site="试跑"),
            ]

        from app.providers.indexer.generic_html import GenericHtmlIndexer

        monkeypatch.setattr(GenericHtmlIndexer, "search", fake_search)
        result = run(
            ai_site.verify(
                {"provider": "html_generic", "url": "https://x.com", "options": {}},
                keyword="流浪地球",
            )
        )
        assert result["success"] is True
        assert result["count"] == 2
        assert result["keyword"] == "流浪地球"
        assert "2" in result["message"]
        assert len(result["samples"]) == 2
        assert result["samples"][0]["title"].startswith("流浪地球")

    def test_零结果不算通过但要解释可能原因(self, monkeypatch):
        """"没结果"未必是配置错，也可能这站真没这片——提示要说清两种可能。"""

        async def fake_search(self, keyword, **kwargs):
            return []

        from app.providers.indexer.generic_html import GenericHtmlIndexer

        monkeypatch.setattr(GenericHtmlIndexer, "search", fake_search)
        result = run(
            ai_site.verify({"provider": "html_generic", "url": "https://x.com"})
        )
        assert result["success"] is False
        assert result["count"] == 0
        assert "字段映射" in result["message"]

    def test_抛异常时不炸并带上异常类型(self, monkeypatch):
        """试跑本来就是拿不可信配置去打真站，抛异常是常态，不能让接口 500。"""

        async def fake_search(self, keyword, **kwargs):
            raise httpx.ConnectTimeout("connect timeout")

        from app.providers.indexer.generic_html import GenericHtmlIndexer

        monkeypatch.setattr(GenericHtmlIndexer, "search", fake_search)
        result = run(ai_site.verify({"provider": "html_generic", "url": "https://x.com"}))
        assert result["success"] is False
        assert "ConnectTimeout" in result["message"]

    def test_样例条数有上限(self, monkeypatch):
        """回 30 条样例会把弹窗刷爆，也没人一条条看。"""

        async def fake_search(self, keyword, **kwargs):
            return [
                Resource(title=f"第{i}条", link=f"magnet:?xt=urn:btih:{i:040d}", site="试跑")
                for i in range(30)
            ]

        from app.providers.indexer.generic_html import GenericHtmlIndexer

        monkeypatch.setattr(GenericHtmlIndexer, "search", fake_search)
        result = run(ai_site.verify({"provider": "html_generic", "url": "https://x.com"}))
        assert result["count"] == 30
        assert len(result["samples"]) == 3


# ================================================================== API 端点
AI_SITE_NAME = "cf_test_ai_site"


def _purge_site() -> None:
    with session_scope() as session:
        session.query(SiteConfig).filter(SiteConfig.name == AI_SITE_NAME).delete(
            synchronize_session=False
        )


@pytest.fixture
def clean_site():
    _purge_site()
    yield
    _purge_site()


SUGGESTION = {
    "url": "https://ai-site.example",
    "provider": "maccms",
    "kind": "indexer",
    "options": {"max_items": 6},
    "confidence": 0.9,
    "reason": "generator 是 maccms",
    "notes": "",
}


class TestAI接口:
    def test_必须登录(self, client):
        assert client.get("/api/v1/ai/config").status_code == 401
        assert client.post("/api/v1/ai/analyze", json={"url": "https://x.com"}).status_code == 401
        assert client.post("/api/v1/ai/verify", json={"suggestion": SUGGESTION}).status_code == 401
        assert (
            client.post(
                "/api/v1/ai/apply", json={"suggestion": SUGGESTION, "name": AI_SITE_NAME}
            ).status_code
            == 401
        )

    def test_config返回状态且不泄露密钥(self, client, auth_headers, ai_on):
        response = client.get("/api/v1/ai/config", headers=auth_headers)
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["ready"] is True
        assert "sk-test-123456" not in response.text

    def test_未配置时analyze返回400并说明去哪配(self, client, auth_headers):
        response = client.post(
            "/api/v1/ai/analyze", headers=auth_headers, json={"url": "https://x.example"}
        )
        assert response.status_code == 400
        assert "设置" in response.json()["detail"]

    def test_地址太短被pydantic拦在入口(self, client, auth_headers):
        response = client.post("/api/v1/ai/analyze", headers=auth_headers, json={"url": "x"})
        assert response.status_code == 422

    def test_verify走真实试跑(self, client, auth_headers, monkeypatch):
        async def fake_search(self, keyword, **kwargs):
            return [Resource(title=keyword, link="magnet:?xt=urn:btih:ver001", site="试跑")]

        from app.providers.indexer.maccms import MacCmsIndexer

        monkeypatch.setattr(MacCmsIndexer, "search", fake_search)
        response = client.post(
            "/api/v1/ai/verify",
            headers=auth_headers,
            json={"suggestion": SUGGESTION, "keyword": "流浪地球"},
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["success"] is True and data["count"] == 1

    def test_apply落库且默认不启用(self, client, auth_headers, clean_site):
        """默认不启用是刻意的：先让用户自己测一次连通性再放进搜索链路。"""
        response = client.post(
            "/api/v1/ai/apply",
            headers=auth_headers,
            json={"suggestion": SUGGESTION, "name": AI_SITE_NAME, "priority": 66},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["name"] == AI_SITE_NAME
        assert body["provider"] == "maccms"
        assert body["enabled"] is False
        assert body["priority"] == 66

        with session_scope() as session:
            row = session.query(SiteConfig).filter(SiteConfig.name == AI_SITE_NAME).one()
            # 留痕：日后排查"这个奇怪的配置哪来的"能立刻定位
            assert row.options["_ai_generated"] is True
            assert row.options["_ai_reason"] == "generator 是 maccms"
            assert row.options["max_items"] == 6
            assert row.url == "https://ai-site.example"

    def test_apply重名返回409(self, client, auth_headers, clean_site):
        first = client.post(
            "/api/v1/ai/apply",
            headers=auth_headers,
            json={"suggestion": SUGGESTION, "name": AI_SITE_NAME},
        )
        assert first.status_code == 200
        again = client.post(
            "/api/v1/ai/apply",
            headers=auth_headers,
            json={"suggestion": SUGGESTION, "name": AI_SITE_NAME},
        )
        assert again.status_code == 409

    def test_apply拒绝未注册的provider(self, client, auth_headers, clean_site):
        """绕过界面直接调接口塞一个不存在的 provider，也必须被拦。"""
        bad = dict(SUGGESTION, provider="magic_parser")
        response = client.post(
            "/api/v1/ai/apply", headers=auth_headers, json={"suggestion": bad, "name": AI_SITE_NAME}
        )
        assert response.status_code == 400
        with session_scope() as session:
            assert (
                session.query(SiteConfig).filter(SiteConfig.name == AI_SITE_NAME).count() == 0
            )

    @pytest.mark.parametrize("provider", ["qbittorrent", "emby", "telegram", "ytdlp"])
    def test_apply拒绝清单外但已注册的provider(
        self, client, auth_headers, clean_site, provider
    ):
        """必须用【已注册却不在 AI 清单】的名字来测，否则测不到白名单。

        `magic_parser` 那种没注册的名字会被第二道 `get_provider_class`
        顺手拦下，删掉白名单校验测试照样绿（实测过这个假绿）。
        而这里的风险是真的：直接 POST `provider=qbittorrent`，就能建出一个
        「kind=indexer 的下载器」——它会进搜索链路，每次搜索白等一次超时。
        """
        bad = dict(SUGGESTION, provider=provider)
        response = client.post(
            "/api/v1/ai/apply", headers=auth_headers, json={"suggestion": bad, "name": AI_SITE_NAME}
        )
        assert response.status_code == 400, f"{provider} 不该能通过 /ai/apply 落库"
        assert provider in response.json()["detail"]
        with session_scope() as session:
            assert (
                session.query(SiteConfig).filter(SiteConfig.name == AI_SITE_NAME).count() == 0
            )

    def test_pan建议落成pan类型(self, client, auth_headers, clean_site):
        """kind 落错，站点就会被搜索链路整类漏掉。"""
        suggestion = dict(SUGGESTION, provider="pan_generic", kind="pan")
        response = client.post(
            "/api/v1/ai/apply",
            headers=auth_headers,
            json={"suggestion": suggestion, "name": AI_SITE_NAME},
        )
        assert response.status_code == 200, response.text
        assert response.json()["kind"] == "pan"

    def test_设置页含内置AI分组(self, client, auth_headers):
        """配置项要能在界面上改：不然用户得去改 .env 再重启容器。"""
        response = client.get("/api/v1/system/settings", headers=auth_headers)
        assert response.status_code == 200
        groups = {group["title"]: group for group in response.json()["groups"]}
        # 断言确切标题：用 "AI" in key 模糊匹配时，标题被改成
        # 「内置 AI（已隐藏）」也照样能找到，测试就形同虚设（实测过这个假绿）。
        title = "内置 AI（站点分析）"
        assert title in groups, sorted(groups)
        keys = {item["key"] for item in groups[title]["items"]}
        assert {"AI_ENABLED", "AI_BASE_URL", "AI_API_KEY", "AI_MODEL"} <= keys
        flat = {item["key"]: item for item in groups[title]["items"]}
        # 密钥必须标为敏感且不回传原值
        assert flat["AI_API_KEY"]["secret"] is True
        assert flat["AI_API_KEY"]["raw"] is None
        assert flat["AI_ENABLED"]["editable"] is True


# ============================================== 敏感项「留空 = 不修改」（本轮修的）
class Test敏感项不被误清空:
    """界面脱敏不回显密钥，用户改隔壁字段时这一格天然是空的。

    修复前：那次保存会把 `AI_API_KEY` 落成空串，表现为"我只改了个超时，
    AI 就不工作了"。真要清空请用「恢复默认」，那是显式意图。
    """

    @pytest.fixture(autouse=True)
    def _clean(self, client):
        settings_store.delete_setting(config_store.KEY_RUNTIME)
        config_store.reset()
        yield
        config_store.reset()
        settings_store.delete_setting(config_store.KEY_RUNTIME)

    def test_识别敏感键(self):
        for key in ("AI_API_KEY", "SECRET_KEY", "API_TOKEN", "PAN_PASSWORD", "ai_api_key"):
            assert config_store.is_secret(key), key
        for key in ("AI_MODEL", "AI_TIMEOUT", "SEARCH_TIMEOUT", "AI_BASE_URL"):
            assert not config_store.is_secret(key), key

    def test_改隔壁字段不会洗掉已存的密钥(self):
        config_store.update({"AI_API_KEY": "sk-keep-me"})
        assert settings.AI_API_KEY == "sk-keep-me"
        # 界面提交时密钥格是空的（脱敏不回显），只有超时被改了
        applied = config_store.update({"AI_TIMEOUT": 45, "AI_API_KEY": ""})
        assert "AI_API_KEY" not in applied
        assert settings.AI_API_KEY == "sk-keep-me", "密钥被空提交洗掉了"
        assert settings.AI_TIMEOUT == 45
        assert config_store.overrides()["AI_API_KEY"] == "sk-keep-me"

    def test_全是空的敏感项时明确报错(self):
        """全部被跳过就等于没提交内容，要报错而不是假装保存成功。"""
        with pytest.raises(ValueError, match="没有需要保存"):
            config_store.update({"AI_API_KEY": ""})

    def test_非敏感项的空值仍然照常写入(self):
        """把「留空不改」扩到所有字段就走反了：清空模型名是合法操作。"""
        config_store.update({"AI_MODEL": "deepseek-chat"})
        applied = config_store.update({"AI_MODEL": ""})
        assert applied == {"AI_MODEL": ""}
        assert settings.AI_MODEL == ""

    def test_密钥非空时正常更新(self):
        config_store.update({"AI_API_KEY": "sk-old"})
        config_store.update({"AI_API_KEY": "sk-new"})
        assert settings.AI_API_KEY == "sk-new"

    def test_恢复默认才是清空密钥的正确途径(self):
        config_store.update({"AI_API_KEY": "sk-temp"})
        assert config_store.reset(["AI_API_KEY"]) == ["AI_API_KEY"]
        assert settings.AI_API_KEY == ""

    def test_通过接口保存也遵守留空不改(self, client, auth_headers):
        client.put(
            "/api/v1/system/settings",
            headers=auth_headers,
            json={"values": {"AI_API_KEY": "sk-via-api"}},
        )
        response = client.put(
            "/api/v1/system/settings",
            headers=auth_headers,
            json={"values": {"AI_API_KEY": "", "AI_MODEL": "qwen-plus"}},
        )
        assert response.status_code == 200, response.text
        assert settings.AI_API_KEY == "sk-via-api"
        assert settings.AI_MODEL == "qwen-plus"


# ======================================================== 底线：不做灰产对抗
def test_源码里不出现绕过风控的提示词():
    """AI 也不例外：只允许在既有 Provider 能力范围内选型（ADR-24 口径）。

    这条是元测试——防的是后来者顺手往 prompt 里加一句"如果有验证码就绕过"。
    """
    import ast
    import inspect

    source = inspect.getsource(ai_site)
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            text = ast.get_docstring(node)
            if text:
                docstrings.add(text)

    prompt = ai_site._SYSTEM_PROMPT
    for word in ("绕过", "破解", "破译", "bypass", "解析接口", "vip解析"):
        assert word not in prompt.lower(), word

    lowered = source.lower()
    for doc in docstrings:
        lowered = lowered.replace(doc.lower(), "")
    for word in ("jiexi", "/?url=", "xiguadh"):
        assert word not in lowered, f"源码里不该出现解析网关线索：{word}"
