"""网盘目录展开与多文件下载状态汇总。"""

from __future__ import annotations

import asyncio
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from app.providers.downloader.base import BaseDownloader, TorrentState
from app.schemas.enums import TaskStatus


async def resolve_files(result: dict[str, Any]) -> list[dict[str, Any]]:
    """只展开本次选中的文件或目录，不把网盘根目录当成分享内容。"""
    from app.services import pan_storage

    site_id = int(result.get("site_id") or result.get("storage_id") or 0)
    storage = pan_storage.get_storage(site_id)
    if not storage:
        raise ValueError("网盘不存在或未启用")
    saved_path = str(result.get("saved_path") or "")
    paths = result.get("saved_files") or [saved_path]
    if paths == ["/"] or not saved_path:
        raise ValueError("转存结果未给出具体文件或独立目录，请在网盘文件列表选择要下载的资源")
    files: list[dict[str, Any]] = []
    seen: set[str] = set()

    async def collect(path: str, relative: PurePosixPath, is_dir: bool | None, depth: int = 0, file_id: str | None = None):
        if depth > 8 or len(files) >= 200:
            raise ValueError("目录超过 8 层或 200 个文件，请选择更小的目录")
        if ".." in PurePosixPath(path).parts or any("\\" in part or ":" in part or part == ".." for part in relative.parts):
            raise ValueError("网盘返回了无效文件路径")
        if path in seen:
            return
        seen.add(path)
        resolved = None if is_dir else await pan_storage.resolve_download_url(site_id, path, file_id=file_id)
        if resolved and resolved.get("success"):
            if urlsplit(resolved.get("url") or "").scheme not in ("http", "https"):
                raise ValueError("该文件没有可供 aria2 下载的 HTTP 直链")
            files.append({**resolved, "path": path, "relative": relative.as_posix()})
            return
        if is_dir is False:
            raise ValueError((resolved or {}).get("message") or "换取直链失败")
        entries = await storage.list_dir_for_sync(path)
        if not entries:
            raise ValueError("目录为空、转存尚未完成或登录已失效，请稍后重试")
        for entry in entries:
            # 用路径校验阻止越过用户选择的目录，同时保留子目录，避免同名文件互相覆盖。
            child = PurePosixPath(entry.path).relative_to(PurePosixPath(path))
            if not child.parts or any(part in ("..", "\\") for part in child.parts):
                raise ValueError("网盘返回了目录范围外的文件")
            await collect(entry.path, relative / child, entry.is_dir, depth + 1, entry.file_id)

    base = PurePosixPath(saved_path)
    for path in paths:
        selected = PurePosixPath(str(path))
        relative = selected.relative_to(base) if selected != base else PurePosixPath()
        await collect(str(selected), relative, result.get("is_dir"), file_id=result.get("file_id"))
    return files


async def combined_state(downloader: BaseDownloader, ids: list[str], root: str) -> TorrentState | None:
    """全部文件真正完成后才把整条网盘下载任务置为完成。"""
    states = await asyncio.gather(*(downloader.get(gid) for gid in ids))
    if any(state is None for state in states):
        return None
    finished = all(state.finished for state in states)
    error = next((state.error or "网盘文件下载失败" for state in states if state.status == TaskStatus.FAILED.value), None)
    size = sum(state.size for state in states)
    downloaded = sum(state.downloaded for state in states)
    speed = sum(state.speed for state in states)
    progress = downloaded / size if size else sum(state.progress for state in states) / len(states)
    if error:
        status = TaskStatus.FAILED.value
    elif finished:
        status = TaskStatus.COMPLETED.value
    elif all(state.status == TaskStatus.PAUSED.value for state in states):
        status = TaskStatus.PAUSED.value
    else:
        status = TaskStatus.DOWNLOADING.value
    return TorrentState(
        external_id=ids[0], status=status, size=size, downloaded=downloaded, speed=speed,
        progress=1.0 if finished and not error else min(progress, 0.9999),
        eta=int(max(size - downloaded, 0) / speed) if speed else 0,
        save_path=root, content_path=root, error=error,
        files=[path for state in states for path in state.files],
    )
