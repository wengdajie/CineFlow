"""NFO 渲染测试：纯函数、离线、可穷举边界。"""

from __future__ import annotations

from xml.etree import ElementTree as ET

from app.core import nfo
from app.core.meta import parse

DETAIL = {
    "title": "庆余年",
    "original_title": "Joy of Life",
    "overview": "剧情简介",
    "year": 2024,
    "vote_average": 8.3,
    "genres": ["剧情", "古装"],
    "actors": [{"name": "张三", "role": "范闲", "thumb": "http://x/a.jpg"}],
    "directors": ["李四"],
    "studios": ["腾讯视频"],
    "tmdb_id": 106449,
    "imdb_id": "tt1234567",
    "poster": "http://x/p.jpg",
    "backdrop": "http://x/b.jpg",
    "status": "Returning Series",
    "number_of_episodes": 36,
}


def test_movie_nfo_is_valid_xml_with_metadata():
    """电影 NFO：根节点 movie，带 TMDB/IMDb 唯一 ID 与演员表。"""
    meta = parse("沙丘2 2024 2160p BluRay HDR")
    doc = nfo.build_movie_nfo(meta, DETAIL, basename="沙丘2 (2024)")
    root = ET.fromstring(doc.content)
    assert root.tag == "movie"
    assert doc.filename == "沙丘2 (2024).nfo"
    ids = {node.get("type"): node.text for node in root.findall("uniqueid")}
    assert ids["tmdb"] == "106449"
    assert ids["imdb"] == "tt1234567"
    assert root.findtext("plot") == "剧情简介"
    assert [node.findtext("name") for node in root.findall("actor")] == ["张三"]


def test_movie_nfo_degrades_without_tmdb():
    """TMDB 不可用时也要写出**合法**的最小 NFO，只是没有简介/评分。"""
    meta = parse("沙丘2 2024 1080p BluRay")
    doc = nfo.build_movie_nfo(meta, None, basename="沙丘2 (2024)")
    root = ET.fromstring(doc.content)
    assert root.tag == "movie"
    assert root.findtext("title")
    assert root.findtext("plot") in (None, "")
    assert not doc.images, "没有 TMDB 就不该有图片下载任务"


def test_tvshow_and_season_and_episode_layout():
    """剧集三层布局：tvshow.nfo / season.nfo / 同名单集 nfo。"""
    meta = parse("庆余年 S02E05 2160p WEB-DL H265 DDP5.1")
    assert meta.season == 2 and meta.episodes == [5]

    show = nfo.build_tvshow_nfo(meta, DETAIL)
    assert show.filename == "tvshow.nfo"
    assert ET.fromstring(show.content).tag == "tvshow"

    season = nfo.build_season_nfo(meta, DETAIL)
    assert season.filename == "season.nfo"
    season_root = ET.fromstring(season.content)
    assert season_root.tag == "season"
    assert season_root.findtext("seasonnumber") == "2"

    episode = nfo.build_episode_nfo(
        meta,
        {"name": "第五集", "overview": "本集简介", "air_date": "2024-05-20"},
        basename="庆余年 - S02E05",
    )
    assert episode.filename == "庆余年 - S02E05.nfo"
    ep_root = ET.fromstring(episode.content)
    assert ep_root.tag == "episodedetails"
    assert ep_root.findtext("season") == "2"
    assert ep_root.findtext("episode") == "5"
    assert ep_root.findtext("title") == "第五集"


def test_build_for_dispatches_by_media_type():
    """build_for 按类型给出该写的全部文档，且都是合法 XML。"""
    for raw, expect_files in (
        ("庆余年 S02E05 2160p WEB-DL", {"tvshow.nfo", "season.nfo", "X.nfo"}),
        ("沙丘2 2024 1080p BluRay", {"X.nfo"}),
    ):
        meta = parse(raw)
        docs = nfo.build_for(meta, DETAIL, basename="X", episode_detail={"name": "E"})
        assert {doc.filename for doc in docs} == expect_files
        for doc in docs:
            ET.fromstring(doc.content)


def test_special_chars_are_escaped():
    """标题里的 & < > 必须转义，否则媒体服务器解析 NFO 会直接报错。"""
    meta = parse("A&B <危险> 2024 1080p")
    doc = nfo.build_movie_nfo(meta, {"title": "A&B <危险>", "overview": "x & y"}, basename="A")
    root = ET.fromstring(doc.content)  # 不合法就抛异常
    assert "&" in (root.findtext("title") or "")


def test_parse_nfo_tmdb_id_roundtrip(tmp_path):
    """写进去的 TMDB ID 要能再读出来（增量刮削靠它判断是否已刮过）。"""
    meta = parse("沙丘2 2024 1080p")
    doc = nfo.build_movie_nfo(meta, DETAIL, basename="沙丘2")
    written = doc.write_to(tmp_path)
    assert written is not None and written.exists()
    assert nfo.parse_nfo_tmdb_id(written) == 106449

    # 不是 NFO / 没有 ID / 文件不存在，都必须返回 None 而不是抛异常
    broken = tmp_path / "broken.nfo"
    broken.write_text("不是 XML", encoding="utf-8")
    assert nfo.parse_nfo_tmdb_id(broken) is None
    empty = tmp_path / "empty.nfo"
    empty.write_text("<movie></movie>", encoding="utf-8")
    assert nfo.parse_nfo_tmdb_id(empty) is None
    assert nfo.parse_nfo_tmdb_id(tmp_path / "不存在.nfo") is None


def test_write_to_respects_overwrite_flag(tmp_path):
    """overwrite=False 时不能覆盖用户手工改过的 NFO。"""
    meta = parse("沙丘2 2024 1080p")
    doc = nfo.build_movie_nfo(meta, DETAIL, basename="沙丘2")
    first = doc.write_to(tmp_path)
    assert first is not None
    first.write_text("用户手改", encoding="utf-8")
    assert doc.write_to(tmp_path, overwrite=False) is None
    assert first.read_text(encoding="utf-8") == "用户手改"
    assert doc.write_to(tmp_path, overwrite=True) is not None
    assert first.read_text(encoding="utf-8") != "用户手改"


def test_image_filenames_cover_media_server_convention():
    """图片命名要符合 Emby/Jellyfin 惯例，否则挂不上海报。"""
    assert nfo.IMAGE_FILENAMES["poster"] == "poster"
    assert nfo.IMAGE_FILENAMES["backdrop"] == "fanart"
    assert set(nfo.IMAGE_FILENAMES) >= {"poster", "backdrop", "thumb"}
