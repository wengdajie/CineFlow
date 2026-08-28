"""NFO 刮削：把识别到的元数据写成媒体服务器能读的 XML + 图片。

**为什么需要**：v1.3.0 之前 CineFlow 只做「规范命名」，识别工作全丢给
Emby/Jellyfin/Plex 自己做。对国产剧、冷门片、动漫，媒体服务器的匹配率很低，
表现为「入库了但没有海报、剧情、演员」。写 NFO 是业界共识做法
（对标项目里几乎人人都有，见 docs/09 差距矩阵 #6）。

**分层**：本模块属于 ``core``，**不做任何网络 IO**——只负责把
``MetaInfo`` + 可选的 TMDB 字典渲染成 XML 文本，因此可以完全离线单测。
真正的下载（图片、TMDB 请求）在 ``app/services/scraper.py``。

**NFO 规范参考**：Kodi/Emby/Jellyfin 共用一套约定：
- 电影：与视频同名的 ``xxx.nfo``，根节点 ``<movie>``
- 剧集单集：与视频同名的 ``xxx.nfo``，根节点 ``<episodedetails>``
- 剧集根目录：``tvshow.nfo``，根节点 ``<tvshow>``
- 季目录：``season.nfo``，根节点 ``<season>``
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from app.core.meta import MetaInfo
from app.schemas.enums import MediaType

#: NFO 里图片类型 -> 媒体服务器约定的本地文件名（不含扩展名）
#: 注意 Emby 认 ``poster``/``fanart``，Kodi 也认，是最通用的一组命名
IMAGE_FILENAMES: dict[str, str] = {
    "poster": "poster",
    "backdrop": "fanart",
    "logo": "logo",
    "thumb": "thumb",
}


@dataclass
class NfoDocument:
    """一份待落盘的 NFO（及其配套图片）。

    用 dataclass 而不是直接写文件，是为了让 ``core`` 保持无 IO：
    service 层拿到它再决定写到哪、要不要下图。
    """

    #: 相对于媒体文件所在目录的文件名，如 ``movie.nfo`` / ``tvshow.nfo``
    filename: str
    #: XML 文本（已含声明）
    content: str
    #: 图片类型 -> 远程 URL，交给 service 层下载
    images: dict[str, str] = field(default_factory=dict)

    def write_to(self, directory: Path, *, overwrite: bool = True) -> Path | None:
        """把 XML 落盘，返回实际写入路径；跳过时返回 ``None``。"""
        target = Path(directory) / self.filename
        if target.exists() and not overwrite:
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.content, encoding="utf-8")
        return target


def _sub(parent: ET.Element, tag: str, value: Any) -> ET.Element | None:
    """添加子节点；空值直接跳过，避免生成 ``<plot></plot>`` 这种噪音。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    node = ET.SubElement(parent, tag)
    node.text = text
    return node


def _serialize(root: ET.Element) -> str:
    """序列化为带声明的 UTF-8 XML 文本。

    手动加声明而不用 ``ET.ElementTree.write``，是为了统一换行与缩进，
    让生成的文件对人类可读（用户经常要手工核对 NFO）。
    """
    _indent(root)
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + body + "\n"


def _indent(element: ET.Element, level: int = 0) -> None:
    """就地美化缩进（Python 3.9+ 有 ET.indent，这里自己实现以保持行为稳定）。"""
    pad = "\n" + "  " * level
    if len(element):
        if not (element.text or "").strip():
            element.text = pad + "  "
        for child in element:
            _indent(child, level + 1)
        if not (child.tail or "").strip():
            child.tail = pad
    if level and not (element.tail or "").strip():
        element.tail = pad


def _add_common(root: ET.Element, meta: MetaInfo, detail: dict[str, Any] | None) -> None:
    """写入电影/剧集通用字段。

    ``detail`` 是 TMDB 返回的规范化字典（可能为 None——**未配 TMDB 时降级**，
    仍然写出标题/年份/季集，保证 NFO 一定可用）。
    """
    detail = detail or {}
    title = detail.get("title") or meta.cn_title or meta.title or meta.raw
    _sub(root, "title", title)
    original = detail.get("original_title") or meta.en_title
    if original and original != title:
        _sub(root, "originaltitle", original)
    _sub(root, "sorttitle", title)
    _sub(root, "plot", detail.get("overview"))
    _sub(root, "outline", detail.get("overview"))
    year = detail.get("year") or meta.year
    _sub(root, "year", year)
    _sub(root, "premiered", detail.get("release_date"))
    rating = detail.get("vote_average")
    if rating:
        node = ET.SubElement(root, "rating")
        node.text = f"{float(rating):.1f}"

    for genre in detail.get("genres") or []:
        _sub(root, "genre", genre)
    for studio in detail.get("studios") or []:
        _sub(root, "studio", studio)

    # 演职员：Emby 用 <actor><name>/<role>/<thumb>
    for person in detail.get("actors") or []:
        actor = ET.SubElement(root, "actor")
        _sub(actor, "name", person.get("name"))
        _sub(actor, "role", person.get("role"))
        _sub(actor, "thumb", person.get("thumb"))
    for director in detail.get("directors") or []:
        _sub(root, "director", director)

    # 外部 ID：这是媒体服务器"认人"的关键，有它就不会刮错片
    tmdb_id = detail.get("tmdb_id") or detail.get("id")
    if tmdb_id:
        _sub(root, "tmdbid", tmdb_id)
        unique = ET.SubElement(root, "uniqueid", {"type": "tmdb", "default": "true"})
        unique.text = str(tmdb_id)
    if detail.get("imdb_id"):
        _sub(root, "imdbid", detail["imdb_id"])
        unique = ET.SubElement(root, "uniqueid", {"type": "imdb"})
        unique.text = str(detail["imdb_id"])

    # 技术规格写进 <fileinfo>，让客户端不必探测文件就知道分辨率
    if meta.resolution or meta.video_codec or meta.audio_codec:
        fileinfo = ET.SubElement(root, "fileinfo")
        stream = ET.SubElement(fileinfo, "streamdetails")
        if meta.resolution or meta.video_codec:
            video = ET.SubElement(stream, "video")
            _sub(video, "codec", meta.video_codec)
            # 只写常见档位的像素高度，未知分辨率不猜
            heights = {"2160p": 2160, "1080p": 1080, "720p": 720, "480p": 480}
            height = heights.get((meta.resolution or "").lower())
            if height:
                _sub(video, "height", height)
                _sub(video, "width", int(height * 16 / 9))
        if meta.audio_codec:
            audio = ET.SubElement(stream, "audio")
            _sub(audio, "codec", meta.audio_codec)


def _collect_images(detail: dict[str, Any] | None) -> dict[str, str]:
    """从 TMDB 详情里挑出要下载的图片 URL。"""
    detail = detail or {}
    images: dict[str, str] = {}
    for key, field_name in (("poster", "poster"), ("backdrop", "backdrop")):
        url = detail.get(field_name)
        if url:
            images[key] = str(url)
    return images


def build_movie_nfo(
    meta: MetaInfo,
    detail: dict[str, Any] | None = None,
    *,
    basename: str | None = None,
) -> NfoDocument:
    """生成电影 NFO。

    ``basename`` 传视频文件的主干名（不含扩展名）时，NFO 与视频同名——
    这是 Emby 最可靠的关联方式；不传则回退成 ``movie.nfo``。
    """
    root = ET.Element("movie")
    _add_common(root, meta, detail)
    filename = f"{basename}.nfo" if basename else "movie.nfo"
    return NfoDocument(filename, _serialize(root), _collect_images(detail))


def build_tvshow_nfo(
    meta: MetaInfo, detail: dict[str, Any] | None = None
) -> NfoDocument:
    """生成剧集根目录的 ``tvshow.nfo``。"""
    root = ET.Element("tvshow")
    _add_common(root, meta, detail)
    detail = detail or {}
    _sub(root, "season", meta.season if meta.season is not None else -1)
    _sub(root, "episode", detail.get("number_of_episodes") or meta.total_episodes)
    _sub(root, "status", detail.get("status"))
    return NfoDocument("tvshow.nfo", _serialize(root), _collect_images(detail))


def build_season_nfo(
    meta: MetaInfo, detail: dict[str, Any] | None = None
) -> NfoDocument:
    """生成季目录的 ``season.nfo``。"""
    season = meta.season if meta.season is not None else 1
    root = ET.Element("season")
    detail = detail or {}
    _sub(root, "title", detail.get("name") or f"第 {season} 季")
    _sub(root, "plot", detail.get("overview"))
    _sub(root, "seasonnumber", season)
    _sub(root, "year", detail.get("year") or meta.year)
    return NfoDocument("season.nfo", _serialize(root), _collect_images(detail))


def build_episode_nfo(
    meta: MetaInfo,
    detail: dict[str, Any] | None = None,
    *,
    basename: str,
) -> NfoDocument:
    """生成单集 NFO（必须与视频同名）。

    ``detail`` 这里期望是**单集**信息（``tmdb.season_episodes()`` 的一项），
    含 ``name`` / ``overview`` / ``air_date`` / ``still``。
    """
    detail = detail or {}
    root = ET.Element("episodedetails")
    season = meta.season if meta.season is not None else 1
    episode = meta.episode_start if meta.episode_start is not None else 1
    _sub(root, "title", detail.get("name") or f"第 {episode} 集")
    _sub(root, "showtitle", meta.cn_title or meta.title)
    _sub(root, "season", season)
    _sub(root, "episode", episode)
    _sub(root, "plot", detail.get("overview"))
    _sub(root, "aired", detail.get("air_date"))
    rating = detail.get("vote_average")
    if rating:
        node = ET.SubElement(root, "rating")
        node.text = f"{float(rating):.1f}"
    images = {}
    if detail.get("still"):
        images["thumb"] = str(detail["still"])
    return NfoDocument(f"{basename}.nfo", _serialize(root), images)


def build_for(
    meta: MetaInfo,
    detail: dict[str, Any] | None = None,
    *,
    basename: str,
    episode_detail: dict[str, Any] | None = None,
) -> list[NfoDocument]:
    """按媒体类型生成该写的全部 NFO。

    电影 → 1 份；剧集 → ``tvshow.nfo`` + ``season.nfo`` + 单集 NFO。
    调用方负责把它们写到正确的目录层级（见 ``services/scraper.py``）。
    """
    if meta.media_type == MediaType.MOVIE.value:
        return [build_movie_nfo(meta, detail, basename=basename)]
    if meta.media_type in (MediaType.TV.value, MediaType.ANIME.value):
        return [
            build_tvshow_nfo(meta, detail),
            build_season_nfo(meta, detail),
            build_episode_nfo(meta, episode_detail, basename=basename),
        ]
    # 类型未知时**不猜**：按电影格式写一份最小 NFO 总比不写好，
    # 但不生成 tvshow/season，避免把电影目录污染成剧集结构。
    return [build_movie_nfo(meta, detail, basename=basename)]


def parse_nfo_tmdb_id(path: Path) -> int | None:
    """从已有 NFO 里读回 TMDB ID（用于跳过重复刮削）。"""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    for tag in ("tmdbid", "uniqueid"):
        start = text.find(f"<{tag}")
        while start != -1:
            close = text.find(">", start)
            end = text.find(f"</{tag}>", close)
            if close == -1 or end == -1:
                break
            value = html.unescape(text[close + 1 : end]).strip()
            if value.isdigit():
                return int(value)
            start = text.find(f"<{tag}", end)
    return None
