"""媒体分类判定测试：纯函数，可穷举边界。"""

from __future__ import annotations

import pytest

from app.core import categories
from app.core.meta import parse


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("航拍中国 第三季 2160p", "documentary"),
        ("地球脉动 S02E01 1080p", "documentary"),
        ("奔跑吧兄弟 20240501 1080p", "variety"),
        ("[新番][鬼灭之刃][01][1080p]", "anime"),
        ("庆余年 S02E05 1080p", "tv"),
        ("沙丘2 2024 2160p BluRay", "movie"),
    ],
)
def test_detect_from_filename_features(raw, expected):
    """只靠文件名特征词也要能把常见类型分对。"""
    assert categories.detect(parse(raw)) == expected


def test_tmdb_genres_win_over_filename():
    """TMDB 类型比文件名可靠：文件名看不出纪录片，genres 说了就听 genres。"""
    meta = parse("Some Show S01E01 1080p")
    assert categories.detect(meta) == "tv"
    assert categories.detect(meta, genres=["纪录"]) == "documentary"
    assert categories.detect(meta, genres=["Documentary"]) == "documentary"


def test_animation_language_disambiguates_anime_and_kids():
    """动画 + 日语/中文 → 动漫；动画 + 其他语言 → 儿童动画片。

    这条规则很重要：Emby 里「动漫」和「儿童」通常是两个媒体库，
    把《汪汪队》塞进动漫库会污染番剧刮削。
    """
    meta = parse("Some Animation S01E01 1080p")
    assert categories.detect(meta, genres=["动画"], original_language="ja") == "anime"
    assert categories.detect(meta, genres=["Animation"], original_language="zh") == "anime"
    assert categories.detect(meta, genres=["Animation"], original_language="en") == "kids"


def test_undetectable_returns_none_instead_of_guessing():
    """判不出来就返回 None，不猜——归错分类要用户手动挪回成百个文件。"""
    meta = parse("完全无法判断")
    assert categories.detect(meta) is None
    assert categories.directory_for(meta) is None


def test_directory_for_maps_to_chinese_names():
    """目录名用中文，和用户在 Emby 里建的媒体库名对得上。"""
    assert categories.directory_for(parse("沙丘2 2024 1080p")) == "电影"
    assert categories.directory_for(parse("庆余年 S02E05")) == "电视剧"
    assert categories.directory_for(parse("[新番][某番][01]")) == "动漫"
    assert set(categories.CATEGORY_NAMES) == {
        "movie",
        "tv",
        "anime",
        "documentary",
        "variety",
        "kids",
    }
