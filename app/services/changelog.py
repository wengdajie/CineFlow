"""更新日志解析：把 ``docs/08-变更日志.md`` 变成结构化数据给「更新日志」页用。

**为什么解析 Markdown 而不是单独维护一份数据**：变更日志已经是每次发版必写的
唯一事实来源（78KB、十多个版本）。再单独维护一张表或一个 JSON，必然出现
"文档写了、接口没同步"的漂移——那比没有这个页面更糟。所以这里**只读**，
把已有文档当数据库。

**关于镜像里的路径**：``docs/`` 原先没有被 ``COPY`` 进 Docker 镜像，
所以线上会解析不到任何内容（本地能跑、Docker 里空白，是最难发现的一类问题）。
v1.11.0 已在 Dockerfile 里补上 ``COPY docs/ ./docs/``。即使如此，
这里仍然对"文件不存在"做优雅降级：返回空列表而不是抛 500。

解析规则贴着文档现有格式，不要求作者改写习惯：

* ``## v1.10.1 · 2026-08-30 · 标题`` → 一个版本节（version / date / title）
* 节内 ``### ✨ 新增`` / ``### 🐛 修复`` … → 分组（去掉 emoji 留纯文字）
* 分组内 ``**① xxx**`` → 条目标题；其下 ``- `` 列表 → 条目要点
* ``### 🧪 门禁数字`` 里的表格不拆条目，整段作为 ``notes`` 保留

早期版本（v1.0.0~v1.2.0）没有 ``###`` 分组，而是 ``- 🆕 xxx`` 这样的扁平列表。
不能因此让这几个版本在界面上显示成空白，所以按 emoji 前缀归类到「新增/变更/
修复/文档」，并把没有分组的裸列表兜到「更新内容」里。
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from app.core.config import ROOT_DIR
from app.core.logger import get_logger

logger = get_logger(__name__)

CHANGELOG_PATH = ROOT_DIR / "docs" / "08-变更日志.md"

#: ``## v1.10.1 · 2026-08-30 · 标题``（分隔符是全角间隔号，日期可缺省）
_HEADING = re.compile(
    r"^##\s+v(?P<version>[0-9][0-9A-Za-z.\-]*)\s*(?:·\s*(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})\s*)?(?:·\s*(?P<title>.*))?$"
)
#: ``### ✨ 新增``
_SECTION = re.compile(r"^###\s+(?P<name>.+?)\s*$")
#: ``**① 标题**`` 或 ``**标题**``
_ITEM = re.compile(r"^\*\*(?P<text>.+?)\*\*\s*$")
#: 去掉标题里的 emoji 与前后空白，只留"新增/变更/修复"这类纯文字。
#: 范围必须覆盖 U+1F100–1F1FF（含 🆕 U+1F195，早期日志大量使用）——
#: 漏掉它会让分组名变成"🆕 新增"这种带图形的脏字符串直接显示到界面上。
_EMOJI = re.compile(
    "["
    "\U0001f100-\U0001f1ff"  # 括号/字母图形符，🆕 在这里
    "\U0001f300-\U0001faff"  # 常规表情与符号
    "\U00002600-\U000027bf"  # 杂项符号与装饰
    "\U00002b00-\U00002bff"  # 箭头类
    "\u2190-\u21ff"          # 箭头
    "\ufe0f\u20e3"           # 变体选择符 / 组合键帽
    "]+"
)


#: 早期扁平列表的 emoji 前缀 → 分组名。顺序无关，取首个命中。
_PREFIX_GROUPS = (
    ("🆕", "新增"),
    ("✨", "新增"),
    ("🔧", "变更"),
    ("🔄", "变更"),
    ("🐛", "修复"),
    ("🩹", "修复"),
    ("📝", "文档"),
    ("📚", "文档"),
    ("✅", "门禁数字"),
    ("🧪", "门禁数字"),
)


def _group_for(raw_point: str) -> str:
    """按行首 emoji 判断这条属于哪个分组，认不出就归「更新内容」。"""
    text = raw_point.strip()
    for marker, name in _PREFIX_GROUPS:
        if text.startswith(marker):
            return name
    return "更新内容"


def _clean(text: str) -> str:
    """去掉 emoji 与 Markdown 强调符，供界面直接展示。"""
    text = _EMOJI.sub("", text or "")
    text = text.replace("**", "").replace("`", "")
    return text.strip(" ·-—\t")


def _strip_marker(text: str) -> str:
    """去掉条目前的编号圈符（①②…），界面自己会排序号。"""
    return re.sub(r"^[\u2460-\u2473\u3251-\u325f\u32b1-\u32bf]\s*", "", text).strip()


def _parse(raw: str) -> list[dict[str, Any]]:
    releases: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    section: dict[str, Any] | None = None
    item: dict[str, Any] | None = None

    for line in raw.splitlines():
        stripped = line.strip()
        heading = _HEADING.match(stripped)
        if heading:
            current = {
                "version": heading.group("version"),
                "date": heading.group("date") or "",
                "title": _clean(heading.group("title") or ""),
                "sections": [],
                "summary": "",
            }
            releases.append(current)
            section = None
            item = None
            continue
        if current is None:
            continue  # 文件开头的说明文字，跳过

        # 引用块当版本摘要（文档里每节开头的 "> 本轮用户提了三组需求…"）
        if stripped.startswith(">"):
            text = _clean(stripped.lstrip("> ").strip())
            if text:
                current["summary"] = (current["summary"] + " " + text).strip()
            continue

        match = _SECTION.match(stripped)
        if match:
            section = {"name": _clean(match.group("name")), "items": [], "notes": []}
            current["sections"].append(section)
            item = None
            continue

        if section is None:
            # 早期版本没有 ### 分组，直接就是 "- 🆕 xxx" 列表。
            # 按 emoji 前缀归类，避免这些版本在界面上是空白的。
            if stripped.startswith(("- ", "* ")):
                raw_point = stripped[2:]
                name = _group_for(raw_point)
                point = _clean(raw_point)
                if not point:
                    continue
                bucket = next(
                    (s for s in current["sections"] if s["name"] == name), None
                )
                if bucket is None:
                    bucket = {"name": name, "items": [], "notes": []}
                    current["sections"].append(bucket)
                bucket["items"].append({"title": point, "points": []})
            continue

        match = _ITEM.match(stripped)
        if match:
            item = {"title": _strip_marker(_clean(match.group("text"))), "points": []}
            section["items"].append(item)
            continue

        if stripped.startswith(("- ", "* ")):
            point = _clean(stripped[2:])
            if not point:
                continue
            if item is not None:
                item["points"].append(point)
            else:
                # 没有粗体小标题的裸列表，自成一条
                section["items"].append({"title": point, "points": []})
            continue

        # 表格与其它正文（如门禁数字表）整行留存，前端用等宽字体展示
        if stripped.startswith("|"):
            section["notes"].append(stripped)

    # 统计条目数，前端不用自己数
    for release in releases:
        release["item_count"] = sum(len(s["items"]) for s in release["sections"])
    return releases


@lru_cache(maxsize=1)
def _cached(mtime: float) -> list[dict[str, Any]]:
    """按文件 mtime 缓存：文档不变就不重复解析 78KB。"""
    try:
        raw = CHANGELOG_PATH.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("读取变更日志失败 %s: %s", CHANGELOG_PATH, exc)
        return []
    return _parse(raw)


def releases() -> list[dict[str, Any]]:
    """全部版本，按文档顺序（新版在前）。文件缺失时返回空列表。"""
    try:
        mtime = CHANGELOG_PATH.stat().st_mtime
    except OSError:
        logger.warning("变更日志不存在：%s", CHANGELOG_PATH)
        return []
    return _cached(mtime)


def latest() -> dict[str, Any] | None:
    """最新一个版本节。"""
    items = releases()
    return items[0] if items else None
