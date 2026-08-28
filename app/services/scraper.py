"""刮削服务：为已入库文件生成 NFO 与本地图片。

**分工**：``app/core/nfo.py`` 负责纯渲染（无 IO，可离线单测）；
本模块负责有 IO 的部分——查 TMDB、下图片、写文件、记录结果。

**设计原则**：刮削是**锦上添花**，绝不能拖垮入库。
所以每一步都是 best-effort：TMDB 不可用 → 用本地识别结果写最小 NFO；
图片下载失败 → 只是没图，NFO 照样写；整个刮削抛异常 → 记日志，
入库流程继续（调用方用 ``try`` 包住即可）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core import nfo as nfo_builder
from app.core.config import settings
from app.core.logger import get_logger
from app.core.meta import MetaInfo, parse
from app.schemas.enums import MediaType
from app.utils.http import async_client

logger = get_logger(__name__)

#: 图片类型 -> 落地文件名（不含扩展名），与 Kodi/Emby 约定一致
_IMAGE_NAMES = nfo_builder.IMAGE_FILENAMES


def _tmdb():
    """延迟取 TMDB 客户端（未配 key 时 available=False）。"""
    from app.providers.metadata.tmdb import tmdb

    return tmdb


async def _download_image(url: str, target: Path) -> bool:
    """下载单张图片。

    已存在则跳过（刮削会被反复触发，不该每次都重下几百 KB）。
    """
    if target.exists() and target.stat().st_size > 0:
        return True
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        async with async_client(timeout=30) as client:
            response = await client.get(url)
            if response.status_code != 200 or not response.content:
                return False
            target.write_bytes(response.content)
        return True
    except Exception as exc:
        logger.debug("图片下载失败 %s: %s", url, exc)
        return False


async def _fetch_detail(meta: MetaInfo) -> dict[str, Any] | None:
    """查 TMDB 详情；不可用或查不到时返回 None（调用方降级）。"""
    client = _tmdb()
    if not client.available:
        return None
    try:
        recognized = await client.recognize(
            meta.title or meta.raw,
            year=meta.year,
            media_type=meta.media_type if meta.media_type != MediaType.UNKNOWN.value else None,
        )
        if not recognized or not recognized.get("tmdb_id"):
            return None
        return await client.detail(
            int(recognized["tmdb_id"]), recognized.get("media_type") or meta.media_type
        )
    except Exception as exc:
        logger.debug("TMDB 详情获取失败 %s: %s", meta.title, exc)
        return None


async def _fetch_episode_detail(
    detail: dict[str, Any] | None, meta: MetaInfo
) -> dict[str, Any] | None:
    """取单集信息（标题、简介、剧照）。"""
    if not detail or not detail.get("tmdb_id"):
        return None
    if meta.media_type not in (MediaType.TV.value, MediaType.ANIME.value):
        return None
    episode_no = meta.episode_start
    if episode_no is None:
        return None
    client = _tmdb()
    if not client.available:
        return None
    try:
        episodes = await client.season_episodes(
            int(detail["tmdb_id"]), meta.season if meta.season is not None else 1
        )
    except Exception:
        return None
    for item in episodes or []:
        if item.get("episode_number") == episode_no:
            return item
    return None


async def scrape_file(
    media_path: str | Path,
    meta: MetaInfo | None = None,
    *,
    download_images: bool | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """为单个媒体文件刮削 NFO 与图片。

    目录布局遵循媒体服务器惯例：

    ``剧集根/tvshow.nfo`` · ``剧集根/Season 02/season.nfo``
    · ``剧集根/Season 02/剧名 - S02E05.nfo``

    电影则 NFO 与视频同目录同名。

    返回统计字典，**不抛异常**——刮削失败不该影响入库。
    """
    path = Path(media_path)
    result: dict[str, Any] = {
        "path": str(path),
        "nfo": [],
        "images": [],
        "tmdb_id": None,
        "degraded": False,
        "message": "",
    }
    if not path.exists():
        result["message"] = "文件不存在"
        return result

    if meta is None:
        meta = parse(path.name, is_file=True)
        if not meta.title:
            meta = parse(f"{path.parent.name} {path.name}", is_file=True)

    detail = await _fetch_detail(meta)
    if detail is None:
        # 关键降级路径：没有 TMDB 也要写出 NFO，否则用户什么都得不到
        result["degraded"] = True
        result["message"] = "未获取到 TMDB 元数据，已按本地识别结果生成"
    else:
        result["tmdb_id"] = detail.get("tmdb_id")

    episode_detail = await _fetch_episode_detail(detail, meta)
    basename = path.stem
    documents = nfo_builder.build_for(
        meta, detail, basename=basename, episode_detail=episode_detail
    )

    if download_images is None:
        download_images = settings.SCRAPE_IMAGES

    is_tv = meta.media_type in (MediaType.TV.value, MediaType.ANIME.value)
    season_dir = path.parent
    # 剧集：视频通常在 ``.../剧名/Season 02/`` 下，tvshow.nfo 要放到剧名目录
    show_dir = season_dir.parent if is_tv else season_dir

    for document in documents:
        if document.filename == "tvshow.nfo":
            target_dir = show_dir
        elif document.filename == "season.nfo":
            target_dir = season_dir
        else:
            target_dir = season_dir
        written = document.write_to(target_dir, overwrite=overwrite)
        if written:
            result["nfo"].append(str(written))

        if not download_images or not document.images:
            continue
        for kind, url in document.images.items():
            name = _IMAGE_NAMES.get(kind, kind)
            # 单集剧照按 Kodi 约定叫 ``xxx-thumb.jpg``
            if document.filename.endswith(".nfo") and kind == "thumb":
                filename = f"{basename}-thumb.jpg"
            else:
                filename = f"{name}.jpg"
            image_target = target_dir / filename
            if await _download_image(url, image_target):
                result["images"].append(str(image_target))

    if not result["message"]:
        result["message"] = f"已生成 {len(result['nfo'])} 个 NFO、{len(result['images'])} 张图片"
    logger.info("刮削 %s：%s", path.name, result["message"])
    return result


async def scrape_library(
    root: str | Path | None = None, *, limit: int = 200, overwrite: bool = False
) -> dict[str, Any]:
    """批量刮削媒体库中尚无 NFO 的文件。

    只处理**缺 NFO** 的文件（除非 ``overwrite``），
    因此可以安全地挂成定时任务反复跑。
    """
    from app.core.organizer import iter_media_files

    base = Path(root or settings.LIBRARY_DIR)
    stats: dict[str, Any] = {
        "scanned": 0,
        "scraped": 0,
        "skipped": 0,
        "degraded": 0,
        "details": [],
    }
    if not base.exists():
        stats["message"] = f"媒体库目录不存在：{base}"
        return stats

    for media_file in iter_media_files(base)[: max(limit, 1)]:
        stats["scanned"] += 1
        sidecar = media_file.with_suffix(".nfo")
        if sidecar.exists() and not overwrite:
            stats["skipped"] += 1
            continue
        outcome = await scrape_file(media_file, overwrite=overwrite)
        if outcome["nfo"]:
            stats["scraped"] += 1
            if outcome["degraded"]:
                stats["degraded"] += 1
            stats["details"].append(
                {
                    "file": media_file.name,
                    "nfo": len(outcome["nfo"]),
                    "images": len(outcome["images"]),
                    "degraded": outcome["degraded"],
                }
            )

    stats["message"] = (
        f"扫描 {stats['scanned']} 个文件，刮削 {stats['scraped']} 个，"
        f"跳过 {stats['skipped']} 个（已有 NFO）"
    )
    logger.info("媒体库刮削完成：%s", stats["message"])
    return stats
