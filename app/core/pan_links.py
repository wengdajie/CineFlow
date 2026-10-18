"""按分享 URL 的主机名识别网盘，避免把其他平台交给默认账号。"""

from urllib.parse import urlsplit

PAN_DOMAINS = {
    "baidu": ("pan.baidu.com", "yun.baidu.com", "eyun.baidu.com"),
    "quark": ("pan.quark.cn",),
    "alipan": ("alipan.com", "aliyundrive.com"),
    "pan115": ("115.com", "115cdn.com"),
    "xunlei": ("pan.xunlei.com",),
}
PAN_LABELS = {
    "baidu": "百度网盘",
    "quark": "夸克网盘",
    "alipan": "阿里云盘",
    "pan115": "115 网盘",
    "xunlei": "迅雷网盘",
}


def pan_family(url: str) -> str | None:
    try:
        parsed = urlsplit(str(url or "").strip())
        host = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    for family, domains in PAN_DOMAINS.items():
        if any(host == domain or host.endswith("." + domain) for domain in domains):
            return family
    return None
