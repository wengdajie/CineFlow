"""影视订阅、雷达和 RSS 共用的缺集资源选择。"""

from __future__ import annotations

from typing import Any


def pick_missing(
    ranked: list[dict[str, Any]], missing: set[int], *, media_type: str, best_version: bool
) -> tuple[list[dict[str, Any]], set[int]]:
    remaining = set(missing)
    picked = []
    for resource in ranked:
        if not remaining:
            break
        info = resource.get("_meta") or resource.get("meta") or {}
        if not isinstance(info, dict):
            info = info.to_dict()
        if media_type == "movie":
            picked.append(resource)
            remaining.clear()
            break
        episodes = set(info.get("episodes") or [])
        if episodes:
            useful = episodes & remaining
        elif info.get("is_season_pack"):
            useful = set(remaining)
        else:
            continue
        if not useful:
            continue
        picked.append(resource)
        remaining -= useful
        if best_version:
            break
    return picked, remaining
