"""按分享 URL 的主机名识别网盘，避免把其他平台交给默认账号。"""

from urllib.parse import parse_qs, urlsplit

PAN_DOMAINS = {
    "baidu": ("pan.baidu.com", "yun.baidu.com", "eyun.baidu.com"),
    "quark": ("pan.quark.cn",),
    "alipan": ("alipan.com", "aliyundrive.com"),
    "pan115": ("115.com", "115cdn.com"),
    "xunlei": ("pan.xunlei.com",),
    "tianyi": ("cloud.189.cn",),
    "mobile": ("caiyun.139.com",),
    "uc": ("drive.uc.cn",),
    "pikpak": ("mypikpak.com",),
    "123pan": ("123pan.com", "123pan.cn", "123865.com", "123912.com"),
}
PAN_LABELS = {
    "baidu": "百度网盘",
    "quark": "夸克网盘",
    "alipan": "阿里云盘",
    "pan115": "115 网盘",
    "xunlei": "迅雷网盘",
    "tianyi": "天翼云盘",
    "mobile": "移动云盘",
    "uc": "UC网盘",
    "pikpak": "PikPak",
    "123pan": "123云盘",
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


def share_password(url: str, password: str | None = None) -> str | None:
    """显式提取码优先，否则读取分享 URL 自带的密码参数。"""
    if password:
        return password
    try:
        query = parse_qs(urlsplit(url).query)
    except ValueError:
        return None
    return next((query[key][0] for key in ("pwd", "passcode", "password") if query.get(key)), None)
