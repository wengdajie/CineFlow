"""内置 AI：分析一个陌生站点该用哪种 Provider 接入，并能一键添加。

**为什么需要**：项目已经有 5 种通用适配器（`html_generic` 正则式、
`api_generic` 字段映射式、`wp_film` WordPress、`maccms` 在线站、
`torznab`/`rss`），但用户面对一个新站时**不知道该选哪个、字段怎么填**——
`row_pattern` 这种正则要人肉去读 HTML 才写得出来。这一步正是 AI 擅长的：
读一页 HTML，判断它属于哪个套路，把字段映射填好。

**设计约束（都是有意为之）**：

1. **默认关闭**。开启意味着把站点页面正文发给第三方模型，
   必须用户显式同意，不能"装完就在往外发数据"。
2. **只用 OpenAI 兼容的 `/chat/completions`**，所以 OpenAI / DeepSeek /
   智谱 / 通义 / Ollama / OneAPI 都能用，不为某一家写专属代码。
3. **AI 只出建议，不直接写库**。分析结果先返回给用户确认，
   `apply` 才落库，且沿用既有的站点创建校验。理由：模型会编造字段，
   直接自动建站等于把一堆坏配置塞进用户的搜索链路。
4. **建议出来后本地先验一遍**（`verify`）：拿建议的配置真跑一次搜索，
   有结果才算可用。"模型说能用"和"真能搜到"是两件事。
5. **不做任何风控对抗/付费墙绕过**：AI 也不例外。它只被允许在既有
   Provider 能力范围内做选型，不会去生成"绕过 WAF"的方案。
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.core.config import settings
from app.core.logger import get_logger
from app.utils.http import async_client, fetch_text

logger = get_logger(__name__)

#: 可供 AI 选择的接入方案。给模型看的"菜单"——限定在这几项里选，
#: 它就不会编造一个我们没实现的 provider。
PROVIDER_CHOICES: dict[str, dict[str, Any]] = {
    "html_generic": {
        "label": "通用网页（正则）",
        "when": "站点只有 HTML 页面，没有 JSON 接口；搜索结果在页面里",
        "fields": ["search_url", "row_pattern", "field_patterns", "magnet_only", "max_rows", "encoding"],
    },
    "api_generic": {
        "label": "通用 JSON API（字段映射）",
        "when": "站点有返回 JSON 的搜索接口",
        "fields": ["api_base", "search_path", "query_key", "list_path", "item_map"],
    },
    "wp_film": {
        "label": "WordPress 影视站",
        "when": "站点是 WordPress，/?s=关键词&feed=rss2 能返回 RSS，磁力在文章详情页",
        "fields": ["search_url", "article_limit", "per_article_limit", "encoding"],
    },
    "maccms": {
        "label": "MacCMS 在线影视站",
        "when": "URL 形如 /index.php/vod/detail/id/123.html，播放页有 player_aaaa 配置",
        "fields": ["max_items"],
    },
    "torznab": {
        "label": "Torznab / Jackett",
        "when": "站点提供 Torznab 接口（Jackett、Prowlarr 或 PT 站官方 API）",
        "fields": ["api_key"],
    },
    "rss": {
        "label": "RSS 种子源",
        "when": "只有一个 RSS 订阅地址，没有搜索能力",
        "fields": ["rss_url"],
    },
    "pan_generic": {
        "label": "通用盘搜接口",
        "when": "第三方网盘搜索 API，返回网盘分享链接",
        "fields": ["method", "query_key", "list_path", "field_map"],
    },
}

_SYSTEM_PROMPT = """你是一个资源站点接入分析助手。用户会给你一个站点的页面 HTML 片段，
你要判断它适合用哪一种适配器接入，并给出字段配置。

严格要求：
1. provider 只能从给定清单里选一个，不要发明新的。
2. 只输出一个 JSON 对象，不要 markdown 代码块，不要解释文字。
3. 字段拿不准就留空字符串或省略，不要编造。宁缺勿错。
4. confidence 用 0~1 的小数，表示你有多确定。
5. reason 用中文一句话说明依据（看到了什么特征）。

输出格式：
{"provider": "...", "kind": "indexer|pan", "confidence": 0.9,
 "reason": "...", "options": {...}, "notes": "..."}
"""

_TAG_SCRIPT = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)


def is_configured() -> tuple[bool, str]:
    """AI 能不能用。返回 ``(可用吗, 不可用的原因)``。

    原因要能指导操作，而不是只说"未配置"。
    """
    if not settings.AI_ENABLED:
        return False, "内置 AI 未启用：设置 → 内置 AI → 打开「启用 AI 站点分析」"
    if not str(settings.AI_BASE_URL or "").strip():
        return False, "未填写 AI 接口地址：设置 → 内置 AI → AI 接口地址"
    if not str(settings.AI_MODEL or "").strip():
        return False, "未填写模型名：设置 → 内置 AI → 模型名"
    # 本地模型（Ollama 等）通常不校验 key，所以 key 为空不算错，只提示
    return True, ""


def describe() -> dict[str, Any]:
    """当前 AI 配置（密钥脱敏）与可选方案清单，供界面展示。"""
    ready, reason = is_configured()
    key = str(settings.AI_API_KEY or "")
    return {
        "ready": ready,
        "reason": reason,
        "enabled": bool(settings.AI_ENABLED),
        "base_url": str(settings.AI_BASE_URL or ""),
        "model": str(settings.AI_MODEL or ""),
        # 只回显长度，不回显内容
        "api_key_set": bool(key),
        "api_key_hint": f"已配置（{len(key)} 位）" if key else "未配置",
        "timeout": int(settings.AI_TIMEOUT),
        "max_page_chars": int(settings.AI_MAX_PAGE_CHARS),
        "providers": [
            {"provider": name, **{k: v for k, v in meta.items() if k != "fields"}, "fields": meta["fields"]}
            for name, meta in PROVIDER_CHOICES.items()
        ],
    }


def condense(html: str, limit: int | None = None) -> str:
    """把页面压成适合喂给模型的样子。

    去掉 script/style：它们通常占了大半体积却几乎不含结构信息，
    留着只是白烧 token 并把真正有用的 HTML 挤出上下文。
    """
    text = _TAG_SCRIPT.sub(" ", str(html or ""))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    cap = int(limit if limit is not None else settings.AI_MAX_PAGE_CHARS)
    if cap > 0 and len(text) > cap:
        # 头尾各留一半：<head> 里有 generator/meta，尾部常有分页与脚本线索
        head = text[: cap // 2]
        tail = text[-(cap // 2) :]
        return f"{head}\n\n…（已截断 {len(text) - cap} 字符）…\n\n{tail}"
    return text


def extract_json(content: str) -> dict[str, Any]:
    """从模型回复里抠出 JSON。

    模型常无视"不要代码块"的指令，所以这里必须容错：
    先剥 ```json 围栏，再退化到"第一个 { 到最后一个 }"。
    抠不出来就抛 ValueError，由上层转成可读错误。
    """
    text = str(content or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("模型没有返回可解析的 JSON") from None
        try:
            data = json.loads(text[start : end + 1])
        except (ValueError, TypeError):
            raise ValueError("模型返回的 JSON 无法解析") from None
    if not isinstance(data, dict):
        raise ValueError("模型返回的不是 JSON 对象")
    return data


async def chat(messages: list[dict[str, str]]) -> str:
    """调一次 OpenAI 兼容的 /chat/completions，返回回复正文。"""
    ready, reason = is_configured()
    if not ready:
        raise ValueError(reason)

    base = str(settings.AI_BASE_URL or "").rstrip("/")
    url = f"{base}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if str(settings.AI_API_KEY or "").strip():
        headers["Authorization"] = f"Bearer {str(settings.AI_API_KEY).strip()}"

    payload = {
        "model": str(settings.AI_MODEL),
        "messages": messages,
        "temperature": float(settings.AI_TEMPERATURE),
    }
    async with async_client(timeout=float(settings.AI_TIMEOUT), headers=headers) as client:
        response = await client.post(url, json=payload)
        if response.status_code >= 400:
            # 把上游原文带出来：401/404/模型名写错都靠它定位
            detail = response.text[:300]
            raise ValueError(f"AI 接口返回 {response.status_code}：{detail}")
        data = response.json()

    choices = data.get("choices") or []
    if not choices:
        raise ValueError("AI 接口没有返回任何结果")
    message = (choices[0] or {}).get("message") or {}
    content = str(message.get("content") or "").strip()
    if not content:
        raise ValueError("AI 返回了空内容")
    return content


def _normalize(suggestion: dict[str, Any], url: str) -> dict[str, Any]:
    """校验并规整模型的建议。

    模型会编造 provider 名或把 options 给成字符串，这里全部拦下来——
    坏建议一旦落库，用户的搜索链路就会莫名其妙少结果。
    """
    provider = str(suggestion.get("provider") or "").strip().lower()
    if provider not in PROVIDER_CHOICES:
        raise ValueError(
            f"AI 建议了不支持的接入方式「{provider or '空'}」，"
            f"只能是：{'、'.join(PROVIDER_CHOICES)}"
        )
    options = suggestion.get("options")
    if not isinstance(options, dict):
        options = {}
    try:
        confidence = float(suggestion.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    kind = str(suggestion.get("kind") or "").strip().lower()
    if kind not in ("indexer", "pan"):
        kind = "pan" if provider == "pan_generic" else "indexer"
    return {
        "url": url,
        "provider": provider,
        "provider_label": PROVIDER_CHOICES[provider]["label"],
        "kind": kind,
        "confidence": max(0.0, min(1.0, confidence)),
        "reason": str(suggestion.get("reason") or "")[:500],
        "notes": str(suggestion.get("notes") or "")[:500],
        "options": options,
    }


async def analyze_site(url: str, *, keyword: str = "流浪地球") -> dict[str, Any]:
    """抓站点页面 → 交给 AI → 返回规整后的接入建议。

    ``keyword`` 用来试探搜索页：拿到搜索结果页 AI 才能看出"条目长什么样"，
    只看首页很容易判错。
    """
    target = str(url or "").strip().rstrip("/")
    if not target.lower().startswith(("http://", "https://")):
        raise ValueError("站点地址必须以 http:// 或 https:// 开头")

    ready, reason = is_configured()
    if not ready:
        raise ValueError(reason)

    home = await fetch_text(target)
    if not home:
        raise ValueError("无法访问该站点（可能被 WAF 拦截、需要代理，或域名已失效）")

    # 顺手探几个常见搜索路径，命中就一起给 AI 看
    probes = {
        "maccms": f"{target}/index.php/vod/search.html?wd={keyword}",
        "wordpress": f"{target}/?s={keyword}&feed=rss2",
    }
    samples: dict[str, str] = {}
    for name, probe_url in probes.items():
        text = await fetch_text(probe_url)
        if text and len(text) > 200:
            samples[name] = condense(text, limit=4000)

    user_parts = [
        f"站点地址：{target}",
        "可选接入方式清单（只能从中选一个）：",
        json.dumps(
            {name: {"说明": meta["when"], "可填字段": meta["fields"]}
             for name, meta in PROVIDER_CHOICES.items()},
            ensure_ascii=False,
            indent=1,
        ),
        "首页 HTML（已去掉 script/style 并截断）：",
        condense(home),
    ]
    for name, sample in samples.items():
        user_parts.append(f"探测 {name} 搜索路径的响应片段：")
        user_parts.append(sample)

    content = await chat(
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": "\n\n".join(user_parts)},
        ]
    )
    result = _normalize(extract_json(content), target)
    result["probes_hit"] = sorted(samples)
    logger.info(
        "AI 站点分析完成：%s → %s（置信度 %.2f）",
        target,
        result["provider"],
        result["confidence"],
    )
    return result


async def verify(suggestion: dict[str, Any], *, keyword: str = "流浪地球") -> dict[str, Any]:
    """拿 AI 的建议真跑一次搜索，验证它到底能不能用。

    **为什么必须有这一步**：模型"说得很像"但字段填错是常态。
    不验就让用户添加，等于把一个搜不到东西的站点塞进搜索链路，
    以后每次搜索都白等它一次超时。
    """
    from app.providers.registry import create_provider

    provider_name = str(suggestion.get("provider") or "")
    if provider_name not in PROVIDER_CHOICES:
        return {"success": False, "message": f"不支持的接入方式：{provider_name}"}

    config = {
        "name": f"AI 试跑 · {provider_name}",
        "url": str(suggestion.get("url") or ""),
        "options": dict(suggestion.get("options") or {}),
    }
    provider = create_provider(provider_name, config)
    if provider is None:
        return {"success": False, "message": f"Provider {provider_name} 初始化失败"}

    try:
        results = await provider.search(keyword)
    except Exception as exc:
        logger.warning("AI 建议试跑失败: %s", exc)
        return {
            "success": False,
            "message": f"按该配置试搜失败：{type(exc).__name__}: {exc}"[:300],
        }

    count = len(results or [])
    return {
        "success": count > 0,
        "count": count,
        "keyword": keyword,
        "message": (
            f"试搜「{keyword}」命中 {count} 条，配置可用"
            if count
            else f"试搜「{keyword}」没有结果：可能是字段映射不对，也可能该站确实没有这部片。"
            "建议换个关键词再试，或手工微调后添加"
        ),
        "samples": [
            {"title": item.title, "link": item.link[:120], "kind": item.kind}
            for item in (results or [])[:3]
        ],
    }
