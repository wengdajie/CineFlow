"""用真实浏览器逐页点检 CineFlow 前端，捕获任何 JS 报错。"""
import os
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
    ("schedules", "定时任务"),
    ("downloads", "下载任务"),
    ("library", "媒体库"),
    ("sites", "站点管理"),
    ("plugins", "插件"),
    ("logs", "运行日志"),
]

errors = []
failed_requests = []


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
        # 11 个功能页 + 1 个退出按钮
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
        print("6e) 交互测试：热度排行榜单切换")
        print("=" * 68)
        page.goto(f"{BASE}/#trending", wait_until="networkidle")
        page.wait_for_timeout(1500)
        body = page.inner_text("body")
        print(f"   热度页含「热度排行」：{'热度排行' in body}")
        segs = page.locator(".segment button")
        print(f"   榜单切换按钮 {segs.count()} 个")
        for i in range(min(segs.count(), 4)):
            label = segs.nth(i).inner_text()
            segs.nth(i).click()
            page.wait_for_timeout(1800)
            text = page.inner_text("body")
            print(f"     切到「{label}」-> 文本长度 {len(text)}")
        page.screenshot(path=str(SHOTS / "21-trending.png"), full_page=True)
        heat_bars = page.locator(".heat-bar")
        rank_cells = page.locator(".rank")
        print(f"   热度条 {heat_bars.count()} 个 / 排名徽标 {rank_cells.count()} 个")

        print("\n" + "=" * 68)
        print("6f) 交互测试：定时任务设置改期弹窗")
        print("=" * 68)
        page.goto(f"{BASE}/#schedules", wait_until="networkidle")
        page.wait_for_timeout(1500)
        body = page.inner_text("body")
        print(f"   定时任务页含「定时任务」：{'定时任务' in body}")
        for name in ("订阅巡检", "追新雷达", "下载状态同步", "媒体库全量扫描"):
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
real_errors = [item for item in errors if "favicon" not in item.lower()]
real_failed = [item for item in failed_requests if "favicon" not in item.lower()]
print(f"JS 报错   : {len(real_errors)}")
for item in real_errors:
    print(f"   {item}")
print(f"失败请求  : {len(real_failed)}")
for item in real_failed:
    print(f"   {item}")
print(f"截图目录  : {SHOTS.resolve()}")
sys.exit(1 if (real_errors or real_failed) else 0)
