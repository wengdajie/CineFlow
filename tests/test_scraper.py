"""刮削服务测试：NFO 落盘布局、TMDB 降级、增量跳过（全程离线，不触 TMDB）。"""

from __future__ import annotations

import asyncio
from xml.etree import ElementTree as ET

import pytest

from app.core.config import settings
from app.services import scraper


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def tv_file(tmp_path):
    """构造剧集布局：``剧名/Season 02/剧名 - S02E05.mkv``。"""
    season_dir = tmp_path / "庆余年" / "Season 02"
    season_dir.mkdir(parents=True)
    media = season_dir / "庆余年 - S02E05 - 2160p.mkv"
    media.write_bytes(b"0" * 2048)
    return media


@pytest.fixture
def movie_file(tmp_path):
    """构造电影布局：``沙丘2 (2024)/沙丘2 (2024) - 2160p.mkv``。"""
    movie_dir = tmp_path / "沙丘2 (2024)"
    movie_dir.mkdir(parents=True)
    media = movie_dir / "沙丘2 (2024) - 2160p.mkv"
    media.write_bytes(b"0" * 2048)
    return media


@pytest.fixture(autouse=True)
def no_images(monkeypatch):
    """测试里不下图（无网络），只验证 NFO 与流程。"""
    monkeypatch.setattr(settings, "SCRAPE_IMAGES", False)


# ------------------------------------------------------------------ 目录布局
def test_scrape_tv_writes_three_level_nfo(tv_file):
    """剧集刮削要写 tvshow.nfo（剧名目录）+ season.nfo（季目录）+ 同名单集 NFO。"""
    result = run(scraper.scrape_file(tv_file))
    show_dir = tv_file.parent.parent
    season_dir = tv_file.parent

    assert (show_dir / "tvshow.nfo").exists()
    assert (season_dir / "season.nfo").exists()
    assert (season_dir / (tv_file.stem + ".nfo")).exists()
    assert len(result["nfo"]) == 3

    # 三份都必须是合法 XML，否则媒体服务器会直接跳过整个目录
    assert ET.fromstring((show_dir / "tvshow.nfo").read_text(encoding="utf-8")).tag == "tvshow"
    assert ET.fromstring((season_dir / "season.nfo").read_text(encoding="utf-8")).tag == "season"
    episode_root = ET.fromstring((season_dir / (tv_file.stem + ".nfo")).read_text(encoding="utf-8"))
    assert episode_root.tag == "episodedetails"
    assert episode_root.findtext("season") == "2"
    assert episode_root.findtext("episode") == "5"


def test_scrape_movie_writes_sidecar_nfo(movie_file):
    """电影 NFO 与视频同目录同名，这是 Emby 最可靠的关联方式。"""
    result = run(scraper.scrape_file(movie_file))
    sidecar = movie_file.with_suffix(".nfo")
    assert sidecar.exists()
    assert len(result["nfo"]) == 1
    assert ET.fromstring(sidecar.read_text(encoding="utf-8")).tag == "movie"


# -------------------------------------------------------------------- 降级
def test_scrape_degrades_without_tmdb(tv_file):
    """没配 TMDB（测试环境就是）时仍要写出 NFO，只是标记 degraded。"""
    result = run(scraper.scrape_file(tv_file))
    assert result["degraded"] is True
    assert result["nfo"], "降级也必须产出 NFO，否则用户什么都得不到"
    assert "TMDB" in result["message"]


def test_scrape_missing_file_does_not_raise(tmp_path):
    """文件不存在时返回原因而不是抛异常——刮削失败不该打断入库。"""
    result = run(scraper.scrape_file(tmp_path / "不存在.mkv"))
    assert result["nfo"] == []
    assert "不存在" in result["message"]


# -------------------------------------------------------------- 覆盖与增量
def test_scrape_does_not_overwrite_by_default(movie_file):
    """默认不覆盖：用户手工修过的 NFO 不能被定时任务冲掉。"""
    run(scraper.scrape_file(movie_file))
    sidecar = movie_file.with_suffix(".nfo")
    sidecar.write_text("用户手改过", encoding="utf-8")

    run(scraper.scrape_file(movie_file))
    assert sidecar.read_text(encoding="utf-8") == "用户手改过"

    run(scraper.scrape_file(movie_file, overwrite=True))
    assert sidecar.read_text(encoding="utf-8") != "用户手改过"


def test_scrape_library_skips_files_with_existing_nfo(tv_file, movie_file, tmp_path):
    """批量刮削只处理缺 NFO 的文件，因此可以安全地挂定时任务反复跑。"""
    first = run(scraper.scrape_library(tmp_path))
    assert first["scanned"] == 2
    assert first["scraped"] == 2
    assert first["skipped"] == 0
    assert first["degraded"] == 2

    second = run(scraper.scrape_library(tmp_path))
    assert second["scanned"] == 2
    assert second["scraped"] == 0
    assert second["skipped"] == 2
    assert "跳过 2" in second["message"]


def test_scrape_library_respects_limit(tv_file, movie_file, tmp_path):
    """limit 要真的限流，避免一次任务卡住调度器。"""
    result = run(scraper.scrape_library(tmp_path, limit=1))
    assert result["scanned"] == 1


def test_scrape_library_missing_root(tmp_path):
    """目录不存在时给出可读原因，不抛异常。"""
    result = run(scraper.scrape_library(tmp_path / "没有这个目录"))
    assert result["scanned"] == 0
    assert "不存在" in result["message"]
