"""资源类型 → 下载方式的路由与前置检查。

**为什么需要这一层**：投递逻辑原先写死"没指定就拿默认下载器"，
完全不看这个下载器收不收这种资源。实测（只启用 yt-dlp 时）：

    磁力 magnet:?xt=urn:btih:aaa… → downloader=yt-dlp / status=downloading

yt-dlp 根本下不了磁力，任务却被标成"正在下载"，用户要等到它烂在队列里
才发现不对。反过来，网页视频被投给 qBittorrent 会下到一个几 KB 的 HTML。

所以：

1. 每个下载器用 ``supported_kinds`` 声明自己收哪些资源类型；
2. 投递前先按类型筛候选，筛不出来就**不投**，给出可行动的提示
   （"缺 aria2，去设置 → 下载器添加"），而不是投给一个必然失败的下载器；
3. 前端可以先调 :func:`describe` 把"哪些类型现在下不了"提前显示出来，
   而不是等用户点了才报错。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.schemas.enums import ResourceKind

if TYPE_CHECKING:  # pragma: no cover - 仅类型标注，避免运行时循环导入
    from app.providers.downloader.base import BaseDownloader


#: 每种资源类型的下载方式说明。
#:
#: ``providers``：能处理它的下载器 provider 名（用于提示"去装哪个"）；
#: ``hint``：缺下载器时给用户看的**可行动**提示——必须说清去哪儿加什么，
#: 只说"未配置下载器"等于把排查工作全推给用户。
_ROUTES: dict[str, dict[str, Any]] = {
    ResourceKind.MAGNET.value: {
        "label": "磁力链接",
        "providers": ("qbittorrent", "transmission", "aria2", "xunlei"),
        "hint": "磁力资源需要 BT 下载器：设置 → 下载器 → 添加 qBittorrent / Transmission / aria2 / 迅雷",
    },
    ResourceKind.TORRENT.value: {
        "label": "种子文件",
        "providers": ("qbittorrent", "transmission", "aria2", "xunlei"),
        "hint": "种子资源需要 BT 下载器：设置 → 下载器 → 添加 qBittorrent / Transmission / aria2 / 迅雷",
    },
    ResourceKind.DIRECT.value: {
        "label": "直链",
        "providers": ("aria2", "xunlei"),
        "hint": "直链资源需要 aria2（或迅雷）：设置 → 下载器 → 添加 aria2",
    },
    ResourceKind.WEBVIDEO.value: {
        "label": "视频网页",
        "providers": ("ytdlp",),
        "hint": "视频网页需要 yt-dlp：设置 → 下载器 → 添加 yt-dlp",
    },
    ResourceKind.PAN.value: {
        "label": "网盘分享",
        # 网盘分享要下到本地，链路是「转存进自己的盘 → 换临时直链 → 投 aria2」，
        # 所以既要网盘账号也要 aria2。只想留在云端的话用「转存」按钮即可。
        "providers": ("aria2",),
        "hint": "网盘资源下到本地需要 aria2：设置 → 下载器 → 添加 aria2；只想留在云端请用「转存」",
        "needs_pan_account": True,
    },
}


def route_of(kind: str) -> dict[str, Any]:
    """取某资源类型的下载方式说明；未知类型按种子处理。"""
    return _ROUTES.get(str(kind or ""), _ROUTES[ResourceKind.TORRENT.value])


def label_of(kind: str) -> str:
    return str(route_of(kind)["label"])


def hint_of(kind: str) -> str:
    """缺下载器时的可行动提示。"""
    return str(route_of(kind)["hint"])


def candidates_for(kind: str, prefer: str | None = None) -> list[BaseDownloader]:
    """按资源类型筛出**真能收**它的下载器候选（保持原有策略顺序）。

    这是修掉"磁力被投给 yt-dlp"的关键：先筛能力，再谈策略与换源。
    """
    from app.services import sites as site_service

    target = str(kind or ResourceKind.TORRENT.value)
    # 网盘分享落地到本地时投的是**换出来的直链**，所以按 direct 找下载器
    probe = ResourceKind.DIRECT.value if target == ResourceKind.PAN.value else target
    return [
        item
        for item in site_service.downloader_candidates(prefer)
        if item.accepts(probe)
    ]


def has_pan_account() -> bool:
    """是否配了可用来转存的网盘账号。"""
    try:
        from app.services import pan_storage

        return any(item.supports_save for item in pan_storage.storages())
    except Exception:  # pragma: no cover - 网盘模块异常不该影响下载判断
        return False


def downloader_reachability() -> dict[str, str]:
    """下载器名 → 最近一次健康巡检结论（``ok`` / ``error`` / ``unknown``）。

    ``candidates_for()`` 只回答「配没配、收不收这种资源」，
    回答不了「连不连得上」。实测最常见的一类求助就是：
    示例配置里的 qBittorrent 指向 ``127.0.0.1:8080`` 但那儿什么都没起，
    于是 ``/downloads/routing`` 报 ``ready=true``、用户点下载才拿到
    「投递失败 → qBittorrent: 拒绝或超时」。

    这里读站点健康巡检的历史结论（不额外发探测请求，保持 routing 是个快接口），
    把「配了但连不上」如实标出来。没巡检过就是 ``unknown``，
    此时**不阻止**下载 —— 没有证据说明它坏，不能凭猜测拦住用户。
    """
    try:
        from app.services import site_health

        return {
            str(name): str(status or "unknown")
            for name, status in (site_health.downloader_health() or {}).items()
        }
    except Exception:  # pragma: no cover - 健康数据不可用时不影响下载判断
        return {}


def _unreachable(names: list[str]) -> list[str]:
    """从候选下载器名里挑出「有确凿证据连不上」的。

    ⚠️ 判据必须是 ``SiteHealthStatus.DOWN``（值为 ``"down"``）。写死字符串
    ``"error"`` 会永远匹配不上 —— 这个类型的错误不会报异常，只会让整套提示
    静默失效（第一版就踩了，实测 qBittorrent 明明是 down 却报 unreachable=[]）。
    ``degraded``（能连通但结果异常）**不算**连不上，不能拦下载。
    """
    from app.schemas.enums import SiteHealthStatus

    health = downloader_reachability()
    return [name for name in names if health.get(name) == SiteHealthStatus.DOWN.value]

def check(kind: str, prefer: str | None = None) -> tuple[bool, str]:
    """能不能下这种资源。返回 ``(可以吗, 不行的原因)``。

    原因是给人看的，必须包含"接下来该做什么"。
    """
    route = route_of(kind)
    picked = candidates_for(kind, prefer)
    if not picked:
        return False, hint_of(kind)
    if route.get("needs_pan_account") and not has_pan_account():
        return False, (
            "网盘资源下到本地需要先转存进自己的网盘："
            "请到「网盘管理」登录夸克 / 115 / AList 等账号"
        )
    # 配了 ≠ 连得上。全部候选都被巡检判定为 error 时，投出去必然失败，
    # 不如提前说清是哪个下载器连不上（示例配置指向 127.0.0.1 的情况极常见）。
    names = [item.site_name for item in picked]
    dead = _unreachable(names)
    if dead and len(dead) == len(names):
        return False, (
            "下载器连不上：" + "、".join(dead[:3])
            + "。请到「站点健康」重新巡检，或到设置 → 下载器检查地址/端口/密码"
        )
    return True, ""


def pan_pending_hint() -> str:
    """网盘任务停在 ``pending`` 时，告诉用户接下来该做什么。

    网盘资源有两条正当去处，缺哪条都要说清楚：
    转存进自己的盘（要网盘账号）、或下到本地（还要 aria2）。
    """
    parts = []
    if not has_pan_account():
        parts.append("「转存」需要到「网盘管理」登录夸克 / 115 / AList 等账号")
    if not candidates_for(ResourceKind.PAN.value):
        parts.append("「下载到本地」需要到设置 → 下载器添加 aria2")
    if not parts:
        return ""
    return "网盘资源已登记，但当前无法自动处理：" + "；".join(parts)


def describe(prefer: str | None = None) -> dict[str, Any]:
    """各资源类型当前能否下载，供界面**提前**提示而不是点了才报错。"""
    items = []
    for kind in (
        ResourceKind.MAGNET.value,
        ResourceKind.TORRENT.value,
        ResourceKind.PAN.value,
        ResourceKind.DIRECT.value,
        ResourceKind.WEBVIDEO.value,
    ):
        ready, reason = check(kind, prefer)
        names = [item.site_name for item in candidates_for(kind, prefer)]
        dead = _unreachable(names)
        items.append(
            {
                "kind": kind,
                "label": label_of(kind),
                "ready": ready,
                "reason": reason,
                "hint": hint_of(kind),
                "downloaders": names,
                "providers": list(route_of(kind)["providers"]),
                # 部分候选连不上时仍然 ready（还有别的能投），但要让界面能提示
                # 「会自动换源」而不是让用户困惑为什么慢
                "unreachable": dead,
            }
        )
    return {"items": items}
