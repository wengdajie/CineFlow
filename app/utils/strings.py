"""字符串与数值工具。"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone

_SIZE_UNITS = {
    "B": 1,
    "KB": 1024,
    "KIB": 1024,
    "MB": 1024**2,
    "MIB": 1024**2,
    "GB": 1024**3,
    "GIB": 1024**3,
    "TB": 1024**4,
    "TIB": 1024**4,
}
_ILLEGAL_PATH_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')
_SPACES = re.compile(r"\s+")


def normalize(text: str | None) -> str:
    """标准化文本：全角转半角、分隔符转空格、压缩空白。"""
    if not text:
        return ""
    value = unicodedata.normalize("NFKC", str(text))
    value = value.replace("_", " ").replace(".", " ").replace("+", " ")
    return _SPACES.sub(" ", value).strip()


def safe_filename(name: str, replacement: str = " ") -> str:
    """清理文件名中的非法字符。"""
    cleaned = _ILLEGAL_PATH_CHARS.sub(replacement, str(name or ""))
    cleaned = _SPACES.sub(" ", cleaned).strip(" .")
    return cleaned or "unknown"


def parse_size(value: str | float | None) -> int:
    """把 ``1.5 GB`` 之类的体积文本解析为字节数。"""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().upper().replace(",", "")
    if not text:
        return 0
    if text.isdigit():
        return int(text)
    match = re.match(r"([\d.]+)\s*([KMGT]?I?B)", text)
    if not match:
        digits = re.sub(r"[^\d]", "", text)
        return int(digits) if digits else 0
    number, unit = match.groups()
    try:
        return int(float(number) * _SIZE_UNITS.get(unit, 1))
    except ValueError:
        return 0


def format_size(num: float | None) -> str:
    """字节数格式化为可读文本。"""
    size = float(num or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.2f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.2f} TB"


def parse_datetime(value: str | None) -> datetime | None:
    """尽力解析多种时间格式，返回 naive UTC。"""
    if not value:
        return None
    text = str(value).strip()
    patterns = (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
    )
    for pattern in patterns:
        try:
            parsed = datetime.strptime(text, pattern)
        except ValueError:
            continue
        if parsed.tzinfo:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    return None


def split_keywords(value: str | None) -> list[str]:
    """把逗号/顿号/空格分隔的关键词拆成列表。"""
    if not value:
        return []
    parts = re.split(r"[,，、|/\s]+", str(value))
    return [part.strip() for part in parts if part.strip()]


def match_keywords(text: str, keywords: list[str] | str | None, *, mode: str = "any") -> bool:
    """关键词匹配，支持正则；``mode`` 为 ``any`` 或 ``all``。"""
    items = split_keywords(keywords) if isinstance(keywords, str) else (keywords or [])
    if not items:
        return mode == "all"
    lowered = text.lower()
    results = []
    for item in items:
        try:
            hit = re.search(item, text, re.IGNORECASE) is not None
        except re.error:
            hit = item.lower() in lowered
        results.append(hit)
    return all(results) if mode == "all" else any(results)


def truncate(text: str | None, limit: int = 120) -> str:
    """截断长文本。"""
    value = str(text or "")
    return value if len(value) <= limit else value[: limit - 1] + "…"


#: 公开 tracker。裸磁力要靠 DHT 慢慢找 peer，补几个公开 tracker
#: 能显著加快起步速度（原 torrents_csv.DEFAULT_TRACKERS，上移共享）。
DEFAULT_TRACKERS = (
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://exodus.desync.com:6969/announce",
)


def build_magnet(infohash: str, title: str, trackers: tuple[str, ...] = DEFAULT_TRACKERS) -> str:
    """由 infohash 拼出磁力链。

    ``dn=`` 必须带上：下游 ``guess_kind``/入库命名都靠标题，
    而详情页二段抓取的经验（见 generic_html 文档）是「不带 dn 的磁力
    会让标题退化」。数据源本来就给了 name，没有理由丢掉。

    ``trackers`` 由调用方决定：裸磁力要靠 DHT 慢慢找 peer，
    补几个公开 tracker 能显著加快起步速度（见 torrents_csv 的
    ``DEFAULT_TRACKERS``）；站点已提供完整磁力时不需要。
    """
    from urllib.parse import quote

    parts = [f"magnet:?xt=urn:btih:{str(infohash).lower()}"]
    if title:
        parts.append(f"dn={quote(title)}")
    parts.extend(f"tr={quote(tracker, safe='')}" for tracker in trackers)
    return "&".join(parts)
