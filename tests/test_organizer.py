"""整理/命名/转移测试。"""

from __future__ import annotations

from pathlib import Path

from app.core import meta
from app.core.organizer import (
    is_media_file,
    iter_media_files,
    render_name,
    transfer_directory,
    transfer_file,
)
from app.schemas.enums import TransferMode


def test_render_tv_name():
    """剧集按 S01E05 结构命名。"""
    info = meta.parse("工作细胞.S01E05.2160p.WEB-DL.H265-Group.mkv", is_file=True)
    rendered = render_name(info, ".mkv")
    assert rendered == "工作细胞/Season 01/工作细胞 - S01E05.mkv"


def test_render_movie_name():
    """电影按 标题 (年份) 结构命名。"""
    info = meta.parse("Oppenheimer.2023.2160p.BluRay.REMUX.mkv", is_file=True)
    rendered = render_name(info, ".mkv")
    assert rendered.startswith("Oppenheimer (2023)/")
    assert "2160p" in rendered


def test_render_custom_template():
    """自定义模板可用。"""
    info = meta.parse("Show.S02E03.1080p.mkv", is_file=True)
    rendered = render_name(info, ".mkv", "{title}/S{season:02d}/E{episode:02d}{ext}")
    assert rendered == "Show/S02/E03.mkv"


def test_is_media_file(tmp_media):
    """按扩展名识别媒体文件。"""
    video = tmp_media("Show.S01E01.1080p.mkv")
    text = tmp_media("readme.txt")
    assert is_media_file(video) is True
    assert is_media_file(text) is False


def test_iter_media_files(tmp_media):
    """递归收集媒体文件。"""
    tmp_media("pack/Show.S01E01.1080p.mkv")
    tmp_media("pack/Show.S01E02.1080p.mkv")
    tmp_media("pack/note.nfo")
    root = Path(tmp_media("pack/x.mkv")).parent
    files = iter_media_files(root)
    assert len(files) == 3
    assert all(item.suffix == ".mkv" for item in files)


def test_transfer_file_hardlink(tmp_media, tmp_path):
    """硬链接整理成功，且不改变源文件。"""
    source = tmp_media("Show.S01E01.1080p.WEB-DL.mkv")
    library = tmp_path / "library"
    result = transfer_file(source, library_dir=library, mode=TransferMode.LINK.value)

    assert result.success is True
    assert result.target is not None
    assert result.target.exists()
    assert source.exists()
    assert result.target.stat().st_size == source.stat().st_size
    assert "Season 01" in str(result.target)


def test_transfer_file_dry_run(tmp_media, tmp_path):
    """试运行不落盘。"""
    source = tmp_media("Show.S01E02.1080p.mkv")
    library = tmp_path / "library2"
    result = transfer_file(source, library_dir=library, dry_run=True)
    assert result.success is True
    assert not result.target.exists()


def test_transfer_skips_existing(tmp_media, tmp_path):
    """已存在且未开启覆盖时跳过。"""
    source = tmp_media("Show.S01E03.1080p.mkv")
    library = tmp_path / "library3"
    first = transfer_file(source, library_dir=library)
    assert first.success is True

    second = transfer_file(source, library_dir=library)
    assert second.success is False
    assert "已存在" in second.message


def test_transfer_strm(tmp_media, tmp_path, monkeypatch):
    """STRM 模式写出指向源文件的文本。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "STRM_DIR", tmp_path / "strm")
    source = tmp_media("Movie.2024.1080p.WEB-DL.mkv")
    result = transfer_file(source, mode=TransferMode.STRM.value)
    assert result.success is True
    assert result.target.suffix == ".strm"
    assert result.target.read_text(encoding="utf-8") == str(source)


def test_transfer_directory_with_title(tmp_media, tmp_path):
    """目录整理可指定剧名与季，补齐缺失信息。"""
    tmp_media("某剧集合集/EP01.1080p.mkv")
    tmp_media("某剧集合集/EP02.1080p.mkv")
    source = Path(tmp_media("某剧集合集/x.mkv")).parent
    library = tmp_path / "library4"

    results = transfer_directory(
        source, library_dir=library, title="我的剧集", season=1
    )
    succeeded = [item for item in results if item.success]
    assert len(succeeded) == 3
    assert all("我的剧集" in str(item.target) for item in succeeded)


def test_transfer_missing_source(tmp_path):
    """源不存在时安全失败。"""
    result = transfer_file(tmp_path / "nope.mkv")
    assert result.success is False
    assert "不存在" in result.message


def test_subtitle_follows_video(tmp_media, tmp_path):
    """同名字幕随视频一起整理。"""
    source = tmp_media("Drama.S01E09.1080p.WEB-DL.mkv")
    subtitle = source.with_suffix(".srt")
    subtitle.write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n", encoding="utf-8")

    library = tmp_path / "library5"
    result = transfer_file(source, library_dir=library)
    assert result.success is True
    assert result.target.with_suffix(".srt").exists()


# ------------------------------------------------------- 分类归档（v1.4.0）
def test_category_archive_adds_second_level_dir(tmp_media, tmp_path, monkeypatch):
    """开启分类后，剧集要落进「电视剧/」，动漫落进「动漫/」。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "CATEGORY_ENABLED", True)
    library = tmp_path / "lib"

    tv = tmp_media("分类剧.S01E01.1080p.WEB-DL.mkv")
    result = transfer_file(tv, library_dir=library, mode="copy")
    assert result.success, result.message
    assert "电视剧" in str(result.target)

    doc = tmp_media("航拍中国.S03E01.2160p.WEB-DL.mkv")
    doc_result = transfer_file(doc, library_dir=library, mode="copy")
    assert "纪录片" in str(doc_result.target)


def test_category_uses_tmdb_genres(tmp_media, tmp_path, monkeypatch):
    """有 TMDB 类型时以它为准（文件名看不出是纪录片）。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "CATEGORY_ENABLED", True)
    source = tmp_media("Genre.Show.S01E01.1080p.WEB-DL.mkv")
    result = transfer_file(
        source, library_dir=tmp_path / "lib2", mode="copy", genres=["纪录"]
    )
    assert "纪录片" in str(result.target)


def test_category_disabled_keeps_flat_layout(tmp_media, tmp_path, monkeypatch):
    """默认不开分类，目录结构必须与老版本完全一致（不能悄悄改变已有库布局）。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "CATEGORY_ENABLED", False)
    source = tmp_media("不分类剧.S01E01.1080p.WEB-DL.mkv")
    result = transfer_file(source, library_dir=tmp_path / "lib3", mode="copy")
    assert "电视剧" not in str(result.target)
