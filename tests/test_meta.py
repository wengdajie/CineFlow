"""资源名称识别测试。"""

from __future__ import annotations

import pytest

from app.core import meta
from app.schemas.enums import MediaType


@pytest.mark.parametrize(
    "name,title,season,episodes,resolution,quality",
    [
        (
            "工作细胞.S01E05.2160p.WEB-DL.H265.DDP-OurTV.mkv",
            "工作细胞", 1, [5], "2160p", "WEB-DL",
        ),
        (
            "The.Last.of.Us.S02E01-E03.1080p.WEB-DL.DDP5.1.H.264-NTb",
            "The Last of Us", 2, [1, 2, 3], "1080p", "WEB-DL",
        ),
        (
            "Oppenheimer.2023.2160p.UHD.BluRay.REMUX.DV.HDR.TrueHD.Atmos-FraMeSToR",
            "Oppenheimer", None, [], "2160p", "REMUX",
        ),
        (
            "凡人修仙传 第二季 第105集 4K HDR 国语中字",
            "凡人修仙传", 2, [105], "2160p", None,
        ),
        (
            "间谍过家家 第25话 [1080P]",
            "间谍过家家", None, [25], "1080p", None,
        ),
    ],
)
def test_parse_basic(name, title, season, episodes, resolution, quality):
    """常见命名应被正确解析。"""
    info = meta.parse(name, is_file=name.endswith(".mkv"))
    assert info.title == title
    assert info.season == season
    assert info.episodes == episodes
    assert info.resolution == resolution
    assert info.quality == quality


def test_parse_anime_group():
    """动漫资源应识别字幕组并归类为 anime。"""
    info = meta.parse("[喵萌奶茶屋] 葬送的芙莉莲 / Sousou no Frieren [12][1080p][简繁日内封字幕]")
    assert info.title == "葬送的芙莉莲"
    assert info.en_title == "Sousou no Frieren"
    assert info.episodes == [12]
    assert info.media_type == MediaType.ANIME.value
    assert info.release_group == "喵萌奶茶屋"


def test_parse_season_pack():
    """整季合集应标记 is_season_pack。"""
    info = meta.parse("庆余年第二季全36集 1080p WEB-DL H264 国语中字")
    assert info.season == 2
    assert info.total_episodes == 36
    assert info.is_season_pack is True


def test_parse_movie_year():
    """电影应识别年份并归类为 movie。"""
    info = meta.parse("流浪地球2.2023.BluRay.1080p.x264.DTS-HD.MA-CMCT.mkv", is_file=True)
    assert info.year == 2023
    assert info.media_type == MediaType.MOVIE.value
    assert info.audio_codec == "DTS-HD MA"
    assert info.extension == ".mkv"


def test_season_episode_text():
    """季集短标识格式正确。"""
    assert meta.parse("Show.S03E07.1080p").season_episode_text == "S03E07"
    assert meta.parse("Show.S03E07-E09.1080p").season_episode_text == "S03E07-E09"
    assert meta.parse("Show.S03.1080p").season_episode_text == "S03"


def test_cn_number_season():
    """中文数字季号可解析。"""
    assert meta.parse("某剧 第十二季 第3集").season == 12
    assert meta.parse("某剧 第二十季 第3集").season == 20


def test_parse_empty():
    """空输入不应抛错。"""
    info = meta.parse("")
    assert info.title == ""
    assert info.media_type == MediaType.UNKNOWN.value
