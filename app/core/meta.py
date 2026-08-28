"""资源名称识别（种子名/网盘文件名 -> 结构化元数据）。

这是自动化链路的第一环：把 ``工作细胞.S01E05.2160p.WEB-DL.H265-OurTV.mkv``
这类名称解析成标题、季、集、分辨率、质量、编码、字幕组等字段。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.schemas.enums import MediaType
from app.utils.strings import normalize, safe_filename

# ---------------------------------------------------------------- 词典
RESOLUTIONS: dict[str, tuple[str, ...]] = {
    "2160p": ("2160p", "4k", "uhd", "2160i", "3840x2160"),
    "1080p": ("1080p", "1080i", "fhd", "1920x1080"),
    "720p": ("720p", "hd", "1280x720"),
    "576p": ("576p",),
    "480p": ("480p", "sd"),
}

QUALITIES: dict[str, tuple[str, ...]] = {
    "REMUX": ("remux",),
    "BluRay": ("bluray", "blu-ray", "bdrip", "brrip", "bd"),
    "UHDBluRay": ("uhdbluray", "uhd bluray", "uhd-bluray"),
    "WEB-DL": ("web-dl", "webdl", "webrip", "web"),
    "HDTV": ("hdtv", "hdtvrip"),
    "DVD": ("dvdrip", "dvd", "dvdscr"),
    "TC": ("tc", "hdtc"),
    "CAM": ("cam", "hdcam", "枪版"),
}

VIDEO_CODECS: dict[str, tuple[str, ...]] = {
    "H265": ("h265", "h 265", "h.265", "x265", "x 265", "hevc"),
    "H264": ("h264", "h 264", "h.264", "x264", "x 264", "avc"),
    "AV1": ("av1",),
    "VC-1": ("vc-1", "vc1"),
    "MPEG2": ("mpeg2", "mpeg-2"),
}

AUDIO_CODECS: dict[str, tuple[str, ...]] = {
    "TrueHD Atmos": ("truehd atmos", "atmos"),
    "TrueHD": ("truehd", "true-hd"),
    "DTS-HD MA": ("dts-hd ma", "dtshd ma", "dts-hdma"),
    "DTS-X": ("dts-x", "dtsx"),
    "DTS": ("dts",),
    "FLAC": ("flac",),
    "DDP": ("ddp", "eac3", "e-ac3", "dd+"),
    "AC3": ("ac3", "dd5 1", "dd2 0"),
    "AAC": ("aac",),
    "MP3": ("mp3",),
}

EFFECTS: dict[str, tuple[str, ...]] = {
    "Dolby Vision": ("dolby vision", "dovi", "dv"),
    "HDR10+": ("hdr10+", "hdr10plus"),
    "HDR10": ("hdr10",),
    "HDR": ("hdr",),
    "SDR": ("sdr",),
    "3D": ("3d",),
}

ANIME_HINTS = (
    "anime", "番剧", "动漫", "动画", "bangumi", "喵萌", "桜都", "澄空",
    "lolihouse", "sweetsub", "nekomoe", "ants", "vcb-studio", "ohys",
)

CN_NUMBERS = {
    "零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
    "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12,
    "十三": 13, "十四": 14, "十五": 15, "十六": 16, "十七": 17,
    "十八": 18, "十九": 19, "二十": 20, "廿": 20,
}

# ---------------------------------------------------------------- 正则
_YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
_SxxExx_RE = re.compile(r"s(\d{1,3})\s?e(\d{1,4})(?:\s?-\s?e?(\d{1,4}))?", re.IGNORECASE)
_SEASON_RE = re.compile(r"(?:\bs(?:eason)?\s?(\d{1,3})\b)", re.IGNORECASE)
_CN_SEASON_RE = re.compile(r"第\s*([0-9]+|[零一二三四五六七八九十廿]+)\s*[季部]")
_EPISODE_ONLY_RE = re.compile(r"\be(?:p|pisode)?\s?(\d{1,4})(?:\s?-\s?e?p?(\d{1,4}))?\b", re.IGNORECASE)
_CN_EPISODE_RE = re.compile(
    r"第\s*([0-9]+|[零一二三四五六七八九十廿]+)\s*(?:-\s*第?\s*([0-9]+|[零一二三四五六七八九十廿]+)\s*)?[集话話期]"
)
_BRACKET_EPISODE_RE = re.compile(r"[\[【(]\s*(\d{1,4})(?:\s?v\d)?\s*(?:end|fin|完)?\s*[\]】)]", re.IGNORECASE)
_RANGE_RE = re.compile(r"(?<!\d)(\d{1,3})\s*[-~]\s*(\d{1,3})(?:\s*集|\s*话|\s*話|\s*ep?)", re.IGNORECASE)
_GROUP_RE = re.compile(r"[-@]\s*([A-Za-z0-9_.]{2,20})\s*$")
_BRACKET_GROUP_RE = re.compile(r"^[\[【]([^\]】]{2,24})[\]】]")
_TAIL_TAGS_RE = re.compile(
    r"\b(complete|repack|proper|internal|limited|extended|uncut|remastered|"
    r"多国语言|国粤双语|国语|中字|简繁|简体|繁体|双语|无删减|完结|全集|"
    r"内嵌|外挂|特效字幕|合集)\b",
    re.IGNORECASE,
)
_EPISODE_TOTAL_RE = re.compile(r"(?:全|共)\s*(\d{1,4})\s*[集话話期]")
_CN_TITLE_RE = re.compile(r"[\u4e00-\u9fff]")


def _cn_to_int(text: str) -> int | None:
    """中文数字转整数（支持 ``十二`` / ``二十三``）。"""
    text = text.strip()
    if text.isdigit():
        return int(text)
    if text in CN_NUMBERS:
        return CN_NUMBERS[text]
    if "十" in text:
        left, _, right = text.partition("十")
        tens = CN_NUMBERS.get(left, 1) if left else 1
        ones = CN_NUMBERS.get(right, 0) if right else 0
        return tens * 10 + ones
    return None


def _match_dict(text: str, mapping: dict[str, tuple[str, ...]]) -> str | None:
    """在文本中按词典顺序匹配第一个命中的标准名。"""
    lowered = f" {text.lower()} "
    for standard, aliases in mapping.items():
        for alias in aliases:
            pattern = re.escape(alias)
            # 后接数字是合法的（DDP5.1 / HDR10 / H 264），故只排除后接字母
            if re.search(rf"(?<![a-z0-9]){pattern}(?![a-z])", lowered):
                return standard
    return None


@dataclass
class MetaInfo:
    """资源元数据识别结果。"""

    raw: str = ""
    title: str = ""
    cn_title: str = ""
    en_title: str = ""
    year: int | None = None
    media_type: str = MediaType.UNKNOWN.value
    season: int | None = None
    episodes: list[int] = field(default_factory=list)
    total_episodes: int | None = None
    resolution: str | None = None
    quality: str | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    effect: str | None = None
    release_group: str | None = None
    is_season_pack: bool = False
    extension: str | None = None

    @property
    def episode_start(self) -> int | None:
        return min(self.episodes) if self.episodes else None

    @property
    def episode_end(self) -> int | None:
        return max(self.episodes) if self.episodes else None

    @property
    def season_episode_text(self) -> str:
        """``S01E05`` / ``S01E05-E08`` / ``S01`` 形式的短标识。"""
        if self.season is None and not self.episodes:
            return ""
        if not self.episodes:
            return f"S{self.season:02d}"
        season = self.season if self.season is not None else 1
        if len(self.episodes) == 1:
            return f"S{season:02d}E{self.episodes[0]:02d}"
        return f"S{season:02d}E{self.episode_start:02d}-E{self.episode_end:02d}"

    def display_name(self) -> str:
        """人类可读名称。"""
        parts = [self.title or self.raw]
        if self.year:
            parts.append(f"({self.year})")
        marker = self.season_episode_text
        if marker:
            parts.append(marker)
        if self.resolution:
            parts.append(self.resolution)
        if self.quality:
            parts.append(self.quality)
        return " ".join(parts)

    def to_dict(self) -> dict:
        return {
            "raw": self.raw,
            "title": self.title,
            "cn_title": self.cn_title,
            "en_title": self.en_title,
            "year": self.year,
            "media_type": self.media_type,
            "season": self.season,
            "episodes": self.episodes,
            "total_episodes": self.total_episodes,
            "resolution": self.resolution,
            "quality": self.quality,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "effect": self.effect,
            "release_group": self.release_group,
            "is_season_pack": self.is_season_pack,
            "extension": self.extension,
        }


def _first_segment(text: str) -> str:
    """取 ``/`` 或 ``|`` 分隔的首个非空片段。"""
    for segment in re.split(r"[/|]", text):
        cleaned = segment.strip(" -–—.")
        if cleaned:
            return cleaned
    return ""


def _extract_titles(text: str) -> tuple[str, str]:
    """从已裁剪的标题段中分离中文名与英文名。"""
    text = text.strip(" -–—.[]【】()")
    if not text:
        return "", ""
    # 常见形式：中文名 English Name / 中文名.English.Name
    chinese_chars = _CN_TITLE_RE.findall(text)
    if not chinese_chars:
        return "", text.strip()
    # 找到最后一个中文字符的位置，其后视为英文名
    last_cn = max(text.rfind(char) for char in set(chinese_chars))
    cn_part = text[: last_cn + 1]
    en_part = text[last_cn + 1 :]
    # 中文名内部可能夹着英文（如 "凡人修仙传 A Record"），保留分段首部
    cn_part = _first_segment(cn_part)
    en_part = _first_segment(en_part)
    if len(en_part) < 2:
        en_part = ""
    return cn_part, en_part


def parse(name: str, *, is_file: bool = False) -> MetaInfo:
    """解析资源名称。

    Args:
        name: 种子标题、网盘分享标题或文件名。
        is_file: 为 ``True`` 时会剥离扩展名。
    """
    meta = MetaInfo(raw=str(name or ""))
    if not meta.raw:
        return meta

    work = meta.raw
    if is_file:
        match = re.search(r"(\.[A-Za-z0-9]{2,5})$", work)
        if match:
            meta.extension = match.group(1).lower()
            work = work[: match.start()]

    # 括号中的字幕组通常在最前
    group_match = _BRACKET_GROUP_RE.match(work)
    if group_match and not _YEAR_RE.fullmatch(group_match.group(1).strip()):
        candidate = group_match.group(1).strip()
        if not re.fullmatch(r"[\d\s.\-]+", candidate):
            meta.release_group = candidate

    text = normalize(work.replace("】", "] ").replace("【", " ["))
    text = text.replace("[", " ").replace("]", " ").replace("(", " ").replace(")", " ")
    text = re.sub(r"\s+", " ", text).strip()

    # ---- 技术属性
    meta.resolution = _match_dict(text, RESOLUTIONS)
    meta.quality = _match_dict(text, QUALITIES)
    meta.video_codec = _match_dict(text, VIDEO_CODECS)
    meta.audio_codec = _match_dict(text, AUDIO_CODECS)
    meta.effect = _match_dict(text, EFFECTS)

    if not meta.release_group:
        tail = _GROUP_RE.search(work.strip())
        if tail:
            candidate = tail.group(1).strip(" .")
            if candidate and not candidate.isdigit():
                meta.release_group = candidate

    # ---- 季集
    cut_index = len(text)

    se_match = _SxxExx_RE.search(text)
    if se_match:
        meta.season = int(se_match.group(1))
        start = int(se_match.group(2))
        end = int(se_match.group(3)) if se_match.group(3) else start
        meta.episodes = list(range(min(start, end), max(start, end) + 1))
        cut_index = min(cut_index, se_match.start())
    else:
        season_match = _CN_SEASON_RE.search(text)
        if season_match:
            meta.season = _cn_to_int(season_match.group(1))
            cut_index = min(cut_index, season_match.start())
        else:
            plain_season = _SEASON_RE.search(text)
            if plain_season:
                meta.season = int(plain_season.group(1))
                cut_index = min(cut_index, plain_season.start())

        cn_episode = _CN_EPISODE_RE.search(text)
        range_match = _RANGE_RE.search(text)
        if cn_episode:
            start = _cn_to_int(cn_episode.group(1))
            end = _cn_to_int(cn_episode.group(2)) if cn_episode.group(2) else start
            if start is not None and end is not None:
                meta.episodes = list(range(min(start, end), max(start, end) + 1))
            cut_index = min(cut_index, cn_episode.start())
        elif range_match:
            start, end = int(range_match.group(1)), int(range_match.group(2))
            if 0 < start <= end <= 999:
                meta.episodes = list(range(start, end + 1))
                cut_index = min(cut_index, range_match.start())
        else:
            ep_match = _EPISODE_ONLY_RE.search(text)
            if ep_match:
                start = int(ep_match.group(1))
                end = int(ep_match.group(2)) if ep_match.group(2) else start
                meta.episodes = list(range(min(start, end), max(start, end) + 1))
                cut_index = min(cut_index, ep_match.start())
            else:
                bracket_ep = _BRACKET_EPISODE_RE.search(work)
                if bracket_ep:
                    value = int(bracket_ep.group(1))
                    if 0 < value <= 999:
                        meta.episodes = [value]

    total_match = _EPISODE_TOTAL_RE.search(text)
    if total_match:
        meta.total_episodes = int(total_match.group(1))
        cut_index = min(cut_index, total_match.start())

    # ---- 年份
    year_iter = list(_YEAR_RE.finditer(text))
    if year_iter:
        chosen = year_iter[0]
        for candidate in year_iter:
            if candidate.start() < cut_index:
                chosen = candidate
                break
        meta.year = int(chosen.group(1))
        cut_index = min(cut_index, chosen.start())

    # 技术关键字位置也是标题的边界
    for keyword_map in (RESOLUTIONS, QUALITIES, VIDEO_CODECS, EFFECTS):
        for aliases in keyword_map.values():
            for alias in aliases:
                found = re.search(
                    rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text, re.IGNORECASE
                )
                if found and found.start() > 0:
                    cut_index = min(cut_index, found.start())

    title_part = text[:cut_index] if cut_index > 0 else text
    title_part = _TAIL_TAGS_RE.sub(" ", title_part)
    title_part = re.sub(r"\s+", " ", title_part).strip(" -–—.")
    if meta.release_group and title_part.lower().startswith(meta.release_group.lower()):
        title_part = title_part[len(meta.release_group) :].strip(" -–—.")

    # 括号型集号（如 ``[12]``）不参与标题裁剪，需从尾部单独剔除
    if meta.episodes:
        title_part = re.sub(
            rf"\s+0*{meta.episodes[0]}(?:\s+end|\s+fin|\s+完)?$",
            "",
            title_part,
            flags=re.IGNORECASE,
        ).strip(" -–—.")

    meta.cn_title, meta.en_title = _extract_titles(title_part)
    meta.title = safe_filename(meta.cn_title or meta.en_title or title_part or meta.raw)

    # ---- 类型判定
    lowered = f"{meta.raw.lower()} {text.lower()}"
    if meta.episodes or meta.season is not None or meta.total_episodes:
        meta.media_type = (
            MediaType.ANIME.value
            if any(hint in lowered for hint in ANIME_HINTS)
            else MediaType.TV.value
        )
    elif meta.year:
        meta.media_type = MediaType.MOVIE.value

    meta.is_season_pack = bool(
        meta.season is not None
        and (not meta.episodes or len(meta.episodes) > 1)
    ) or bool(re.search(r"\b(complete|全集|合集)\b", lowered))

    return meta


def guess_episodes(name: str) -> list[int]:
    """便捷函数：仅取集号列表。"""
    return parse(name).episodes


