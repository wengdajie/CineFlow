"""BT 覆盖面优化：BT4G/Bitsearch 索引器、假种识别、季号写法变体、站点诊断口径。

背景（用户反馈「大部分内容搜不到 BT 资源，返回的几乎全是网盘」）：
实测关键词「喜剧之王单口季 第三季」在 v1.25.0 上的结果是
**42 条网盘 + 1 条网页视频 + 0 条 BT**，而 BD电影首发站 却报告「命中 68 条」。
逐条查证后确认是**四个独立缺陷**，这个文件逐个钉死。
"""

import pathlib

import pytest

from app.core.filters import FilterRule, _is_fake_torrent, filter_and_rank
from app.providers.indexer.bitsearch import BitsearchIndexer
from app.providers.indexer.bitsearch import _parse_date as _bs_parse_date
from app.providers.indexer.bitsearch import _to_int as _bs_to_int
from app.providers.indexer.bt4g import Bt4gIndexer, _parse_description, _parse_pub_date
from app.services.search import (
    _cn_number,
    _cn_to_int,
    _season_spelling_variants,
    build_keywords,
)

MB = 1024 * 1024

#: 在**干净的子进程**里验证「只调 load_builtin_providers() 就能拿到这个 Provider」。
#:
#: 为什么不能在本进程里做：本文件顶部为了测解析函数已经 import 了这些模块，
#: 而 ``@register`` 是 import 时执行的 —— 注册表里于是**必然**有它，
#: 哪怕从 ``load_builtin_providers()`` 的模块清单里删掉也一样绿（约定 4 / ADR-97）。
_DEFAULT_LOAD_SCRIPT = """\
import sys

# 脚本在临时目录里，sys.path[0] 是那个临时目录而**不是** cwd ——
# 只设 cwd 会得到 ModuleNotFoundError: No module named 'app'（实测踩过）。
sys.path.insert(0, sys.argv[3])

from app.providers.registry import get_provider_class, load_builtin_providers

name, expected = sys.argv[1], sys.argv[2]
load_builtin_providers()
cls = get_provider_class(name)
if cls is None:
    raise SystemExit(f"NOT-REGISTERED:{name}")
if cls.__name__ != expected:
    raise SystemExit(f"WRONG-CLASS:{cls.__name__}!={expected}")
print("OK")
"""


def _assert_registered_on_default_load(name: str, expected_cls: str) -> None:
    """断言默认加载后能按名字取到该 Provider 类（独立进程）。"""
    import subprocess
    import sys
    import tempfile

    root = pathlib.Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory() as tmp:
        script = pathlib.Path(tmp) / "check.py"
        script.write_text(_DEFAULT_LOAD_SCRIPT, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(script), name, expected_cls, str(root)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(root),
        )
    assert proc.returncode == 0, (
        f"默认加载拿不到 Provider {name!r}："
        f"{(proc.stdout + proc.stderr)[-800:]}"
    )


# --------------------------------------------------------------------------
# 缺陷 1：网盘引流假种。标题写着 4K，体积只有 288KB。
# --------------------------------------------------------------------------
class Test假种识别:
    """实测：bitsearch 上「喜剧之王单口季第三季」的 6 条结果全是 288KB~4.5MB。

    下载 ``.torrent`` 后 bdecode 内容是::

        751 B      ...-网资源分享.txt
        255 B      网盘链接在这.txt
        293,629 B  资源封面.jpg

    标题却写着「(2026)[4K1080P][国语中字][更至0815第7期4]」。
    **不看体积无法分辨**，用户点下载只会得到一个 txt。
    """

    def test_小体积却标注高清判为假种(self):
        fake, reason = _is_fake_torrent(
            {
                "title": "\u30108i2.fit\u3011\u559c\u5267\u4e4b\u738b\u5355\u53e3\u5b63\u7b2c\u4e09\u5b63(2026)[4K1080P][\u56fd\u8bed\u4e2d\u5b57]",
                "size": int(0.3 * MB),
                "kind": "magnet",
            }
        )
        assert fake is True
        assert "\u9ad8\u6e05" in reason

    def test_体积未知时不能判为假种(self):
        """``size == 0`` 是「**未知**」不是「零」。

        很多 RSS 源压根不给体积字段，一刀切会把整个源判死。
        """
        fake, _ = _is_fake_torrent(
            {"title": "Some Release 1080p", "size": 0, "kind": "magnet"}
        )
        assert fake is False

    def test_体积够大就不是假种(self):
        fake, _ = _is_fake_torrent(
            {"title": "Some Movie 2024 1080p WEB-DL", "size": 3000 * MB, "kind": "magnet"}
        )
        assert fake is False

    def test_只有画质标签才构成矛盾(self):
        """不能退化成「凡是小体积都杀」—— 样片/预告本来就小。"""
        fake, _ = _is_fake_torrent(
            {"title": "trailer clip no quality tag", "size": int(0.3 * MB), "kind": "magnet"}
        )
        assert fake is False

    def test_假种检测只管BT不管网盘(self):
        """网盘分享链接没有「体积与画质矛盾」这个问题。"""
        fake, _ = _is_fake_torrent(
            {"title": "\u7f51\u76d8 4K1080P \u5168\u96c6", "size": int(0.3 * MB), "kind": "pan"}
        )
        assert fake is False

    def test_阈值边界(self):
        """500MB 是分界：正好等于阈值时不判假种（>= 放过）。"""
        blocked, _ = _is_fake_torrent(
            {"title": "Movie 1080p", "size": 499 * MB, "kind": "magnet"}
        )
        assert blocked is True
        passed, _ = _is_fake_torrent(
            {"title": "Movie 1080p", "size": 500 * MB, "kind": "magnet"}
        )
        assert passed is False

    def test_假种检测真的接进了过滤链路(self):
        """约定 11：函数存在 != 被调用。用真实的 filter_and_rank 验证。"""
        items = [
            {
                "title": "\u5047\u79cd 4K1080P \u56fd\u8bed\u4e2d\u5b57",
                "size": int(0.3 * MB),
                "kind": "magnet",
                "seeders": 1,
            }
        ]
        kept = filter_and_rank(items, FilterRule())
        assert kept == []
        assert "\u5047\u79cd" in items[0]["filter_reason"]


# --------------------------------------------------------------------------
# 缺陷 3：季号写法。站点标题不带空格，且中文/阿拉伯数字混用。
# --------------------------------------------------------------------------
class Test季号写法变体:
    """实测：BT4G 上「喜剧之王单口季 **第三季**」→ **0 条**，
    「喜剧之王单口季」→ **15 条**。

    资源站写的是「…单口季第三季」「…单口季第3季」，
    **没有任何一条**用带空格的写法。
    """

    def test_中文数字转换(self):
        assert _cn_number(1) == "\u4e00"
        assert _cn_number(3) == "\u4e09"
        assert _cn_number(10) == "\u5341"
        assert _cn_number(12) == "\u5341\u4e8c"

    def test_超出范围直接返回阿拉伯数字(self):
        """季号超过 19 的剧集现实中不存在，为此装个中文数字库属于过度设计。"""
        assert _cn_number(20) == "20"
        assert _cn_number(0) == "0"

    def test_中文数字反向解析(self):
        assert _cn_to_int("\u4e09") == 3
        assert _cn_to_int("\u5341") == 10
        assert _cn_to_int("\u5341\u4e8c") == 12
        assert _cn_to_int("3") == 3

    def test_认不出的写法返回None(self):
        assert _cn_to_int("") is None
        assert _cn_to_int("abc") is None
        assert _cn_to_int("\u767e") is None

    def test_生成无空格与阿拉伯数字两种写法(self):
        variants = _season_spelling_variants(
            "\u559c\u5267\u4e4b\u738b\u5355\u53e3\u5b63 \u7b2c\u4e09\u5b63"
        )
        assert "\u559c\u5267\u4e4b\u738b\u5355\u53e3\u5b63\u7b2c\u4e09\u5b63" in variants
        assert "\u559c\u5267\u4e4b\u738b\u5355\u53e3\u5b63\u7b2c3\u5b63" in variants

    def test_阿拉伯数字季号也生成中文写法(self):
        """两条路都要覆盖：只测一边时改坏另一边仍绿。"""
        variants = _season_spelling_variants(
            "\u559c\u5267\u4e4b\u738b\u5355\u53e3\u5b63 \u7b2c3\u5b63"
        )
        assert "\u559c\u5267\u4e4b\u738b\u5355\u53e3\u5b63\u7b2c\u4e09\u5b63" in variants

    def test_变体始终保留季号(self):
        """这是**换写法**而不是**放宽**。

        去掉季号会把第二季混进来 —— 用户搜 S03 拿到 S02 比搜不到更糟。
        """
        variants = _season_spelling_variants(
            "\u559c\u5267\u4e4b\u738b\u5355\u53e3\u5b63 \u7b2c\u4e09\u5b63"
        )
        assert variants
        for item in variants:
            assert "\u7b2c" in item
            assert "\u5b63" in item

    def test_没有内嵌季号时不产生变体(self):
        assert _season_spelling_variants("\u6d41\u6d6a\u5730\u7403") == []

    def test_第几部也算季标记(self):
        variants = _season_spelling_variants("\u67d0\u7cfb\u5217 \u7b2c\u4e8c\u90e8")
        assert "\u67d0\u7cfb\u5217\u7b2c\u4e8c\u90e8" in variants

    def test_季号后面的内容被保留(self):
        variants = _season_spelling_variants(
            "\u67d0\u5267 \u7b2c\u4e09\u5b63 1080p"
        )
        assert any("1080p" in item for item in variants)

    def test_build_keywords里变体排在裸片名之前(self):
        """``_search_one`` 命中即止：裸片名几乎总能命中并直接 return。

        变体放在它后面等于**永远用不到**（实测踩过）。
        """
        keywords = build_keywords("\u559c\u5267\u4e4b\u738b\u5355\u53e3\u5b63 \u7b2c\u4e09\u5b63")
        variant = "\u559c\u5267\u4e4b\u738b\u5355\u53e3\u5b63\u7b2c\u4e09\u5b63"
        assert variant in keywords
        assert keywords.index(variant) < len(keywords) - 1

    def test_裸片名放在最后作为兜底(self):
        keywords = build_keywords("\u559c\u5267\u4e4b\u738b\u5355\u53e3\u5b63 \u7b2c\u4e09\u5b63")
        assert keywords[-1] == "\u559c\u5267\u4e4b\u738b\u5355\u53e3\u5b63 \u7b2c\u4e09\u5b63"

    def test_season参数分支也生成无空格写法(self):
        """``season=`` 参数与标题内嵌季号是两条独立路径，都要覆盖。

        注意 ``media_type`` 必须是 tv/anime —— 季号对电影没有意义，
        实现里用它来决定要不要展开季号（实测漏传会一条变体都拿不到）。
        """
        keywords = build_keywords("\u5e86\u4f59\u5e74", media_type="tv", season=2)
        assert "\u5e86\u4f59\u5e74\u7b2c\u4e8c\u5b63" in keywords
        assert "\u5e86\u4f59\u5e74\u7b2c2\u5b63" in keywords

    def test_电影不展开季号(self):
        """季号对电影没有意义，展开只会污染关键词并拖慢搜索。"""
        keywords = build_keywords("\u6d41\u6d6a\u5730\u7403", media_type="movie", season=2)
        assert keywords == ["\u6d41\u6d6a\u5730\u7403"]

    def test_剧集带集号时生成SxxExx(self):
        keywords = build_keywords(
            "\u5e86\u4f59\u5e74", media_type="tv", season=2, episode=5
        )
        assert "\u5e86\u4f59\u5e74 S02E05" in keywords
        assert keywords[-1] == "\u5e86\u4f59\u5e74"


# --------------------------------------------------------------------------
# 缺陷 2：所有 BT 源对华语综艺零覆盖 —— 补 BT4G。
# --------------------------------------------------------------------------
class TestBt4g解析:
    """实测 ``<description>`` 形如
    ``标题<br>2.32GB<br>Movie<br><40位infohash>``，
    体积**不在**独立标签里 —— 通用 RSS 方言层拿不到，全部 size=0。
    """

    RSS = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel><item>'
        "<title>\u3010\u9ad8\u6e05\u5267\u96c6\u7f51\u3011\u559c\u5267\u4e4b\u738b\u5355\u53e3\u5b63.\u7b2c\u4e8c\u5b63</title>"
        "<link>magnet:?xt=urn:btih:8cc170d44591c91cfc672977ddc0155fbb773073</link>"
        "<description><![CDATA[\u6807\u9898<br>2.32GB<br>Movie<br>"
        "8cc170d44591c91cfc672977ddc0155fbb773073]]></description>"
        "<pubDate>Fri,3 Oct 2025 12:00:00 +0000</pubDate>"
        "<guid>https://bt4gprx.com/magnet/8cc170d44591c91cfc672977ddc0155fbb773073</guid>"
        "</item></channel></rss>"
    )

    def _provider(self):
        return Bt4gIndexer({"name": "BT4G", "site_name": "BT4G"})

    def test_从description抽出体积与分类(self):
        size, category = _parse_description(
            "\u3010\u9ad8\u6e05\u5267\u96c6\u7f51\u53d1\u5e03\u3011\u559c\u5267\u4e4b\u738b\u5355\u53e3\u5b63.\u7b2c\u4e8c\u5b63<br>2.32GB<br>Movie<br>"
            "8cc170d44591c91cfc672977ddc0155fbb773073"
        )
        assert size == int(2.32 * 1024**3)
        assert category == "Movie"

    def test_infohash不会被当成分类(self):
        """40 位纯 hex 是 infohash，混进 category 会污染前端展示。"""
        _, category = _parse_description(
            "\u6807\u9898<br>1.5GB<br>8cc170d44591c91cfc672977ddc0155fbb773073"
        )
        assert category == ""

    def test_解析不出体积时返回0(self):
        """0 在这里等价于「未知」：BT4G 不存在 0 字节的种子。"""
        size, _ = _parse_description("\u53ea\u6709\u6807\u9898")
        assert size == 0

    def test_体积单位大小写混用也能解析(self):
        size, _ = _parse_description("\u6807\u9898<br>500mb<br>TV")
        assert size == 500 * MB

    def test_pubDate逗号后无空格也能解析(self):
        """实测 BT4G 输出 ``Fri,3 Oct 2025 12:00:00 +0000`` ——
        **逗号后没有空格**，不符 RFC 822，标准库解析器会失败。"""
        parsed = _parse_pub_date("Fri,3 Oct 2025 12:00:00 +0000")
        assert parsed is not None
        assert (parsed.year, parsed.month, parsed.day) == (2025, 10, 3)

    def test_pubDate认不出时返回None(self):
        """拿 now() 顶替会让所有条目看起来都是今天发布。"""
        assert _parse_pub_date("") is None
        assert _parse_pub_date("garbage") is None

    def test_解析出磁力与体积(self):
        resources = self._provider()._parse(self.RSS)
        assert len(resources) == 1
        assert resources[0].link.startswith("magnet:?xt=urn:btih:8cc170d4")
        assert resources[0].size == int(2.32 * 1024**3)

    def test_infohash与详情页都被带上(self):
        resource = self._provider()._parse(self.RSS)[0]
        assert resource.extra["infohash"] == "8cc170d44591c91cfc672977ddc0155fbb773073"
        assert resource.page_url

    def test_做种数如实写0而不编造(self):
        """RSS 里没有 seeders 字段。编一个看起来健康的数字是欺骗用户。"""
        resource = self._provider()._parse(self.RSS)[0]
        assert resource.seeders == 0
        assert resource.leechers == 0

    def test_没有磁力的条目被丢弃(self):
        rss = self.RSS.replace(
            "magnet:?xt=urn:btih:8cc170d44591c91cfc672977ddc0155fbb773073",
            "https://example.com/not-a-magnet",
        )
        assert self._provider()._parse(rss) == []

    def test_HTML错误页必须报错而不是当成空结果(self):
        """``<html>Moved Permanently</html>`` 是**合法 XML**，
        光靠 ``ET.ParseError`` 拦不住 —— 必须校验根节点 tag。

        实测踩过：不跟随 301 时拿到 17 字节的 ``Moved Permanently``，
        解析成 0 条就成了静默失败（ADR-82）。"""
        from app.utils.http import FetchError

        with pytest.raises(FetchError):
            self._provider()._parse("<html><body>Moved Permanently</body></html>")

    def test_非XML也要报错(self):
        from app.utils.http import FetchError

        with pytest.raises(FetchError):
            self._provider()._parse("Moved Permanently")

    def test_关键词走quote因为它是路径段(self):
        """BT4G 把关键词当**路径段**，``quote_plus`` 的 ``+``
        不会被解成空格，会变成字面加号从而搜不到东西（实测踩过）。"""
        url = self._provider()._search_url("king of comedy")
        assert "king%20of%20comedy" in url
        assert "+" not in url.split("/search/")[1].split("/")[0]

    def test_没有全站最新时如实返回空(self):
        import asyncio

        assert asyncio.run(self._provider().fetch_latest()) == []

    def test_已注册进内置provider(self):
        """必须用**独立进程**（约定 4 / ADR-97）。

        ``@register`` 是 import 时执行的：本测试文件顶部已经
        ``from app.providers.indexer.bt4g import ...``，于是同进程里
        它**早就注册好了** —— 哪怕从 ``load_builtin_providers()`` 的清单里
        删掉，断言照样绿（实测踩到的假绿）。子进程只调加载器、不 import 模块，
        才能真正验证"默认加载就能造出来"。
        """
        _assert_registered_on_default_load("bt4g", "Bt4gIndexer")

    def test_已进实测站点清单(self):
        from app.services.site_catalog import BT_SITE_PRESETS

        preset = next((item for item in BT_SITE_PRESETS if item["id"] == "bt4g"), None)
        assert preset is not None
        assert preset["provider"] == "bt4g"
        assert preset["measured"]
        assert preset["caveat"]

# --------------------------------------------------------------------------
# 缺陷 4：站点诊断口径。「命中 68 条」而结果里 0 条。
# --------------------------------------------------------------------------
class Test站点诊断口径:
    """``kept`` 原先在 ``apply_site_quota`` 里就定死了（= 配额裁剪后的条数），
    但**标题相关性过滤发生在那之后**。于是 BD电影首发站 搜
    「喜剧之王单口季 第三季」时前端显示「命中 68 条」而列表里一条都没有
    —— 用户只能理解成"程序把结果吃了"。

    实测那 68 条全是 1999 年电影《喜剧之王》：该站磁力只在详情页且
    共用详情页标题，属于整站误报，不是本项目的解析 bug。
    """

    def test_kept反映过滤后的真实条数(self):
        """"返回了一堆但全对不上" 与 "确实有货" 必须能区分开。

        调**真实的** ``recount_kept``，不在测试里重写一遍归并逻辑 ——
        那样改坏实现照样绿（实测踩过的假绿）。
        """
        from app.services.search import SiteOutcome, recount_kept

        outcome = SiteOutcome(site="BD电影首发站", status="ok", raw=68, kept=68)
        recount_kept([], [outcome], "喜剧之王单口季 第三季")
        assert outcome.kept == 0

    def test_盘搜的分组后缀按主站名归并(self):
        """归并方向很容易搞反：**条目**那侧才带「·分组」后缀
        （PanSou 盘搜·baidu / ·quark…），``outcome.site`` 是不带后缀的主站名。

        反了会让盘搜永远统计成 0 —— 这是实测踩过的回归。
        """
        from app.services.search import SiteOutcome, recount_kept

        outcome = SiteOutcome(site="PanSou 盘搜", status="ok", raw=32, kept=32)
        ranked = [
            {"site": "PanSou 盘搜·baidu"},
            {"site": "PanSou 盘搜·quark"},
            {"site": "PanSou 盘搜·quark"},
        ]
        recount_kept(ranked, [outcome], "x")
        assert outcome.kept == 3
        assert outcome.status == "ok"

    def test_全被过滤的站点状态改成filtered(self):
        """``empty``    = 这个站没这部片（换关键词也没用）；
        ``filtered`` = 这个站返回了 N 条但全对不上（该换站点/改关键词）。

        都显示成「无匹配」会让用户无法判断下一步该做什么（ADR-82）。
        """
        from app.services.search import SiteOutcome, recount_kept

        outcome = SiteOutcome(site="BD电影首发站", status="ok", raw=68, kept=68)
        recount_kept([], [outcome], "喜剧之王单口季 第三季")
        assert outcome.status == "filtered"

    def test_filtered提示里要写出关键词(self):
        """不写关键词，用户就不知道是"跟什么"不匹配。"""
        from app.services.search import SiteOutcome, recount_kept

        outcome = SiteOutcome(site="BD电影首发站", status="ok", raw=68, kept=68)
        recount_kept([], [outcome], "喜剧之王单口季 第三季")
        assert "喜剧之王单口季 第三季" in outcome.message
        assert "68" in outcome.message

    def test_empty的站点不会被误改成filtered(self):
        """``raw == 0`` 是真的没有，不是被过滤掉的。"""
        from app.services.search import SiteOutcome, recount_kept

        outcome = SiteOutcome(site="dmhy", status="empty", raw=0, kept=0)
        recount_kept([], [outcome], "x")
        assert outcome.status == "empty"

    def test_失败的站点状态不被覆盖(self):
        """``error``/``timeout`` 必须保留 —— 那是要用户去修的，
        改成 filtered 会让真故障被当成"关键词不对"（ADR-82）。
        """
        from app.services.search import SiteOutcome, recount_kept

        for status in ("error", "timeout", "skipped"):
            outcome = SiteOutcome(site="Jackett", status=status, raw=0, kept=0)
            recount_kept([], [outcome], "x")
            assert outcome.status == status

    def test_有结果的站点保持ok(self):
        from app.services.search import SiteOutcome, recount_kept

        outcome = SiteOutcome(site="BT4G", status="ok", raw=15, kept=15)
        recount_kept([{"site": "BT4G"}] * 4, [outcome], "x")
        assert outcome.status == "ok"
        assert outcome.kept == 4

    def test_recount_kept被真的接进搜索链路(self):
        """约定 11：函数存在 != 被调用。"""
        source = pathlib.Path("app/services/search.py").read_text(encoding="utf-8")
        code = "\n".join(line.split("#")[0] for line in source.splitlines())
        assert "recount_kept(" in code
        # 定义 + 至少一处调用
        assert code.count("recount_kept(") >= 2

    def test_流式搜索真的会推出filtered状态(self):
        """纯文本断言挡不住行为回归：把流式路径的 ``filtered`` 改成 ``"ok"``
        文本测试仍会绿。这里**真跑** ``search_stream`` 断言状态（约定 12：
        一个逻辑在 N 处就要 N 个案例，流式/非流式各一份）。

        ``providers`` 是 ``list[SearchProvider]`` —— 传字典不会报错，
        只会一个站都不查然后什么都断言不到（踩过）。
        """
        import asyncio

        from app.providers.base import Resource
        from app.services import search as search_mod

        class _FakeProvider:
            site_name = "假站点"
            priority = 10
            name = "fake"
            kind = "indexer"
            config: dict = {}

            async def search(self, keyword, **kwargs):
                # 返回一条与关键词完全无关的结果 —— 会被标题相关性过滤掉
                return [
                    Resource(
                        title="Totally Unrelated Movie 2019 1080p",
                        link="magnet:?xt=urn:btih:" + "a" * 40,
                        site="假站点",
                        kind="magnet",
                        size=8 * 1024**3,
                        seeders=10,
                    )
                ]

        events = []

        async def run():
            async for event in search_mod.search_stream(
                "喜剧之王单口季 第三季",
                providers=[_FakeProvider()],
                save_history=False,
            ):
                events.append(event)

        asyncio.run(run())
        # 站点诊断可能挂在 site 事件上，也可能在 done 的 outcomes 里
        statuses = [e.get("status") for e in events if e.get("type") == "site"]
        for event in events:
            for outcome in event.get("sites") or event.get("outcomes") or []:
                if isinstance(outcome, dict):
                    statuses.append(outcome.get("status"))
        assert "filtered" in statuses, f"实际状态：{statuses}"

    def test_流式路径也把kept修正成过滤后的条数(self):
        """约定 12：同一个口径在流式/非流式各有一份实现，就要各有一个案例。

        流式**不能**复用 ``recount_kept()``（它按站点名统计全量结果，
        而流式此刻只有本站这一批），但**口径必须一致** ——
        否则同一次搜索，流式与非流式会给出互相矛盾的诊断。

        这里断言的是行为：一条结果全被过滤时 ``kept`` 必须是 0，
        而不是站点原始返回的条数。
        """
        import asyncio

        from app.providers.base import Resource
        from app.services import search as search_mod

        class _FakeProvider:
            site_name = "假站点"
            priority = 10
            name = "fake"
            kind = "indexer"
            config: dict = {}

            async def search(self, keyword, **kwargs):
                # 两条都与关键词无关 —— raw=2 而 kept 应当变成 0
                return [
                    Resource(
                        title=f"Totally Unrelated {idx} 2019 1080p",
                        link="magnet:?xt=urn:btih:" + str(idx) * 40,
                        site="假站点",
                        kind="magnet",
                        size=8 * 1024**3,
                        seeders=10,
                    )
                    for idx in (1, 2)
                ]

        kepts = []

        async def run():
            async for event in search_mod.search_stream(
                "喜剧之王单口季 第三季",
                providers=[_FakeProvider()],
                save_history=False,
            ):
                if event.get("type") == "site":
                    site = event.get("site") or {}
                    kepts.append((site.get("raw"), site.get("kept")))

        asyncio.run(run())
        assert kepts, "流式没有推出任何站点事件"
        raw, kept = kepts[0]
        assert raw == 2, f"站点原始条数应为 2，实际 {raw}"
        assert kept == 0, f"全被过滤时 kept 必须是 0，实际 {kept}"

    def test_前端能显示filtered状态(self):
        """约定 11：后端加了状态 != 用户看得到。

        M55 的僵尸站点、M58 的 AppKey 入口都是同一类错。
        """
        js = "\n".join(p.read_text(encoding="utf-8") for p in pathlib.Path("web/assets").rglob("*.js"))
        code = "\n".join(line.split("//")[0] for line in js.splitlines())
        # 断言展示文案本身，而不是子串 "filtered:" ——
        # 后者会被 ORDER 里的 `filtered: 3` 满足，删掉 SITE_STATUS 那行仍绿
        assert '"全被过滤"' in code
        assert "filtered: [" in code

    def test_filtered在站点排序里靠前(self):
        """掉到末尾用户根本翻不到 —— 它是需要采取行动的状态。"""
        js = "\n".join(p.read_text(encoding="utf-8") for p in pathlib.Path("web/assets").rglob("*.js"))
        code = "\n".join(line.split("//")[0] for line in js.splitlines())
        assert "filtered: 3" in code
        assert "empty: 4" in code


class Test假种的第二道闸门:
    """体积装不下一集正片 + 自称某季某期 = 假种（不吹画质也要拦）。

    实测依据（Bitsearch，「喜剧之王单口季第三季」，2026-09-04）：
    6 条结果里 **4 条标题不含任何高清词**，写的是
    「(2026)更至7.20期[综艺脱口秀][黄渤郭麒麟]」，体积 0.2~0.5MB。
    只靠 `_FAKE_QUALITY_KEYWORDS` 会全部漏过 —— 用户点下载只得到一个 txt。
    """

    def test_不吹画质但自称某期而体积装不下一集要判假种(self):
        fake, reason = _is_fake_torrent(
            {
                "title": "【8i2.fit】喜剧之王单口季第三季(2026)更至7.20期[综艺脱口秀]",
                "size": int(0.5 * MB),
                "kind": "magnet",
            }
        )
        assert fake is True
        assert "装不下一集" in reason

    def test_体积未知时第二道闸门也不能开火(self):
        """size==0 是「未知」不是「零」：很多 RSS 源不给体积。"""
        fake, _ = _is_fake_torrent(
            {"title": "某剧第一季全集更至10期", "size": 0, "kind": "magnet"}
        )
        assert fake is False

    def test_有剧集标记但体积够大就不是假种(self):
        """闸门是「小体积」与「自称剧集」的**合取**，不能只看标题。"""
        fake, _ = _is_fake_torrent(
            {
                "title": "高清剧集网 喜剧之王单口季.第二季[第35-36集]",
                "size": 2375 * MB,
                "kind": "magnet",
            }
        )
        assert fake is False

    def test_体积小但标题没有任何剧集标记就放过(self):
        """不能退化成「凡是小体积都杀」—— 那会把体积字段不准的源整体判死。"""
        fake, _ = _is_fake_torrent(
            {"title": "Random release no markers", "size": int(0.4 * MB), "kind": "magnet"}
        )
        assert fake is False

    def test_第二道闸门也只管BT不管网盘(self):
        fake, _ = _is_fake_torrent(
            {"title": "网盘分享 第三季全集", "size": int(0.4 * MB), "kind": "pan"}
        )
        assert fake is False

    def test_两道闸门的阈值不同且第二道更严(self):
        """120MB 带剧集标记应放过（>50MB），400MB 带高清标记应拦下（<500MB）。

        钉住两个常量的**相对关系**：如果有人把它们改成同一个值，
        要么误杀大量小体积字幕/样片，要么放过一批假种。
        """
        passed, _ = _is_fake_torrent(
            {"title": "某剧第一季全集", "size": 120 * MB, "kind": "magnet"}
        )
        assert passed is False
        blocked, _ = _is_fake_torrent(
            {"title": "Movie 2024 1080p", "size": 400 * MB, "kind": "magnet"}
        )
        assert blocked is True

    def test_第二道闸门真的接进了过滤链路(self):
        """约定 11：函数存在 != 被调用。用真实的 filter_and_rank 验证。"""
        items = [
            {
                "title": "喜剧之王单口季第三季(2026)更至7.20期综艺",
                "size": int(0.5 * MB),
                "kind": "magnet",
                "seeders": 1,
            }
        ]
        kept = filter_and_rank(items, FilterRule())
        assert kept == []
        assert "装不下一集" in items[0]["filter_reason"]


class TestBitsearch解析:
    """Bitsearch 索引器：唯一同时给做种数与中文覆盖的免鉴权源。

    实测（2026-09-04，关键词「喜剧之王单口季」）：20 条，
    做种数 0~11，体积 237KB~9.12GB，20/20 相关。
    """

    CARD = '<div class="bg-white rounded-lg shadow-sm border border-gray-200 p-6 hover:shadow-md">\n  <h3><a href="/torrent/6a988369dc4d69dbc52ee12e" class="hover:text-primary">\n      【不太灵影视】喜剧之王单口季.第二季[第11-12集].1080p\n  </a></h3>\n  <span class="inline-flex"><i class="fas fa-video text-blue-500"></i><span>Other/Video</span></span>\n  <span class="inline-flex"><i class="fas fa-download"></i><span>4.52 GB</span></span>\n  <span class="inline-flex"><i class="fas fa-calendar"></i><span>7/26/2025</span></span>\n  <span class="inline-flex text-green-600"><i class="fas fa-arrow-up"></i><span class="font-medium">11</span><span>seeders</span></span>\n  <span class="inline-flex text-red-600"><i class="fas fa-arrow-down"></i><span class="font-medium">18</span><span>leechers</span></span>\n  <a href="/download/torrent/52B40CF30576C173D2F0FBB4FF1C8E724959DF76?title=x">Torrent</a>\n  <a href="magnet:?xt&#x3D;urn:btih:52B40CF30576C173D2F0FBB4FF1C8E724959DF76&amp;dn&#x3D;%5BBitsearch.to%5D&amp;tr&#x3D;DHT">Magnet</a>\n</div>'
    NO_RESULT = '<div class="space-y-4" data-impression-ids="[]"></div><h3 class="text-lg font-medium text-gray-900 mb-2">No results found</h3><p>We could not find any torrents matching zzzz.</p>'

    def _provider(self):
        return BitsearchIndexer({"name": "Bitsearch", "site_name": "Bitsearch"})

    def test_磁力链必须反HTML转义(self):
        """源码里是 ``&#x3D;`` / ``&amp;``，不反转义会得到无效磁力。

        这是最容易静默失败的一处：链接看起来"有"，下载器却收下后什么都不做。
        """
        resources = self._provider()._parse(self.CARD)
        assert len(resources) == 1
        link = resources[0].link
        assert link.startswith("magnet:?xt=urn:btih:52B40CF30576")
        assert "&#x3D;" not in link
        assert "&amp;" not in link
        assert "&dn=" in link

    def test_做种数与下载数按图标取而非按顺序(self):
        """站点把两个数字的先后顺序换一下，按顺序取就会静默取错。

        原始卡片里 seeders(11) 恰好排在 leechers(18) 前面，
        所以"取第一个 font-medium"这种宽松写法**也能碰巧取对** ——
        必须再造一张**顺序颠倒**的卡片才拦得住（实测踩到的假绿）。
        """
        resource = self._provider()._parse(self.CARD)[0]
        assert resource.seeders == 11
        assert resource.leechers == 18

        # 把两块 swarm 统计的先后顺序换过来，数值不变
        up = (
            '<span class="inline-flex text-green-600"><i class="fas fa-arrow-up">'
            '</i><span class="font-medium">11</span><span>seeders</span></span>'
        )
        down = (
            '<span class="inline-flex text-red-600"><i class="fas fa-arrow-down">'
            '</i><span class="font-medium">18</span><span>leechers</span></span>'
        )
        assert up in self.CARD and down in self.CARD
        swapped = self.CARD.replace(up + "\n  " + down, down + "\n  " + up)
        assert swapped != self.CARD, "顺序颠倒的卡片没造出来"
        resource = self._provider()._parse(swapped)[0]
        assert resource.seeders == 11, "做种数按出现顺序取了，站点换序就会取错"
        assert resource.leechers == 18

    def test_体积解析成字节(self):
        resource = self._provider()._parse(self.CARD)[0]
        assert resource.size == int(4.52 * 1024**3)

    def test_日期按美式月日年解析(self):
        """站点输出 ``7/26/2025`` —— 月在前且不补零。"""
        resource = self._provider()._parse(self.CARD)[0]
        assert resource.publish_at is not None
        assert (
            resource.publish_at.year,
            resource.publish_at.month,
            resource.publish_at.day,
        ) == (2025, 7, 26)

    def test_日期解析不出时返回None而不是当前时间(self):
        """拿 now() 顶替会让所有条目看起来都是今天发布，按时间排序彻底失效。"""
        assert _bs_parse_date("") is None
        assert _bs_parse_date("not-a-date") is None
        assert _bs_parse_date("13/45/2025") is None

    def test_做种数带千分位逗号也能解析(self):
        assert _bs_to_int("1,234") == 1234
        assert _bs_to_int(None) == 0
        assert _bs_to_int("abc") == 0

    def test_infohash与详情页都被带上(self):
        resource = self._provider()._parse(self.CARD)[0]
        assert resource.extra["infohash"] == "52b40cf30576c173d2f0fbb4ff1c8e724959df76"
        assert resource.page_url == "https://bitsearch.eu/torrent/6a988369dc4d69dbc52ee12e"

    def test_标题去标签并反转义(self):
        """标题里可能嵌着 ``<span>`` 高亮和 ``&amp;`` 实体。

        原卡片的标题恰好是纯文本，所以"原样返回"也能通过前两条断言 ——
        必须造一个**真的带标签和实体**的标题（实测的假绿）。
        """
        resource = self._provider()._parse(self.CARD)[0]
        assert "<" not in resource.title
        assert resource.title.startswith("【不太灵影视】")

        marked = self.CARD.replace(
            "【不太灵影视】喜剧之王单口季.第二季[第11-12集].1080p",
            "<span class=\"hl\">Tom &amp; Jerry</span> S01",
        )
        assert marked != self.CARD
        title = self._provider()._parse(marked)[0].title
        assert "<" not in title and ">" not in title, f"标签没去掉：{title!r}"
        assert "&amp;" not in title, f"实体没反转义：{title!r}"
        assert title == "Tom & Jerry S01"

    def test_资源类型是磁力(self):
        from app.schemas.enums import ResourceKind

        resource = self._provider()._parse(self.CARD)[0]
        assert resource.kind == ResourceKind.MAGNET.value

    def test_无结果页返回空列表而不报错(self):
        """「站点确实没有这部片」是正常的零结果。"""
        assert self._provider()._parse(self.NO_RESULT) == []

    def test_既无卡片又无无结果标志时必须报错(self):
        """ADR-82：站点改版导致解析失效，与「没这部片」都表现为 0 条，
        但处置完全相反 —— 前者要报错，后者是正常结果。"""
        from app.utils.http import FetchError

        with pytest.raises(FetchError):
            self._provider()._parse("<html><body>totally different layout</body></html>")

    def test_没有磁力的卡片被丢弃(self):
        """留下来等于给用户一条点了没反应的结果。"""
        card = self.CARD.replace("magnet:?xt&#x3D;urn:btih:", "http://example.com/nope?x=")
        assert self._provider()._parse(card) == []

    def test_磁力正则的前缀是字面量所以无需二次校验(self):
        """钉住一个**设计判断**：``_MAGNET_RE`` 的 ``magnet:?xt`` 是字面量，
        匹配上就必然以 ``magnet:`` 开头，HTML 反转义也改不了它。

        实测过三种绕法，全部直接失配：
        ``&#x6D;agnet:?xt=…``、``magnet&#x3A;?xt=…``、``http://…``。

        因此解析里**不再**写 ``link.startswith("magnet:")`` ——
        那会是一段永不执行的分支，注入测试无法使其转红，
        却让人误以为有一层真实防护（约定 7：删功能就反转断言）。
        """
        import html as html_mod

        from app.providers.indexer.bitsearch import _MAGNET_RE

        for bogus in (
            'href="&#x6D;agnet:?xt=urn:btih:AAAA"',
            'href="magnet&#x3A;?xt=urn:btih:AAAA"',
            'href="http://example.com/?xt=urn:btih:AAAA"',
        ):
            assert _MAGNET_RE.search(bogus) is None, f"这种写法本该失配：{bogus}"

        found = _MAGNET_RE.search('href="magnet:?xt&#x3D;urn:btih:AAAA"')
        assert found is not None
        assert html_mod.unescape(found.group(1)).startswith("magnet:")

    def test_搜索词走quote_plus因为它是query参数(self):
        """与 BT4G 相反：BT4G 的关键词是**路径段**必须用 quote，
        本站是 ``?q=`` **query 参数**，``+`` 正是空格的正确编码。
        两者不能互换。"""
        url = self._provider()._search_url("king of comedy")
        assert "q=king+of+comedy" in url
        assert "%20" not in url

    def test_分页从1开始(self):
        """内部 page 从 0 开始，站点从 1 开始，差一就会跳过或重复一页。"""
        provider = self._provider()
        assert "page=" not in provider._search_url("x", 0)
        assert "page=2" in provider._search_url("x", 1)

    def test_排序参数来自配置(self):
        provider = BitsearchIndexer(
            {
                "name": "Bitsearch",
                "site_name": "Bitsearch",
                "options": {"sort": "seeders"},
            }
        )
        assert "sort=seeders" in provider._search_url("x")

    def test_没有全站最新时如实返回空且不委托给search(self):
        """站点没有该端点，伪造一批热门内容会污染订阅。

        光断言 ``== []`` 不够：删掉本类的覆写后，基类的
        ``fetch_latest`` 会退化成 ``await self.search("")``，
        而空关键词也提前返回 ``[]`` —— 既不发请求、也确实是空，
        于是断言全都通过（实测的假绿）。

        真正要钉的是「**本类自己**如实返回空」，
        所以断言它一次都没调用 ``search``。
        """
        import asyncio

        provider = self._provider()
        calls = []

        async def _spy(*args, **kwargs):
            calls.append((args, kwargs))
            return []

        provider.search = _spy  # type: ignore[method-assign]
        assert asyncio.run(provider.fetch_latest()) == []
        assert not calls, (
            "fetch_latest 委托给了 search —— 说明本类的覆写没了，"
            "走的是基类的「空搜索」退化路径"
        )

    def test_限流的500必须报错且带上状态码(self):
        """实测陷阱：并发压测时该站返回 ``500``/588 字节，看起来像
        「站点用 500 表达查无此项」。改成**串行 + 间隔 1.5s** 后，
        同一关键词 14 次请求**全部 200**，无结果时返回的是完整的
        ``No results found`` 页面 —— 那批 500 是限流。

        所以 500 必须**抛错**（限流，重试有意义），
        当成空结果会让用户以为站点没收录（ADR-82）。

        **为什么断言 status 而不只是"抛了 FetchError"**：
        换成 ``fetch_text`` 后它返回 ``None``，接着走到
        ``if not text: raise FetchError("返回空响应")`` —— 一样抛 FetchError！
        于是 ``pytest.raises(FetchError)`` 照样绿（实测的假绿）。
        真正的区别是 ``fetch_text_result`` 把 **HTTP 状态码**带了上来，
        用户才能看出是"站点 500 了"而不是"网络断了"。
        """
        import asyncio

        import httpx

        from app.utils import http as http_mod
        from app.utils.http import FetchError

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="x" * 588)

        original = http_mod.async_client

        def factory(timeout=None, headers=None, follow_redirects=True):
            return httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                headers=headers,
                follow_redirects=follow_redirects,
            )

        http_mod.async_client = factory
        try:
            with pytest.raises(FetchError) as caught:
                asyncio.run(self._provider().search("喜剧之王单口季"))
        finally:
            http_mod.async_client = original
        assert caught.value.status == 500, (
            "状态码没被带上来 —— 大概是用了 fetch_text（失败只返回 None），"
            f"实际 status={caught.value.status!r}"
        )

    def test_健康检查把限流如实报成失败(self):
        """降级不能装作成功（ADR-82）：连不上就要说连不上。"""
        import asyncio

        import httpx

        from app.utils import http as http_mod

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="rate limited")

        original = http_mod.async_client

        def factory(timeout=None, headers=None, follow_redirects=True):
            return httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                headers=headers,
                follow_redirects=follow_redirects,
            )

        http_mod.async_client = factory
        try:
            ok, message = asyncio.run(self._provider().health_check())
        finally:
            http_mod.async_client = original
        assert ok is False
        assert message

    def test_已注册进内置provider(self):
        """约定 11 的另一面：类写好了但没进 registry，用户根本用不到。

        必须用独立进程 —— 理由见 :meth:`TestBt4g解析.test_已注册进内置provider`。
        """
        _assert_registered_on_default_load("bitsearch", "BitsearchIndexer")

    def test_已进实测站点清单(self):
        """不进 BT_SITE_PRESETS，导航站上就看不到、加不了。"""
        from app.services.site_catalog import BT_SITE_PRESETS

        preset = next((item for item in BT_SITE_PRESETS if item["id"] == "bitsearch"), None)
        assert preset is not None
        assert preset["provider"] == "bitsearch"
        assert preset["measured"]
        assert preset["caveat"]
