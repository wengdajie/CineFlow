"""片名别名扩展：让**中文订阅**也能搜到国际站的**英文/罗马音**资源。

## 这个模块解决的真缺陷

用户反馈「大部分内容无法返回 BT 资源」。除了「站点太少」这个显而易见的原因，
实测还挖出一个**静默得多**的缺陷：**搜到了也会被自己丢掉**。

复现（v1.19.0）：订阅「凡人修仙传」→ AnimeTosho 取回 **74 条**真实种子 →
`filter_and_rank()` 保留 **0 条**。原因是这些种子叫
`A Record of a Mortal's Journey to Immortality - 176`，
而 `_title_variants("凡人修仙传")` 只产出 `['凡人修仙传']`，
于是每一条都被 `reject` 成「标题与订阅名称不匹配」。

这个缺陷比「站点少」更值得修：它意味着**加站点也没用** ——
新加的国际站会一样被过滤器清空，用户看到的仍然是「搜不到」。

## 两处都要用别名，缺一不可

| 用途 | 不做的后果 |
|---|---|
| 扩展**搜索关键词** | 国际站拿中文词搜，本来就零命中（Nyaa「沙丘」实测 1 条 / 「Dune」9 条） |
| 扩展**标题过滤白名单** | 站点命中了，但结果全被判「与订阅名称不匹配」而丢掉（上面那 74 → 0） |

## 别名从哪来（三级，逐级降级）

1. **TMDB `alternative_titles`**：最权威，含原名与各地区译名。需 API Key。
2. **TMDB `original_title`**：识别时本来就会拿到，零额外请求。
3. **本地启发式**：中英混排标题拆分（原 `_title_variants` 的能力，保留）。

未配 TMDB_API_KEY 时只有第 3 级 —— 这也是**刻意**的降级路径：
不能因为没有 API Key 就让搜索报错，只是覆盖面回到原状。

## 为什么加缓存

同一部剧的订阅巡检每 30 分钟跑一次，别名却几乎不变。
TMDB 的 `_get` 自带 `METADATA_CACHE_TTL` 缓存，但**识别一步**
（片名 → tmdb_id）在没有 tmdb_id 时每次都要重新搜。
本模块按「片名 + 类型」再缓存一层，避免把订阅巡检变成 TMDB 压测。
"""

from __future__ import annotations

import re
import time
from typing import Any

from app.core.config import settings
from app.core.logger import get_logger
from app.schemas.enums import MediaType

logger = get_logger(__name__)

#: 别名缓存：``(title, media_type) -> (过期时间, 别名列表)``
_CACHE: dict[tuple[str, str], tuple[float, list[str]]] = {}

#: 缓存有效期（秒）。别名基本不变，给足 6 小时。
CACHE_TTL = 6 * 3600

#: 单个作品最多取多少别名。别名可以有几十个（每个地区一条），
#: 全部拿去搜会让一次订阅巡检变成几十次站点请求 —— 站点会限流，
#: 而且第 10 个地区的译名几乎不可能是种子命名用的那个。
MAX_ALIASES = 4

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def has_cjk(text: str) -> bool:
    """标题里是否含中日韩字符。"""
    return bool(_CJK_RE.search(str(text or "")))


def has_latin(text: str) -> bool:
    """标题里是否含拉丁字母。"""
    return bool(_LATIN_RE.search(str(text or "")))


def _cache_get(key: tuple[str, str]) -> list[str] | None:
    item = _CACHE.get(key)
    if not item:
        return None
    expire_at, value = item
    if expire_at < time.time():
        _CACHE.pop(key, None)
        return None
    return value


def _cache_set(key: tuple[str, str], value: list[str]) -> None:
    _CACHE[key] = (time.time() + CACHE_TTL, value)


def clear_cache() -> None:
    """清空别名缓存（测试与「立即重试」用）。"""
    _CACHE.clear()


def _is_useful(alias: str, base: str) -> bool:
    """这个别名值得拿去搜吗？

    过滤掉三类噪音，否则会白白多出几次站点请求：

    * 与原标题只差大小写/空白
    * 太短（1~2 个字符的缩写，搜出来全是噪音）
    * 与原标题**同语种**且高度重合（如「凡人修仙传」vs「凡人修仙传 第二季」）——
      同语种别名对「让国际站能命中」这个目标没有贡献
    """
    cleaned = str(alias or "").strip()
    if len(cleaned) < 3:
        return False
    if cleaned.lower() == str(base or "").strip().lower():
        return False
    # 原标题是中文时，只有含拉丁字母的别名才有价值（反之亦然）
    if has_cjk(base) and not has_latin(cleaned):
        return False
    # 原标题是英文，别名也是英文：只有明显不同才留（避免 the/a 之类的冠词差异）
    same_english = (
        not has_cjk(base)
        and has_latin(base)
        and not has_cjk(cleaned)
        and _strip_article(cleaned) == _strip_article(base)
    )
    return not same_english


def _strip_article(text: str) -> str:
    """去掉冠词后比较，避免「The Dune」与「Dune」被当成两个别名。"""
    return str(text or "").lower().replace("the ", "").strip()


async def aliases_for(
    title: str,
    *,
    media_type: str | None = None,
    tmdb_id: int | None = None,
    original_title: str | None = None,
    limit: int = MAX_ALIASES,
) -> list[str]:
    """取某部作品的可搜索别名（不含原标题本身）。

    ``tmdb_id`` / ``original_title`` 若调用方已有就传进来，可省掉一次识别请求
    （订阅记录里通常已经存了 ``tmdb_id``）。

    **任何异常都只降级不抛出**：别名是「锦上添花」，
    不能因为 TMDB 抽风就让整个搜索失败。
    """
    base = str(title or "").strip()
    if not base:
        return []
    kind = str(media_type or MediaType.UNKNOWN.value)
    cache_key = (base.lower(), kind)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached[:limit]

    collected: list[str] = []

    def _push(value: Any) -> None:
        text = str(value or "").strip()
        if text and _is_useful(text, base) and text not in collected:
            collected.append(text)

    # 第 2 级先用：调用方已经拿到的 original_title，零请求
    _push(original_title)

    try:
        from app.providers.metadata.tmdb import tmdb

        if tmdb.available:
            resolved_id = tmdb_id
            resolved_kind = kind
            if not resolved_id:
                found = await tmdb.recognize(base, media_type=media_type or None)
                if found:
                    resolved_id = found.get("tmdb_id")
                    resolved_kind = str(found.get("media_type") or kind)
                    _push(found.get("original_title"))
            if resolved_id:
                # anime 在 TMDB 里就是 tv，别名接口没有 anime 这个类别
                lookup_kind = (
                    MediaType.MOVIE.value
                    if resolved_kind == MediaType.MOVIE.value
                    else MediaType.TV.value
                )
                for alias in await tmdb.alternative_titles(int(resolved_id), lookup_kind):
                    _push(alias)
    except Exception as exc:  # pragma: no cover - 别名失败只降级
        logger.warning("取别名失败（降级为仅用原标题）%s: %s", base, exc)

    _cache_set(cache_key, collected)
    if collected:
        logger.info("别名扩展「%s」→ %s", base, collected[:limit])
    return collected[:limit]


#: 标题里内嵌的季号，如「第三季」「第 3 季」「第三部」。
#: 与 ``search._EMBEDDED_SEASON_RE`` 同一套写法，但这里服务于**过滤**：
#: 搜的时候换了写法，过滤白名单也必须认同一批写法，否则自相矛盾。
_SEASON_IN_TITLE_RE = re.compile(r"第\s*([0-9]{1,2}|[零一二三四五六七八九十]{1,3})\s*(季|部)")

_CN_DIGITS_ALIAS = "零一二三四五六七八九十"


def _cn_num(value: int) -> str:
    """阿拉伯数字转中文（1→一 … 20→二十）。仅覆盖季号的实际取值范围。"""
    if value <= 10:
        return _CN_DIGITS_ALIAS[value]
    if value < 20:
        return "十" + _CN_DIGITS_ALIAS[value - 10]
    if value % 10 == 0 and value < 100:
        return _CN_DIGITS_ALIAS[value // 10] + "十"
    return str(value)


def _cn_to_int_alias(text: str) -> int | None:
    """中文或阿拉伯数字转 int，认不出来返回 None。"""
    raw = str(text or "").strip()
    if raw.isdigit():
        return int(raw)
    if not raw or any(ch not in _CN_DIGITS_ALIAS for ch in raw):
        return None
    if raw == "十":
        return 10
    if raw.startswith("十"):
        return 10 + _CN_DIGITS_ALIAS.index(raw[1])
    if raw.endswith("十"):
        return _CN_DIGITS_ALIAS.index(raw[0]) * 10
    if "十" in raw:
        tens, _, ones = raw.partition("十")
        return _CN_DIGITS_ALIAS.index(tens) * 10 + _CN_DIGITS_ALIAS.index(ones)
    if len(raw) == 1:
        return _CN_DIGITS_ALIAS.index(raw)
    return None


def _season_rewrites(base: str) -> set[str]:
    """标题内嵌季号时，产出季号的**其它等价写法**。

    **为什么过滤侧也需要它**（v1.28.0 实测查出的跨层缺陷）：
    ``search.build_keywords()`` 会刻意用「第三季」「第3季」两种写法去搜
    （资源站两种都存在），但白名单以前只按用户原始写法生成。结果是
    **用一种写法搜到、用另一种写法判不匹配**，自己把自己的结果扔掉。

    实测「喜剧之王单口季 第三季」：

    | 关键词 | PanSou raw | kept |
    |---|---|---|
    | 喜剧之王单口季第三季 | 58 | 58 |
    | 喜剧之王单口季 第三季（带空格，用户实际输入）| 1 | **0** |
    | 喜剧之王单口季（裸片名）| 120 | 120 |

    中间那一行就是这个缺陷：站点按「第3季」写法返回，白名单里只有
    「第三季」，于是 kept=0 —— 界面上显示「返回 1 条但都不匹配」，
    用户以为没有资源，其实是过滤层把它拒了。
    """
    found = _SEASON_IN_TITLE_RE.search(base)
    if not found:
        return set()
    number = _cn_to_int_alias(found.group(1))
    if number is None:
        return set()
    marker = found.group(2)
    prefix = base[: found.start()].rstrip()
    suffix = base[found.end() :].lstrip()
    out: set[str] = set()
    for spelled in {_cn_num(number), str(number)}:
        for joiner in ("", " "):
            candidate = f"{prefix}{joiner}第{spelled}{marker}"
            if suffix:
                candidate = f"{candidate} {suffix}"
            out.add(candidate.strip())
        # 「…单口季3」这种把季号缩成纯数字的写法在网盘标题里很常见
        if not suffix:
            out.add(f"{prefix}{number}")
    return out


def title_match_variants(title: str, aliases: list[str] | None = None) -> list[str]:
    """生成**标题相关性过滤**用的正则白名单（原标题变体 + 别名变体）。

    这是原 ``search._title_variants`` 的超集：额外把别名也算进去，
    否则国际站命中的英文标题会被判「与订阅名称不匹配」而全部丢掉。

    白名单必须覆盖 :func:`_season_rewrites` 给出的季号写法变体 ——
    搜索侧换写法而过滤侧不换，等于自己否决自己搜到的结果（见该函数文档）。
    """
    from app.utils.strings import normalize

    variants: set[str] = set()

    def _add_variants(text: str) -> None:
        base = normalize(text).strip()
        if not base:
            return
        forms = {base, *_season_rewrites(base)}
        for form in forms:
            if not form:
                continue
            variants.add(form)
            chinese = "".join(re.findall(r"[\u4e00-\u9fff0-9]+", form))
            if len(chinese) >= 2:
                variants.add(chinese)
            english = " ".join(re.findall(r"[A-Za-z]+", form))
            if len(english) >= 3:
                variants.add(english)
                variants.add(english.replace(" ", "."))
                variants.add(english.replace(" ", ""))

    _add_variants(title)
    for alias in aliases or []:
        _add_variants(alias)
    return [re.escape(item) for item in variants if item]


def enabled() -> bool:
    """是否启用别名扩展（配置开关，默认开）。"""
    return bool(getattr(settings, "SEARCH_ALIAS_EXPAND", True))
