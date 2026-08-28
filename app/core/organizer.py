"""媒体整理：命名模板渲染 + 文件转移（硬链/复制/移动/软链/STRM）。"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings
from app.core.logger import get_logger
from app.core.meta import MetaInfo, parse
from app.schemas.enums import MediaType, TransferMode
from app.utils.strings import safe_filename

logger = get_logger(__name__)


@dataclass
class TransferResult:
    """单文件整理结果。"""

    success: bool
    source: Path
    target: Path | None = None
    mode: str = TransferMode.LINK.value
    message: str = ""
    size: int = 0
    meta: MetaInfo | None = None


def is_media_file(path: Path) -> bool:
    """是否是需要整理的媒体文件（按扩展名与体积过滤）。"""
    if not path.is_file():
        return False
    if path.suffix.lower() not in settings.MEDIA_EXTENSIONS:
        return False
    try:
        if path.stat().st_size < settings.MIN_FILE_SIZE_MB * 1024**2:
            return False
    except OSError:
        return False
    return True


def is_subtitle_file(path: Path) -> bool:
    """是否是字幕文件。"""
    return path.is_file() and path.suffix.lower() in settings.SUBTITLE_EXTENSIONS


def iter_media_files(root: Path) -> list[Path]:
    """递归收集媒体文件；``root`` 为单文件时直接判定。"""
    root = Path(root)
    if root.is_file():
        return [root] if is_media_file(root) else []
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*") if is_media_file(path))


def render_name(meta: MetaInfo, extension: str, template: str | None = None) -> str:
    """按模板渲染目标相对路径。"""
    is_tv = meta.media_type in (MediaType.TV.value, MediaType.ANIME.value)
    if template is None:
        template = settings.TV_TEMPLATE if is_tv else settings.MOVIE_TEMPLATE

    season = meta.season if meta.season is not None else 1
    episode = meta.episode_start if meta.episode_start is not None else 1
    quality_parts = [
        part
        for part in (meta.resolution, meta.quality, meta.video_codec, meta.effect)
        if part
    ]

    values = {
        "title": safe_filename(meta.title or "unknown"),
        "en_title": safe_filename(meta.en_title or meta.title or "unknown"),
        "cn_title": safe_filename(meta.cn_title or meta.title or "unknown"),
        "year": meta.year or "",
        "season": season,
        "episode": episode,
        "resolution": meta.resolution or "",
        "quality": " ".join(quality_parts) or "unknown",
        "video_codec": meta.video_codec or "",
        "audio_codec": meta.audio_codec or "",
        "effect": meta.effect or "",
        "group": safe_filename(meta.release_group or ""),
        "ext": extension,
    }

    try:
        rendered = template.format(**values)
    except (KeyError, ValueError, IndexError) as exc:
        logger.warning("命名模板渲染失败(%s)，退回默认命名: %s", exc, template)
        marker = meta.season_episode_text
        base = values["title"]
        if values["year"]:
            base = f"{base} ({values['year']})"
        rendered = f"{base}/{base}{(' ' + marker) if marker else ''}{extension}"

    # 逐段清洗，避免模板里的空字段留下 " ()" 之类的残渣
    parts = []
    for segment in Path(rendered.replace("\\", "/")).parts:
        cleaned = segment.replace("()", "").replace("[]", "")
        cleaned = " ".join(cleaned.split())
        cleaned = cleaned.replace(" - .", ".").replace(" .", ".")
        if cleaned not in ("", ".", ".."):
            parts.append(cleaned)
    return "/".join(parts)


def _link_or_copy(source: Path, target: Path, mode: str) -> str:
    """执行具体的文件动作，返回实际使用的模式。

    硬链接跨盘会失败，此时自动降级为复制。
    """
    if mode == TransferMode.MOVE.value:
        shutil.move(str(source), str(target))
        return mode
    if mode == TransferMode.SOFTLINK.value:
        os.symlink(str(source), str(target))
        return mode
    if mode == TransferMode.LINK.value:
        try:
            os.link(str(source), str(target))
            return mode
        except OSError as exc:
            logger.info("硬链接失败(%s)，降级为复制: %s", exc, source.name)
            shutil.copy2(str(source), str(target))
            return TransferMode.COPY.value
    shutil.copy2(str(source), str(target))
    return TransferMode.COPY.value


def transfer_file(
    source: Path | str,
    *,
    library_dir: Path | str | None = None,
    mode: str | None = None,
    template: str | None = None,
    meta: MetaInfo | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
    genres: list[str] | None = None,
    original_language: str | None = None,
) -> TransferResult:
    """把单个媒体文件整理进媒体库。

    ``genres`` / ``original_language`` 传 TMDB 信息时，
    开启 ``CF_CATEGORY_ENABLED`` 可按「电影/剧集/动漫/纪录片/综艺」二级归档。
    """
    source = Path(source)
    mode = (mode or settings.TRANSFER_MODE).lower()
    library = Path(library_dir or settings.LIBRARY_DIR)

    if not source.exists():
        return TransferResult(False, source, message="源文件不存在", mode=mode)

    info = meta or parse(source.name, is_file=True)
    if not info.title:
        info = parse(f"{source.parent.name} {source.name}", is_file=True)

    extension = source.suffix.lower()
    relative = render_name(info, extension, template)

    # 二级分类目录（判定不出来时不归档，避免把片子塞进错误分类）
    if settings.CATEGORY_ENABLED:
        from app.core.categories import directory_for

        category = directory_for(
            info, genres=genres, original_language=original_language
        )
        if category:
            relative = f"{category}/{relative}"

    size = source.stat().st_size

    if mode == TransferMode.STRM.value:
        return _write_strm(source, library, relative, info, size, dry_run=dry_run)

    target = library / relative
    if dry_run:
        return TransferResult(
            True, source, target, mode, "试运行", size=size, meta=info
        )

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if not overwrite:
                return TransferResult(
                    False, source, target, mode, "目标已存在，跳过", size=size, meta=info
                )
            target.unlink()
        used_mode = _link_or_copy(source, target, mode)
    except Exception as exc:
        logger.error("整理失败 %s -> %s: %s", source, target, exc)
        return TransferResult(False, source, target, mode, str(exc), size, info)

    _transfer_subtitles(source, target, used_mode)
    logger.info("整理完成[%s] %s -> %s", used_mode, source.name, target)
    return TransferResult(True, source, target, used_mode, "成功", size, info)


def _write_strm(
    source: Path,
    library: Path,
    relative: str,
    info: MetaInfo,
    size: int,
    *,
    dry_run: bool = False,
) -> TransferResult:
    """生成 STRM 文件（网盘直链播放场景）。"""
    strm_root = Path(settings.STRM_DIR)
    target = strm_root / Path(relative).with_suffix(".strm")
    if dry_run:
        return TransferResult(
            True, source, target, TransferMode.STRM.value, "试运行", size, info
        )
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(source), encoding="utf-8")
    except Exception as exc:
        return TransferResult(
            False, source, target, TransferMode.STRM.value, str(exc), size, info
        )
    return TransferResult(
        True, source, target, TransferMode.STRM.value, "成功", size, info
    )


def _transfer_subtitles(source: Path, target: Path, mode: str) -> None:
    """把同目录同名字幕一起搬过去。"""
    try:
        stem = source.stem.lower()
        for sibling in source.parent.iterdir():
            if not is_subtitle_file(sibling):
                continue
            if not sibling.stem.lower().startswith(stem[: max(len(stem) - 2, 4)]):
                continue
            suffix = sibling.name[len(source.stem) :] if sibling.name.startswith(source.stem) else sibling.suffix
            subtitle_target = target.with_name(target.stem + suffix)
            if subtitle_target.exists():
                continue
            _link_or_copy(sibling, subtitle_target, mode)
    except Exception as exc:  # pragma: no cover - 字幕失败不影响主流程
        logger.debug("字幕整理跳过: %s", exc)


def transfer_directory(
    source: Path | str,
    *,
    library_dir: Path | str | None = None,
    mode: str | None = None,
    template: str | None = None,
    title: str | None = None,
    season: int | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
) -> list[TransferResult]:
    """整理目录（或单文件）下的所有媒体文件。"""
    files = iter_media_files(Path(source))
    if not files:
        return []

    results: list[TransferResult] = []
    for path in files:
        info = parse(path.name, is_file=True)
        # 单集文件名往往缺少剧名，用外层目录名补齐
        if title:
            info.title = safe_filename(title)
        elif not info.title or len(info.title) < 2:
            info = parse(f"{Path(source).name} {path.name}", is_file=True)
        if season is not None and info.season is None:
            info.season = season
        results.append(
            transfer_file(
                path,
                library_dir=library_dir,
                mode=mode,
                template=template,
                meta=info,
                overwrite=overwrite,
                dry_run=dry_run,
            )
        )
    return results
