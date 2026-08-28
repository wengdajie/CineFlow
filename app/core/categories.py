"""媒体分类：把资源归到「电影/剧集/动漫/纪录片/综艺」等二级目录。

**为什么需要**：v1.3.0 只有 movie/tv 两类，所有剧集混在一个目录下。
对标项目普遍支持主分类归档（docs/09 差距矩阵 #12），因为
Emby 里「动漫」和「电视剧」通常是两个独立媒体库，混在一起会导致
刮削器用错元数据源（动漫该用 TVDB/AniDB 而不是 TMDB 剧集）。

**纯函数、无 IO**，因此可穷举测试。判定依据优先级：
TMDB 类型/关键词 > 文件名特征词 > 媒体类型兜底。
**判定不了就返回 None**——不猜，宁可不归档也不要归错
（归错了用户要手动把成百个文件挪回去）。
"""

from __future__ import annotations

from app.core.meta import MetaInfo
from app.schemas.enums import MediaType

#: 分类标识 -> 目录名
CATEGORY_NAMES: dict[str, str] = {
    "movie": "电影",
    "tv": "电视剧",
    "anime": "动漫",
    "documentary": "纪录片",
    "variety": "综艺",
    "kids": "儿童",
}

#: 各分类的判定关键词（命中即归类，越靠前优先级越高）
_KEYWORDS: dict[str, tuple[str, ...]] = {
    "documentary": (
        "纪录片", "纪实", "documentary", "discovery", "national geographic",
        "bbc earth", "自然世界", "航拍中国", "地球脉动",
    ),
    "variety": (
        "综艺", "真人秀", "variety", "talk show", "脱口秀", "相声",
        "跑男", "奔跑吧", "极限挑战", "歌手", "选秀",
    ),
    "anime": (
        "动漫", "动画", "anime", "番剧", "剧场版", "bangumi",
        "aniversion", "简繁日", "简日", "繁日", "月刊", "新番",
    ),
    "kids": ("儿童", "少儿", "亲子", "kids", "cartoon", "幼儿"),
}

#: TMDB genre 名 -> 分类（TMDB 的类型比文件名可靠得多）
_GENRE_MAP: dict[str, str] = {
    "纪录": "documentary",
    "纪录片": "documentary",
    "documentary": "documentary",
    "动画": "anime",
    "animation": "anime",
    "真人秀": "variety",
    "reality": "variety",
    "talk": "variety",
    "脱口秀": "variety",
    "儿童": "kids",
    "kids": "kids",
    "family": "kids",
}


def detect(
    meta: MetaInfo,
    *,
    genres: list[str] | None = None,
    original_language: str | None = None,
) -> str | None:
    """判定分类标识；判定不了返回 ``None``（调用方不归档）。

    ``genres`` 传 TMDB 的类型列表时准确率最高。
    """
    # 1) TMDB 类型最可靠
    for genre in genres or []:
        key = str(genre or "").strip().lower()
        for needle, category in _GENRE_MAP.items():
            if needle in key:
                # 动画 + 日语 → 动漫；动画 + 其他语言多为儿童动画片
                if category == "anime" and original_language and original_language not in ("ja", "zh"):
                    return "kids"
                return category

    # 2) 文件名/标题特征词
    haystack = " ".join(
        part.lower() for part in (meta.raw, meta.title, meta.cn_title, meta.en_title) if part
    )
    for category, words in _KEYWORDS.items():
        if any(word.lower() in haystack for word in words):
            return category

    # 3) 媒体类型兜底
    if meta.media_type == MediaType.ANIME.value:
        return "anime"
    if meta.media_type == MediaType.MOVIE.value:
        return "movie"
    if meta.media_type == MediaType.TV.value:
        return "tv"
    return None


def directory_for(
    meta: MetaInfo,
    *,
    genres: list[str] | None = None,
    original_language: str | None = None,
) -> str | None:
    """返回该媒体应归入的二级目录名（如 ``动漫``）。"""
    category = detect(meta, genres=genres, original_language=original_language)
    return CATEGORY_NAMES.get(category) if category else None
