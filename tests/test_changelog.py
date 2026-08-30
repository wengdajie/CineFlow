"""更新日志解析的测试。

**为什么值得测**：这个功能把「文档」当数据源，而文档是人手写的自由格式。
解析器一旦对某种写法失效，界面上就是**静默的空白**——不报错、不告警，
只是少了内容，很难被发现。所以这里既测真实文档能被解析出内容，
也用构造的样例钉住各种格式分支。
"""

from __future__ import annotations

import pytest

from app.services import changelog


def test_real_changelog_parses() -> None:
    """仓库里的真实变更日志必须能解析出内容。"""
    items = changelog.releases()
    assert len(items) >= 14, f"只解析出 {len(items)} 个版本"
    versions = [item["version"] for item in items]
    # 新版在前
    assert versions[0].startswith("1."), versions[:3]
    assert "1.0.0" in versions


def test_every_release_has_content() -> None:
    """每个版本都必须有标题且有条目——空白版本说明解析漏了某种写法。

    早期 v1.0.0~v1.2.0 用的是 ``- 🆕 xxx`` 扁平列表（没有 ### 分组），
    这条断言就是为了防止那几个版本在界面上显示成空卡片。
    """
    empty: list[str] = []
    for item in changelog.releases():
        if not item["title"] or item["item_count"] == 0:
            empty.append(item["version"])
    assert not empty, f"这些版本解析后没有内容：{empty}"


def test_latest_matches_first() -> None:
    latest = changelog.latest()
    assert latest is not None
    assert latest == changelog.releases()[0]


def test_no_markdown_or_emoji_leaks_into_titles() -> None:
    """界面直接展示这些文字，不该漏出 Markdown 记号或 emoji。

    分组名是**自由文本**（文档里除了「新增/修复」还有「破坏性变更」
    「站点适配实测结论」这类），所以不做白名单，只校验"干净"：
    没有 ``**``、没有反引号、不以 emoji 或标点开头。
    早期日志大量使用 🆕，emoji 范围一旦漏了就会变成「🆕 新增」直接显示出来。
    """
    for item in changelog.releases():
        assert not changelog._EMOJI.search(item["title"]), item["title"]
        for section in item["sections"]:
            name = section["name"]
            assert "**" not in name and "`" not in name, name
            assert not changelog._EMOJI.search(name), f"分组名残留 emoji：{name!r}"
            assert name == name.strip(), f"分组名有多余空白：{name!r}"
            assert name and name[0] not in "#*-·>", f"分组名以标记开头：{name!r}"
            for entry in section["items"]:
                assert "**" not in entry["title"]
                assert "`" not in entry["title"]
                assert not changelog._EMOJI.search(entry["title"]), entry["title"]


def test_parse_structured_format() -> None:
    """标准格式：## 版本 → ### 分组 → **条目** → - 要点。"""
    raw = """# 08 · 变更日志

前言，应被忽略。

---

## v9.9.9 · 2026-01-02 · 示例标题

> 这是摘要一行。
> 还有第二行。

### ✨ 新增

**① 做了件大事**
- 细节甲
- 细节乙

**② 又做一件**
- 细节丙

### 🐛 修复

**③ 修了个 bug**

### 🧪 门禁数字

| 门禁 | 前 | 后 |
|---|---|---|
| pytest | 1 | 2 |
"""
    items = changelog._parse(raw)
    assert len(items) == 1
    release = items[0]
    assert release["version"] == "9.9.9"
    assert release["date"] == "2026-01-02"
    assert release["title"] == "示例标题"
    assert "这是摘要一行。" in release["summary"]
    assert "还有第二行。" in release["summary"]
    names = [s["name"] for s in release["sections"]]
    assert names == ["新增", "修复", "门禁数字"]

    added = release["sections"][0]
    assert [i["title"] for i in added["items"]] == ["做了件大事", "又做一件"]
    assert added["items"][0]["points"] == ["细节甲", "细节乙"]
    # 圈码序号要被剥掉，界面自己排序号
    assert not added["items"][0]["title"].startswith("①")

    fixed = release["sections"][1]
    assert fixed["items"][0]["points"] == []

    gate = release["sections"][2]
    # 表格整行留在 notes，不拆成条目
    assert gate["items"] == []
    assert any("pytest" in line for line in gate["notes"])
    assert release["item_count"] == 3


def test_parse_flat_legacy_format() -> None:
    """早期扁平格式：没有 ### 分组，靠行首 emoji 归类。"""
    raw = """## v1.0.0 · 2026-08-28 · 首个版本

- 🆕 新功能甲
- 🆕 新功能乙
- 🔧 改了点东西
- 📝 补了文档
- ✅ 156 tests
- 没有 emoji 的一条
"""
    release = changelog._parse(raw)[0]
    groups = {s["name"]: [i["title"] for i in s["items"]] for s in release["sections"]}
    assert groups["新增"] == ["新功能甲", "新功能乙"]
    assert groups["变更"] == ["改了点东西"]
    assert groups["文档"] == ["补了文档"]
    assert groups["门禁数字"] == ["156 tests"]
    assert groups["更新内容"] == ["没有 emoji 的一条"]
    assert release["item_count"] == 6


def test_parse_heading_without_date_or_title() -> None:
    """标题里缺日期/缺副标题也不能崩。"""
    release = changelog._parse("## v2.0.0\n\n- 🆕 甲\n")[0]
    assert release["version"] == "2.0.0"
    assert release["date"] == ""
    assert release["title"] == ""
    assert release["item_count"] == 1


def test_parse_empty_and_garbage() -> None:
    """空文件与无版本节的文本都返回空列表，不抛异常。"""
    assert changelog._parse("") == []
    assert changelog._parse("随便一段没有版本号的文字\n- 列表\n") == []


def test_missing_file_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    """文档不存在时返回空列表而不是抛 500。

    这条很重要：``docs/`` 是否在 Docker 镜像里曾经是个真实差异
    （v1.11.0 才补上 COPY docs/），不能因为少个文件就让接口 500。
    """
    monkeypatch.setattr(
        changelog, "CHANGELOG_PATH", changelog.CHANGELOG_PATH.parent / "不存在.md"
    )
    assert changelog.releases() == []
    assert changelog.latest() is None
