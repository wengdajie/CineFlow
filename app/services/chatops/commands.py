"""ChatOps 指令解析。

把自然一点的中文/英文口令解析成结构化指令，避免用户去记复杂语法。
支持的说法示例：

    搜索 庆余年              /  search 庆余年
    搜 凡人修仙传 第二季
    下载 2                   （承接上一次搜索结果，选第 2 条）
    订阅 凡人修仙传 第二季
    状态 / status            （下载中的任务概览）
    订阅列表 / subs
    转存                     （批量转存待处理网盘资源）
    帮助 / help
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: 指令别名 -> 规范指令名
ALIASES: dict[str, str] = {
    # 搜索
    "搜索": "search", "搜": "search", "查": "search", "查找": "search",
    "search": "search", "s": "search", "find": "search",
    # 下载
    "下载": "download", "下": "download", "download": "download", "dl": "download",
    "d": "download", "get": "download",
    # 订阅
    "订阅": "subscribe", "追": "subscribe", "追剧": "subscribe", "追新": "subscribe",
    "subscribe": "subscribe", "sub": "subscribe",
    # 订阅列表
    "订阅列表": "subscribes", "我的订阅": "subscribes", "subs": "subscribes",
    "subscribes": "subscribes",
    # 状态
    "状态": "status", "进度": "status", "任务": "status", "status": "status",
    "st": "status",
    # 网盘转存
    "转存": "transfer", "网盘": "transfer", "transfer": "transfer", "save": "transfer",
    # 热榜
    "热榜": "trending", "排行": "trending", "热度": "trending", "trending": "trending",
    "hot": "trending",
    # 帮助
    "帮助": "help", "help": "help", "?": "help", "？": "help", "菜单": "help",
}

#: 中文季号 -> 数字
_CN_NUMBERS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

_SEASON_RE = re.compile(r"第\s*([0-9一二三四五六七八九十]+)\s*季|(?<![A-Za-z0-9])S(\d{1,2})(?!\d)", re.I)
_EPISODE_RE = re.compile(r"第\s*(\d{1,4})\s*集|(?<![A-Za-z0-9])E(\d{1,4})(?!\d)", re.I)


@dataclass
class Command:
    """解析后的指令。"""

    name: str
    #: 主参数（如搜索关键词）
    argument: str = ""
    #: 序号（如「下载 2」里的 2）
    index: int | None = None
    season: int | None = None
    episode: int | None = None
    raw: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.name)


def _parse_season(text: str) -> tuple[int | None, str]:
    """抽取季号并从文本中移除。"""
    match = _SEASON_RE.search(text)
    if not match:
        return None, text
    raw = match.group(1) or match.group(2) or ""
    season = int(raw) if raw.isdigit() else _CN_NUMBERS.get(raw.strip())
    return season, (text[: match.start()] + " " + text[match.end() :]).strip()


def _parse_episode(text: str) -> tuple[int | None, str]:
    """抽取集号并从文本中移除。"""
    match = _EPISODE_RE.search(text)
    if not match:
        return None, text
    raw = match.group(1) or match.group(2) or ""
    episode = int(raw) if raw.isdigit() else None
    return episode, (text[: match.start()] + " " + text[match.end() :]).strip()


def parse(text: str) -> Command:
    """把一条消息解析成指令。

    无法识别时返回 ``name`` 为空的 :class:`Command`，
    调用方应回复帮助信息而不是静默丢弃。
    """
    raw = str(text or "").strip()
    if not raw:
        return Command(name="", raw=raw)

    # 去掉 @机器人 与前导斜杠（Telegram 习惯 /search）
    cleaned = re.sub(r"@[\w\-.]+", " ", raw).strip()
    cleaned = re.sub(r"^/+", "", cleaned).strip()
    if not cleaned:
        return Command(name="", raw=raw)

    # 首个词作为指令；支持「搜索:庆余年」「搜索：庆余年」。
    # 注意只替换紧跟在指令词后的那个冒号，否则会把 magnet:?xt= 和 https:// 拆坏。
    cleaned = re.sub(r"^([^\s:：]+)[:：]\s*", r"\1 ", cleaned)
    parts = cleaned.split(None, 1)
    head = parts[0].strip().lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    name = ALIASES.get(head)
    if not name:
        # 整句没有指令词：若是纯数字当作「下载 N」，否则当作搜索
        if cleaned.isdigit():
            return Command(name="download", index=int(cleaned), raw=raw)
        return Command(name="search", argument=cleaned, raw=raw)

    command = Command(name=name, raw=raw)

    if name == "download":
        # 「下载 2」取序号；「下载 magnet:...」或「下载 https://」直接投链接
        if rest.isdigit():
            command.index = int(rest)
        elif rest:
            # 直接给链接（magnet:/http）或给片名，都放进 argument
            command.argument = rest
        return command

    if name in ("search", "subscribe"):
        season, rest = _parse_season(rest)
        episode, rest = _parse_episode(rest)
        command.season = season
        command.episode = episode
        command.argument = re.sub(r"\s{2,}", " ", rest).strip()
        return command

    command.argument = rest
    return command


HELP_TEXT = """CineFlow 指令帮助

🔍 搜索 <片名>          例：搜索 庆余年 第二季
   也可直接发片名，默认按搜索处理
⬇️ 下载 <序号>          例：下载 2（承接上一次搜索结果）
   下载 <磁力/链接>      直接投递指定链接
⭐ 订阅 <片名>          例：订阅 凡人修仙传 第二季（自动追新）
📋 订阅列表             查看追剧中的订阅与缺集
📊 状态                 查看下载中的任务进度
☁️ 转存                 批量转存待处理的网盘资源
🔥 热榜                 查看资源热度排行
❓ 帮助                 显示本说明"""
