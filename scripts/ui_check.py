"""用真实浏览器逐页点检 CineFlow 前端，捕获任何 JS 报错。"""
import os
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")

#: 服务地址，可用 CF_BASE_URL / CF_PORT 覆盖
BASE = os.environ.get("CF_BASE_URL") or f"http://127.0.0.1:{os.environ.get('CF_PORT', '6060')}"
SHOTS = Path("data/ui_shots")
SHOTS.mkdir(parents=True, exist_ok=True)

PAGES = [
    ("dashboard", "仪表盘"),
    ("search", "资源搜索"),
    ("trending", "热度排行"),
    ("subscribes", "订阅追新"),
    ("radar", "追新雷达"),
    ("ranking", "榜单订阅"),
    ("rules", "过滤规则组"),
    ("schedules", "定时任务"),
    ("downloads", "下载任务"),
    ("library", "媒体库"),
    ("storage", "网盘管理"),
    ("pansub", "分享追更"),
    ("videosub", "视频追更"),
    ("strm", "STRM 同步"),
    ("sites", "站点管理"),
    ("sitehealth", "站点健康"),
    ("chatops", "机器人"),
    ("plugins", "插件"),
    ("users", "用户权限"),
    ("logs", "运行日志"),
    ("changelog", "更新日志"),
    ("settings", "设置"),
]

errors = []
failed_requests = []


def close_modal(page):
    """关掉当前弹窗。

    优先点「取消」/「关闭」，都找不到就按 Escape——
    弹窗遗留的 .modal-mask 会拦截后续所有点击，导致后面的点检莫名超时。
    """
    for label in ("取消", "关闭"):
        button = page.get_by_text(label, exact=True).first
        if button.count():
            try:
                button.click(timeout=3000)
                page.wait_for_timeout(400)
                return
            except Exception:
                break
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)


def first_number_input(page, timeout=15000):
    """等设置页的数字输入框渲染出来再返回。

    设置页的保存/恢复默认都会触发整页重渲染（先 loading 再拉全量配置），
    期间输入框会短暂消失。固定 sleep 能不能撞上全看运气，显式等待才稳。
    """
    locator = page.locator('.card input[type="number"]').first
    locator.wait_for(state="visible", timeout=timeout)
    return locator


def wait_button(page, label, timeout=15000):
    """等某个按钮真正出现再返回，找不到返回 None。

    页面数据全是异步拉的，前一段刚触发过「立即巡检」这类慢请求时，
    networkidle 之后立刻找按钮会偶发落空——那是点检脚本抢跑，不是页面坏了。
    """
    button = page.get_by_role("button", name=label, exact=True).first
    try:
        button.wait_for(state="visible", timeout=timeout)
    except Exception as exc:
        print(f"   [debug] 等按钮「{label}」失败：{type(exc).__name__} {str(exc)[:120]}")
        # 只报数量等于没报：把实际渲染出来的按钮名列出来，
        # 才能区分「页面没渲染完」和「按钮改名了/权限没给」
        names = page.evaluate(
            "Array.from(document.querySelectorAll('button')).map(b => (b.innerText||'').trim())"
            ".filter(Boolean)"
        )
        print(f"   [debug] 当前 URL={page.url} 按钮数={len(names)}")
        print(f"   [debug] 按钮清单={names}")
        print(f"   [debug] 弹窗遮罩={page.locator('.modal-mask').count()} 弹窗={page.locator('.modal').count()}")
        return None
    return button


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 960})
        page = context.new_page()

        page.on("pageerror", lambda exc: errors.append(f"[pageerror] {exc}"))
        page.on(
            "console",
            lambda msg: errors.append(f"[console.{msg.type}] {msg.text}")
            if msg.type == "error"
            else None,
        )
        page.on(
            "requestfailed",
            lambda request: failed_requests.append(
                f"{request.method} {request.url} -> {request.failure}"
            ),
        )

        print("=" * 68)
        print("1) 打开登录页")
        print("=" * 68)
        page.goto(BASE, wait_until="networkidle")
        page.screenshot(path=str(SHOTS / "00-login.png"))
        assert page.locator("input[type=password]").count() == 1, "未渲染登录表单"
        print("   登录页渲染正常")

        print("\n" + "=" * 68)
        print("2) 登录 admin / cineflow")
        print("=" * 68)
        inputs = page.locator("input")
        inputs.nth(0).fill("admin")
        page.locator("input[type=password]").fill("cineflow")
        page.get_by_text("登录", exact=True).last.click()
        page.wait_for_timeout(1500)
        page.screenshot(path=str(SHOTS / "01-after-login.png"))

        nav_count = page.locator("aside .nav-item").count()
        group_count = page.locator("aside .nav-label").count()
        print(f"   登录成功，侧边导航项 {nav_count} 个（分组 {group_count} 个）")
        # 功能页数 + 1 个退出按钮
        if nav_count != len(PAGES) + 1:
            errors.append(
                f"[nav] 导航项 {nav_count} 个，期望 {len(PAGES) + 1} 个"
            )

        print("\n" + "=" * 68)
        print("3) 逐页点检")
        print("=" * 68)
        for index, (route, label) in enumerate(PAGES, start=1):
            before = len(errors)
            page.goto(f"{BASE}/#{route}", wait_until="networkidle")
            page.wait_for_timeout(1200)
            page.screenshot(path=str(SHOTS / f"{index + 1:02d}-{route}.png"), full_page=True)

            body = page.inner_text("body")
            new_errors = errors[before:]
            has_label = label in body
            status = "OK " if (has_label and not new_errors) else "WARN"
            print(f"   {status} #{route:11} 标题命中={has_label} 文本长度={len(body)} 新报错={len(new_errors)}")
            for item in new_errors:
                print(f"        {item}")

        print("\n" + "=" * 68)
        print("4) 交互测试：打开新增订阅弹窗")
        print("=" * 68)
        page.goto(f"{BASE}/#subscribes", wait_until="networkidle")
        page.wait_for_timeout(800)
        add = page.get_by_text("新增订阅", exact=False).first
        if add.count():
            add.click()
            page.wait_for_timeout(600)
            modal_visible = page.locator(".modal").count() > 0
            print(f"   订阅弹窗渲染：{modal_visible}")
            page.screenshot(path=str(SHOTS / "10-subscribe-modal.png"))
            cancel = page.get_by_text("取消", exact=True).first
            if cancel.count():
                cancel.click()
                page.wait_for_timeout(300)
        else:
            print("   未找到新增订阅按钮")

        print("\n" + "=" * 68)
        print("5) 交互测试：插件页启用/配置")
        print("=" * 68)
        page.goto(f"{BASE}/#plugins", wait_until="networkidle")
        page.wait_for_timeout(1000)
        cards = page.locator(".card")
        print(f"   插件卡片 {cards.count()} 个")
        body = page.inner_text("body")
        for name in ("自动清理", "网盘转存助手", "每日追剧日报"):
            print(f"   插件「{name}」显示：{name in body}")

        enable_btn = page.get_by_text("启用", exact=True).first
        if enable_btn.count():
            enable_btn.click()
            page.wait_for_timeout(1500)
            page.screenshot(path=str(SHOTS / "11-plugin-enabled.png"))
            print(f"   点击启用后页面含「停用」：{'停用' in page.inner_text('body')}")

        config_btn = page.get_by_text("配置", exact=True).first
        if config_btn.count():
            config_btn.click()
            page.wait_for_timeout(800)
            fields = page.locator(".modal .field").count()
            print(f"   配置弹窗字段数：{fields}")
            page.screenshot(path=str(SHOTS / "12-plugin-config.png"))
            cancel = page.get_by_text("取消", exact=True).first
            if cancel.count():
                cancel.click()
                page.wait_for_timeout(400)

        disable_btn = page.get_by_text("停用", exact=True).first
        if disable_btn.count():
            disable_btn.click()
            page.wait_for_timeout(1200)
            print("   已恢复停用状态")

        print("\n" + "=" * 68)
        print("6) 交互测试：执行一次资源搜索（无站点应给出空态而非崩溃）")
        print("=" * 68)
        page.goto(f"{BASE}/#search", wait_until="networkidle")
        page.wait_for_timeout(800)
        search_input = page.locator("input").first
        if search_input.count():
            search_input.fill("庆余年")
            page.keyboard.press("Enter")
            page.wait_for_timeout(2500)
            page.screenshot(path=str(SHOTS / "13-search.png"), full_page=True)
            print(f"   搜索后页面文本长度：{len(page.inner_text('body'))}")

        print("\n" + "=" * 68)
        print("6b) 交互测试：自定义站点（模板 / 字段映射 / 发现）")
        print("=" * 68)
        page.goto(f"{BASE}/#sites", wait_until="networkidle")
        page.wait_for_timeout(1000)

        # v1.10.0 需求 2：下载器已搬到设置页，站点管理页不该再有它的卡片，
        # 且要提供跳转入口（否则老用户会以为下载器配置丢了）。
        sites_body = page.inner_text("body")
        dl_group = re.search(r"下载器（\d+）", sites_body)
        print(f"   站点管理页仍有下载器分组：{bool(dl_group)}（期望 False）")
        if dl_group:
            errors.append("[sites] 下载器已搬到设置页，站点管理页不该再列出下载器分组")
        print(f"   含跳转提示「下载器请到「设置」页配置」：{'下载器请到' in sites_body}")
        if "下载器请到" not in sites_body:
            errors.append("[sites] 缺少「下载器请到设置页配置」的引导文案")
        jump = page.get_by_role("button", name="下载器设置")
        print(f"   「下载器设置」跳转按钮：{jump.count() > 0}")
        if not jump.count():
            errors.append("[sites] 缺少「下载器设置」跳转按钮")

        tpl = page.get_by_text("从模板添加", exact=False).first
        if tpl.count():
            tpl.click()
            page.wait_for_timeout(700)
            body = page.inner_text("body")
            print(f"   模板弹窗含 Mukaku：{'Mukaku' in body}")
            print(f"   模板弹窗含「已验证」：{'已验证' in body}")
            use = page.get_by_text("使用此模板", exact=True).first
            if use.count():
                use.click()
                page.wait_for_timeout(700)
                modal_body = page.inner_text(".modal") if page.locator(".modal").count() else ""
                print(f"   套用模板后表单含 options：{'options' in modal_body}")
                page.screenshot(path=str(SHOTS / "15-site-preset.png"))
            close = page.get_by_text("取消", exact=True).first
            if not close.count():
                close = page.get_by_text("关闭", exact=True).first
            if close.count():
                close.click()
                page.wait_for_timeout(400)

        discover = page.get_by_text("发现站点", exact=False).first
        if discover.count():
            discover.click()
            page.wait_for_timeout(600)
            print(f"   发现弹窗渲染：{page.locator('.modal').count() > 0}")
            scan = page.get_by_text("开始发现", exact=True).first
            if scan.count():
                scan.click()
                page.wait_for_timeout(6000)
                modal_text = page.inner_text(".modal") if page.locator(".modal").count() else ""
                print(f"   发现结果文本长度：{len(modal_text)}")
                print(f"   含「发现」统计：{'发现' in modal_text}")
                page.screenshot(path=str(SHOTS / "16-site-discover.png"), full_page=True)
            close = page.get_by_text("关闭", exact=True).first
            if close.count():
                close.click()
                page.wait_for_timeout(400)

        print("\n" + "=" * 68)
        print("6c) 交互测试：追新雷达预览")
        print("=" * 68)
        page.goto(f"{BASE}/#radar", wait_until="networkidle")
        page.wait_for_timeout(1200)
        body = page.inner_text("body")
        print(f"   雷达页含「定时追新任务」：{'定时追新任务' in body}")
        print(f"   雷达页含定时任务名：{'追新雷达' in body}")

        dry = page.get_by_text("预览匹配", exact=True).first
        if dry.count():
            dry.click()
            page.wait_for_timeout(4000)
            body = page.inner_text("body")
            print(f"   预览匹配后含统计标签：{'活跃订阅' in body}")
            page.screenshot(path=str(SHOTS / "17-radar.png"), full_page=True)

        feed = page.get_by_text("预览最新流", exact=True).first
        if feed.count():
            feed.click()
            page.wait_for_timeout(4000)
            body = page.inner_text("body")
            print(f"   最新流区块渲染：{'最新资源流' in body}")
            page.screenshot(path=str(SHOTS / "18-radar-feed.png"), full_page=True)

        print("\n" + "=" * 68)
        print("6d) 交互测试：暗色 / 浅色主题切换")
        print("=" * 68)
        page.goto(f"{BASE}/#dashboard", wait_until="networkidle")
        page.wait_for_timeout(900)
        theme_before = page.evaluate("() => document.documentElement.dataset.theme")
        print(f"   初始主题：{theme_before}")
        switched = []
        btn = page.locator(".theme-toggle").first
        if btn.count():
            # 依次点击主题切换控件内的每个可点击项
            options = page.locator(".theme-toggle button[data-theme-btn]")
            total = options.count()
            print(f"   主题切换控件按钮 {total} 个")
            for i in range(total):
                options.nth(i).click()
                page.wait_for_timeout(500)
                now = page.evaluate("() => document.documentElement.dataset.theme")
                stored = page.evaluate("() => localStorage.getItem('cf_theme')")
                switched.append((stored, now))
                print(f"     点击 #{i + 1} -> data-theme={now} localStorage.cf_theme={stored}")
            page.screenshot(path=str(SHOTS / "19-theme.png"), full_page=True)
            themes_seen = {item[1] for item in switched}
            print(f"   出现过的主题：{sorted(themes_seen)}")
            print(f"   主题确实发生切换：{len(themes_seen) > 1}")
            # 校验浅色主题下背景色确实变化
            page.evaluate("() => { localStorage.setItem('cf_theme','light'); }")
            page.reload(wait_until="networkidle")
            page.wait_for_timeout(900)
            light_bg = page.evaluate(
                "() => getComputedStyle(document.body).backgroundColor"
            )
            light_attr = page.evaluate("() => document.documentElement.dataset.theme")
            page.screenshot(path=str(SHOTS / "20-theme-light.png"), full_page=True)
            page.evaluate("() => { localStorage.setItem('cf_theme','dark'); }")
            page.reload(wait_until="networkidle")
            page.wait_for_timeout(900)
            dark_bg = page.evaluate(
                "() => getComputedStyle(document.body).backgroundColor"
            )
            dark_attr = page.evaluate("() => document.documentElement.dataset.theme")
            print(f"   浅色：data-theme={light_attr} body bg={light_bg}")
            print(f"   暗色：data-theme={dark_attr} body bg={dark_bg}")
            print(f"   两主题背景色不同：{light_bg != dark_bg}")
            if light_bg == dark_bg:
                errors.append("[theme] 浅色与暗色背景色相同，主题未生效")
        else:
            errors.append("[theme] 未找到 .theme-toggle 控件")
            print("   未找到主题切换控件")

        print("\n" + "=" * 68)
        print("6e) 交互测试：热度排行（六分类切换 / 视频类直接下载）")
        print("=" * 68)
        page.goto(f"{BASE}/#trending", wait_until="networkidle")
        # 发现榜要打真实外部接口（豆瓣/B站），首屏比其他页慢
        page.wait_for_timeout(3500)
        body = page.inner_text("body")
        print(f"   热度页含「热度排行」：{'热度排行' in body}")
        # v1.9.0：只保留发现榜，四个旧页签必须彻底消失（ADR-43）
        for gone in ("资源热榜", "实时热榜", "搜索热词", "站点贡献"):
            if gone in body:
                errors.append(f"[trending] 已下线的页签「{gone}」仍出现在页面上")
            print(f"   已移除「{gone}」：{gone not in body}")
        # v1.10.0：页内搜索卡片已整体下线（改为点「搜资源」跳资源搜索页），
        # 所以这里反过来断言旧 DOM 必须彻底消失，避免样式残留或回退。
        for gone_sel in (".trending-split", ".side-panel", ".side-item"):
            leftover = page.locator(gone_sel).count()
            print(f"   已移除 {gone_sel}：{leftover == 0}")
            if leftover:
                errors.append(f"[trending] 已下线的页内搜索 DOM {gone_sel} 仍存在 {leftover} 个")

        # v1.12.0：七个分类页签（豆瓣四类 + Bilibili + YouTube + 新番）
        #
        # ⚠️ 必须限定在**第一个** .segment 里。筛选栏其实有三组 segment：
        # 分类 / 二级分区 / 视图（画板|列表|日历）。早先用 ".segment button"
        # 全选，等于把「列表」也当成一个分类去点 —— 于是循环结束时视图被切成
        # 列表，紧接着的「画板必须有卡片」断言就必然失败。
        # 之前没暴露是因为点到「Bilibili」后二级分区出现、把索引挤走了，
        # 纯属巧合；分类一加到 7 个这个巧合就不成立了。
        segs = page.locator(".segment").first.locator("button")
        seg_labels = [segs.nth(i).inner_text().strip() for i in range(segs.count())]
        print(f"   分类切换按钮 {segs.count()} 个：{seg_labels}")
        for want in ("电影", "电视剧", "动漫", "综艺", "Bilibili", "YouTube", "新番"):
            if want not in seg_labels:
                errors.append(f"[trending] 分类页签缺少「{want}」")
        if len(seg_labels) != 7:
            errors.append(f"[trending] 分类页签应为 7 个，实际 {seg_labels}")
        # 逐个点一遍：任何分类都不能把页面点成空白/报错
        for i in range(segs.count()):
            label = segs.nth(i).inner_text().strip()
            segs.nth(i).click()
            page.wait_for_timeout(2500)
            text = page.inner_text("body")
            print(f"     切到「{label}」-> 文本长度 {len(text)}")
            if len(text) < 200:
                errors.append(f"[trending] 切到「{label}」后页面几乎空白")
        page.screenshot(path=str(SHOTS / "21-trending.png"), full_page=True)

        print("\n" + "=" * 68)
        print("6e-1) 交互测试：视频类分类（Bilibili/YouTube）直接给下载按钮")
        print("=" * 68)
        # kind=video 的分类不该出现「搜资源」——它们本身就有播放地址，
        # 直接交给 yt-dlp，多一步搜资源是无意义的绕路。
        for label, sub_label in (("Bilibili", "分区"), ("YouTube", "地区")):
            tab = page.get_by_role("button", name=label, exact=True)
            if not tab.count():
                continue
            tab.first.click()
            page.wait_for_timeout(3500)
            body_v = page.inner_text("body")
            has_sub = sub_label in body_v
            print(f"   「{label}」二级切换（{sub_label}）：{has_sub}")
            if not has_sub:
                errors.append(f"[trending] 「{label}」缺少{sub_label}二级切换")
            cards_v = page.locator(".board-card").count()
            dl_btn = page.locator(".board-card").locator("text=下载").count()
            search_btn = page.locator(".board-card").locator("text=搜资源").count()
            print(f"   卡片 {cards_v} 张 / 下载按钮 {dl_btn} 个 / 搜资源按钮 {search_btn} 个")
            if cards_v and search_btn:
                errors.append(f"[trending] 「{label}」是视频类，卡片不该出现「搜资源」按钮")
            if cards_v and not dl_btn:
                errors.append(f"[trending] 「{label}」卡片缺少「下载」按钮")
            if not cards_v:
                # YouTube 依赖公开 Piped 实例，实例挂了属正常，只提示不算失败
                print(f"   （「{label}」本次无数据，可能是上游实例不可用，跳过按钮校验）")

        print("\n" + "=" * 68)
        print("6e-1b) 交互测试：新番放送日历（v1.12.0）")
        print("=" * 68)
        # 「新番」页签独有的第三种视图：按周一~周日分列，标出今天。
        # 它与「动漫」页签口径不同（那个是豆瓣热度榜，答不了"周几更新"）。
        bangumi_tab = page.get_by_role("button", name="新番", exact=True)
        if bangumi_tab.count():
            bangumi_tab.first.click()
            page.wait_for_timeout(4000)
            cal_btn = page.get_by_role("button", name="日历", exact=True)
            print(f"   「新番」提供日历视图：{cal_btn.count() > 0}")
            if not cal_btn.count():
                errors.append("[trending] 「新番」页签缺少日历视图")
            else:
                cal_btn.first.click()
                page.wait_for_timeout(5000)
                cols = page.locator(".cal-col").count()
                items = page.locator(".cal-item").count()
                today = page.locator(".cal-col.today").count()
                print(f"   日历列 {cols} 列 / 条目 {items} 个 / 今天高亮 {today} 列")
                # 上游（Bangumi）不可用时列数为 0 属正常，只在有数据时校验结构
                if cols:
                    if cols < 7:
                        errors.append(f"[trending] 放送日历应有 7 列（含未定可为 8），实际 {cols}")
                    if today != 1:
                        errors.append(f"[trending] 放送日历应恰好高亮 1 列「今天」，实际 {today}")
                    page.screenshot(path=str(SHOTS / "21b-bangumi-calendar.png"), full_page=True)
                else:
                    print("   （Bangumi 本次无数据，跳过日历结构校验）")
                # 切回画板：视图状态跨页签保留，留在日历会影响后续画板断言
                board_back = page.get_by_role("button", name="画板", exact=True)
                if board_back.count():
                    board_back.first.click()
                    page.wait_for_timeout(1500)
        else:
            errors.append("[trending] 找不到「新番」分类页签")

        print("\n" + "=" * 68)
        print("6e-2) 交互测试：发现榜画板模式与封面降级")
        print("=" * 68)
        # 回到第一个分类（上一步循环可能停在被限流而空的分类上）
        segs.nth(0).click()
        page.wait_for_timeout(3000)
        # 视图状态是跨页签保留的，前面的点检可能把它留在「列表」上。
        # 画板断言之前显式切回画板，否则断言的是另一个视图。
        board_reset = page.get_by_role("button", name="画板", exact=True)
        if board_reset.count():
            board_reset.first.click()
            page.wait_for_timeout(2000)
        board_btn = page.get_by_role("button", name="画板", exact=True)
        list_btn = page.get_by_role("button", name="列表", exact=True)
        print(f"   视图切换按钮：画板 {board_btn.count()} 个 / 列表 {list_btn.count()} 个")
        if not board_btn.count() or not list_btn.count():
            errors.append("[trending] 资源榜未提供画板/列表视图切换")
        cards = page.locator(".board-card")
        print(f"   默认画板卡片 {cards.count()} 张")
        if cards.count() == 0:
            errors.append("[trending] 画板模式未渲染卡片")
        # 封面：真图不能裂；站点没给图时必须降级成占位色块而不是空白
        real = page.locator(".board-cover img")
        placeholders = page.locator(".poster-ph")
        broken = page.evaluate(
            "Array.from(document.querySelectorAll('.board-cover img'))"
            ".filter(i => i.complete && i.naturalWidth === 0).length"
        )
        print(f"   真实封面 {real.count()} 张 / 占位 {placeholders.count()} 个 / 裂图 {broken} 张")
        if broken:
            errors.append(f"[trending] 画板有 {broken} 张裂图（onerror 未退占位）")
        if real.count() + placeholders.count() < cards.count():
            errors.append("[trending] 有卡片既无封面也无占位")
        # 切列表再切回画板，确认两个视图都能渲染
        for label in ("列表", "画板"):
            target = page.get_by_role("button", name=label)
            if target.count():
                target.first.click()
                page.wait_for_timeout(2000)
                count = page.locator(".board-card").count() if label == "画板" else page.locator("tbody tr").count()
                print(f"     切到「{label}」-> {count} 项")
                if count == 0:
                    errors.append(f"[trending] 切到「{label}」后无内容")

        print("\n" + "=" * 68)
        print("6e-3) 交互测试：榜单首屏 30 条 + 下拉加载更多")
        print("=" * 68)
        first_page = page.locator(".board-card").count()
        print(f"   首屏卡片 {first_page} 张（期望 30；被限流的分类可能更少）")
        more_box = page.locator(".board-more")
        print(f"   加载更多区域 {more_box.count()} 个")
        if not more_box.count():
            errors.append("[trending] 榜单缺少「加载更多」区域")
        more_btn = page.locator(".board-more button")
        if more_btn.count() and first_page:
            more_btn.first.click()
            # 加载更多要打一次外部接口，给足时间
            page.wait_for_timeout(4000)
            second_page = page.locator(".board-card").count()
            print(f"   点击加载更多后 {first_page} -> {second_page} 张")
            # 只要没变少就算过：某些分类第二页可能因限流为空并显示「已到底」
            if second_page < first_page:
                errors.append(
                    f"[trending] 加载更多后卡片反而变少（{first_page} -> {second_page}）")
        else:
            print("   已到底或无更多数据，跳过加载更多点击")

        print("\n" + "=" * 68)
        print("6f) 交互测试：定时任务设置改期弹窗")
        print("=" * 68)
        page.goto(f"{BASE}/#schedules", wait_until="networkidle")
        page.wait_for_timeout(1500)
        body = page.inner_text("body")
        print(f"   定时任务页含「定时任务」：{'定时任务' in body}")
        for name in ("订阅巡检", "追新雷达", "下载状态同步", "媒体库全量扫描", "网盘待转存",
                     "网盘分享追更", "STRM 同步", "媒体库补刮", "洗版巡检"):
            print(f"   任务「{name}」显示：{name in body}")
        page.screenshot(path=str(SHOTS / "22-schedules.png"), full_page=True)

        edit = page.get_by_text("修改周期", exact=False).first
        if not edit.count():
            edit = page.get_by_text("编辑", exact=True).first
        if edit.count():
            edit.click()
            page.wait_for_timeout(800)
            modal = page.locator(".modal")
            print(f"   改期弹窗渲染：{modal.count() > 0}")
            if modal.count():
                modal_text = page.inner_text(".modal")
                print(f"   弹窗含触发方式选择：{'interval' in modal_text or '间隔' in modal_text}")
                print(f"   弹窗含 cron 输入：{'cron' in modal_text.lower()}")
                page.screenshot(path=str(SHOTS / "23-schedule-modal.png"))
            close = page.get_by_text("取消", exact=True).first
            if close.count():
                close.click()
                page.wait_for_timeout(400)
        else:
            print("   未找到改期按钮")

        run_now = page.get_by_text("立即执行", exact=False).first
        if run_now.count():
            run_now.click()
            page.wait_for_timeout(2500)
            print(f"   立即执行后页面文本长度：{len(page.inner_text('body'))}")
            page.screenshot(path=str(SHOTS / "24-schedule-run.png"), full_page=True)

        print("\n" + "=" * 68)
        print("6g) 交互测试：网盘管理页（容量卡 / 转存弹窗）")
        print("=" * 68)
        page.goto(f"{BASE}/#storage", wait_until="networkidle")
        page.wait_for_timeout(1600)
        body = page.inner_text("body")
        print(f"   网盘页含「网盘管理」：{'网盘管理' in body}")
        print(f"   含待转存队列：{'待转存队列' in body}")
        print(f"   含转存记录：{'转存记录' in body}")
        print(f"   容量卡片 {page.locator('.pan-card').count()} 个")
        page.screenshot(path=str(SHOTS / "25-storage.png"), full_page=True)

        save_btn = page.get_by_text("转存分享链接", exact=False).first
        if save_btn.count():
            save_btn.click()
            page.wait_for_timeout(700)
            modal = page.locator(".modal")
            print(f"   转存弹窗渲染：{modal.count() > 0}")
            if modal.count():
                modal_text = page.inner_text(".modal")
                print(f"   弹窗含分享链接字段：{'分享链接' in modal_text}")
                print(f"   弹窗含目标网盘选择：{'目标网盘' in modal_text}")
                page.screenshot(path=str(SHOTS / "26-storage-modal.png"))
            close = page.get_by_text("取消", exact=True).first
            if close.count():
                close.click()
                page.wait_for_timeout(400)
        else:
            errors.append("[storage] 未找到「转存分享链接」按钮")

        print("\n" + "=" * 68)
        print("6h) 交互测试：机器人页（平台配置 / 指令试跑）")
        print("=" * 68)
        page.goto(f"{BASE}/#chatops", wait_until="networkidle")
        page.wait_for_timeout(1600)
        body = page.inner_text("body")
        for name in ("飞书", "钉钉", "Telegram"):
            print(f"   平台「{name}」卡片显示：{name in body}")
        print(f"   回调地址框 {page.locator('.webhook-box').count()} 个")
        print(f"   含指令审计：{'指令审计' in body}")
        page.screenshot(path=str(SHOTS / "27-chatops.png"), full_page=True)

        try_input = page.locator("#app input.input").first
        if try_input.count():
            try_input.fill("状态")
            run = page.get_by_text("执行", exact=True).first
            if run.count():
                run.click()
                page.wait_for_timeout(2500)
                reply = page.inner_text("pre.logs") if page.locator("pre.logs").count() else ""
                print(f"   试跑「状态」回复长度：{len(reply)}")
                print(f"   回复含任务统计：{'累计已完成' in reply}")
                if "累计已完成" not in reply:
                    errors.append("[chatops] 状态指令回复内容不符合预期")
                if not reply or "执行中" in reply:
                    errors.append("[chatops] 指令试跑没有返回回复")
                page.screenshot(path=str(SHOTS / "28-chatops-test.png"), full_page=True)
            parse = page.get_by_text("只解析", exact=True).first
            if parse.count():
                try_input.fill("搜索 沙丘 第二季")
                parse.click()
                page.wait_for_timeout(1200)
                parsed = page.inner_text("pre.logs") if page.locator("pre.logs").count() else ""
                print(f"   解析结果含 season：{'season' in parsed}")
        else:
            errors.append("[chatops] 未找到指令试跑输入框")

        cfg_btn = page.get_by_text("配置密钥", exact=True).first
        if cfg_btn.count():
            cfg_btn.click()
            page.wait_for_timeout(700)
            fields = page.locator(".modal .field").count()
            print(f"   平台配置弹窗字段数：{fields}")
            page.screenshot(path=str(SHOTS / "29-chatops-config.png"))
            close = page.get_by_text("取消", exact=True).first
            if close.count():
                close.click()
                page.wait_for_timeout(400)

        print("\n" + "=" * 68)
        print("6i) 交互测试：设置页（配置分组 / 脱敏）")
        print("=" * 68)
        page.goto(f"{BASE}/#settings", wait_until="networkidle")
        page.wait_for_timeout(1500)
        body = page.inner_text("body")
        print(f"   设置页含「设置」：{'设置' in body}")
        for name in ("网盘管理", "ChatOps 机器人", "调度", "刮削与分类", "STRM 同步", "分享追更与洗版"):
            print(f"   配置分组「{name}」显示：{name in body}")
        print(f"   敏感项已脱敏：{'已设置' in body or 'SECRET_KEY' in body}")

        # v1.10.0 需求 3：只读项（服务/目录/安全）收进折叠卡片，默认收起。
        # 所以 CF_PORT 这类只读环境变量名默认不该出现在正文里，展开后才出现。
        ro_card = page.get_by_text("只读配置（需重启生效）", exact=True)
        print(f"   只读配置折叠卡片：{ro_card.count() > 0}")
        if not ro_card.count():
            errors.append("[settings] 缺少「只读配置（需重启生效）」折叠卡片")
        else:
            if "CF_PORT" in body:
                errors.append("[settings] 只读配置默认应收起，CF_PORT 不该直接出现")
            print(f"   默认收起（正文无 CF_PORT）：{'CF_PORT' not in body}")
            expand = page.get_by_role("button", name="展开查看")
            if expand.count():
                expand.first.click()
                page.wait_for_timeout(600)
                body_ro = page.inner_text("body")
                print(f"   展开后可见 CF_PORT：{'CF_PORT' in body_ro}")
                if "CF_PORT" not in body_ro:
                    errors.append("[settings] 展开只读配置后仍看不到 CF_PORT")
                page.get_by_role("button", name="收起").first.click()
                page.wait_for_timeout(400)
            else:
                errors.append("[settings] 只读配置卡片没有「展开查看」按钮")

        # v1.10.0 需求 2：下载器从站点管理搬到设置页
        dl_card = page.get_by_text("下载器", exact=True)
        print(f"   设置页含「下载器」卡片：{dl_card.count() > 0}")
        if not dl_card.count():
            errors.append("[settings] 设置页缺少「下载器」卡片（v1.10.0 已从站点管理搬来）")

        # v1.10.0 需求 3：可改配置组走多列布局，宽屏下不能再是单列长条
        cols = page.locator(".grid.cols-settings")
        print(f"   多列容器 {cols.count()} 个")
        if not cols.count():
            errors.append("[settings] 可编辑配置组未使用多列布局容器 .cols-settings")
        else:
            col_count = page.evaluate(
                "getComputedStyle(document.querySelector('.grid.cols-settings'))"
                ".getPropertyValue('column-count')"
            )
            print(f"   计算后 column-count = {col_count}（视口 {page.viewport_size})")
            if col_count in ("", "auto", "1"):
                errors.append(f"[settings] 多列布局未生效（column-count={col_count}）")
        page.screenshot(path=str(SHOTS / "30-settings.png"), full_page=True)

        pwd = page.get_by_text("修改密码", exact=True).first
        if pwd.count():
            pwd.click()
            page.wait_for_timeout(600)
            print(f"   改密弹窗渲染：{page.locator('.modal').count() > 0}")
            close = page.get_by_text("取消", exact=True).first
            if close.count():
                close.click()
                page.wait_for_timeout(300)

        print("\n" + "=" * 68)
        print("6j-1) 交互测试：更新日志页（v1.11.0 需求 3）")
        print("=" * 68)
        page.goto(f"{BASE}/#changelog", wait_until="networkidle")
        page.wait_for_timeout(1800)
        cards = page.locator(".chg-card").count()
        opened = page.locator(".chg-card.open").count()
        print(f"   版本卡 {cards} 张，默认展开 {opened} 张")
        if cards < 10:
            errors.append(f"[changelog] 只解析出 {cards} 个版本，疑似解析失效")
        # 默认只展开最新一版，否则页面会长到没法用
        if opened != 1:
            errors.append(f"[changelog] 默认应只展开 1 张，实际 {opened} 张")
        if not page.locator(".chg-head .tag.dot.ok").count():
            errors.append("[changelog] 没有标出「当前版本」")
        # 分组标签里不能残留 emoji（早期日志用 🆕，正则漏了就会漏出来）
        dirty = page.evaluate(
            "() => [...document.querySelectorAll('.chg-section-head .tag')]"
            ".map(e => e.textContent)"
            # 前缀 r：这里的 \u{...} 是 JS 正则语法，不能让 Python 先解析掉
            r".filter(x => /[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(x))"
        )
        print(f"   分组标签残留 emoji：{dirty}")
        if dirty:
            errors.append(f"[changelog] 分组标签残留 emoji：{dirty}")
        # 全部展开后条目数必须显著增加
        page.get_by_text("全部展开", exact=True).last.click()
        page.wait_for_timeout(1800)
        all_open = page.locator(".chg-card.open").count()
        items = page.locator(".chg-item-title").count()
        print(f"   全部展开后 {all_open} 张 / 改动条目 {items} 条")
        if all_open != cards:
            errors.append(f"[changelog] 全部展开失效（{all_open}/{cards}）")
        if items < 50:
            errors.append(f"[changelog] 改动条目只有 {items} 条，疑似解析不全")
        # 搜索过滤
        page.locator(".chg-search input").fill("qBittorrent")
        page.wait_for_timeout(1200)
        hit = page.locator(".chg-card").count()
        print(f"   搜索「qBittorrent」命中 {hit} 个版本")
        if not hit:
            errors.append("[changelog] 搜索 qBittorrent 无命中")
        page.locator(".chg-search input").fill("绝不存在的关键词zzz")
        page.wait_for_timeout(1200)
        if not page.locator(".empty").count():
            errors.append("[changelog] 搜索无结果时没有空态提示")
        page.locator(".chg-search input").fill("")
        page.wait_for_timeout(1000)
        page.screenshot(path=str(SHOTS / "30b-changelog.png"), full_page=True)

        print("\n" + "=" * 68)
        print("6j-2) 交互测试：仪表盘运行状态条不得留大片空白（v1.11.0 需求 2）")
        print("=" * 68)
        page.goto(f"{BASE}/#dashboard", wait_until="networkidle")
        page.wait_for_timeout(1800)
        strip = page.locator(".status-strip")
        print(f"   状态条存在：{strip.count() > 0}")
        if not strip.count():
            errors.append("[dashboard] 缺少 .status-strip 横向状态条")
        else:
            metrics = page.evaluate(
                """() => {
                  const s = document.querySelector('.status-strip');
                  const c = s.closest('.card');
                  const cr = c.getBoundingClientRect();
                  let bottom = 0;
                  c.querySelectorAll(':scope > *').forEach(ch => {
                    const r = ch.getBoundingClientRect();
                    if (r.bottom > bottom) bottom = r.bottom;
                  });
                  return {height: Math.round(cr.height),
                          blank: Math.round(cr.bottom - bottom),
                          items: document.querySelectorAll('.status-item').length};
                }"""
            )
            print(f"   状态卡高 {metrics['height']}px，底部空白 {metrics['blank']}px，"
                  f"状态项 {metrics['items']} 个")
            # 改造前是 711px / 空白 477px；这里留足余量防误报
            if metrics["height"] > 220:
                errors.append(
                    f"[dashboard] 运行状态卡片过高（{metrics['height']}px），空白又回来了"
                )
            if metrics["blank"] > 60:
                errors.append(
                    f"[dashboard] 运行状态卡片底部空白 {metrics['blank']}px 过大"
                )
            if metrics["items"] < 4:
                errors.append(f"[dashboard] 状态项只有 {metrics['items']} 个")

        print("\n" + "=" * 68)
        print("6j) 交互测试：STRM 同步页（概览卡 / 链接模式 / 同步弹窗）")
        print("=" * 68)
        page.goto(f"{BASE}/#strm", wait_until="networkidle")
        page.wait_for_timeout(1600)
        body = page.inner_text("body")
        print(f"   STRM 页含「STRM 同步」：{'STRM 同步' in body}")
        print(f"   含链接模式说明：{'链接模式' in body}")
        print(f"   含 302 播放端点说明：{'/api/v1/strm/play/' in body}")
        print(f"   概览卡片 {page.locator('.card.stat').count()} 个")
        page.screenshot(path=str(SHOTS / "31-strm.png"), full_page=True)

        sync_btn = page.get_by_text("手动同步", exact=False).first
        if sync_btn.count():
            sync_btn.click()
            page.wait_for_timeout(900)
            modal = page.locator(".modal")
            print(f"   同步弹窗渲染：{modal.count() > 0}")
            if modal.count():
                modal_text = page.inner_text(".modal")
                print(f"   弹窗含链接模式选择：{'proxy' in modal_text and 'direct' in modal_text}")
                print(f"   弹窗含失效清理开关：{'清理失效' in modal_text}")
                page.screenshot(path=str(SHOTS / "32-strm-modal.png"))
            close = page.get_by_text("取消", exact=True).first
            if close.count():
                close.click()
                page.wait_for_timeout(400)
        else:
            errors.append("[strm] 未找到「手动同步」按钮")

        print("\n" + "=" * 68)
        print("6k) 交互测试：分享追更页（巡检节奏 / 新建弹窗）")
        print("=" * 68)
        page.goto(f"{BASE}/#pansub", wait_until="networkidle")
        page.wait_for_timeout(1600)
        body = page.inner_text("body")
        print(f"   分享追更页含「分享追更」：{'分享追更' in body}")
        print(f"   含巡检节奏卡：{'巡检节奏' in body}")
        print(f"   含追更任务表：{'追更任务' in body}")
        page.screenshot(path=str(SHOTS / "33-pansub.png"), full_page=True)

        new_btn = page.get_by_text("新建任务", exact=False).first
        if new_btn.count():
            new_btn.click()
            page.wait_for_timeout(900)
            modal = page.locator(".modal")
            fields = page.locator(".modal .field").count()
            print(f"   新建弹窗渲染：{modal.count() > 0}（字段 {fields} 个）")
            if modal.count():
                modal_text = page.inner_text(".modal")
                print(f"   弹窗含正则过滤字段：{'只要匹配' in modal_text and '排除匹配' in modal_text}")
                print(f"   弹窗含重命名字段：{'重命名' in modal_text}")
                print(f"   弹窗含执行日字段：{'星期几' in modal_text}")
                page.screenshot(path=str(SHOTS / "34-pansub-modal.png"))
            close = page.get_by_text("取消", exact=True).first
            if close.count():
                close.click()
                page.wait_for_timeout(400)
        else:
            errors.append("[pansub] 未找到「新建任务」按钮")

        print("\n" + "=" * 68)
        print("6l) 交互测试：媒体库补刮 / 订阅洗版试算")
        print("=" * 68)
        page.goto(f"{BASE}/#library", wait_until="networkidle")
        page.wait_for_timeout(1400)
        body = page.inner_text("body")
        print(f"   媒体库页含「补刮 NFO」按钮：{'补刮' in body}")
        scrape = page.get_by_text("补刮 NFO", exact=False).first
        if scrape.count():
            scrape.click()
            page.wait_for_timeout(3000)
            print(f"   补刮后页面文本长度：{len(page.inner_text('body'))}")
            page.screenshot(path=str(SHOTS / "35-library-scrape.png"), full_page=True)
        else:
            errors.append("[library] 未找到「补刮 NFO」按钮")

        print("\n" + "=" * 68)
        print("6m) 交互测试：站点健康页（概览 / 手动探测）")
        print("=" * 68)
        page.goto(f"{BASE}/#sitehealth", wait_until="networkidle")
        page.wait_for_timeout(1600)
        body = page.inner_text("body")
        print(f"   站点健康页含「站点健康」：{'站点健康' in body}")
        print(f"   含四档状态卡：{all(word in body for word in ('正常', '亚健康', '掉线', '未探测'))}")
        print(f"   含巡检策略卡：{'巡检策略' in body}")
        # 这句话是该页存在的理由，掉了就说明改坏了
        print(f"   含「真搜一次」说明：{'搜一次' in body}")
        print(f"   概览卡片 {page.locator('.card.stat').count()} 个")
        page.screenshot(path=str(SHOTS / "36-sitehealth.png"), full_page=True)

        check_btn = page.get_by_text("立即巡检", exact=True).first
        if check_btn.count():
            # 真探测会去各站点搜一次，给足超时
            check_btn.click()
            page.wait_for_timeout(8000)
            print(f"   巡检后页面文本长度：{len(page.inner_text('body'))}")
            page.screenshot(path=str(SHOTS / "37-sitehealth-checked.png"), full_page=True)
        else:
            errors.append("[sitehealth] 未找到「立即巡检」按钮")

        print("\n" + "=" * 68)
        print("6n) 交互测试：榜单订阅页（新建规则弹窗 / 试算）")
        print("=" * 68)
        page.goto(f"{BASE}/#ranking", wait_until="networkidle")
        page.wait_for_timeout(1500)
        body = page.inner_text("body")
        print(f"   榜单订阅页含「榜单订阅」：{'榜单订阅' in body}")
        print(f"   含概览卡：{all(word in body for word in ('榜单规则', '启用中', '巡检周期'))}")
        print(f"   含定时执行卡：{'定时执行' in body}")
        page.screenshot(path=str(SHOTS / "38-ranking.png"), full_page=True)

        new_rule = wait_button(page, "新建")
        if new_rule:
            new_rule.click()
            page.wait_for_timeout(900)
            modal = page.locator(".modal")
            fields = page.locator(".modal .field").count()
            print(f"   榜单规则弹窗渲染：{modal.count() > 0}（字段 {fields} 个）")
            if modal.count():
                modal_text = page.inner_text(".modal")
                print(f"   弹窗含来源选择：{'来源' in modal_text}")
                print(f"   弹窗含评分门槛：{'评分' in modal_text}")
                print(f"   弹窗含数量上限：{'数量' in modal_text or '条数' in modal_text}")
                page.screenshot(path=str(SHOTS / "39-ranking-modal.png"))
            close_modal(page)
        else:
            errors.append("[ranking] 未找到「新建」按钮")

        print("\n" + "=" * 68)
        print("6o) 交互测试：过滤规则组页（内置模板 / 试算分层）")
        print("=" * 68)
        page.goto(f"{BASE}/#rules", wait_until="networkidle")
        page.wait_for_timeout(1500)
        body = page.inner_text("body")
        print(f"   规则组页含「过滤规则组」：{'规则组' in body}")
        # init_db 内置 4 个模板，页面上应当能看到其中的画质优先
        print(f"   含内置模板「画质优先」：{'画质优先' in body}")
        print(f"   含层级说明：{'层' in body}")
        page.screenshot(path=str(SHOTS / "40-rules.png"), full_page=True)

        preview_btn = page.get_by_text("试算", exact=True).first
        if preview_btn.count():
            preview_btn.click()
            page.wait_for_timeout(2500)
            body = page.inner_text("body")
            print(f"   试算后页面含层级标注：{'层' in body}")
            page.screenshot(path=str(SHOTS / "41-rules-preview.png"), full_page=True)
            # 试算结果是弹窗，不关掉的话遮罩会挡住下面的「新建」按钮
            close_modal(page)

        new_group = wait_button(page, "新建")
        if new_group:
            new_group.click()
            page.wait_for_timeout(900)
            modal = page.locator(".modal")
            print(f"   规则组弹窗渲染：{modal.count() > 0}")
            if modal.count():
                modal_text = page.inner_text(".modal")
                print(f"   弹窗含分辨率条件：{'分辨率' in modal_text}")
                print(f"   弹窗含兜底开关：{'兜底' in modal_text or '未命中' in modal_text}")
                page.screenshot(path=str(SHOTS / "42-rules-modal.png"))
            close_modal(page)

        print("\n" + "=" * 68)
        print("6p) 交互测试：用户权限页（列表 / 新建弹窗 / 角色选择）")
        print("=" * 68)
        page.goto(f"{BASE}/#users", wait_until="networkidle")
        page.wait_for_timeout(1500)
        body = page.inner_text("body")
        print(f"   用户权限页含「用户」：{'用户' in body}")
        print(f"   含 admin 账号行：{'admin' in body}")
        print(f"   含角色标签：{'管理员' in body}")
        page.screenshot(path=str(SHOTS / "43-users.png"), full_page=True)

        new_user = page.get_by_text("新增用户", exact=False).first
        if new_user.count():
            new_user.click()
            page.wait_for_timeout(900)
            modal = page.locator(".modal")
            fields = page.locator(".modal .field").count()
            print(f"   新增用户弹窗渲染：{modal.count() > 0}（字段 {fields} 个）")
            if modal.count():
                modal_text = page.inner_text(".modal")
                print(f"   弹窗含角色选择：{'角色' in modal_text}")
                print(f"   弹窗含备注字段：{'备注' in modal_text}")
                page.screenshot(path=str(SHOTS / "44-users-modal.png"))
            close_modal(page)
        else:
            errors.append("[users] 未找到「新增用户」按钮")

        print("\n" + "=" * 68)
        print("6q) 交互测试：设置页可编辑表单（改一项并保存 / 恢复默认）")
        print("=" * 68)
        page.goto(f"{BASE}/#settings", wait_until="networkidle")
        page.wait_for_timeout(1800)
        body = page.inner_text("body")
        print(f"   设置页含分组标题：{'站点健康' in body}")
        print(f"   含下载器调度分组：{'下载器' in body}")
        print(f"   含榜单订阅分组：{'榜单' in body}")
        inputs = page.locator(".card input, .card select").count()
        print(f"   可编辑控件 {inputs} 个")
        if inputs == 0:
            errors.append("[settings] 设置页没有任何可编辑控件")
        print(f"   含「需重启」标记：{'需重启' in body or '重启' in body}")
        # 「能改」必须配「能改回来」：这里真的改一项再恢复，光看按钮在不在证明不了什么
        has_save = wait_button(page, "保存并生效") is not None
        print(f"   含保存按钮：{has_save}")
        if not has_save:
            errors.append("[settings] 设置页缺少「保存并生效」按钮")
        else:
            # 先清掉历史覆盖再取"原值"：否则上一轮点检若中途失败，
            # 残留的覆盖值会被当成默认值，最后一步的比对必然对不上
            # （这是点检脚本的状态污染，不是产品缺陷）
            page.on("dialog", lambda dialog: dialog.accept())
            stale = wait_button(page, "全部恢复默认", timeout=4000)
            if stale is not None:
                stale.click()
                page.wait_for_timeout(2500)
                print("   已清理上一轮遗留的在线覆盖")
            number_input = first_number_input(page)
            original = number_input.input_value()
            number_input.fill(str(int(original or 0) + 1))
            page.get_by_role("button", name="保存并生效", exact=True).first.click()
            marked = False
            for _ in range(16):  # 最多等 8 秒：PUT + 重新拉全量配置
                page.wait_for_timeout(500)
                if "恢复默认" in page.inner_text("body"):
                    marked = True
                    break
            print(f"   保存后出现在线覆盖标记：{marked}")
            # 覆盖项存在时才渲染「全部恢复默认」，所以这一步必须在改动之后查
            reset = wait_button(page, "全部恢复默认", timeout=6000)
            if reset is None:
                errors.append("[settings] 改动生效后没有出现「全部恢复默认」按钮")
            else:
                reset.click()
                # 恢复默认要走 POST + 重新拉全量配置，轮询到值回落为止；
                # 一次性读取会读到重渲染前的旧值，得到假阴性
                restored = None
                for _ in range(16):
                    page.wait_for_timeout(500)
                    restored = first_number_input(page).input_value()
                    if restored == original:
                        break
                print(f"   恢复默认后回到原值：{restored == original}（{original}）")
                if restored != original:
                    errors.append("[settings] 恢复默认没有把值改回去")
        page.screenshot(path=str(SHOTS / "45-settings.png"), full_page=True)

        print("\n" + "=" * 68)
        print("6r) 交互测试：网络视频下载弹窗（yt-dlp）")
        print("=" * 68)
        page.goto(f"{BASE}/#downloads", wait_until="networkidle")
        page.wait_for_timeout(1500)
        entry = page.get_by_role("button", name="下载网络视频")
        if not entry.count():
            errors.append("[webvideo] 下载页未找到「下载网络视频」入口")
        else:
            entry.first.click()
            page.wait_for_timeout(900)
            modal = page.locator(".modal")
            print(f"   弹窗渲染：{modal.count() > 0}")
            if modal.count():
                modal_text = page.inner_text(".modal")
                # 合规文案必须出现在入口，避免用户以为能下 VIP 正片
                print(f"   含合规说明：{'公开' in modal_text}")
                if "公开" not in modal_text:
                    errors.append("[webvideo] 弹窗缺少「仅支持公开内容」的说明")
                url_input = page.locator(".modal input").first
                url_input.fill("https://www.iqiyi.com/v_19rr7f0m0k.html")
                parse_btn = page.get_by_role("button", name="解析")
                if parse_btn.count():
                    parse_btn.first.click()
                    page.wait_for_timeout(2500)
                    modal_text = page.inner_text(".modal") + page.inner_text("body")
                    blocked = "会员" in modal_text or "不提供" in modal_text
                    print(f"   付费墙地址被拒绝并给出原因：{blocked}")
                    if not blocked:
                        errors.append("[webvideo] 付费墙地址没有被拒绝或未提示原因")
                else:
                    errors.append("[webvideo] 弹窗内未找到「解析」按钮")
                page.screenshot(path=str(SHOTS / "46-webvideo.png"))
            close_modal(page)

        print("\n" + "=" * 68)
        print("6s) 交互测试：慢请求返回后不得覆盖已切走的页面")
        print("=" * 68)
        # 这是本轮点检暴露出的真 bug：站点健康巡检要真去各站点探测（十几秒），
        # 期间若切到别的页，旧请求返回后会把站点健康页糊上来——
        # 地址栏是 #settings、内容却是站点健康，之后所有点击都作用在幽灵页面上。
        # 必须用应用内导航（点侧边栏）。page.goto 会整页刷新、连带丢弃在途请求，
        # 复现不出这个 bug——实测：关掉守卫时点侧边栏 6.4 秒即被抢屏，goto 则一直正常。
        page.get_by_role("button", name="站点健康", exact=True).first.click()
        page.wait_for_timeout(1500)
        probe = page.get_by_role("button", name="立即巡检", exact=True)
        if not probe.count():
            print("   跳过：站点健康页没有「立即巡检」按钮")
        else:
            probe.first.click()
            page.wait_for_timeout(300)  # 不等它结束，立刻切页
            page.get_by_role("button", name="设置", exact=True).first.click()
            hijacked = False
            for _ in range(10):  # 盯 20 秒，等慢请求回来看它会不会抢屏
                page.wait_for_timeout(2000)
                body = page.inner_text("body")
                if "立即巡检" in body or "提前发现" in body:
                    hijacked = True
                    break
            body = page.inner_text("body")
            print(f"   切页后仍停在设置页：{not hijacked}")
            print(f"   设置页内容完好：{'保存并生效' in body}")
            if hijacked:
                errors.append("[race] 慢请求返回后覆盖了已切走的页面（幽灵页面）")
            if "保存并生效" not in body:
                errors.append("[race] 切页后设置页内容缺失")

        print("\n" + "=" * 68)
        print("6t) 交互测试：AI 分析站点弹窗（v1.13.0）")
        print("=" * 68)
        # 这个入口用了一个本轮新加的图标（sparkles）与两个新样式类
        # （.notice / .pre-wrap）。图标名写错不会报 JS 错、只会画成占位点，
        # 样式缺失也不报错、只是布局塌掉——都得靠点检真点开来看。
        page.goto(f"{BASE}/#sites", wait_until="networkidle")
        page.wait_for_timeout(1500)
        ai_entry = page.get_by_role("button", name="AI 分析站点", exact=True)
        if not ai_entry.count():
            errors.append("[ai] 站点管理页未找到「AI 分析站点」入口")
        else:
            # 图标必须真的画出路径，而不是回落成 ICONS.dot 占位
            icon_paths = ai_entry.first.locator("svg.icon path").count()
            print(f"   按钮图标路径数：{icon_paths}")
            if icon_paths < 2:
                errors.append("[ai] AI 分析按钮图标缺失（sparkles 未注册，回落成占位点）")
            ai_entry.first.click()
            page.wait_for_timeout(1200)
            modal = page.locator(".modal")
            print(f"   弹窗渲染：{modal.count() > 0}")
            if not modal.count():
                errors.append("[ai] AI 分析弹窗没有渲染出来")
            else:
                modal_text = page.inner_text(".modal")
                # AI 默认关闭，所以这里必须看到「去哪儿配」的可行动提示
                notice = page.locator(".modal .notice")
                print(f"   提示条渲染：{notice.count() > 0}")
                print(f"   未配置时说明去哪儿配：{'设置' in modal_text}")
                if not notice.count():
                    errors.append("[ai] 未配置 AI 时没有渲染提示条（.notice 样式或分支缺失）")
                if "设置" not in modal_text:
                    errors.append("[ai] 未配置提示没有告诉用户去哪儿配")
                # 必须检查 .notice **基类独有**的属性。
                # 用背景色判断会假绿：`.notice.warn` 自己也定义了 background，
                # 于是把基类整条规则改坏，背景色照样在（实测过这个假绿）。
                # display:flex 与 padding 只在基类里出现，缺了布局就真塌了。
                if notice.count():
                    style = notice.first.evaluate(
                        "el => { const s = getComputedStyle(el);"
                        " return { display: s.display, padding: s.paddingLeft,"
                        " radius: s.borderTopLeftRadius }; }"
                    )
                    ok = style["display"] == "flex" and style["padding"] != "0px"
                    print(f"   提示条样式生效：{ok}（{style}）")
                    if not ok:
                        errors.append(
                            f"[ai] .notice 基类样式未生效（CSS 缺失）：{style}"
                        )
                # 三个按钮的初始可用状态：只有「开始分析」可点
                for label, should_disable in (
                    ("开始分析", False), ("试跑验证", True), ("添加为站点", True)
                ):
                    button = page.get_by_role("button", name=label, exact=True)
                    if not button.count():
                        errors.append(f"[ai] 弹窗内缺少「{label}」按钮")
                        continue
                    disabled = button.first.is_disabled()
                    print(f"   {label} 初始禁用={disabled}（期望 {should_disable}）")
                    if disabled != should_disable:
                        errors.append(
                            f"[ai] 「{label}」初始禁用状态错误"
                            f"（实际 {disabled} 期望 {should_disable}）"
                        )
                page.screenshot(path=str(SHOTS / "47-ai-site.png"))
            close_modal(page)

        print("\n" + "=" * 68)
        print("7) 响应式检查（移动端 430x900）")
        print("=" * 68)
        mobile = context.new_page()
        mobile.set_viewport_size({"width": 430, "height": 900})
        mobile.goto(f"{BASE}/#dashboard", wait_until="networkidle")
        mobile.wait_for_timeout(1200)
        mobile.screenshot(path=str(SHOTS / "14-mobile.png"), full_page=True)
        print(f"   移动端渲染文本长度：{len(mobile.inner_text('body'))}")

        browser.close()


main()

print("\n" + "=" * 68)
print("结论")
print("=" * 68)
def _is_noise(item):
    """判断一条报错是否属于「与本项目无关」的噪声。

    - favicon：浏览器自动请求，缺了不影响功能
    - 第三方图片域：榜单封面来自资源站，本机没直连/走代理时必然失败，
      前端已用 onerror 退占位（画板点检里专门验过裂图数为 0）
    - 预期内的 400：付费墙拦截用例是我们主动触发的，返回 400 才是对的
    """
    lowered = item.lower()
    if "favicon" in lowered:
        return True
    # ERR_CONNECTION_CLOSED 与前两者同类：外站图床挂了/被墙。
    # 只要不是本机地址就算噪声——本机连不上才是真问题。
    for signal in (
        "err_socket_not_connected",
        "err_name_not_resolved",
        "err_connection_closed",
        "err_connection_reset",
        "err_connection_timed_out",
        "err_certificate",
    ):
        if signal in lowered:
            return BASE.replace("http://", "") not in lowered
    return "400 (bad request)" in lowered


real_errors = [item for item in errors if not _is_noise(item)]
noise_errors = [item for item in errors if _is_noise(item)]
real_failed = [item for item in failed_requests if not _is_noise(item)]
noise_failed = [item for item in failed_requests if _is_noise(item)]
print(f"JS 报错   : {len(real_errors)}")
for item in real_errors:
    print(f"   {item}")
print(f"失败请求  : {len(real_failed)}")
for item in real_failed:
    print(f"   {item}")
print(f"已忽略噪声: {len(noise_errors) + len(noise_failed)}（外站图片/favicon/预期内 400）")
for item in (noise_errors + noise_failed)[:6]:
    print(f"   {item[:140]}")
print(f"截图目录  : {SHOTS.resolve()}")
sys.exit(1 if (real_errors or real_failed) else 0)
