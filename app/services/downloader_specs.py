"""下载器配置字段清单（供「设置」页渲染详细表单）。

**为什么需要这个模块**：下载器原先和 BT 站点、通知渠道挤在「站点管理」页，
共用一张通用表单——地址、用户名、密码，加一个 ``options`` 的 JSON 文本框。
qBittorrent 的分类、yt-dlp 的画质上限/限速/字幕语言这些**下载器专有参数**
全得手写 JSON，用户既不知道有哪些键，也不知道合法值是什么（v1.9.2 之前的状态）。

本模块把每个下载器的可配项显式登记出来，前端据此渲染成真正的表单控件。
这样做的额外好处是：**新增下载器只要在这里加一段，前端零改动**。

字段与 Provider 代码里的 ``self.option("xxx")`` / ``self.config["xxx"]``
一一对应——顺序不能乱来，改这里必须同步看 ``app/providers/downloader/``。
测试 ``tests/test_downloader_specs.py`` 会反向校验：登记的每个 option
必须真的被对应 Provider 读取，避免出现"界面能填但代码不读"的假配置项。
"""

from __future__ import annotations

from typing import Any

#: 所有下载器共用的连接字段。``target`` 指明该值存到 ``SiteConfig`` 的哪一列，
#: ``option`` 表示存进 ``options`` JSON。
COMMON_FIELDS: list[dict[str, Any]] = [
    {
        "key": "url",
        "target": "column",
        "label": "地址",
        "type": "str",
        "placeholder": "http://127.0.0.1:8080",
        "hint": "下载器 WebUI / RPC 地址，含端口",
    },
    {
        "key": "username",
        "target": "column",
        "label": "用户名",
        "type": "str",
        "hint": "没有认证就留空",
    },
    {
        "key": "password",
        "target": "column",
        "label": "密码",
        "type": "password",
        "hint": "留空表示不修改已保存的密码",
    },
    {
        "key": "priority",
        "target": "column",
        "label": "优先级",
        "type": "int",
        "minimum": 1,
        "maximum": 999,
        "default": 50,
        "hint": "数字越小越优先，多下载器时决定先投给谁",
    },
    {
        "key": "timeout",
        "target": "column",
        "label": "超时(秒)",
        "type": "int",
        "minimum": 3,
        "maximum": 300,
        "default": 20,
    },
    {
        "key": "save_path",
        "target": "option",
        "label": "默认保存目录",
        "type": "str",
        "hint": "留空则用全局 CF_DOWNLOAD_DIR；填的是**下载器所在机器**上的路径",
    },
]

#: 各下载器专有字段。键必须与 Provider 里 ``self.option(...)`` 的名字一致。
PROVIDER_FIELDS: dict[str, list[dict[str, Any]]] = {
    "qbittorrent": [
        {
            "key": "category",
            "target": "option",
            "label": "分类 category",
            "type": "str",
            "hint": "投递时写入的 qB 分类，便于在 qB 里筛选本项目的任务",
        },
        {
            "key": "tags",
            "target": "option",
            "label": "标签 tags",
            "type": "str",
            "hint": "多个用逗号分隔",
        },
    ],
    "transmission": [],
    "aria2": [
        {
            "key": "api_key",
            "target": "column",
            "label": "RPC Secret",
            "type": "password",
            "hint": "对应 aria2.conf 里的 rpc-secret，留空表示未设密钥",
        },
    ],
    "xunlei": [
        {
            "key": "device_name",
            "target": "option",
            "label": "设备名称",
            "type": "str",
            "hint": "同一迅雷账号绑了多台 NAS 时用来指定；留空用第一台。名称见迅雷 App",
        },
        {
            "key": "download_root_dir",
            "target": "option",
            "label": "下载根目录名",
            "type": "str",
            "hint": "迅雷页面上的目录名（如「迅雷下载」），留空用第一个",
        },
    ],
    "ytdlp": [
        {
            "key": "max_height",
            "target": "option",
            "label": "画质上限",
            "type": "choice",
            "choices": ["480", "720", "1080", "1440", "2160"],
            "default": "1080",
            "hint": "默认下载不超过这个高度；界面上单条手动选画质时会覆盖它",
        },
        {
            "key": "format",
            "target": "option",
            "label": "自定义 format 表达式",
            "type": "str",
            "hint": "填了就完全覆盖画质上限，语法同 yt-dlp -f；不懂就留空",
        },
        {
            "key": "rate_limit",
            "target": "option",
            "label": "限速(KB/s)",
            "type": "int",
            "minimum": 0,
            "maximum": 1024000,
            "hint": "0 或留空 = 不限速；NAS 上建议限一下免得占满带宽",
        },
        {
            "key": "proxy",
            "target": "option",
            "label": "代理",
            "type": "str",
            "placeholder": "http://127.0.0.1:7890",
            "hint": "YouTube 通常需要；留空则用全局 CF_HTTP_PROXY",
        },
        {
            "key": "cookie_file",
            "target": "option",
            "label": "Cookie 文件路径",
            "type": "str",
            "hint": "Netscape 格式。下载会员/登录可见内容时才需要",
        },
        {
            "key": "write_subtitles",
            "target": "option",
            "label": "下载字幕",
            "type": "bool",
            "default": True,
        },
        {
            "key": "subtitle_langs",
            "target": "option",
            "label": "字幕语言",
            "type": "list",
            "default": ["zh-Hans", "zh-CN", "zh", "en"],
            "hint": "逗号分隔，按优先级排列",
        },
        {
            "key": "write_thumbnail",
            "target": "option",
            "label": "保存封面图",
            "type": "bool",
            "default": True,
            "hint": "便于媒体库刮削识别；无 ffmpeg 时会自动跳过",
        },
        {
            "key": "no_playlist",
            "target": "option",
            "label": "只下单个视频",
            "type": "bool",
            "default": True,
            "hint": "关掉后，给一个合集地址会把整个合集都下下来",
        },
        {
            "key": "retries",
            "target": "option",
            "label": "重试次数",
            "type": "int",
            "minimum": 0,
            "maximum": 20,
            "default": 3,
        },
        {
            "key": "socket_timeout",
            "target": "option",
            "label": "连接超时(秒)",
            "type": "int",
            "minimum": 5,
            "maximum": 300,
            "default": 20,
        },
        {
            "key": "fragments",
            "target": "option",
            "label": "分片并发数",
            "type": "int",
            "minimum": 1,
            "maximum": 32,
            "default": 4,
            "hint": "调大能提速，但对站点压力也大",
        },
        {
            "key": "probe_retries",
            "target": "option",
            "label": "解析重试次数",
            "type": "int",
            "minimum": 1,
            "maximum": 10,
            "default": 3,
            "hint": "B 站连续解析会回 412，多试几次能绕过",
        },
    ],
}

#: 下载器的界面说明：告诉用户这个下载器**能下什么**，避免配错。
PROVIDER_NOTES: dict[str, str] = {
    "qbittorrent": "处理 BT 种子与磁力，最常用。需要在 qB 里开启 WebUI。",
    "transmission": "处理 BT 种子与磁力，轻量。**飞牛 fnOS / 群晖自带的下载器就是它**，默认端点 http://NAS地址:9091。",
    "aria2": "处理 HTTP/FTP 直链（网盘直链下载），不处理磁力。",
    "xunlei": "把 NAS 上的迅雷套件当下载器用，处理磁力。需先在 NAS 的迅雷页面扫码登录自己的账号；不支持迅雷云端离线。",
    "ytdlp": "处理 B 站 / YouTube / 抖音等视频网页，榜单里的「下载」按钮走的就是它。",
}


#: 各下载器**用不到**的公共字段，必须排掉，否则界面会出现"能填但代码不读"的假配置项。
#:
#: * aria2 用 RPC Secret（api_key 列）认证，没有用户名的概念；
#: * yt-dlp 是**本地进程**，不连远程服务——地址/用户名/密码全都不读，
#:   它要登录时用的是 ``cookie_file``；
#: * 迅雷（NAS 本地）的鉴权是套件自己发的本地 JWT，从 Web 页面自动抠取，
#:   不需要填账号密码；它也不能指定任意保存路径，只能选它自己管的下载目录，
#:   所以 ``save_path`` 也排掉（子目录由投递时的分类目录名自动带上）。
EXCLUDED_COMMON: dict[str, tuple[str, ...]] = {
    "aria2": ("username",),
    "ytdlp": ("url", "username", "password"),
    "xunlei": ("username", "password", "save_path"),
}


def fields_for(provider: str) -> list[dict[str, Any]]:
    """某个下载器的完整字段清单（公共 + 专有）。

    aria2 的 RPC Secret 复用 ``api_key`` 列而不是新增字段，所以它出现在
    专有字段里；这里保证同名字段不会重复渲染两次。

    同时按 ``EXCLUDED_COMMON`` 剔掉该下载器根本不读的公共字段——
    宁可界面上少一个输入框，也不要给一个填了没用的框（ADR-18）。
    """
    key = str(provider or "").strip().lower()
    specific = PROVIDER_FIELDS.get(key, [])
    seen = {item["key"] for item in specific}
    dropped = EXCLUDED_COMMON.get(key, ())
    common = [
        item
        for item in COMMON_FIELDS
        if item["key"] not in seen and item["key"] not in dropped
    ]
    return common + list(specific)


def schema() -> list[dict[str, Any]]:
    """全部下载器的表单描述，供前端一次拉取后渲染。"""
    from app.providers.registry import get_provider_class

    items: list[dict[str, Any]] = []
    for name in PROVIDER_FIELDS:
        provider_cls = get_provider_class(name)
        items.append(
            {
                "provider": name,
                "display_name": provider_cls.display_name if provider_cls else name,
                "note": PROVIDER_NOTES.get(name, ""),
                "fields": fields_for(name),
            }
        )
    return items
