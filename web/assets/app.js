/* ============================================================
   CineFlow 控制台 · 零依赖单页应用
   ------------------------------------------------------------
   视觉方案参考的开源项目（仅借鉴 token 与交互范式，未引入代码）：
     · Radix Colors    12 级语义色阶 → 双主题变量
     · Open Props      尺度/阴影/圆角 token 化
     · shadcn/ui       surface/muted/accent/ring 命名与卡片布局
     · Feather Icons   线性图标风格（此处为等价手写路径）
   无任何 CDN 依赖，NAS 离线环境完全可用。
   ============================================================ */
(function () {
  "use strict";

  const API = "/api/v1";
  const THEME_KEY = "cf_theme";

  const store = {
    token: localStorage.getItem("cf_token") || "",
    username: localStorage.getItem("cf_user") || "",
    // 角色只用于**隐藏没权限的入口**（少点误操作），真正的鉴权在服务端
    role: localStorage.getItem("cf_role") || "admin",
    page: location.hash.replace("#", "") || "dashboard",
    theme: localStorage.getItem(THEME_KEY) || "auto",
  };

  const ROLE_RANK = { viewer: 1, operator: 2, admin: 3 };
  const ROLE_LABEL = { viewer: "访客", operator: "操作员", admin: "管理员" };
  const canDo = (minimum) => (ROLE_RANK[store.role] || 3) >= (ROLE_RANK[minimum] || 3);

  // ---------------- 主题（暗色 / 浅色 / 跟随系统） ----------------
  const media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: light)") : null;

  const resolveTheme = (mode) => {
    if (mode === "light" || mode === "dark") return mode;
    return media && media.matches ? "light" : "dark";
  };

  function applyTheme() {
    const resolved = resolveTheme(store.theme);
    document.documentElement.setAttribute("data-theme", resolved);
    document.documentElement.setAttribute("data-theme-mode", store.theme);
    return resolved;
  }

  function setTheme(mode) {
    store.theme = mode;
    localStorage.setItem(THEME_KEY, mode);
    applyTheme();
    document.querySelectorAll("[data-theme-btn]").forEach((node) => {
      node.classList.toggle("on", node.getAttribute("data-theme-btn") === mode);
    });
  }

  applyTheme();
  if (media && media.addEventListener) {
    media.addEventListener("change", () => {
      if (store.theme === "auto") applyTheme();
    });
  }

  // ---------------- 图标（内联 SVG，线性风格） ----------------
  const ICONS = {
    dashboard: '<rect x="3" y="3" width="7.5" height="7.5" rx="2"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="2"/><rect x="3" y="13.5" width="7.5" height="7.5" rx="2"/><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="2"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="M16.5 16.5 21 21"/>',
    star: '<path d="M12 3.5l2.6 5.4 5.9.8-4.3 4.2 1 5.9-5.2-2.8-5.2 2.8 1-5.9L3.5 9.7l5.9-.8z"/>',
    download: '<path d="M12 3v12"/><path d="M7.5 10.5 12 15l4.5-4.5"/><path d="M4 20h16"/>',
    library: '<path d="M4 5h16v14H4z"/><path d="M4 10h16"/><path d="M9 10v9"/>',
    radar: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r="1"/><path d="M12 12 19 6"/>',
    compass: '<circle cx="12" cy="12" r="9"/><path d="M15.5 8.5l-2 5-5 2 2-5z"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.2 5.2l2.1 2.1M16.7 16.7l2.1 2.1M18.8 5.2l-2.1 2.1M7.3 16.7 5.2 18.8"/>',
    plugin: '<path d="M9 3v4"/><path d="M15 3v4"/><path d="M5 7h14v6a6 6 0 0 1-6 6h-2a6 6 0 0 1-6-6z"/>',
    logs: '<path d="M5 4h9l5 5v11H5z"/><path d="M14 4v5h5"/><path d="M8 13h8M8 17h6"/>',
    flame: '<path d="M12 3s5 4.2 5 8.6A5 5 0 0 1 7 12c0-1.6.7-2.9 1.6-4 .3 1.3 1.1 2.1 2 2.3-.5-2.6.6-5.6 1.4-7.3z"/>',
    clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3.2 2"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M18.7 5.3l-1.6 1.6M6.9 17.1 5.3 18.7"/>',
    moon: '<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z"/>',
    auto: '<circle cx="12" cy="12" r="8.5"/><path d="M12 3.5v17a8.5 8.5 0 0 0 0-17z" fill="currentColor" stroke="none"/>',
    logout: '<path d="M14 5H6v14h8"/><path d="M13 12h8"/><path d="m18 8.5 3.5 3.5L18 15.5"/>',
    refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4.5V11h-6"/>',
    play: '<path d="M8 5.5 18.5 12 8 18.5z"/>',
    pause: '<path d="M9.5 5v14M14.5 5v14"/>',
    trash: '<path d="M4.5 7h15"/><path d="M9.5 7V4.5h5V7"/><path d="M6.5 7 7.5 20h9L17.5 7"/>',
    edit: '<path d="M5 19h3.5L19 8.5 15.5 5 5 15.5z"/><path d="M14 6.5 17.5 10"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    check: '<path d="M4.5 12.5 9.5 17.5 19.5 6.5"/>',
    close: '<path d="M6 6l12 12M18 6 6 18"/>',
    alert: '<path d="M12 4 2.8 20h18.4z"/><path d="M12 10v4.5M12 17.2v.4"/>',
    info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5M12 7.7v.4"/>',
    box: '<path d="M3.5 8 12 3.5 20.5 8v8L12 20.5 3.5 16z"/><path d="M3.5 8 12 12.5 20.5 8M12 12.5v8"/>',
    cloud: '<path d="M7 18a4 4 0 0 1-.4-8A5.5 5.5 0 0 1 17 10.5a3.8 3.8 0 0 1 .3 7.5z"/>',
    shield: '<path d="M12 3.5 19 6v6c0 4.2-2.9 7.6-7 8.5-4.1-.9-7-4.3-7-8.5V6z"/><path d="M9 12l2 2 4-4"/>',
    link: '<path d="M9.5 14.5 14.5 9.5"/><path d="M11 6.5 13 4.5a4 4 0 0 1 5.7 5.7l-2 2"/><path d="M13 17.5 11 19.5a4 4 0 0 1-5.7-5.7l2-2"/>',
    film: '<rect x="3" y="4.5" width="18" height="15" rx="2"/><path d="M7.5 4.5v15M16.5 4.5v15M3 12h18"/>',
    video: '<rect x="2.5" y="6" width="13" height="12" rx="2"/><path d="M15.5 10.5l6-3v9l-6-3z"/>',
    tv: '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M8.5 21h7M12 6V3"/>',
    server: '<rect x="3.5" y="4" width="17" height="6" rx="2"/><rect x="3.5" y="14" width="17" height="6" rx="2"/><path d="M7 7h.4M7 17h.4"/>',
    chart: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    inbox: '<path d="M3.5 12.5 6 5h12l2.5 7.5v6.5h-17z"/><path d="M3.5 12.5H9l1 2.5h4l1-2.5h5.5"/>',
    robot: '<rect x="4" y="8" width="16" height="11" rx="3"/><path d="M12 4v4"/><circle cx="9" cy="13" r="1.2"/><circle cx="15" cy="13" r="1.2"/><path d="M9.5 16.5h5"/>',
    folder: '<path d="M3.5 6.5h5l2 2.5h9.5v9.5h-16.5z"/>',
    dot: '<circle cx="12" cy="12" r="4"/>',
    users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c0-3.6 2.9-6 6.5-6s6.5 2.4 6.5 6"/><path d="M16.5 5.2a3.5 3.5 0 0 1 0 6.6"/><path d="M18 14.4c2.1.7 3.5 2.4 3.5 4.6"/>',
    pulse: '<path d="M2.5 12.5h4L9 7l3.5 10L15 12h6.5"/>',
    trophy: '<path d="M7 4h10v4a5 5 0 0 1-10 0z"/><path d="M7 5.5H4.5A3.5 3.5 0 0 0 8 9"/><path d="M17 5.5h2.5A3.5 3.5 0 0 1 16 9"/><path d="M12 13v4"/><path d="M8.5 20h7"/>',
    layers: '<path d="M12 3.5 3.5 8 12 12.5 20.5 8z"/><path d="m3.5 12.5 8.5 4.5 8.5-4.5"/><path d="m3.5 16.5 8.5 4.5 8.5-4.5"/>',
    qr: '<rect x="3.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.5"/><path d="M13.5 13.5h3v3h-3zM17.5 17.5h3v3h-3z"/>',
    key: '<circle cx="8" cy="12" r="4"/><path d="M12 12h9"/><path d="M17 12v3.5M20 12v2.5"/>',
    history: '<path d="M12 7.5V12l3.5 2"/><path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1"/><path d="M3.5 4v4.5H8"/>',
    back: '<path d="M20 12H4.5"/><path d="M10.5 6 4.5 12l6 6"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M4.5 20.5c0-4 3.4-6.5 7.5-6.5s7.5 2.5 7.5 6.5"/>',
    rss: '<path d="M5 19.5h.01"/><path d="M4.5 13.5a6.5 6.5 0 0 1 6.5 6.5"/><path d="M4.5 8a12 12 0 0 1 12 12"/>',
    eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
    sparkles: '<path d="M11 3.5 12.6 8 17 9.6 12.6 11.2 11 15.6 9.4 11.2 5 9.6 9.4 8z"/><path d="M17.5 15 18.3 17.2 20.5 18l-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z"/>',
  };

  function icon(name, cls) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("class", "icon" + (cls ? " " + cls : ""));
    svg.setAttribute("aria-hidden", "true");
    svg.innerHTML = ICONS[name] || ICONS.dot;
    return svg;
  }

  // ---------------- 基础工具 ----------------
  const el = (tag, attrs, children) => {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([key, value]) => {
      if (value === null || value === undefined || value === false) return;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value);
    });
    const list = Array.isArray(children) ? children : children ? [children] : [];
    list
      .filter((child) => child !== null && child !== undefined && child !== false)
      .forEach((child) => {
        node.appendChild(
          typeof child === "object" ? child : document.createTextNode(String(child))
        );
      });
    return node;
  };

  const fmtSize = (bytes) => {
    let size = Number(bytes) || 0;
    if (!size) return "-";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let index = 0;
    while (size >= 1024 && index < units.length - 1) {
      size /= 1024;
      index += 1;
    }
    return size.toFixed(index ? 1 : 0) + " " + units[index];
  };

  const fmtSpeed = (value) => (Number(value) ? fmtSize(value) + "/s" : "-");

  /** 秒 → 时长文本。视频动辄几十分钟到几小时，纯秒数不可读。 */
  const fmtDuration = (seconds) => {
    const total = Math.round(Number(seconds) || 0);
    if (!total) return "-";
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    return h
      ? h + ":" + String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0")
      : m + ":" + String(s).padStart(2, "0");
  };
  /** 大数字压成中文量级：847421 → 84.7万。B 站播放量动辄上亿，
      直接铺出来会把卡片撑破，也没人读得出位数。 */
  const fmtCompact = (value) => {
    const n = Number(value) || 0;
    if (n >= 100000000) return (n / 100000000).toFixed(1).replace(/\.0$/, "") + "亿";
    if (n >= 10000) return (n / 10000).toFixed(1).replace(/\.0$/, "") + "万";
    return String(n);
  };
  /** 取路径最后一段（Windows/Unix 分隔符都吃）。 */
  const baseName = (value) => {
    const text = String(value || "");
    const index = Math.max(text.lastIndexOf("/"), text.lastIndexOf("\\"));
    return index >= 0 ? text.slice(index + 1) : text;
  };
  const fmtTime = (value) =>
    value ? String(value).replace("T", " ").slice(0, 19) : "-";
  const pad2 = (value) => String(value).padStart(2, "0");

  const fmtRelative = (value) => {
    if (!value) return "-";
    const then = new Date(String(value).replace(" ", "T"));
    const diff = (then.getTime() - Date.now()) / 1000;
    const abs = Math.abs(diff);
    const unit =
      abs < 60 ? [abs, "秒"] : abs < 3600 ? [abs / 60, "分钟"] : abs < 86400 ? [abs / 3600, "小时"] : [abs / 86400, "天"];
    const text = Math.round(unit[0]) + " " + unit[1];
    return diff >= 0 ? text + "后" : text + "前";
  };

  const toast = (message, kind) => {
    const name = kind === "ok" ? "check" : kind === "err" || kind === "warn" ? "alert" : "info";
    const node = el("div", { class: "toast " + (kind || "") }, [
      icon(name, "sm"),
      el("div", { text: message }),
    ]);
    document.getElementById("toasts").appendChild(node);
    setTimeout(() => node.remove(), 3800);
  };

  async function api(path, options) {
    const config = Object.assign({ headers: {} }, options || {});
    config.headers = Object.assign(
      { "Content-Type": "application/json" },
      config.headers,
      store.token ? { Authorization: "Bearer " + store.token } : {}
    );
    const isForm = config.body instanceof URLSearchParams;
    if (config.body && typeof config.body !== "string" && !isForm) {
      config.body = JSON.stringify(config.body);
    }
    if (isForm) delete config.headers["Content-Type"];

    const response = await fetch(API + path, config);
    if (response.status === 401) {
      logout(true);
      throw new Error("登录已过期，请重新登录");
    }
    const text = await response.text();
    let payload = null;
    try {
      payload = text ? JSON.parse(text) : null;
    } catch (err) {
      payload = { message: text };
    }
    if (!response.ok) {
      const detail = payload && (payload.detail || payload.message);
      throw new Error(
        typeof detail === "string" ? detail : "请求失败(" + response.status + ")"
      );
    }
    return payload;
  }

  /**
   * NDJSON 流式请求：每读到一行就回调一次。
   *
   * 为什么不用 EventSource：它不能自定义请求头（带不了 Bearer token），
   * 也只支持 GET，没法把完整搜索条件放进 body。用 fetch + ReadableStream
   * 反而更简单，还能拿到 AbortController 用于「换关键词就取消上一次」。
   *
   * 返回 ``{ done, abort }``：``done`` 是整条流读完的 Promise，
   * ``abort`` 供调用方主动取消（用户重新搜索时必须取消上一次，
   * 否则两次搜索的结果会交叉写进同一个列表）。
   */
  function apiStream(path, options, onEvent) {
    const config = Object.assign({ method: "POST", headers: {} }, options || {});
    config.headers = Object.assign(
      { "Content-Type": "application/json" },
      config.headers,
      store.token ? { Authorization: "Bearer " + store.token } : {}
    );
    if (config.body && typeof config.body !== "string") {
      config.body = JSON.stringify(config.body);
    }
    const controller = new AbortController();
    config.signal = controller.signal;

    const done = (async function () {
      const response = await fetch(API + path, config);
      if (response.status === 401) {
        logout(true);
        throw new Error("登录已过期，请重新登录");
      }
      if (!response.ok) {
        const text = await response.text();
        let detail = "";
        try {
          const payload = text ? JSON.parse(text) : null;
          detail = payload && (payload.detail || payload.message);
        } catch (err) {
          detail = text;
        }
        throw new Error(
          typeof detail === "string" && detail ? detail : "请求失败(" + response.status + ")"
        );
      }
      // 老浏览器/代理不支持 body 流时退回「一次性读完再逐行回放」：
      // 体验回到非流式，但功能不会坏（渐进增强）
      if (!response.body || !response.body.getReader) {
        const text = await response.text();
        text.split("\n").forEach(function (line) {
          if (line.trim()) onEvent(JSON.parse(line));
        });
        return;
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const chunk = await reader.read();
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true });
        // 按行切分：最后一段可能是**半行**，留在 buffer 里等下一个 chunk
        const lines = buffer.split("\n");
        buffer = lines.pop();
        for (let i = 0; i < lines.length; i += 1) {
          const line = lines[i].trim();
          if (!line) continue;
          try {
            onEvent(JSON.parse(line));
          } catch (err) {
            // 单行坏了不该让整条流中断
            console.warn("流式响应解析失败", line);
          }
        }
      }
      if (buffer.trim()) {
        try {
          onEvent(JSON.parse(buffer));
        } catch (err) {
          console.warn("流式响应尾行解析失败", buffer);
        }
      }
    })();

    return { done: done, abort: function () { controller.abort(); } };
  }

  const STATUS_MAP = {
    active: ["追新中", "ok"],
    completed: ["已完成", "brand"],
    paused: ["已暂停", "warn"],
    failed: ["失败", "err"],
    downloading: ["下载中", "ok"],
    pending: ["等待中", "warn"],
    transferred: ["已入库", "brand"],
    canceled: ["已取消", ""],
  };

  const statusTag = (status) => {
    const pair = STATUS_MAP[status] || [status, ""];
    return el("span", { class: "tag dot " + pair[1], text: pair[0] });
  };

  const typeLabel = (value) =>
    ({ movie: "电影", tv: "剧集", anime: "动漫", unknown: "未知" }[value] ||
      value ||
      "-");

  const typeIcon = (value) =>
    ({ movie: "film", tv: "tv", anime: "star" }[value] || "box");

  const kindLabel = (value) =>
    ({ torrent: "种子", magnet: "磁力", pan: "网盘", direct: "直链" }[value] ||
      value);

  //: 每站诊断状态 → [展示名, 标签色]。后端从 v1.6.0 就返回 sites 诊断，
  //: 但界面一直没渲染 —— 于是「开了 8 个站却只看到 1 个站的资源」时，
  //: 用户看不到到底是超时、被熔断跳过还是这个站真的没有，只能怀疑功能坏了。
  const SITE_STATUS = {
    ok: ["有结果", "ok"],
    empty: ["无匹配", ""],
    timeout: ["超时", "warn"],
    skipped: ["已跳过", "warn"],
    error: ["失败", "err"],
  };

  /** 搜索结果里的「站点情况」表：谁出了货、谁超时、谁被熔断跳过。 */
  function siteReportCard(sites, onReset) {
    const rows = (sites || []).slice();
    if (!rows.length) return null;
    const ORDER = { error: 0, timeout: 1, skipped: 2, empty: 3, ok: 4 };
    rows.sort(function (a, b) {
      const d = (ORDER[a.status] === undefined ? 9 : ORDER[a.status]) -
                (ORDER[b.status] === undefined ? 9 : ORDER[b.status]);
      return d !== 0 ? d : (b.kept || 0) - (a.kept || 0);
    });
    const hit = rows.filter(function (r) { return r.status === "ok"; }).length;
    const skipped = rows.filter(function (r) { return r.status === "skipped"; }).length;
    const slow = rows.filter(function (r) { return r.status === "timeout"; }).length;
    return el("div", { class: "card flush" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("pulse", "sm"), el("span", { text: "站点情况（" + hit + "/" + rows.length + " 出货）" })]),
        el("div", { class: "row tight center" }, [
          slow ? el("span", { class: "tag warn", text: slow + " 个超时" }) : null,
          skipped
            ? iconButton("解除跳过", "refresh", async function () {
                try {
                  await api("/search/breaker/reset", { method: "POST" });
                  toast("已解除熔断，下次搜索会重新尝试这些站点", "ok");
                  if (onReset) onReset();
                } catch (error) {
                  toast(error.message, "err");
                }
              }, "sm ghost")
            : null,
        ]),
      ]),
      table(
        [
          { title: "站点", render: function (row) { return el("span", { class: "tag", text: row.site || "-" }); } },
          {
            title: "状态",
            render: function (row) {
              const pair = SITE_STATUS[row.status] || [row.status || "-", ""];
              return el("span", { class: "tag dot " + pair[1], text: pair[0] });
            },
          },
          {
            title: "说明",
            render: function (row) {
              return el("div", { class: "truncate dim tiny", title: row.message || "", text: row.message || "-" });
            },
          },
          { title: "命中", class: "num", render: function (row) { return String(row.kept || 0); } },
          { title: "原始", class: "num", render: function (row) { return String(row.raw || 0); } },
          { title: "耗时", class: "num", render: function (row) { return (row.elapsed_ms || 0) + "ms"; } },
        ],
        rows
      ),
      el("div", { class: "dim tiny", style: "padding:0 22px 18px" }, [
        el("span", { text: "「已跳过」= 该站连续多次吃满超时预算且零结果，已被暂时熔断；" +
          "聚合搜索要等最慢的站，跳过它能让整体立刻变快。到期自动恢复，也可点上方按钮立即解除。" }),
      ]),
    ]);
  }

  const seasonEpisode = (season, episode) =>
    season !== null && season !== undefined && episode !== null && episode !== undefined
      ? "S" + pad2(season) + "E" + pad2(episode)
      : "-";

  const loading = () =>
    el("div", { class: "card" }, [
      el("div", { class: "skeleton" }, [el("i"), el("i"), el("i"), el("i")]),
    ]);

  const emptyBox = (text, iconName) =>
    el("div", { class: "empty" }, [icon(iconName || "inbox"), el("div", { text: text })]);

  function table(columns, rows, emptyText) {
    if (!rows || !rows.length) {
      return emptyBox(emptyText || "暂无数据");
    }
    const head = el("thead", {}, [
      el("tr", {}, columns.map((col) => el("th", { text: col.title }))),
    ]);
    const body = el(
      "tbody",
      {},
      rows.map((row, index) =>
        el(
          "tr",
          {},
          columns.map((col) => {
            const value = col.render ? col.render(row, index) : row[col.key];
            const isNode = value && typeof value === "object";
            return el(
              "td",
              col.class ? { class: col.class } : {},
              isNode ? value : String(value === undefined || value === null ? "-" : value)
            );
          })
        )
      )
    );
    return el("div", { class: "table-wrap" }, [el("table", {}, [head, body])]);
  }

  function segment(items, current, onPick) {
    const box = el("div", { class: "segment" });
    items.forEach((item) => {
      const button = el("button", {
        class: item.value === current ? "on" : "",
        text: item.label,
        onclick: () => onPick(item.value),
      });
      box.appendChild(button);
    });
    return box;
  }

  function themeToggle() {
    const modes = [
      { value: "auto", icon: "auto", title: "跟随系统" },
      { value: "light", icon: "sun", title: "浅色" },
      { value: "dark", icon: "moon", title: "暗色" },
    ];
    return el(
      "div",
      { class: "theme-toggle", role: "group", "aria-label": "主题切换" },
      modes.map((mode) => {
        const button = el("button", {
          class: store.theme === mode.value ? "on" : "",
          title: mode.title,
          "aria-label": mode.title,
          "data-theme-btn": mode.value,
          onclick: () => setTheme(mode.value),
        });
        button.appendChild(icon(mode.icon, "sm"));
        return button;
      })
    );
  }

  // ---------------- 通用弹窗表单 ----------------
  function modal(title, fields, onSubmit, submitText, options) {
    const root = document.getElementById("modal-root");
    const getters = {};

    const rows = fields.map((field) => {
      if (field.type === "checkbox") {
        const input = el("input", { type: "checkbox" });
        input.checked = !!field.value;
        getters[field.key] = () => input.checked;
        return el("label", { class: "field-check" }, [
          input,
          el("span", {}, [
            el("div", { text: field.label }),
            field.hint ? el("div", { class: "dim tiny", text: field.hint }) : null,
          ]),
        ]);
      }

      let input;
      if (field.type === "select") {
        input = el(
          "select",
          { class: "input" },
          (field.options || []).map((opt) =>
            el(
              "option",
              { value: opt.value, selected: opt.value === field.value },
              opt.label
            )
          )
        );
      } else if (field.type === "textarea") {
        input = el("textarea", {
          class: "input",
          rows: field.rows || 3,
          placeholder: field.placeholder || "",
        });
        input.value = field.value || "";
      } else {
        input = el("input", {
          class: "input",
          type: field.type || "text",
          placeholder: field.placeholder || "",
        });
        input.value = field.value !== undefined ? field.value : "";
      }

      getters[field.key] = () =>
        field.type === "number"
          ? input.value === ""
            ? null
            : Number(input.value)
          : input.value.trim();

      return el("div", { class: "field" }, [
        el("label", { text: field.label }),
        input,
        field.hint ? el("div", { class: "dim tiny", style: "margin-top:4px", text: field.hint }) : null,
      ]);
    });

    const close = () => {
      root.innerHTML = "";
    };

    const submit = el("button", { class: "btn primary" }, [
      icon("check", "sm"),
      el("span", { text: submitText || "确定" }),
    ]);
    submit.addEventListener("click", async () => {
      const values = {};
      Object.keys(getters).forEach((key) => {
        values[key] = getters[key]();
      });
      submit.disabled = true;
      try {
        await onSubmit(values);
        close();
      } catch (error) {
        toast(error.message, "err");
        submit.disabled = false;
      }
    });

    const mask = el("div", { class: "modal-mask" }, [
      el("div", { class: "modal" + ((options && options.wide) ? " wide" : "") }, [
        el("h3", { text: title }),
        (options && options.lead) ? el("div", { class: "muted", style: "margin:-8px 0 16px", text: options.lead }) : null,
        ...rows,
        el("div", { class: "modal-actions" }, [
          el("button", { class: "btn ghost", text: "取消", onclick: close }),
          submit,
        ]),
      ]),
    ]);
    mask.addEventListener("click", (event) => {
      if (event.target === mask) close();
    });
    root.appendChild(mask);
  }

  /** 自定义内容弹窗（非表单场景）。 */
  function panelModal(title, lead, content, wide) {
    const root = document.getElementById("modal-root");
    const close = () => {
      root.innerHTML = "";
    };
    const mask = el("div", { class: "modal-mask" }, [
      el("div", { class: "modal" + (wide ? " wide" : "") }, [
        el("h3", { text: title }),
        lead ? el("div", { class: "muted", style: "margin:-8px 0 16px", text: lead }) : null,
        content,
        el("div", { class: "modal-actions" }, [
          el("button", { class: "btn ghost", text: "关闭", onclick: close }),
        ]),
      ]),
    ]);
    mask.addEventListener("click", (event) => {
      if (event.target === mask) close();
    });
    root.appendChild(mask);
    return close;
  }

  // ---------------- 登录 ----------------
  function logout(silent) {
    store.token = "";
    store.username = "";
    localStorage.removeItem("cf_token");
    localStorage.removeItem("cf_user");
    localStorage.removeItem("cf_role");
    render();
    if (!silent) toast("已退出登录");
  }

  function renderLogin() {
    const user = el("input", { class: "input", placeholder: "用户名" });
    user.value = "admin";
    const pass = el("input", {
      class: "input",
      type: "password",
      placeholder: "密码",
    });
    const button = el("button", { class: "btn primary block", text: "登录" });

    const submit = async () => {
      button.disabled = true;
      button.textContent = "登录中…";
      try {
        const form = new URLSearchParams();
        form.set("username", user.value.trim());
        form.set("password", pass.value);
        const data = await api("/auth/login", { method: "POST", body: form });
        store.token = data.access_token;
        store.username = data.username;
        store.role = data.role || "admin";
        localStorage.setItem("cf_token", store.token);
        localStorage.setItem("cf_user", store.username);
        localStorage.setItem("cf_role", store.role);
        toast("欢迎回来，" + data.username, "ok");
        render();
      } catch (error) {
        toast(error.message, "err");
        button.disabled = false;
        button.textContent = "登录";
      }
    };

    button.addEventListener("click", submit);
    pass.addEventListener("keydown", (event) => {
      if (event.key === "Enter") submit();
    });

    document.getElementById("app").replaceChildren(
      el("div", { class: "login-wrap" }, [
        el("div", { class: "login-theme" }, [themeToggle()]),
        el("div", { class: "login-card" }, [
          el("div", { class: "login-head" }, [
            el("div", { class: "brand-mark" }, [icon("film", "lg")]),
            el("div", {}, [
              el("h1", { text: "CineFlow" }),
              el("div", { class: "dim tiny", text: "NAS 自动化观影追剧平台" }),
            ]),
          ]),
          el("p", { class: "lead", text: "聚合 BT 站点与网盘搜索，定时追新自动入库" }),
          el("div", { class: "field" }, [el("label", { text: "用户名" }), user]),
          el("div", { class: "field" }, [el("label", { text: "密码" }), pass]),
          button,
          el("p", {
            class: "dim tiny",
            style: "margin-top:16px",
            text: "默认账号 admin / cineflow，登录后请及时修改密码",
          }),
        ]),
      ])
    );
  }

  // ---------------- 布局 ----------------
  const PAGES = [
    { key: "dashboard", label: "仪表盘", icon: "dashboard", group: "总览" },
    { key: "search", label: "资源搜索", icon: "search", group: "发现" },
    { key: "trending", label: "热度排行", icon: "flame", group: "发现" },
    { key: "subscribes", label: "订阅追新", icon: "star", group: "追剧" },
    { key: "radar", label: "追新雷达", icon: "radar", group: "追剧" },
    { key: "rssfeeds", label: "RSS 追新", icon: "rss", group: "追剧" },
    { key: "ranking", label: "榜单订阅", icon: "trophy", group: "追剧" },
    { key: "rules", label: "过滤规则组", icon: "layers", group: "追剧" },
    { key: "schedules", label: "定时任务", icon: "clock", group: "追剧" },
    { key: "downloads", label: "下载任务", icon: "download", group: "入库" },
    { key: "library", label: "媒体库", icon: "library", group: "入库" },
    { key: "storage", label: "网盘管理", icon: "cloud", group: "入库" },
    { key: "pansub", label: "分享追更", icon: "link", group: "入库" },
    { key: "videosub", label: "视频追更", icon: "video", group: "入库" },
    { key: "strm", label: "STRM 同步", icon: "film", group: "入库" },
    { key: "sites", label: "站点管理", icon: "server", group: "系统" },
    { key: "sitehealth", label: "站点健康", icon: "pulse", group: "系统" },
    { key: "chatops", label: "机器人", icon: "robot", group: "系统" },
    { key: "plugins", label: "插件", icon: "plugin", group: "系统" },
    { key: "users", label: "用户权限", icon: "users", group: "系统", role: "admin" },
    { key: "logs", label: "运行日志", icon: "logs", group: "系统" },
    { key: "changelog", label: "更新日志", icon: "history", group: "系统" },
    { key: "settings", label: "设置", icon: "settings", group: "系统" },
  ];

  /** 当前角色可见的页面（admin 专属页对访客/操作员直接不出现在导航里）。 */
  const visiblePages = () => PAGES.filter((page) => !page.role || canDo(page.role));

  //: 页面标题 → 页面 key。shell() 用它判断"这一屏是谁画的"。
  const PAGE_BY_TITLE = {};
  PAGES.forEach((page) => {
    PAGE_BY_TITLE[page.label] = page.key;
  });

  function shell(content, title, subtitle, actions) {
    // 丢弃过期渲染。页面函数都是「先画 loading，await 拉数据，再画真内容」，
    // 慢请求（如站点健康巡检要真去各站点探测，可达十几秒）返回时用户可能已经切走。
    // 不拦住就会出现"地址栏 #settings、内容却是站点健康页"的幽灵页面，
    // 之后所有点击都作用在错误的页面上。
    // 判据用标题而非计数器：异步回调无法知道自己属于哪一次导航，但它清楚自己在画哪个页面。
    const owner = PAGE_BY_TITLE[title];
    if (owner && owner !== store.page) return;
    const nav = [];
    let lastGroup = null;
    visiblePages().forEach((page) => {
      if (page.group !== lastGroup) {
        lastGroup = page.group;
        nav.push(el("div", { class: "nav-label", text: page.group }));
      }
      nav.push(
        el(
          "button",
          {
            class: "nav-item " + (store.page === page.key ? "active" : ""),
            onclick: () => go(page.key),
          },
          [icon(page.icon), el("span", { text: page.label })]
        )
      );
    });

    const app = document.getElementById("app");
    app.replaceChildren(
      el("div", { class: "layout" }, [
        el("aside", { class: "sidebar" }, [
          el("div", { class: "brand" }, [
            el("div", { class: "brand-mark" }, [icon("film")]),
            el("div", { class: "brand-text" }, [
              el("span", { class: "brand-name", text: "CineFlow" }),
              el("span", { class: "brand-sub", text: "自动追剧中枢" }),
            ]),
          ]),
          ...nav,
          el("div", { class: "nav-spacer" }),
          el("div", { class: "nav-foot" }, [
            el("div", { class: "nav-role" }, [
              icon("users", "sm"),
              el("span", { text: store.username }),
              el("span", { class: "tag tiny", text: ROLE_LABEL[store.role] || store.role }),
            ]),
            el("button", { class: "nav-item", onclick: () => logout() }, [
              icon("logout"),
              el("span", { text: "退出登录" }),
            ]),
          ]),
        ]),
        el("main", { class: "main" }, [
          el("div", { class: "topbar" }, [
            el("div", {}, [
              el("h2", { text: title }),
              subtitle ? el("div", { class: "sub", text: subtitle }) : null,
            ]),
            el("div", { class: "topbar-actions" }, [...(actions || []), themeToggle()]),
          ]),
          content,
        ]),
      ])
    );
    // 整屏换完了：上一屏的封面已经脱离文档，把它们仍在占用的连接还回来。
    // 这里是所有页面渲染的唯一出口（含 pageTrending 切分类时的自调用），
    // 挂在 render() 上会漏掉这些不经过路由的重绘。
    abortDetachedPosters();
  }

  /** 带图标的按钮。 */
  function iconButton(label, iconName, onclick, cls) {
    const button = el("button", { class: "btn " + (cls || "") }, [
      icon(iconName, "sm"),
      el("span", { text: label }),
    ]);
    button.addEventListener("click", onclick);
    return button;
  }

  // ---------------- 仪表盘 ----------------
  function statCard(label, value, hint, iconName) {
    return el("div", { class: "card stat" }, [
      iconName ? el("div", { class: "stat-icon" }, [icon(iconName, "sm")]) : null,
      el("div", { class: "label", text: label }),
      el("div", { class: "value", text: value }),
      hint ? el("div", { class: "hint", text: hint }) : null,
    ]);
  }

  /** 热度条单元格。 */
  function heatCell(percent, value) {
    const width = Math.max(2, Math.min(100, Number(percent) || 0));
    return el("div", { class: "heat" }, [
      el("div", { class: "heat-bar" + (width >= 70 ? " hot" : "") }, [
        el("i", { style: "width:" + width + "%" }),
      ]),
      el("span", { class: "heat-value", text: String(value === undefined ? Math.round(width) : value) }),
    ]);
  }

  function rankCell(rank) {
    const cls = rank === 1 ? " top1" : rank === 2 ? " top2" : rank === 3 ? " top3" : "";
    return el("span", { class: "rank" + cls, text: String(rank) });
  }

  async function pageDashboard() {
    shell(loading(), "仪表盘", "系统概览与最近入库");
    const [data, info, jobs, trending] = await Promise.all([
      api("/system/dashboard"),
      api("/system/info"),
      api("/schedules"),
      api("/trending?limit=5&days=14").catch(() => null),
    ]);

    const stats = el("div", { class: "grid cols-4" }, [
      statCard("追新中订阅", data.subscribes.active, "已完成 " + data.subscribes.completed + " 个", "star"),
      statCard("进行中下载", data.downloads.running, "累计完成 " + data.downloads.finished + " 个", "download"),
      statCard("媒体库文件", data.library.files, fmtSize(data.library.size), "library"),
      statCard(
        "剧集 / 电影",
        data.library.series + " / " + data.library.movies,
        "共 " + data.library.episodes + " 集",
        "film"
      ),
    ]);

    const badge = (ok, okText, badText) =>
      el("span", { class: "tag dot " + (ok ? "ok" : "warn"), text: ok ? okText : badText });

    // 状态项横排成一条，而不是竖着堆成一张高卡片（见 .status-strip 的注释）
    const statusItem = (label, node) =>
      el("div", { class: "status-item" }, [
        el("span", { class: "status-label", text: label }),
        node,
      ]);

    const health = el("div", { class: "card compact" }, [
      el("div", { class: "status-strip" }, [
        el("div", { class: "status-title" }, [
          icon("server", "sm"),
          el("span", { text: "运行状态" }),
        ]),
        statusItem("调度器", badge(info.scheduler_running, "运行中", "已停止")),
        statusItem("TMDB 刮削", badge(info.tmdb_enabled, "已启用", "未配置")),
        statusItem("整理模式", el("span", { class: "tag brand", text: info.transfer_mode })),
        statusItem("版本", el("span", { class: "tag", text: "v" + info.version })),
        el("div", { class: "status-paths dim tiny mono" }, [
          el("div", { class: "truncate", title: info.directories.library, text: "媒体库：" + info.directories.library }),
          el("div", { class: "truncate", title: info.directories.downloads, text: "下载目录：" + info.directories.downloads }),
        ]),
      ]),
    ]);

    const jobCard = el("div", { class: "card" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("clock", "sm"), el("span", { text: "定时任务" })]),
        iconButton("任务设置", "settings", () => go("schedules"), "sm ghost"),
      ]),
      table(
        [
          {
            title: "任务",
            render: (row) =>
              el("div", {}, [
                el("div", { text: row.name }),
                el("div", { class: "cell-sub", text: row.trigger === "cron" ? "cron " + row.cron : "每 " + row.minutes + " 分钟" }),
              ]),
          },
          {
            title: "状态",
            render: (row) =>
              row.enabled
                ? el("span", { class: "tag dot ok", text: "已启用" })
                : el("span", { class: "tag dot", text: "已关闭" }),
          },
          {
            title: "下次执行",
            render: (row) =>
              el("div", {}, [
                el("div", { class: "tiny", text: fmtTime(row.next_run_time) }),
                el("div", { class: "cell-sub", text: fmtRelative(row.next_run_time) }),
              ]),
          },
        ],
        jobs.items,
        "调度器未启动"
      ),
    ]);

    const recent = el("div", { class: "card" }, [
      el("h3", {}, [icon("inbox", "sm"), el("span", { text: "最近入库" })]),
      table(
        [
          {
            title: "标题",
            render: (row) =>
              el("div", { class: "row tight center" }, [
                icon(typeIcon(row.media_type), "sm"),
                el("span", { text: row.title }),
              ]),
          },
          { title: "季集", render: (row) => seasonEpisode(row.season, row.episode) },
          { title: "画质", render: (row) => row.resolution || "-" },
          { title: "时间", render: (row) => el("span", { class: "tiny dim", text: fmtTime(row.created_at) }) },
        ],
        data.recent,
        "还没有入库记录，先添加订阅或搜索下载吧"
      ),
    ]);

    const hotItems = (trending && trending.data.resources.items) || [];
    const hotCard = el("div", { class: "card" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("flame", "sm"), el("span", { text: "热度排行 TOP5" })]),
        iconButton("完整榜单", "chart", () => go("trending"), "sm ghost"),
      ]),
      hotItems.length
        ? table(
            [
              { title: "#", render: (row) => rankCell(row.rank) },
              {
                title: "作品",
                render: (row) =>
                  el("div", {}, [
                    el("div", { class: "truncate", title: row.title, text: row.title }),
                    el("div", { class: "cell-sub", text: typeLabel(row.media_type) + (row.season ? " · 第 " + row.season + " 季" : "") }),
                  ]),
              },
              { title: "热度", render: (row) => heatCell(row.heat_percent, Math.round(row.heat)) },
            ],
            hotItems
          )
        : emptyBox("暂无热度数据：先在资源搜索里搜几次，或启用站点后跑一次追新雷达", "flame"),
    ]);

    const runAll = el("button", { class: "btn primary" }, [
      icon("refresh", "sm"),
      el("span", { text: "立即巡检订阅" }),
    ]);
    runAll.addEventListener("click", async () => {
      runAll.disabled = true;
      runAll.querySelector("span").textContent = "巡检中…";
      try {
        const result = await api("/subscribes/run-all", { method: "POST" });
        toast(
          "巡检完成：" + result.total + " 个订阅，新增 " + result.downloads + " 个任务",
          "ok"
        );
        pageDashboard();
      } catch (error) {
        toast(error.message, "err");
        runAll.disabled = false;
        runAll.querySelector("span").textContent = "立即巡检订阅";
      }
    });

    shell(
      el("div", { class: "grid" }, [
        stats,
        // 状态条铺满整行：它内容少，和高表格并排只会被撑出大片空白
        health,
        // 「定时任务」与「热度排行」都是表格，等高拉伸不浪费空间
        el("div", { class: "grid cols-2 fit" }, [jobCard, hotCard]),
        recent,
      ]),
      "仪表盘",
      "系统概览 · 定时任务 · 热度排行",
      [runAll]
    );
  }

  // ---------------- 资源搜索 ----------------
  const searchState = { items: [], keyword: "", sort: "score", kind: "", sites: [],
    // stream：进行中的流式请求句柄（换关键词要取消它）
    // progress：已收到几个站 / 共几个站，用于搜索中的进度条
    stream: null, progress: { received: 0, total: 0, running: false } };

  const SORTERS = {
    score: (a, b) => (b.score || 0) - (a.score || 0),
    seeders: (a, b) => (b.seeders || 0) - (a.seeders || 0),
    size: (a, b) => (b.size || 0) - (a.size || 0),
    time: (a, b) => String(b.publish_at || "").localeCompare(String(a.publish_at || "")),
  };

  /**
   * 「这种资源现在能不能下」的缓存。
   *
   * 不同资源要不同下载方式：磁力/种子靠 BT 下载器，网盘靠转存或 aria2，
   * 视频网页只能靠 yt-dlp。后端 /downloads/routing 会告知每种类型缺什么，
   * 这里缓存一份，好在**点之前**就把提示显示出来。
   */
  const routingCache = { items: null, at: 0 };

  async function downloadRouting() {
    // 缓存 60s：用户去设置里加完下载器回来，很快就能看到状态变化，
    // 又不会让一屏几十条资源各发一次请求
    if (routingCache.items && Date.now() - routingCache.at < 60000) {
      return routingCache.items;
    }
    try {
      const result = await api("/downloads/routing");
      routingCache.items = result.items || [];
      routingCache.at = Date.now();
    } catch (error) {
      // 拿不到路由信息不该拦住下载：退回"直接试"，失败时后端仍会给出原因
      routingCache.items = [];
      routingCache.at = 0;
    }
    return routingCache.items;
  }

  function routeFor(items, kind) {
    const target = kind || "torrent";
    for (let index = 0; index < items.length; index += 1) {
      if (items[index].kind === target) return items[index];
    }
    return null;
  }

  function downloadButton(row, onDone) {
    const button = el("button", { class: "btn sm primary" }, [
      icon("download", "sm"),
      el("span", { text: "下载" }),
    ]);
    // 异步标注"这类资源当前下不了"：不禁用按钮（用户可能刚配好还没刷新），
    // 但把原因挂到 title 上，并在点击时先确认
    downloadRouting().then((items) => {
      const route = routeFor(items, row.kind);
      if (route && !route.ready) {
        button.classList.add("warn");
        button.title = route.reason || route.hint || "";
      }
    });
    button.addEventListener("click", async () => {
      const route = routeFor(await downloadRouting(), row.kind);
      if (route && !route.ready) {
        // 明确告知缺什么、去哪儿加，而不是投出去等一个必然的失败
        toast(route.reason || route.hint || "当前没有能处理该资源的下载器", "err");
        return;
      }
      button.disabled = true;
      button.querySelector("span").textContent = "提交中…";
      try {
        const res = await api("/downloads", {
          method: "POST",
          body: {
            title: row.title,
            link: row.link,
            kind: row.kind,
            site: row.site,
            size: row.size,
            password: row.password,
            page_url: row.page_url,
            meta: row.meta || {},
          },
        });
        // HTTP 200 不代表投递成功：下载器连不上时后端会落库成 failed 并回
        // success=false。以前这里无条件弹绿色「已加入下载队列」，用户看到成功
        // 提示、任务列表里却是红色失败，属于最难排查的那种误导。
        if (res && res.success === false) {
          toast(res.message || "投递失败，请检查下载器", "err");
          button.disabled = false;
          button.querySelector("span").textContent = "下载";
          if (onDone) onDone();
          return;
        }
        button.querySelector("span").textContent = "已添加";
        // pending（如网盘缺账号/aria2）不是失败，但也没真的开始下，给中性提示
        if (res && res.message) toast(res.message, "warn");
        else toast("已加入下载队列", "ok");
        if (onDone) onDone();
      } catch (error) {
        toast(error.message, "err");
        button.disabled = false;
        button.querySelector("span").textContent = "下载";
      }
    });
    return button;
  }

  /**
   * 网盘资源的「转存」按钮。
   *
   * 为什么和「下载」并列而不是二选一：网盘分享有两种正当用法——
   * 转存进自己的盘（秒传、留存）、或直接下到本地（走 aria2 等）。
   * 后端用 row.actions 明确告知支持哪些，这里按能力位渲染。
   */
  function saveButton(row, onDone) {
    const button = el("button", { class: "btn sm" }, [
      icon("cloud", "sm"),
      el("span", { text: "转存" }),
    ]);
    button.addEventListener("click", async () => {
      // 先取网盘列表：只有一个盘时直接转存，多个盘让用户选，零个盘给明确提示
      let storages = [];
      try {
        const overview = await api("/pan");
        storages = (overview.items || []).filter((item) => item.supports_save);
      } catch (error) {
        toast(error.message, "err");
        return;
      }
      if (!storages.length) {
        toast("尚未启用支持转存的网盘，请先到「站点管理」添加夸克/AList", "err");
        return;
      }

      const doSave = async (siteId, targetDir) => {
        button.disabled = true;
        button.querySelector("span").textContent = "转存中…";
        try {
          const result = await api("/pan/save", {
            method: "POST",
            body: {
              share_url: row.link,
              password: row.password || null,
              site_id: siteId || null,
              target_dir: targetDir || null,
            },
          });
          button.querySelector("span").textContent = "已转存";
          toast("已转存到 " + (result.saved_path || "网盘"), "ok");
          if (onDone) onDone();
        } catch (error) {
          toast(error.message, "err");
          button.disabled = false;
          button.querySelector("span").textContent = "转存";
        }
      };

      modal(
        "转存到网盘",
        [
          {
            key: "site_id",
            label: "目标网盘",
            type: "select",
            options: [{ value: "", label: "自动选择（按链接域名匹配）" }].concat(
              storages.map((item) => ({ value: String(item.site_id), label: item.name }))
            ),
          },
          { key: "target_dir", label: "落地目录", placeholder: "留空用网盘默认目录" },
        ],
        async (values) => {
          await doSave(
            values.site_id ? Number(values.site_id) : null,
            values.target_dir || null
          );
        },
        "开始转存"
      );
    });
    return button;
  }

  /**
   * 按后端下发的能力位渲染资源操作区。
   *
   * 参考 T3FAP 的能力位设计：前端不再猜「这条资源能干什么」，
   * 而是读 row.actions。这样新增资源类型时前端零改动。
   */
  function resourceActions(row, onDone) {
    const actions = row.actions || [];
    const nodes = [];
    // 网盘资源：转存 + 下载 两个按钮并列（本轮需求）
    if (actions.indexOf("save") >= 0) nodes.push(saveButton(row, onDone));
    if (actions.indexOf("download") >= 0) nodes.push(downloadButton(row, onDone));
    if (!nodes.length) nodes.push(downloadButton(row, onDone));
    if (row.page_url) {
      nodes.push(
        iconButton("详情页", "link", () => window.open(row.page_url, "_blank", "noopener"), "sm ghost")
      );
    }
    return el("div", { class: "row tight" }, nodes);
  }

  async function pageSearch() {
    const keyword = el("input", {
      class: "input",
      placeholder: "片名，如：庆余年 / Oppenheimer",
    });
    keyword.value = searchState.keyword;

    const type = el("select", { class: "input" }, [
      el("option", { value: "" }, "全部类型"),
      el("option", { value: "tv" }, "剧集"),
      el("option", { value: "movie" }, "电影"),
      el("option", { value: "anime" }, "动漫"),
    ]);
    const season = el("input", { class: "input", type: "number", placeholder: "季" });
    const episode = el("input", { class: "input", type: "number", placeholder: "集" });
    const results = el("div", {});
    const hotBox = el("div", {});

    const renderResults = () => {
      const filtered = searchState.kind
        ? searchState.items.filter((item) =>
            searchState.kind === "pan"
              ? item.kind === "pan" || item.kind === "direct"
              : item.kind === "torrent" || item.kind === "magnet"
          )
        : searchState.items.slice();
      filtered.sort(SORTERS[searchState.sort] || SORTERS.score);

      const counts = { pan: 0, bt: 0 };
      searchState.items.forEach((item) => {
        if (item.kind === "pan" || item.kind === "direct") counts.pan += 1;
        else counts.bt += 1;
      });

      results.replaceChildren(
        el("div", { class: "card flush" }, [
          el("div", { class: "card-head" }, [
            el("h3", {}, [
              icon("search", "sm"),
              el("span", { text: "搜索结果 " + filtered.length + " / " + searchState.items.length }),
              // 搜索中就把「已回来几个站」显示出来：流式下结果是一批批到的，
              // 不告诉用户还有站没回来，他会以为已经搜完了（而且会少下东西）
              searchState.progress.running
                ? el("span", {
                    class: "tag warn dot",
                    text: "搜索中 " + searchState.progress.received + "/" + searchState.progress.total + " 站",
                  })
                : null,
            ]),
            el("div", { class: "row tight center" }, [
              segment(
                [
                  { value: "", label: "全部 " + searchState.items.length },
                  { value: "bt", label: "BT " + counts.bt },
                  { value: "pan", label: "网盘 " + counts.pan },
                ],
                searchState.kind,
                (value) => {
                  searchState.kind = value;
                  renderResults();
                }
              ),
              segment(
                [
                  { value: "score", label: "综合" },
                  { value: "seeders", label: "做种" },
                  { value: "size", label: "体积" },
                  { value: "time", label: "时间" },
                ],
                searchState.sort,
                (value) => {
                  searchState.sort = value;
                  renderResults();
                }
              ),
            ]),
          ]),
          table(
            [
              { title: "#", render: (row, index) => rankCell(index + 1) },
              {
                title: "资源名称",
                render: (row) =>
                  el("div", {}, [
                    el("div", { class: "row tight center" }, [
                      el("div", { class: "truncate", title: row.title, text: row.title }),
                      // 会员正片在列表上就标出来：否则用户只能靠一个个点去试错
                      row.paywalled
                        ? el("span", {
                            class: "tag warn",
                            title: "长视频平台的会员正片，本工具不提供此类抓取，请用平台官方客户端观看",
                            text: "会员",
                          })
                        : null,
                    ]),
                    el("div", { class: "cell-sub" }, [
                      (row.meta && row.meta.quality) || "",
                      (row.meta && row.meta.video_codec) ? " · " + row.meta.video_codec : "",
                      (row.meta && row.meta.effect) ? " · " + row.meta.effect : "",
                      (row.meta && row.meta.episodes && row.meta.episodes.length)
                        ? " · 第 " + row.meta.episodes.join(",") + " 集"
                        : "",
                    ]),
                  ]),
              },
              { title: "来源", render: (row) => el("span", { class: "tag", text: row.site || "-" }) },
              {
                title: "类型",
                render: (row) =>
                  el("span", { class: "tag " + (row.kind === "pan" ? "brand" : "") }, [
                    icon(row.kind === "pan" ? "cloud" : "link", "sm"),
                    el("span", { text: kindLabel(row.kind) }),
                  ]),
              },
              { title: "画质", render: (row) => (row.meta && row.meta.resolution) || "-" },
              { title: "大小", class: "num", render: (row) => fmtSize(row.size) },
              { title: "做种", class: "num", render: (row) => row.seeders || "-" },
              {
                title: "评分",
                class: "num",
                render: (row) => el("span", { class: "tag brand", text: String(Math.round(row.score || 0)) }),
              },
              { title: "操作", render: (row) => resourceActions(row) },
            ],
            filtered,
            searchState.progress.running
              ? "正在搜索，结果会陆续出现…"
              : "没有匹配的资源，试试更换关键词或启用更多站点"
          ),
        ])
      );
      // 站点情况紧跟结果：结果少的时候，用户第一眼要看到的就是「谁没出货、为什么」
      const report = siteReportCard(searchState.sites, function () {
        if (searchState.keyword) doSearch(searchState.keyword);
      });
      if (report) results.appendChild(report);
    };

    const doSearch = async (value) => {
      const text = (value || keyword.value).trim();
      if (!text) {
        toast("请输入关键词", "err");
        return;
      }
      keyword.value = text;
      searchState.keyword = text;

      // 换关键词必须取消上一次的流，否则两次搜索的结果会交叉写进同一个列表
      if (searchState.stream) {
        searchState.stream.abort();
        searchState.stream = null;
      }
      searchState.items = [];
      searchState.sites = [];
      searchState.progress = { received: 0, total: 0, running: true };
      results.replaceChildren(loading());

      let firstBatch = true;
      const handle = apiStream(
        "/search/stream",
        {
          body: {
            keyword: text,
            media_type: type.value || null,
            season: season.value ? Number(season.value) : null,
            episode: episode.value ? Number(episode.value) : null,
          },
        },
        function (event) {
          if (event.type === "start") {
            searchState.progress.total = event.total_sites || 0;
            // 先把骨架画出来：用户立刻看到「正在查 N 个站」，而不是空白转圈
            renderResults();
            return;
          }
          if (event.type === "site") {
            searchState.progress.received = event.received || 0;
            searchState.progress.total = event.total_sites || searchState.progress.total;
            if (event.site) searchState.sites.push(event.site);
            const batch = event.items || [];
            if (batch.length) {
              searchState.items = searchState.items.concat(batch);
              if (firstBatch) {
                firstBatch = false;
                // 首批到达就把 loading 换成真实表格
                renderResults();
                return;
              }
            }
            renderResults();
            return;
          }
          if (event.type === "done") {
            searchState.progress.running = false;
            if (event.sites && event.sites.length) searchState.sites = event.sites;
            renderResults();
            loadHot();
            const bad = searchState.sites.filter(function (row) {
              return row.status === "timeout" || row.status === "error" || row.status === "skipped";
            }).length;
            const total = event.total || searchState.items.length;
            toast(
              "找到 " + total + " 条资源" +
                (bad ? "（" + bad + " 个站点未出货，见下方站点情况）" : ""),
              total ? (bad ? "warn" : "ok") : "warn"
            );
            return;
          }
          if (event.type === "error") {
            searchState.progress.running = false;
            toast(event.message || "搜索失败", "err");
            renderResults();
          }
        }
      );
      searchState.stream = handle;
      try {
        await handle.done;
      } catch (error) {
        if (error && error.name === "AbortError") return;  // 用户主动换词，不是故障
        searchState.progress.running = false;
        // 已经拿到部分结果时不要清屏：保住已出货的站点，只提示出错
        if (searchState.items.length) {
          renderResults();
          toast(error.message, "err");
        } else {
          results.replaceChildren(el("div", { class: "card" }, [emptyBox(error.message, "alert")]));
        }
      } finally {
        if (searchState.stream === handle) searchState.stream = null;
      }
    };

    /** 热度排行侧栏：搜索页直接可见的榜单，点击即搜。 */
    const loadHot = async () => {
      try {
        const response = await api("/trending?limit=8&days=14");
        const data = response.data;
        const hot = data.resources.items;
        const words = data.keywords.items;

        hotBox.replaceChildren(
          el("div", { class: "grid cols-2" }, [
            el("div", { class: "card flush" }, [
              el("div", { class: "card-head" }, [
                el("h3", {}, [icon("flame", "sm"), el("span", { text: "资源热度榜" })]),
                el("span", { class: "tag", text: "近 " + data.resources.window_days + " 天" }),
              ]),
              hot.length
                ? table(
                    [
                      { title: "#", render: (row) => rankCell(row.rank) },
                      {
                        title: "作品",
                        render: (row) => {
                          const link = el("a", {
                            href: "javascript:void(0)",
                            class: "truncate",
                            title: "点击搜索 " + row.title,
                            text: row.title,
                            onclick: () => doSearch(row.title),
                          });
                          return el("div", {}, [
                            link,
                            el("div", { class: "cell-sub", text:
                              typeLabel(row.media_type) +
                              (row.season ? " · 第 " + row.season + " 季" : "") +
                              (row.latest_episode ? " · 更新至 " + row.latest_episode + " 集" : "") +
                              " · " + row.site_count + " 站 " + row.resource_count + " 条" }),
                          ]);
                        },
                      },
                      { title: "热度", render: (row) => heatCell(row.heat_percent, Math.round(row.heat)) },
                    ],
                    hot
                  )
                : emptyBox("搜索过的资源会在这里聚合成热度榜", "flame"),
            ]),
            el("div", { class: "card flush" }, [
              el("div", { class: "card-head" }, [
                el("h3", {}, [icon("chart", "sm"), el("span", { text: "搜索热词" })]),
                el("span", { class: "tag", text: "近 " + data.keywords.window_days + " 天" }),
              ]),
              words.length
                ? el(
                    "div",
                    { class: "chips", style: "padding:0 22px 22px" },
                    words.map((item) =>
                      el("button", {
                        class: "chip",
                        text: item.keyword + " · " + item.times,
                        onclick: () => doSearch(item.keyword),
                      })
                    )
                  )
                : emptyBox("还没有搜索历史", "search"),
            ]),
          ])
        );
      } catch (error) {
        hotBox.replaceChildren();
      }
    };

    keyword.addEventListener("keydown", (event) => {
      if (event.key === "Enter") doSearch();
    });
    if (searchState.items.length) renderResults();
    loadHot();

    const labeled = (text, node, flex) =>
      el("div", { style: flex ? "flex:" + flex : null }, [
        el("label", { class: "dim tiny", text: text }),
        node,
      ]);

    const searchBtn = el("button", { class: "btn primary", style: "flex:0 0 auto" }, [
      icon("search", "sm"),
      el("span", { text: "搜索" }),
    ]);
    searchBtn.addEventListener("click", () => doSearch());

    shell(
      el("div", { class: "grid" }, [
        el("div", { class: "card" }, [
          el("h3", {}, [icon("search", "sm"), el("span", { text: "聚合搜索（BT 站点 + 网盘）" })]),
          el("div", { class: "row" }, [
            labeled("关键词", keyword, "3"),
            labeled("类型", type),
            labeled("季", season),
            labeled("集", episode),
            searchBtn,
          ]),
        ]),
        results,
        hotBox,
      ]),
      "资源搜索",
      "并发查询所有已启用的索引器与盘搜服务",
      [iconButton("刷新热榜", "refresh", () => loadHot())]
    );
  }

  // ---------------- 热度排行 ----------------
  // 热度排行只保留「发现榜」一种口径（原资源热榜/实时热榜/搜索热词/站点贡献已下线，
  // 它们的数据在「资源搜索」页与「站点管理」页各自有更合适的入口）。
  //: 榜单来源 → 展示名。新增数据源只要在这里加一行，
  //: 卡片副标题、页头徽标、统计行三处会同时跟上，不会漂移。
  const SOURCE_LABEL = {
    douban: "豆瓣",
    bilibili: "Bilibili",
    youtube: "YouTube",
    bangumi: "Bangumi",
  };

  const trendingState = {
    view: "board",
    // 发现榜当前分类（电影/电视剧/动漫/综艺/Bilibili/YouTube）与两个视频站的二级分区
    discoverCat: "movie",
    biliPartition: "all",
    ytRegion: "US",
    // 下拉加载：已加载的条目、下一页偏移、是否还有更多、是否正在加载
    items: [],
    offset: 0,
    hasMore: false,
    loading: false,
    label: "",
    source: "",
    message: "",
    kind: "media",
    //: 新番日历（按周一~周日分组）的数据；只有「新番」页签的日历视图用得到。
    //: 与摊平榜单共用后端同一份缓存，切视图不会多打请求。
    calendar: null,
    //: 跳去资源搜索页前记下的滚动位置与已加载条数。
    //: 榜单可能已经下拉加载了好几页，切回来时必须把这些页**先恢复出来**
    //: 再滚回去，否则内容高度不够，scrollTo 会落空（ADR-48）。
    restore: null,
  };
  //: 每页条数。默认首屏 30 条，下拉再追加 30 条。
  const TRENDING_PAGE_SIZE = 30;

  function subscribeFromTrending(row) {
    modal(
      "订阅《" + row.title + "》",
      [
        { key: "title", label: "片名", value: row.title },
        {
          key: "media_type",
          label: "类型",
          type: "select",
          value: row.media_type === "unknown" ? "tv" : row.media_type,
          options: [
            { value: "tv", label: "剧集" },
            { value: "movie", label: "电影" },
            { value: "anime", label: "动漫" },
          ],
        },
        { key: "season", label: "季", type: "number", value: row.season || 1 },
        { key: "total_episodes", label: "总集数（0 = 持续追新）", type: "number", value: 0 },
        { key: "resolution", label: "分辨率过滤（可选）", placeholder: "如：2160p,1080p" },
      ],
      async (values) => {
        await api("/subscribes", {
          method: "POST",
          body: Object.assign({}, values, {
            season: values.season || 1,
            total_episodes: values.total_episodes || 0,
          }),
        });
        toast("已创建订阅，稍后自动追新", "ok");
      },
      "创建订阅",
      { lead: "热榜里看到想追的，直接建订阅交给自动化。" }
    );
  }

  /** 封面图（带占位降级）。

      站点不一定给封面（盘搜就没有），未配 TMDB 时也没有兜底图源，
      因此必须有占位态：用作品名首字 + 类型图标画一个渐变色块，
      而不是显示裂图。加载失败（外链挂了/防盗链）时同样退回占位。
  */
  function posterBox(row) {
    const holder = el("div", { class: "poster" });
    const placeholder = () => {
      const initial = (row.title || "?").trim().slice(0, 1);
      // 用标题算一个稳定色相：同一部作品每次进页面颜色一致，不会闪
      let hash = 0;
      for (let i = 0; i < (row.title || "").length; i += 1) {
        hash = (hash * 31 + (row.title || "").charCodeAt(i)) % 360;
      }
      return el("div", { class: "poster-ph", style: "--ph-hue:" + hash }, [
        el("span", { class: "poster-ph-text", text: initial }),
        icon(typeIcon(row.media_type), "sm"),
      ]);
    };

    if (!row.poster) {
      holder.appendChild(placeholder());
      return holder;
    }
    const image = el("img", {
      src: posterSrc(row.poster),
      alt: row.title,
      loading: "lazy",
      // 部分站点图床有防盗链，带 referrer 会 403
      referrerpolicy: "no-referrer",
    });
    image.addEventListener("error", () => {
      holder.replaceChildren(placeholder());
    });
    // 登记进在飞清单：切页时要中止，见 abortDetachedPosters() 的说明
    livePosters.push(image);
    holder.appendChild(image);
    return holder;
  }

  //: 正在加载中的封面 <img>。已经从页面上被换掉的，要主动中止。
  const livePosters = [];

  /** 中止「已经不在页面上」的封面图请求。

      为什么必须做：走后端代理的封面（豆瓣/Bangumi）和 API 是**同源**的，
      而浏览器对同一域名只开 ~6 条并发连接。发现榜一屏 30 张封面、
      再切几个分类，就能攒下几十个在飞的图片请求，把连接池占满 ——
      于是**下一屏要用的 /api/v1/... 得排在这些图片后面**。
      实测：逛完热度排行的 7 个分类再进定时任务，页面要等约 10 秒才出内容，
      而后端其实 8ms 就回了（基线 8~9ms，并发搜索时也只有 8~11ms）。

      表现极具误导性：标题、侧边栏、页面副标题全都正常，只有内容区空着 ——
      看起来像「这一页坏了」，实际是**上一屏的封面把网络占死了**。

      判据用 `isConnected`（是否还挂在文档里）而不是「切页就全清」：
      换掉的那一屏图片再也不会被看到，掐掉零损失；而当前这一屏正在加载的封面
      必须留着，否则新页面的封面会全变占位块。
  */
  function abortDetachedPosters() {
    for (let i = livePosters.length - 1; i >= 0; i -= 1) {
      const image = livePosters[i];
      if (!image || image.complete) {
        livePosters.splice(i, 1);
        continue;
      }
      if (!image.isConnected) {
        // src="" 会让浏览器中止这次请求；元素已经被丢弃，不影响观感
        image.src = "";
        livePosters.splice(i, 1);
      }
    }
  }

  /**
   * 把封面地址转成可直接加载的地址。
   *
   * 豆瓣图床对缺少 Referer 的请求返回 418（浏览器跨站会剥离 Referer，
   * 前端无论怎么设都拿不到图），因此这类图床改走后端图片代理：
   * 后端带正确 Referer 代拉后转发。其它图床（B 站/YouTube/TMDB）能直连，
   * 就不绕后端，省一次转发。
   */
  //: 必须走后端代理的图床。
  //: bgm.tv（Bangumi 放送日历封面）是 v1.15.0 补的：后端 images.py 早就把它
  //: 连同专用 Referer 一起写进了白名单——说明本意就是让它走代理——但前端
  //: 这里一直没列，于是浏览器直连 lain.bgm.tv 拿到 ERR_HTTP2_PROTOCOL_ERROR，
  //: 新番日历整片裂图退占位。实测走代理后 200 image/jpeg 正常。
  const PROXY_HOSTS = ["doubanio.com", "douban.com", "bgm.tv"];
  function posterSrc(url) {
    const raw = String(url || "");
    if (!raw) return raw;
    // 协议相对地址（//i0.hdslb.com/...）补上 https 免得走成 file://
    const normalized = raw.indexOf("//") === 0 ? "https:" + raw : raw;
    let host = "";
    try {
      host = new URL(normalized).hostname.toLowerCase();
    } catch (error) {
      return normalized;
    }
    const needsProxy = PROXY_HOSTS.some(
      (suffix) => host === suffix || host.endsWith("." + suffix)
    );
    if (!needsProxy) return normalized;
    return API + "/images/proxy?url=" + encodeURIComponent(normalized);
  }

  /** 视频榜（Bilibili / YouTube）的「下载」按钮。

      这两类榜单的条目**本身就带确切播放地址**，不需要再去 BT/网盘搜资源——
      直接把地址交给后端 yt-dlp 即可。所以它们的卡片不显示「搜资源」，
      而是显示「下载」（本轮需求 1）。

      下载前先 probe 一次拿到真实标题与可用画质：既能让用户下之前看清楚，
      也能挡住会员专享内容（后端会拒绝并给出可读原因），
      而不是让任务默默失败。
  */
  /** 把 yt-dlp 的 format 项拼成一行可读画质名。

      后端下发的字段是 ``height`` / ``ext`` / ``note`` / ``filesize``，
      没有现成的显示名，这里统一拼装：``1080p · mp4 · 高码率（124.5 MB）``。
  */
  function formatLabel(f) {
    const parts = [];
    if (f.height) parts.push(f.height + "p");
    if (f.ext) parts.push(f.ext);
    if (f.note) parts.push(f.note);
    if (!parts.length) parts.push(f.format_id || "未知画质");
    return parts.join(" · ") + (f.filesize ? "（" + fmtSize(f.filesize) + "）" : "");
  }

  function videoDownloadButton(row) {
    const url = row.url || row.douban_url || "";
    const btn = el("button", { class: "btn sm primary" }, [
      icon("download", "sm"),
      el("span", { text: "下载" }),
    ]);
    btn.addEventListener("click", async (event) => {
      event.stopPropagation();
      if (!url) {
        toast("这条榜单数据没有播放地址，无法下载", "err");
        return;
      }
      const span = btn.querySelector("span");
      btn.disabled = true;
      span.textContent = "解析中…";
      try {
        // 先解析：拿到真实标题/时长/画质，也顺带验证这条内容能不能下
        const probe = await api("/downloads/webvideo/probe?url=" + encodeURIComponent(url), {
          method: "POST",
        });
        const info = probe.data || {};
        const formats = info.formats || [];
        modal(
          "下载：" + (info.title || row.title),
          [
            {
              key: "title",
              label: "保存标题",
              value: info.title || row.title,
              hint: info.uploader ? "作者：" + info.uploader : "",
            },
            formats.length
              ? {
                  key: "format",
                  label: "画质",
                  type: "select",
                  value: "",
                  options: [{ value: "", label: "自动（最佳）" }].concat(
                    formats.map((f) => ({
                      value: f.format_id || "",
                      label: formatLabel(f),
                    }))
                  ),
                }
              : null,
            { key: "save_path", label: "保存目录（留空用默认下载目录）", value: "" },
          ].filter(Boolean),
          async (values) => {
            let query =
              "/downloads/webvideo?url=" + encodeURIComponent(url) +
              "&title=" + encodeURIComponent(values.title || row.title);
            if (values.save_path) {
              query += "&save_path=" + encodeURIComponent(values.save_path);
            }
            if (values.format) {
              query += "&video_format=" + encodeURIComponent(values.format);
            }
            await api(query, { method: "POST" });
            toast("已加入下载队列，可在「下载任务」查看进度", "ok");
          },
          "开始下载",
          {
            lead:
              (info.duration ? "时长 " + fmtDuration(info.duration) + " · " : "") +
              (info.site || "") +
              (formats.length ? " · " + formats.length + " 种画质可选" : ""),
          }
        );
      } catch (error) {
        toast(error.message, "err");
      } finally {
        btn.disabled = false;
        span.textContent = "下载";
      }
    });
    return btn;
  }

  /** 榜单卡片右下角的操作区。

      ``kind=video``（B 站/YouTube）→ 只给「下载」；
      ``kind=media``（豆瓣四类）→ 给「订阅 + 搜资源」，
      「搜资源」会跳到资源搜索页（本轮需求 1，不再在榜单页内搜）。
  */
  function discoverActions(row, data, onSearch) {
    if ((data.kind || "media") === "video") {
      return [videoDownloadButton(row)];
    }
    return [
      iconButton("订阅", "star", (event) => {
        if (event) event.stopPropagation();
        subscribeFromTrending({
          title: row.title,
          media_type: row.media_type || "movie",
          season: 1,
        });
      }, "sm primary"),
      iconButton("搜资源", "search", (event) => {
        if (event) event.stopPropagation();
        onSearch(row.title);
      }, "sm ghost"),
    ];
  }

  /** 发现榜画板：来源是豆瓣/B 站/YouTube，字段与本地资源榜不同，单独渲染。

      与本地资源榜的区别：这里没有"做种数/站点数"，而是有评分、
      更新进度、以及**本地是否已有片源**（local_count）——后者是本项目
      相对纯榜单站的价值：榜单上直接看出哪几部你的站点已经能下了。
  */
  function discoverBoard(data, onSearch) {
    const items = data.items || [];
    if (!items.length) {
      return el("div", { class: "pad" }, [
        emptyBox(data.message || "暂无数据", "flame"),
      ]);
    }
    // 视频站（B 站/YouTube）与影视榜（豆瓣）的副标题字段不同：
    // 前者有 UP 主/时长/播放量，后者有类型/更新进度/评分。
    const isVideo = (data.kind || "media") === "video";
    const sourceLabel = SOURCE_LABEL[data.source] || "豆瓣";
    return el(
      "div",
      { class: "board" },
      items.map((row) => {
        const hasLocal = (row.local_count || 0) > 0;
        const card = el("div", { class: "board-card", tabindex: "0", role: "button" }, [
          el("div", { class: "board-cover" }, [
            posterBox(row),
            el("div", { class: "board-badges" }, [
              el("span", {
                class: "board-rank" + (row.rank <= 3 ? " top" : ""),
                text: "#" + row.rank,
              }),
              row.rating ? el("span", { class: "board-score", text: String(row.rating) }) : null,
            ]),
            // 已有片源是最重要的信息，做成醒目角标
            hasLocal
              ? el("div", { class: "board-flag" }, [
                  icon("check", "sm"),
                  el("span", { text: "已有 " + row.local_count + " 条" }),
                ])
              : null,
          ]),
          el("div", { class: "board-body" }, [
            el("div", { class: "board-title", title: row.title, text: row.title }),
            el("div", { class: "board-sub", text:
              [
                isVideo ? row.uploader : typeLabel(row.media_type),
                row.episodes_info || null,
                // 直播条目 duration=0，显示"直播中"比显示 00:00 有意义
                isVideo && row.is_live ? "直播中" : null,
                isVideo && row.duration ? fmtDuration(row.duration) : null,
              ].filter(Boolean).join(" · ") || sourceLabel }),
            el("div", { class: "board-foot" }, [
              el("span", { class: "tiny dim", text: isVideo
                ? fmtCompact(row.heat) + " 播放"
                : (row.rating ? row.rating + " 分" : "暂无评分") }),
              hasLocal
                ? el("span", { class: "tiny dim", text: (row.local_sites || []).slice(0, 2).join("/") })
                : el("span", { class: "tiny dim", text: "未入库" }),
            ]),
          ]),
          el("div", { class: "board-actions" }, discoverActions(row, data, onSearch)),
        ]);
        const open = () => discoverDetail(row, data, onSearch);
        card.addEventListener("click", open);
        card.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            open();
          }
        });
        return card;
      })
    );
  }

  /** 新番放送日历：按周一~周日分列，标出今天。

      与「动漫」页签的区别值得说清楚：动漫是**豆瓣热度榜**（大家在看什么），
      这里是**放送表**（这周哪天更新第几话）。追番真正需要后者，
      而热度榜永远给不出来——它的排序维度不是时间。

      「今天」那一列高亮并排在最前：用户九成的诉求是"今天有什么更新"。
  */
  function bangumiCalendar(data, onSearch) {
    const days = (data || {}).days || [];
    if (!days.length) {
      return el("div", { class: "pad" }, [
        emptyBox((data || {}).message || "暂无放送数据", "clock"),
      ]);
    }
    const todayIndex = (data || {}).today;
    // 从今天开始排。"未定"桶（weekday=null）不参与轮转，永远垫最后。
    const dated = days.filter((day) => day.weekday !== null && day.weekday !== undefined);
    const undated = days.filter((day) => day.weekday === null || day.weekday === undefined);
    const ordered = [];
    for (let step = 0; step < dated.length; step += 1) {
      ordered.push(dated[(todayIndex + step) % dated.length]);
    }
    ordered.push(...undated);

    return el(
      "div",
      { class: "cal" },
      ordered.map((day) => {
        const items = day.items || [];
        const isToday = Boolean(day.is_today);
        return el("div", { class: "cal-col" + (isToday ? " today" : "") }, [
          el("div", { class: "cal-head" }, [
            el("span", { class: "cal-day", text: day.label }),
            isToday ? el("span", { class: "tag brand tiny", text: "今天" }) : null,
            el("span", { class: "dim tiny", text: items.length + " 部" }),
          ]),
          items.length
            ? el("div", { class: "cal-list" }, items.map((row) => {
                const hasLocal = (row.local_count || 0) > 0;
                const card = el("div", { class: "cal-item", tabindex: "0", role: "button" }, [
                  el("div", { class: "cal-thumb" }, [posterBox(row)]),
                  el("div", { class: "cal-body" }, [
                    el("div", { class: "cal-title", title: row.title, text: row.title }),
                    el("div", { class: "cal-sub", text:
                      [
                        row.rating ? row.rating + " 分" : "暂无评分",
                        row.total_episodes ? row.total_episodes + " 话" : null,
                      ].filter(Boolean).join(" · ") }),
                    hasLocal
                      ? el("span", { class: "tag brand tiny", text: "已有 " + row.local_count + " 条" })
                      : null,
                  ]),
                ]);
                // 点条目复用发现榜的详情面板，行为与画板/列表视图一致
                const open = () =>
                  discoverDetail(row, { kind: "media", source: "bangumi" }, onSearch);
                card.addEventListener("click", open);
                card.addEventListener("keydown", (event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    open();
                  }
                });
                return card;
              }))
            : el("div", { class: "cal-empty dim tiny", text: "这天没有新番" }),
        ]);
      })
    );
  }

  /** 发现榜列表视图。 */
  function discoverTable(data, onSearch) {
    const isVideo = (data.kind || "media") === "video";
    return table(
      [
        { title: "#", render: (row) => rankCell(row.rank) },
        {
          title: isVideo ? "视频" : "作品",
          render: (row) =>
            el("div", {}, [
              el("div", { class: "truncate", title: row.title, text: row.title }),
              el("div", { class: "cell-sub", text:
                [isVideo ? row.uploader : typeLabel(row.media_type), row.episodes_info]
                  .filter(Boolean).join(" · ") }),
            ]),
        },
        isVideo
          ? { title: "播放", class: "num", render: (row) => fmtCompact(row.heat) }
          : { title: "评分", class: "num", render: (row) => row.rating || "-" },
        // 视频站条目是单个视频，「本地片源」这一列对它没有意义
        isVideo
          ? {
              title: "时长",
              class: "num",
              render: (row) =>
                row.is_live
                  ? el("span", { class: "tag warn tiny", text: "直播" })
                  : el("span", { text: row.duration ? fmtDuration(row.duration) : "-" }),
            }
          : {
              title: "本地片源",
              render: (row) =>
                (row.local_count || 0) > 0
                  ? el("span", { class: "tag brand", text: row.local_count + " 条" })
                  : el("span", { class: "tiny dim", text: "未入库" }),
            },
        {
          title: "操作",
          render: (row) =>
            el("div", { class: "row tight" }, discoverActions(row, data, onSearch)),
        },
      ],
      data.items || [],
      data.message || "暂无数据"
    );
  }

  function discoverDetail(row, data, onSearch) {
    const isVideo = (data.kind || "media") === "video";
    const isYt = data.source === "youtube";
    const srcName = SOURCE_LABEL[data.source] || "豆瓣";
    const info = (label, value) =>
      el("div", { class: "kv-item" }, [
        el("div", { class: "kv-label", text: label }),
        el("div", { text: value }),
      ]);
    let close = () => {};
    close = panelModal(
      row.title,
      "来自 " + srcName + (isVideo ? " 热门榜" : " 榜单"),
      el("div", {}, [
        el("div", { class: "kv" }, [
          info("排名", "#" + row.rank),
          isVideo
            ? info("播放量", fmtCompact(row.heat))
            : info("评分", row.rating ? String(row.rating) : "暂无"),
          isVideo
            ? info("时长", row.is_live ? "直播中" : (row.duration ? fmtDuration(row.duration) : "-"))
            : info("类型", typeLabel(row.media_type)),
          isVideo
            ? info(isYt ? "频道" : "UP 主", row.uploader || "-")
            : info("更新", row.episodes_info || "-"),
          // 视频站条目是单个视频，不存在"本地片源"的概念，这两格换成来源信息
          isVideo
            ? info("来源", srcName)
            : info("本地片源", (row.local_count || 0) > 0 ? row.local_count + " 条" : "未入库"),
          isVideo
            ? info("可下载", "是（yt-dlp）")
            : info("来源站点", (row.local_sites || []).join("、") || "-"),
        ]),
        row.desc ? el("div", { class: "divider" }) : null,
        row.desc ? el("div", { class: "dim tiny", text: row.desc }) : null,
        el("div", { class: "divider" }),
        el(
          "div",
          { class: "row tight" },
          isVideo
            ? [
                videoDownloadButton(row),
                row.url
                  ? el("a", {
                      class: "btn sm ghost",
                      href: row.url,
                      target: "_blank",
                      rel: "noreferrer noopener",
                      text: "去 " + srcName + " 看",
                    })
                  : null,
              ]
            : [
                iconButton("搜索资源", "search", () => {
                  close();
                  onSearch(row.title);
                }, "sm primary"),
                iconButton("创建订阅", "star", () => {
                  subscribeFromTrending({
                    title: row.title,
                    media_type: row.media_type || "movie",
                    season: 1,
                  });
                }, "sm"),
                row.douban_url
                  ? el("a", {
                      class: "btn sm ghost",
                      href: row.douban_url,
                      target: "_blank",
                      rel: "noreferrer noopener",
                      text: "去豆瓣看",
                    })
                  : null,
              ]
        ),
      ]),
      true
    );
  }

  async function pageTrending() {
    shell(loading(), "热度排行", "当前最热（豆瓣 / Bilibili / YouTube）");

    // 分类与二级分区由后端下发，前端不写死；只在首次进页面时拉一次。
    // 拉失败不阻塞页面——用一份兜底分类，至少还能看电影榜。
    if (!store.discoverCategories) {
      try {
        const meta = await api("/trending/discover/categories");
        store.discoverCategories = meta.data.categories || [];
        store.biliPartitions = meta.data.bili_partitions || [];
        store.ytRegions = meta.data.yt_regions || [];
      } catch (error) {
        store.discoverCategories = [
          { key: "movie", label: "电影", kind: "media" },
          { key: "tv", label: "电视剧", kind: "media" },
          { key: "anime", label: "动漫", kind: "media" },
          { key: "show", label: "综艺", kind: "media" },
          { key: "bilibili", label: "Bilibili", kind: "video" },
          { key: "youtube", label: "YouTube", kind: "video" },
          { key: "bangumi", label: "新番", kind: "media" },
        ];
        store.biliPartitions = [{ key: "all", label: "全站" }];
        store.ytRegions = [{ key: "US", label: "美国" }];
      }
    }

    const listBox = el("div", {});
    const moreBox = el("div", { class: "board-more" });
    const meta = el("div", { class: "dim tiny" });

    /** 点「搜资源」→ 跳到资源搜索页。

        跳走之前把「当前滚动位置 + 已加载了多少条」记进 trendingState.restore，
        用户从搜索页切回榜单时按这份快照复原（本轮需求 1）。
    */
    const goSearch = (title) => {
      trendingState.restore = {
        scrollY: window.scrollY,
        // 记条数而不是页数：下拉过几页后必须把这些条目**先补回来**
        // 再滚动，否则页面高度不足，scrollTo 会滚不到目标位置。
        count: trendingState.items.length,
        cat: trendingState.discoverCat,
        biliPartition: trendingState.biliPartition,
        ytRegion: trendingState.ytRegion,
        view: trendingState.view,
      };
      searchState.keyword = title;
      searchState.items = [];
      go("search");
    };

    const renderList = () => {
      const data = {
        items: trendingState.items,
        label: trendingState.label,
        source: trendingState.source,
        kind: trendingState.kind,
        message: trendingState.message,
        count: trendingState.items.length,
      };
      if (trendingState.view === "calendar") {
        listBox.replaceChildren(bangumiCalendar(trendingState.calendar, goSearch));
        // 日历是"整周一次给全"，没有分页概念，别在下面挂一个永远点不动的加载更多
        moreBox.replaceChildren();
        return;
      }
      listBox.replaceChildren(
        trendingState.view === "board"
          ? discoverBoard(data, goSearch)
          : discoverTable(data, goSearch)
      );
      // 榜单换视图/换分类只重绘 listBox，不经过 shell()，这里也要回收一次
      abortDetachedPosters();

      // 加载更多：有更多才显示按钮，到底了给一句明确的「已到底」
      moreBox.replaceChildren();
      if (trendingState.loading) {
        moreBox.appendChild(el("div", { class: "dim tiny", text: "加载中…" }));
      } else if (trendingState.hasMore) {
        const btn = el("button", { class: "btn ghost" }, [
          icon("chevron-down", "sm"),
          el("span", { text: "加载更多（已显示 " + trendingState.items.length + " 条）" }),
        ]);
        btn.addEventListener("click", () => loadMore());
        moreBox.appendChild(btn);
      } else if (trendingState.items.length) {
        moreBox.appendChild(
          el("div", { class: "dim tiny", text: "已显示全部 " + trendingState.items.length + " 条" })
        );
      }
    };

    const fetchPage = async (offset) => {
      const cat = trendingState.discoverCat;
      let base;
      if (cat === "bilibili") {
        base = "/trending/bilibili/" + trendingState.biliPartition;
      } else if (cat === "youtube") {
        base = "/trending/youtube/" + trendingState.ytRegion;
      } else {
        base = "/trending/discover/" + cat;
      }
      const response = await api(
        base + "?limit=" + TRENDING_PAGE_SIZE + "&offset=" + offset
      );
      return response.data;
    };

    const updateMeta = () => {
      const srcName = SOURCE_LABEL[trendingState.source] || "豆瓣";
      const isVideo = trendingState.kind === "video";
      const hit = trendingState.items.filter((r) => (r.local_count || 0) > 0).length;
      meta.textContent =
        srcName + " · " + trendingState.label +
        " 已加载 " + trendingState.items.length + " 条" +
        // 视频站条目没有"本地片源"概念，别显示这句让人困惑
        (!isVideo && hit ? "，其中 " + hit + " 部你的站点已有片源" : "") +
        (trendingState.message ? " · " + trendingState.message : "");
    };

    const loadMore = async () => {
      if (trendingState.loading || !trendingState.hasMore) return;
      trendingState.loading = true;
      renderList();
      try {
        const data = await fetchPage(trendingState.offset);
        // 去重兜底：万一后端分页有重叠，不让同一条重复出现在画板里
        const key = (r) => r.title + "|" + (r.douban_id || r.bvid || r.video_id || "");
        const seen = new Set(trendingState.items.map(key));
        const fresh = (data.items || []).filter((r) => !seen.has(key(r)));
        trendingState.items = trendingState.items.concat(fresh);
        trendingState.offset += TRENDING_PAGE_SIZE;
        trendingState.hasMore = Boolean(data.has_more);
        trendingState.message = data.message || "";
      } catch (error) {
        toast(error.message, "err");
        trendingState.hasMore = false;
      } finally {
        trendingState.loading = false;
        updateMeta();
        renderList();
      }
    };

    /** 拉整周放送日历（新番页签的日历视图）。

        与摊平榜单走的是同一份后端缓存，切来切去不会重复打 Bangumi。
    */
    const loadCalendar = async () => {
      listBox.replaceChildren(loading());
      moreBox.replaceChildren();
      try {
        const response = await api("/trending/bangumi/calendar");
        trendingState.calendar = response.data || null;
        const total = (trendingState.calendar || {}).total || 0;
        meta.textContent =
          "Bangumi 放送日历 · 本周在播 " + total + " 部 · 今天是 " +
          ((trendingState.calendar || {}).today_label || "-") +
          (((trendingState.calendar || {}).message) ? " · " + trendingState.calendar.message : "");
        renderList();
      } catch (error) {
        trendingState.calendar = null;
        listBox.replaceChildren(el("div", { class: "pad" }, [emptyBox(error.message, "alert")]));
      }
    };

    // 首屏：重置游标后拉第一页
    const load = async () => {
      trendingState.items = [];
      trendingState.offset = 0;
      trendingState.hasMore = false;
      trendingState.loading = false;
      listBox.replaceChildren(loading());
      moreBox.replaceChildren();
      try {
        const data = await fetchPage(0);
        trendingState.items = data.items || [];
        trendingState.offset = TRENDING_PAGE_SIZE;
        trendingState.hasMore = Boolean(data.has_more);
        trendingState.label = data.label || "";
        trendingState.source = data.source || "";
        trendingState.kind = data.kind || "media";
        trendingState.message = data.message || "";
        updateMeta();
        renderList();
        await restoreScroll();
      } catch (error) {
        listBox.replaceChildren(el("div", { class: "pad" }, [emptyBox(error.message, "alert")]));
      }
    };

    /** 从资源搜索页切回来时，复原滚动位置。

        只有「分类/分区/视图都没变」才复原——用户如果切了页签，
        再把他滚到旧位置就是帮倒忙。
    */
    const restoreScroll = async () => {
      const snap = trendingState.restore;
      trendingState.restore = null;
      if (!snap) return;
      if (
        snap.cat !== trendingState.discoverCat ||
        snap.biliPartition !== trendingState.biliPartition ||
        snap.ytRegion !== trendingState.ytRegion ||
        snap.view !== trendingState.view
      ) {
        return;
      }
      // 把跳走前多加载的那几页补回来，否则页面不够高、滚不到原位置
      let guard = 0;
      while (
        trendingState.items.length < snap.count &&
        trendingState.hasMore &&
        guard < 10
      ) {
        guard += 1;
        await loadMore();
      }
      // 等一帧让浏览器完成布局，再滚——DOM 刚插入时高度还没算出来
      requestAnimationFrame(() => {
        window.scrollTo({ top: snap.scrollY, behavior: "auto" });
      });
    };

    // 只有「新番」页签才有日历视图——其他来源是热度榜，没有"周几更新"这个维度
    const viewOptions = [
      { value: "board", label: "画板" },
      { value: "list", label: "列表" },
    ];
    if (trendingState.discoverCat === "bangumi") {
      viewOptions.push({ value: "calendar", label: "日历" });
    }
    const views = segment(
      viewOptions,
      // 从新番切到别的页签时，日历视图不存在了，得退回画板，
      // 否则 renderList 会走进一个当前页签根本渲染不出来的分支，页面变空白
      trendingState.view === "calendar" && trendingState.discoverCat !== "bangumi"
        ? "board"
        : trendingState.view,
      (value) => {
        trendingState.view = value;
        if (value === "calendar") {
          loadCalendar();
          return;
        }
        renderList();
      }
    );

    const cat = trendingState.discoverCat;
    const isBili = cat === "bilibili";
    const isYt = cat === "youtube";
    //: 新番页签多一个「日历」视图（这周哪天更新），另两个视图与其他页签一致
    const isBangumi = cat === "bangumi";

    // 二级切换：B 站按分区、YouTube 按地区，两者互斥
    const subBar = isBili
      ? { label: "分区", list: store.biliPartitions || [{ key: "all", label: "全站" }],
          value: trendingState.biliPartition,
          onPick: (value) => { trendingState.biliPartition = value; pageTrending(); } }
      : isYt
      ? { label: "地区", list: store.ytRegions || [{ key: "US", label: "美国" }],
          value: trendingState.ytRegion,
          onPick: (value) => { trendingState.ytRegion = value; pageTrending(); } }
      : null;

    const filterBar = el("div", { class: "card" }, [
      el("div", { class: "row center" }, [
        el("div", { style: "flex:0 0 auto" }, [
          el("div", { class: "dim tiny", style: "margin-bottom:6px", text: "分类" }),
          segment(
            (store.discoverCategories || []).map((c) => ({ value: c.key, label: c.label })),
            trendingState.discoverCat,
            (value) => {
              trendingState.discoverCat = value;
              pageTrending();
            }
          ),
        ]),
        subBar
          ? el("div", { style: "flex:0 0 auto" }, [
              el("div", { class: "dim tiny", style: "margin-bottom:6px", text: subBar.label }),
              segment(
                subBar.list.map((p) => ({ value: p.key || p.value, label: p.label })),
                subBar.value,
                subBar.onPick
              ),
            ])
          : null,
        el("div", { style: "flex:0 0 auto" }, [
          el("div", { class: "dim tiny", style: "margin-bottom:6px", text: "视图" }),
          views,
        ]),
      ]),
      el("div", { class: "divider" }),
      meta,
    ]);

    const srcTag = SOURCE_LABEL[trendingState.source] ||
      (isYt ? "YouTube" : isBili ? "Bilibili" : isBangumi ? "Bangumi" : "豆瓣");
    const board = el("div", { class: "card flush" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [
          icon(isBili || isYt ? "video" : "flame", "sm"),
          el("span", { text: "当前最热" }),
        ]),
        el("span", { class: "tag brand", text: srcTag }),
      ]),
      listBox,
      moreBox,
    ]);

    shell(
      el("div", { class: "grid" }, [filterBar, board]),
      "热度排行",
      isBili || isYt
        ? srcTag + " 当前最热视频，可直接下载"
        : "豆瓣当前最热，可创建订阅或跳转资源搜索",
      [
        iconButton("刷新", "refresh", () =>
          trendingState.view === "calendar" ? loadCalendar() : load()
        ),
      ]
    );
    if (trendingState.view === "calendar" && isBangumi) {
      loadCalendar();
    } else {
      // 切走新番页签后日历视图不再存在，状态里也要跟着回退，
      // 否则下次再进新番会显示"日历"高亮但内容是画板
      if (trendingState.view === "calendar") trendingState.view = "board";
      load();
    }
  }

  // ---------------- 订阅追新 ----------------
  function subscribeForm() {
    modal(
      "新增订阅",
      [
        {
          key: "title",
          label: "片名",
          placeholder: "如：凡人修仙传",
          hint: "配置 TMDB 后会自动补全年份与总集数",
        },
        {
          key: "media_type",
          label: "类型",
          type: "select",
          value: "tv",
          options: [
            { value: "tv", label: "剧集" },
            { value: "movie", label: "电影" },
            { value: "anime", label: "动漫" },
          ],
        },
        { key: "season", label: "季", type: "number", value: 1 },
        {
          key: "total_episodes",
          label: "总集数（0 = 自动识别/持续追新）",
          type: "number",
          value: 0,
        },
        { key: "resolution", label: "分辨率过滤（可选）", placeholder: "如：2160p,1080p" },
        { key: "include", label: "必含关键词（可选）", placeholder: "如：中字" },
        { key: "exclude", label: "排除关键词（可选）", placeholder: "如：枪版,预告" },
        { key: "best_version", label: "只要最优版本（命中即停）", type: "checkbox", value: false },
        { key: "allow_pan", label: "允许网盘资源", type: "checkbox", value: true },
        { key: "allow_torrent", label: "允许 BT 资源", type: "checkbox", value: true },
      ],
      async (values) => {
        if (!values.title) throw new Error("片名不能为空");
        const payload = Object.assign({}, values, {
          season: values.season || 1,
          total_episodes: values.total_episodes || 0,
        });
        await api("/subscribes", { method: "POST", body: payload });
        toast("订阅已创建，稍后会自动搜索", "ok");
        pageSubscribes();
      },
      "创建订阅",
      { lead: "创建后由「订阅巡检」与「追新雷达」两个定时任务自动追更。" }
    );
  }

  /** 订阅页内嵌的定时任务快捷设置（与定时任务页共用编辑器）。 */
  function scheduleQuickCard(items) {
    const rows = items.filter((item) => item.key === "subscribe" || item.key === "radar");
    return el("div", { class: "card" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("clock", "sm"), el("span", { text: "追新定时任务" })]),
        iconButton("全部任务设置", "settings", () => go("schedules"), "sm ghost"),
      ]),
      el(
        "div",
        { class: "grid cols-2" },
        rows.map((row) =>
          el("div", { class: "card" }, [
            el("div", { class: "row center", style: "justify-content:space-between" }, [
              el("div", { style: "flex:1" }, [
                el("div", { class: "row tight center" }, [
                  icon(row.key === "radar" ? "radar" : "star", "sm"),
                  el("strong", { text: row.name }),
                  row.enabled
                    ? el("span", { class: "tag dot ok", text: "运行中" })
                    : el("span", { class: "tag dot warn", text: "已关闭" }),
                  row.customized ? el("span", { class: "tag brand", text: "已自定义" }) : null,
                ]),
                el("div", { class: "cell-sub", text: row.description }),
              ]),
            ]),
            el("div", { class: "divider" }),
            el("div", { class: "kv" }, [
              el("div", { class: "kv-item" }, [
                el("div", { class: "kv-label", text: "触发规则" }),
                el("div", { class: "mono", text: row.trigger === "cron" ? row.cron : "每 " + row.minutes + " 分钟" }),
              ]),
              el("div", { class: "kv-item" }, [
                el("div", { class: "kv-label", text: "下次执行" }),
                el("div", { text: fmtRelative(row.next_run_time) }),
              ]),
            ]),
            el("div", { class: "row tight", style: "margin-top:14px" }, [
              iconButton("修改周期", "edit", () => scheduleForm(row, pageSubscribes), "sm"),
              iconButton("立即执行", "play", () => runSchedule(row, pageSubscribes), "sm ghost"),
            ]),
          ])
        )
      ),
    ]);
  }

  async function pageSubscribes() {
    shell(loading(), "订阅追新", "自动跟踪剧集更新并下载入库");
    const [items, schedules] = await Promise.all([
      api("/subscribes?limit=500"),
      api("/schedules"),
    ]);

    const progressCell = (row) => {
      const done = (row.downloaded_episodes || []).length;
      const total = row.total_episodes || 0;
      const percent = total ? Math.min(100, Math.round((done / total) * 100)) : 0;
      return el("div", {}, [
        el("div", {
          class: "tiny",
          text: total ? done + " / " + total + " 集（" + percent + "%）" : done + " 集（持续追新）",
        }),
        total
          ? el("div", { class: "progress" + (percent >= 100 ? " done" : ""), style: "margin-top:5px" }, [
              el("i", { style: "width:" + percent + "%" }),
            ])
          : null,
      ]);
    };

    const actionsCell = (row) => {
      const search = el("button", { class: "btn sm" }, [
        icon("search", "sm"),
        el("span", { text: "搜索" }),
      ]);
      search.addEventListener("click", async () => {
        search.disabled = true;
        search.querySelector("span").textContent = "搜索中…";
        try {
          const result = await api("/subscribes/" + row.id + "/run", { method: "POST" });
          toast(
            "缺 " + result.missing.length + " 集，命中 " + result.matched +
              " 条，新增 " + result.downloads.length + " 个任务",
            "ok"
          );
          pageSubscribes();
        } catch (error) {
          toast(error.message, "err");
          search.disabled = false;
          search.querySelector("span").textContent = "搜索";
        }
      });

      const toggle = iconButton(
        row.status === "active" ? "暂停" : "启用",
        row.status === "active" ? "pause" : "play",
        async () => {
          try {
            await api("/subscribes/" + row.id, {
              method: "PATCH",
              body: { status: row.status === "active" ? "paused" : "active" },
            });
            pageSubscribes();
          } catch (error) {
            toast(error.message, "err");
          }
        },
        "sm"
      );

      const remove = iconButton("删除", "trash", async () => {
        if (!confirm("确定删除订阅《" + row.title + "》？")) return;
        try {
          await api("/subscribes/" + row.id, { method: "DELETE" });
          toast("已删除", "ok");
          pageSubscribes();
        } catch (error) {
          toast(error.message, "err");
        }
      }, "sm danger");

      // 洗版只对开了「最优版本」的订阅有意义，别的订阅不显示这个按钮免得误点
      const upgrade = row.best_version
        ? iconButton("洗版试算", "chart", async () => {
            try {
              const result = await api("/subscribes/" + row.id + "/upgrade", {
                method: "POST",
                body: { dry_run: true },
              });
              upgradeReport(row, result);
            } catch (error) {
              toast(error.message, "err");
            }
          }, "sm ghost")
        : null;

      return el("div", { class: "row tight" }, [search, upgrade, toggle, remove].filter(Boolean));
    };

    const content = el("div", { class: "card flush" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("star", "sm"), el("span", { text: "订阅列表（" + items.length + "）" })]),
      ]),
      table(
        [
          {
            title: "片名",
            render: (row) =>
              el("div", {}, [
                el("div", { class: "row tight center" }, [
                  icon(typeIcon(row.media_type), "sm"),
                  el("span", { text: row.title }),
                ]),
                el("div", {
                  class: "cell-sub",
                  text:
                    typeLabel(row.media_type) +
                    (row.year ? " · " + row.year : "") +
                    " · 第 " + row.season + " 季",
                }),
              ]),
          },
          { title: "进度", render: progressCell },
          { title: "状态", render: (row) => statusTag(row.status) },
          {
            title: "过滤",
            render: (row) => {
              const tags = [
                row.resolution,
                row.include ? "含:" + row.include : "",
                row.exclude ? "排:" + row.exclude : "",
              ].filter(Boolean);
              if (!tags.length) return el("span", { class: "dim tiny", text: "默认策略" });
              return el(
                "div",
                { class: "chips" },
                tags.map((text) => el("span", { class: "tag", text: text }))
              );
            },
          },
          {
            title: "最近检查",
            render: (row) =>
              el("div", {}, [
                el("div", { class: "tiny", text: fmtRelative(row.last_check_at) }),
                el("div", { class: "cell-sub", text: fmtTime(row.last_check_at) }),
              ]),
          },
          { title: "操作", render: actionsCell },
        ],
        items,
        "还没有订阅，点击右上角新增即可开始自动追新"
      ),
    ]);

    const runAll = el("button", { class: "btn" }, [
      icon("refresh", "sm"),
      el("span", { text: "巡检全部" }),
    ]);
    runAll.addEventListener("click", async () => {
      runAll.disabled = true;
      try {
        const result = await api("/subscribes/run-all", { method: "POST" });
        toast("巡检完成，新增 " + result.downloads + " 个任务", "ok");
        pageSubscribes();
      } catch (error) {
        toast(error.message, "err");
        runAll.disabled = false;
      }
    });

    const add = el("button", { class: "btn primary" }, [
      icon("plus", "sm"),
      el("span", { text: "新增订阅" }),
    ]);
    add.addEventListener("click", subscribeForm);

    shell(
      el("div", { class: "grid" }, [scheduleQuickCard(schedules.items), content]),
      "订阅追新",
      "共 " + items.length + " 个订阅 · 定时任务可在下方直接调整",
      [add, runAll]
    );
  }

  /** 洗版试算结果弹窗：先让用户看清「哪一集会被什么替换」再决定是否执行。 */
  function upgradeReport(row, result) {
    const candidates = result.candidates || [];
    const willUpgrade = candidates.filter((item) => item.upgrade);
    const body = el("div", { class: "grid" }, [
      el("div", { class: "kv" }, [
        el("div", { class: "kv-item" }, [
          el("div", { class: "kv-label", text: "可洗版" }),
          el("div", { text: willUpgrade.length + " 集" }),
        ]),
        el("div", { class: "kv-item" }, [
          el("div", { class: "kv-label", text: "已跳过" }),
          el("div", { text: (result.skipped || 0) + " 集" }),
        ]),
      ]),
      el("div", { class: "muted", text: result.message || "" }),
      table(
        [
          {
            title: "已入库文件",
            render: (item) =>
              el("div", { class: "truncate tiny mono dim", title: item.library_file || "", text: baseName(item.library_file) }),
          },
          { title: "集", class: "num", render: (item) => (item.episode ? "E" + item.episode : "整季") },
          { title: "现有评分", class: "num", render: (item) => Number(item.current_score || 0).toFixed(1) },
          { title: "候选评分", class: "num", render: (item) => Number(item.candidate_score || 0).toFixed(1) },
          {
            title: "候选资源",
            render: (item) =>
              el("div", { class: "truncate tiny", title: item.candidate || "", text: item.candidate || "-" }),
          },
          {
            title: "结论",
            render: (item) =>
              el("div", {}, [
                item.upgrade
                  ? el("span", { class: "tag dot ok", text: "值得替换" })
                  : el("span", { class: "tag dot warn", text: "保持现状" }),
                el("div", { class: "cell-sub", text: item.reason || "" }),
              ]),
          },
        ],
        candidates,
        "没有找到更优版本"
      ),
      willUpgrade.length
        ? iconButton("确认执行洗版（会替换已入库文件）", "check", async () => {
            try {
              const done = await api("/subscribes/" + row.id + "/upgrade", {
                method: "POST",
                body: { dry_run: false },
              });
              toast("已提交 " + (done.upgraded || 0) + " 个洗版下载任务", "ok");
              pageSubscribes();
            } catch (error) {
              toast(error.message, "err");
            }
          }, "primary")
        : null,
    ]);
    panelModal("洗版试算 · " + row.title, "只在评分提升足够时才替换；旧文件会在新文件确实入库后才删除。", body, true);
  }

  // ---------------- 定时任务设置 ----------------
  const CRON_PRESETS = [
    { label: "每天 04:00", value: "0 4 * * *" },
    { label: "每天 02:30", value: "30 2 * * *" },
    { label: "每 6 小时", value: "0 */6 * * *" },
    { label: "每周一 05:00", value: "0 5 * * 1" },
  ];

  const MINUTE_PRESETS = [5, 10, 15, 30, 60, 120, 360, 720, 1440];

  /** 定时任务编辑弹窗：间隔 / cron 两种触发方式。 */
  function scheduleForm(row, onDone) {
    modal(
      "定时设置：" + row.name,
      [
        { key: "enabled", label: "启用该定时任务", type: "checkbox", value: row.enabled },
        {
          key: "trigger",
          label: "触发方式",
          type: "select",
          value: row.trigger,
          options: [
            { value: "interval", label: "固定间隔（每 N 分钟）" },
            { value: "cron", label: "cron 表达式（指定时刻）" },
          ],
        },
        {
          key: "minutes",
          label: "间隔分钟（触发方式为固定间隔时生效）",
          type: "select",
          value: String(row.minutes),
          options: MINUTE_PRESETS.map((value) => ({
            value: String(value),
            label: value >= 60 ? value / 60 + " 小时（" + value + " 分钟）" : value + " 分钟",
          })),
        },
        {
          key: "cron",
          label: "cron 表达式（触发方式为 cron 时生效）",
          value: row.cron,
          placeholder: "分 时 日 月 周，如 0 4 * * *",
          hint: "常用：" + CRON_PRESETS.map((item) => item.label + "=" + item.value).join("｜"),
        },
      ],
      async (values) => {
        const payload = {
          enabled: values.enabled,
          trigger: values.trigger,
          minutes: Number(values.minutes),
          cron: values.cron,
        };
        const response = await api("/schedules/" + row.key, { method: "PUT", body: payload });
        const data = response.data;
        toast(
          data.enabled
            ? row.name + " 已更新：" + (data.trigger === "cron" ? "cron " + data.cron : "每 " + data.minutes + " 分钟")
            : row.name + " 已关闭",
          "ok"
        );
        if (onDone) onDone();
      },
      "保存并生效",
      {
        lead:
          "修改立即改期并写入数据库，重启后依然生效；默认值为 " +
          (row.default.trigger === "cron"
            ? "cron " + row.default.cron
            : "每 " + row.default.minutes + " 分钟"),
      }
    );
  }

  async function runSchedule(row, onDone) {
    try {
      const result = await api("/schedules/" + row.key + "/run", { method: "POST" });
      toast(result.message, "ok");
      if (onDone) setTimeout(onDone, 800);
    } catch (error) {
      toast(error.message, "err");
    }
  }

  async function pageSchedules() {
    shell(loading(), "定时任务", "追新与入库的自动化节奏");
    const [data, jobs] = await Promise.all([api("/schedules"), api("/system/jobs")]);

    const cards = data.items.map((row) =>
      el("div", { class: "card" }, [
        el("div", { class: "card-head" }, [
          el("h3", {}, [
            icon(
              { subscribe: "star", radar: "radar", download: "download", library: "library",
                pan_transfer: "cloud", pan_subscribe: "link", strm_sync: "film",
                scrape: "box", upgrade: "chart" }[row.key] || "clock",
              "sm"
            ),
            el("span", { text: row.name }),
          ]),
          el("div", { class: "row tight center" }, [
            row.enabled
              ? el("span", { class: "tag dot ok", text: "已启用" })
              : el("span", { class: "tag dot warn", text: "已关闭" }),
            row.customized ? el("span", { class: "tag brand", text: "已自定义" }) : null,
          ]),
        ]),
        el("div", { class: "muted", text: row.description }),
        el("div", { class: "divider" }),
        el("div", { class: "kv" }, [
          el("div", { class: "kv-item" }, [
            el("div", { class: "kv-label", text: "触发方式" }),
            el("div", { text: row.trigger === "cron" ? "cron 表达式" : "固定间隔" }),
          ]),
          el("div", { class: "kv-item" }, [
            el("div", { class: "kv-label", text: "当前规则" }),
            el("div", { class: "mono", text: row.trigger === "cron" ? row.cron : "每 " + row.minutes + " 分钟" }),
          ]),
          el("div", { class: "kv-item" }, [
            el("div", { class: "kv-label", text: "下次执行" }),
            el("div", {}, [
              el("div", { text: fmtRelative(row.next_run_time) }),
              el("div", { class: "cell-sub", text: fmtTime(row.next_run_time) }),
            ]),
          ]),
          el("div", { class: "kv-item" }, [
            el("div", { class: "kv-label", text: "默认值" }),
            el("div", {
              class: "mono dim",
              text: row.default.trigger === "cron" ? row.default.cron : "每 " + row.default.minutes + " 分钟",
            }),
          ]),
        ]),
        el("div", { class: "row tight", style: "margin-top:16px" }, [
          iconButton("修改周期", "edit", () => scheduleForm(row, pageSchedules), "sm primary"),
          iconButton("立即执行", "play", () => runSchedule(row, pageSchedules), "sm"),
          row.customized
            ? iconButton("恢复默认", "refresh", async () => {
                try {
                  await api("/schedules/" + row.key + "/reset", { method: "POST" });
                  toast(row.name + " 已恢复默认周期", "ok");
                  pageSchedules();
                } catch (error) {
                  toast(error.message, "err");
                }
              }, "sm ghost")
            : null,
        ]),
      ])
    );

    const pluginJobs = jobs.items.filter((item) => !item.builtin);
    const pluginCard = el("div", { class: "card flush" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("plugin", "sm"), el("span", { text: "插件任务（" + pluginJobs.length + "）" })]),
      ]),
      table(
        [
          { title: "任务", render: (row) => row.name },
          { title: "ID", render: (row) => el("span", { class: "mono dim", text: row.id }) },
          { title: "触发规则", render: (row) => el("span", { class: "mono dim tiny", text: row.trigger }) },
          { title: "下次执行", render: (row) => fmtRelative(row.next_run_time) },
          {
            title: "操作",
            render: (row) =>
              iconButton("立即执行", "play", async () => {
                try {
                  await api("/system/jobs/" + encodeURIComponent(row.id) + "/run", { method: "POST" });
                  toast("已触发", "ok");
                } catch (error) {
                  toast(error.message, "err");
                }
              }, "sm ghost"),
          },
        ],
        pluginJobs,
        "启用带定时任务的插件后会出现在这里"
      ),
    ]);

    shell(
      el("div", { class: "grid" }, [
        el("div", { class: "grid cols-2" }, cards),
        pluginCard,
      ]),
      "定时任务",
      data.running
        ? "调度器运行中 · 共 " + data.items.length + " 个内置任务"
        : "调度器已停止（CF_SCHEDULER_ENABLED=false）",
      [iconButton("刷新", "refresh", () => pageSchedules())]
    );
  }

  // ---------------- 下载任务 ----------------
  /** 视频网页下载：先解析确认，再入队。

      刻意做成"两步"而不是贴上地址直接下：解析一次很快（1~3 秒），
      但能让用户在下载前看清标题/作者/时长/画质，避免下错内容或
      下到一个几小时的直播回放。
  */
  function webVideoDialog() {
    const urlInput = el("input", {
      type: "text",
      placeholder: "粘贴视频页面地址，如 https://www.bilibili.com/video/BV...",
    });
    const info = el("div", { class: "dim tiny", text: "支持 B 站 / YouTube / 抖音 / TikTok 等 1700+ 站点的公开视频" });
    const preview = el("div", {});
    let parsed = null;

    const probe = async () => {
      const url = urlInput.value.trim();
      if (!url) {
        toast("请先填写地址");
        return;
      }
      preview.replaceChildren(loading());
      parsed = null;
      try {
        const response = await api("/downloads/webvideo/probe?url=" + encodeURIComponent(url), {
          method: "POST",
        });
        parsed = response.data;
        preview.replaceChildren(
          el("div", { class: "kv" }, [
            el("div", { class: "kv-item" }, [
              el("div", { class: "kv-label", text: "标题" }),
              el("div", { text: parsed.title || "-" }),
            ]),
            el("div", { class: "kv-item" }, [
              el("div", { class: "kv-label", text: "作者" }),
              el("div", { text: parsed.uploader || "-" }),
            ]),
            el("div", { class: "kv-item" }, [
              el("div", { class: "kv-label", text: "时长" }),
              el("div", { text: parsed.duration ? fmtDuration(parsed.duration) : "-" }),
            ]),
            el("div", { class: "kv-item" }, [
              el("div", { class: "kv-label", text: "站点" }),
              el("div", { text: parsed.site || "-" }),
            ]),
            el("div", { class: "kv-item" }, [
              el("div", { class: "kv-label", text: "可用画质" }),
              el("div", { text: (parsed.heights || []).map((h) => h + "p").join(" / ") || "-" }),
            ]),
          ])
        );
      } catch (error) {
        parsed = null;
        preview.replaceChildren(emptyBox(error.message, "alert"));
      }
    };

    const closePanel = panelModal(
      "下载网络视频",
      "只支持公开可访问的内容；会员/付费正片不在支持范围",
      el("div", {}, [
        el("div", { class: "field" }, [
          el("label", { text: "视频地址" }),
          urlInput,
          info,
        ]),
        el("div", { class: "row tight", style: "margin:12px 0" }, [
          iconButton("解析", "search", () => probe(), "sm primary"),
          iconButton("开始下载", "download", async () => {
            const url = urlInput.value.trim();
            if (!url) {
              toast("请先填写地址");
              return;
            }
            if (!parsed) {
              toast("请先点「解析」确认内容");
              return;
            }
            try {
              await api(
                "/downloads/webvideo?url=" + encodeURIComponent(url) +
                  (parsed.title ? "&title=" + encodeURIComponent(parsed.title) : ""),
                { method: "POST" }
              );
              toast("已加入下载队列", "ok");
              closePanel();
              pageDownloads();
            } catch (error) {
              toast(error.message, "err");
            }
          }, "sm"),
        ]),
        preview,
      ]),
      true
    );
  }

  async function pageDownloads() {
    shell(loading(), "下载任务", "任务状态与自动整理");
    const items = await api("/downloads?limit=300");

    const control = async (id, action) => {
      try {
        await api("/downloads/" + id + "/" + action, { method: "POST" });
        pageDownloads();
      } catch (error) {
        toast(error.message, "err");
      }
    };

    const actionsCell = (row) => {
      const buttons = [];
      if (row.status === "downloading") {
        buttons.push(iconButton("暂停", "pause", () => control(row.id, "pause"), "sm"));
      }
      if (row.status === "paused") {
        buttons.push(iconButton("继续", "play", () => control(row.id, "resume"), "sm"));
      }
      if (row.kind === "pan" && row.meta && row.meta.page_url) {
        const open = el("a", {
          class: "btn sm",
          href: row.meta.page_url,
          target: "_blank",
          rel: "noreferrer",
        }, [
          icon("cloud", "sm"),
          el("span", { text: row.meta.password ? "打开(码:" + row.meta.password + ")" : "打开网盘" }),
        ]);
        buttons.push(open);
      }
      buttons.push(
        iconButton("删除", "trash", async () => {
          if (!confirm("确定删除该任务？")) return;
          try {
            await api("/downloads/" + row.id, { method: "DELETE" });
            toast("已删除", "ok");
            pageDownloads();
          } catch (error) {
            toast(error.message, "err");
          }
        }, "sm danger")
      );
      return el("div", { class: "row tight" }, buttons);
    };

    const counts = { downloading: 0, pending: 0, done: 0 };
    items.forEach((row) => {
      if (row.status === "downloading") counts.downloading += 1;
      else if (row.status === "pending") counts.pending += 1;
      else if (row.status === "completed" || row.status === "transferred") counts.done += 1;
    });

    const content = el("div", { class: "grid" }, [
      el("div", { class: "grid cols-4" }, [
        statCard("下载中", counts.downloading, "实时同步下载器", "download"),
        statCard("等待中", counts.pending, "含网盘待转存", "clock"),
        statCard("已完成", counts.done, "含已入库", "check"),
        statCard("任务总数", items.length, "最多展示 300 条", "box"),
      ]),
      el("div", { class: "card flush" }, [
        el("div", { class: "card-head" }, [
          el("h3", {}, [icon("download", "sm"), el("span", { text: "任务列表" })]),
        ]),
        table(
          [
            {
              title: "任务",
              render: (row) =>
                el("div", {}, [
                  el("div", { class: "truncate", title: row.title, text: row.title }),
                  el("div", { class: "cell-sub", text:
                    kindLabel(row.kind) + " · " + (row.site || "-") + " · " + fmtSize(row.size) }),
                ]),
            },
            {
              title: "进度",
              render: (row) => {
                const percent = Math.round((row.progress || 0) * 100);
                return el("div", {}, [
                  el("div", { class: "progress" + (percent >= 100 ? " done" : "") }, [
                    el("i", { style: "width:" + percent + "%" }),
                  ]),
                  el("div", { class: "cell-sub", text: percent + "% · " + fmtSpeed(row.speed) }),
                ]);
              },
            },
            { title: "状态", render: (row) => statusTag(row.status) },
            {
              title: "季集",
              render: (row) =>
                row.episodes && row.episodes.length
                  ? "S" + pad2(row.season || 1) + "E" + row.episodes.join(",")
                  : "-",
            },
            {
              title: "创建时间",
              render: (row) =>
                el("div", {}, [
                  el("div", { class: "tiny", text: fmtRelative(row.created_at) }),
                  el("div", { class: "cell-sub", text: fmtTime(row.created_at) }),
                ]),
            },
            { title: "操作", render: actionsCell },
          ],
          items,
          "暂无下载任务"
        ),
      ]),
    ]);

    const sync = el("button", { class: "btn primary" }, [
      icon("refresh", "sm"),
      el("span", { text: "同步状态并整理" }),
    ]);
    sync.addEventListener("click", async () => {
      sync.disabled = true;
      sync.querySelector("span").textContent = "同步中…";
      try {
        const result = await api("/downloads/sync", { method: "POST" });
        toast("检查 " + result.checked + " 个，完成 " + result.completed + " 个", "ok");
        pageDownloads();
      } catch (error) {
        toast(error.message, "err");
        sync.disabled = false;
        sync.querySelector("span").textContent = "同步状态并整理";
      }
    });

    const actions = [sync];
    if (canDo("operator")) {
      actions.unshift(iconButton("下载网络视频", "video", () => webVideoDialog(), "ghost"));
    }
    shell(content, "下载任务", "共 " + items.length + " 个任务", actions);
  }

  // ---------------- 媒体库 ----------------
  function transferForm() {
    modal(
      "手动整理",
      [
        { key: "source", label: "源路径（文件或目录）", placeholder: "如：/downloads/tv/某剧集" },
        { key: "title", label: "指定片名（可选）", placeholder: "留空则自动识别" },
        { key: "season", label: "指定季（可选）", type: "number", value: "" },
        {
          key: "mode",
          label: "整理方式",
          type: "select",
          value: "",
          options: [
            { value: "", label: "使用全局配置" },
            { value: "link", label: "硬链接（推荐）" },
            { value: "copy", label: "复制" },
            { value: "move", label: "移动" },
            { value: "softlink", label: "软链接" },
            { value: "strm", label: "生成 STRM" },
          ],
        },
        { key: "dry_run", label: "试运行（只预览不执行）", type: "checkbox", value: true },
        { key: "overwrite", label: "覆盖已存在文件", type: "checkbox", value: false },
      ],
      async (values) => {
        if (!values.source) throw new Error("请填写源路径");
        const payload = Object.assign({}, values, {
          season: values.season || null,
          mode: values.mode || null,
          title: values.title || null,
        });
        const result = await api("/library/transfer", { method: "POST", body: payload });
        toast(
          "共 " + result.total + " 个文件，成功 " + result.succeeded + " 个",
          result.succeeded ? "ok" : "err"
        );
        pageLibrary();
      },
      "开始整理"
    );
  }

  async function pageLibrary() {
    shell(loading(), "媒体库", "已入库文件与手动整理");
    const [stats, files] = await Promise.all([
      api("/library/stats"),
      api("/library/files?limit=300"),
    ]);
    const data = stats.data;

    const content = el("div", { class: "grid" }, [
      el("div", { class: "grid cols-4" }, [
        statCard("文件总数", data.files, null, "library"),
        statCard("占用空间", fmtSize(data.size), null, "box"),
        statCard("剧集数", data.series, data.episodes + " 集", "tv"),
        statCard("电影数", data.movies, null, "film"),
      ]),
      el("div", { class: "card flush" }, [
        el("div", { class: "card-head" }, [
          el("h3", {}, [icon("inbox", "sm"), el("span", { text: "入库文件" })]),
        ]),
        table(
          [
            {
              title: "标题",
              render: (row) =>
                el("div", { class: "row tight center" }, [
                  icon(typeIcon(row.media_type), "sm"),
                  el("span", { text: row.title }),
                ]),
            },
            { title: "季集", render: (row) => seasonEpisode(row.season, row.episode) },
            { title: "画质", render: (row) => row.resolution || "-" },
            { title: "大小", class: "num", render: (row) => fmtSize(row.size) },
            {
              title: "路径",
              render: (row) =>
                el("div", { class: "truncate mono dim", title: row.path, text: row.path }),
            },
          ],
          files.items,
          "媒体库为空，可点击扫描媒体库导入已有文件"
        ),
      ]),
    ]);

    const scan = el("button", { class: "btn" }, [
      icon("refresh", "sm"),
      el("span", { text: "扫描媒体库" }),
    ]);
    scan.addEventListener("click", async () => {
      scan.disabled = true;
      scan.querySelector("span").textContent = "扫描中…";
      try {
        const result = await api("/library/scan", { method: "POST" });
        toast("扫描 " + result.scanned + " 个文件，新增 " + result.added + " 个", "ok");
        pageLibrary();
      } catch (error) {
        toast(error.message, "err");
        scan.disabled = false;
        scan.querySelector("span").textContent = "扫描媒体库";
      }
    });

    // 补刮按钮：只处理缺 NFO 的文件，可反复点，因此不做二次确认
    const scrape = el("button", { class: "btn" }, [
      icon("box", "sm"),
      el("span", { text: "补刮 NFO" }),
    ]);
    scrape.addEventListener("click", async () => {
      scrape.disabled = true;
      scrape.querySelector("span").textContent = "刮削中…";
      try {
        const result = await api("/library/scrape", {
          method: "POST",
          body: { limit: 200, overwrite: false },
        });
        const degraded = result.degraded ? "（其中 " + result.degraded + " 个因 TMDB 不可用写了最小 NFO）" : "";
        toast(
          "扫描 " + result.scanned + " 个 · 新刮 " + result.scraped + " 个 · 跳过 " + result.skipped + " 个" + degraded,
          "ok"
        );
      } catch (error) {
        toast(error.message, "err");
      }
      scrape.disabled = false;
      scrape.querySelector("span").textContent = "补刮 NFO";
    });

    shell(content, "媒体库", data.files + " 个文件 · " + fmtSize(data.size), [
      iconButton("手动整理", "plus", transferForm),
      scrape,
      scan,
      iconButton("刷新媒体服务器", "server", async () => {
        try {
          const result = await api("/library/refresh", { method: "POST" });
          toast("已通知 " + result.refreshed + " 个媒体服务器", "ok");
        } catch (error) {
          toast(error.message, "err");
        }
      }),
    ]);
  }

  // ---------------- 站点管理 ----------------
  //: 站点管理页负责的类别。
  //: **不含 downloader** —— 下载器从 v1.10.0 起搬到「设置」页，
  //: 用按 provider 定制的详细表单配置，不再挤在通用站点表单里手写 options JSON。
  const KIND_LABELS = {
    indexer: "BT 索引器",
    pan: "网盘搜索",
    mediaserver: "媒体服务器",
    notifier: "通知渠道",
    metadata: "元数据",
  };

  const KIND_ICONS = {
    indexer: "search",
    pan: "cloud",
    mediaserver: "server",
    notifier: "info",
    metadata: "box",
  };

  function optionsField(value) {
    return {
      key: "options",
      label: "高级选项 options（JSON，字段映射/接口路径）",
      type: "textarea",
      rows: 6,
      value: value ? JSON.stringify(value, null, 2) : "",
      hint: "自定义站点的接口路径与字段映射写在这里；留空表示使用默认值",
    };
  }

  function parseOptions(raw) {
    const text = String(raw || "").trim();
    if (!text) return {};
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch (error) {
      throw new Error("options 不是合法 JSON：" + error.message);
    }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("options 必须是 JSON 对象");
    }
    return parsed;
  }

  function siteForm(providers, preset) {
    const base = preset || {};
    modal(
      preset ? "添加站点：" + preset.name : "添加站点",
      [
        { key: "name", label: "站点名称", value: base.name || "", placeholder: "自定义显示名" },
        {
          key: "kind",
          label: "类别",
          type: "select",
          value: base.kind || "indexer",
          options: Object.keys(KIND_LABELS).map((value) => ({
            value: value,
            label: KIND_LABELS[value],
          })),
        },
        {
          key: "provider",
          label: "Provider",
          type: "select",
          value: base.provider || "torznab",
          options: providers.map((item) => ({
            value: item.name,
            label: item.display_name + "（" + item.kind + "）",
          })),
        },
        {
          key: "url",
          label: "地址 URL",
          value: base.url || "",
          placeholder: "例：https://web5.mukaku.com 或 Torznab 地址",
        },
        { key: "api_key", label: "API Key / Token（可选）" },
        { key: "username", label: "用户名（可选）" },
        { key: "password", label: "密码（可选）", type: "password" },
        { key: "cookie", label: "Cookie（可选）", type: "textarea" },
        {
          key: "priority",
          label: "优先级（越小越优先）",
          type: "number",
          value: base.priority || 50,
        },
        optionsField(base.options),
        { key: "enabled", label: "启用", type: "checkbox", value: false },
      ],
      async (values) => {
        if (!values.name || !values.provider) throw new Error("名称与 Provider 必填");
        const payload = Object.assign({}, values, {
          priority: values.priority || 50,
          options: parseOptions(values.options),
        });
        await api("/sites", { method: "POST", body: payload });
        toast("站点已添加，建议先点「测试」验证连通性", "ok");
        pageSites();
      },
      "保存站点"
    );
  }

  function siteEditForm(row) {
    modal(
      "编辑站点：" + row.name,
      [
        { key: "name", label: "站点名称", value: row.name },
        { key: "url", label: "地址 URL", value: row.url || "" },
        { key: "api_key", label: "API Key / Token（留空不改）" },
        { key: "cookie", label: "Cookie（留空不改）", type: "textarea" },
        { key: "priority", label: "优先级", type: "number", value: row.priority },
        { key: "timeout", label: "超时（秒）", type: "number", value: row.timeout },
        optionsField(row.options),
      ],
      async (values) => {
        const payload = {
          name: values.name,
          url: values.url,
          priority: values.priority || 50,
          timeout: values.timeout || 25,
          options: parseOptions(values.options),
        };
        if (values.api_key) payload.api_key = values.api_key;
        if (values.cookie) payload.cookie = values.cookie;
        await api("/sites/" + row.id, { method: "PATCH", body: payload });
        toast("站点已更新", "ok");
        pageSites();
      },
      "保存修改"
    );
  }

  async function presetPicker(providers) {
    const presets = await api("/sites/presets");
    const cards = presets.map((item) =>
      el("div", { class: "card", style: "margin-bottom:10px" }, [
        el("div", { class: "card-head", style: "margin-bottom:6px" }, [
          el("div", {}, [
            el("div", { class: "row tight center" }, [
              icon(KIND_ICONS[item.kind] || "box", "sm"),
              el("strong", { text: item.name }),
            ]),
            el("div", { class: "cell-sub mono", text: item.provider }),
          ]),
          item.verified
            ? el("span", { class: "tag dot ok", text: "已验证" })
            : el("span", { class: "tag dot warn", text: "需填映射" }),
        ]),
        el("div", { class: "muted tiny", style: "margin:6px 0 12px", text: item.description }),
        iconButton("使用此模板", "check", () => {
          document.getElementById("modal-root").innerHTML = "";
          siteForm(providers, item);
        }, "sm primary"),
      ])
    );

    panelModal(
      "选择站点模板",
      "模板已预填接口路径与字段映射，选择后可继续微调。",
      el("div", {}, cards),
      false
    );
  }

  async function discoverDialog() {
    const urlInput = el("input", {
      class: "input",
      placeholder: "导航站地址（留空使用内置：硬核指南）",
    });
    const mediaOnly = el("input", { type: "checkbox" });
    mediaOnly.checked = true;
    const listBox = el("div", {}, [
      el("div", { class: "muted", text: "点击「开始发现」抓取导航站收录的资源站清单。" }),
    ]);

    const scan = el("button", { class: "btn primary" }, [
      icon("radar", "sm"),
      el("span", { text: "开始发现" }),
    ]);
    scan.addEventListener("click", async () => {
      scan.disabled = true;
      scan.querySelector("span").textContent = "抓取中…";
      try {
        const params = new URLSearchParams();
        if (urlInput.value.trim()) params.set("url", urlInput.value.trim());
        params.set("media_only", mediaOnly.checked ? "true" : "false");
        const response = await api("/sites/discover?" + params.toString());
        const data = response.data;
        listBox.replaceChildren(
          el("div", { class: "muted tiny", style: "margin-bottom:8px", text:
            "发现 " + data.total + " 个站点。导航站只提供入口，需配置为自定义站点后才能搜索。" }),
          table(
            [
              { title: "名称", render: (row) => row.name },
              {
                title: "域名",
                render: (row) =>
                  el("a", {
                    class: "mono",
                    href: row.url,
                    target: "_blank",
                    rel: "noreferrer",
                    text: row.domain,
                  }),
              },
              {
                title: "标签",
                render: (row) =>
                  el("div", { class: "chips" },
                    (row.tags || []).slice(0, 3).map((tag) => el("span", { class: "tag", text: tag }))),
              },
              {
                title: "状态",
                render: (row) =>
                  row.already_added
                    ? el("span", { class: "tag dot ok", text: "已添加" })
                    : el("span", { class: "dim tiny", text: "未添加" }),
              },
            ],
            data.sites,
            "未发现候选站点"
          )
        );
        toast("发现 " + data.total + " 个站点", "ok");
      } catch (error) {
        toast(error.message, "err");
      }
      scan.disabled = false;
      scan.querySelector("span").textContent = "开始发现";
    });

    panelModal(
      "从导航站发现资源站点",
      null,
      el("div", {}, [
        el("div", { class: "field" }, [el("label", { text: "导航站地址" }), urlInput]),
        el("label", { class: "field-check" }, [mediaOnly, el("span", { text: "只显示影视相关站点" })]),
        el("div", { class: "row tight", style: "margin-bottom:12px" }, [scan]),
        listBox,
      ]),
      true
    );
  }

  /**
   * 社区站点清单（awesome-zhuiju-free）。
   *
   * 关键的一点：**上游的「可访问」不等于「搜得到」**。上游每天只 GET 首页看
   * 状态码，实测 20 个候选里 14 个标 reachable，但真能搜到可下载链接的只有 4 个。
   * 所以这里显示的是我们自己「真搜一次」的结论（probe），并且只有 searchable
   * 档能一键添加——否则等于把搜不到东西的站塞进搜索链路。
   */
  async function zhuijuDialog(onDone) {
    const listBox = el("div", {}, [el("div", { class: "muted", text: "正在读取清单…" })]);
    const summary = el("div", { class: "muted tiny", style: "margin-bottom:8px" });

    const PROBE_META = {
      searchable: { cls: "ok", label: "可搜到资源" },
      reachable_only: { cls: "warn", label: "能打开但搜不到" },
      blocked: { cls: "err", label: "被拦截" },
      unknown: { cls: "", label: "未探测" },
    };

    function render(data) {
      const stats = data.stats || {};
      const up = data.upstream || {};
      summary.replaceChildren(
        el("span", { text: "清单共 " + (stats.total || 0) + " 个候选：" }),
        el("span", { class: "tag dot ok", text: "可搜到 " + (stats.searchable || 0) }),
        el("span", { text: " " }),
        el("span", { class: "tag dot warn", text: "搜不到 " + (stats.reachable_only || 0) }),
        el("span", { text: " " }),
        el("span", { class: "tag dot err", text: "被拦截 " + (stats.blocked || 0) }),
        el("span", { class: "dim", text: "　上游更新 " + (data.upstream_updated_at || "-") +
          (data.probed_at ? "　本地探测 " + data.probed_at.slice(0, 16).replace("T", " ") : "") })
      );

      const rows = (data.entries || []).slice();
      // 可搜到的排最前：用户最该看的是这几个
      const order = { searchable: 0, reachable_only: 1, unknown: 2, blocked: 3 };
      rows.sort(function (a, b) {
        return (order[a.probe] || 9) - (order[b.probe] || 9);
      });

      listBox.replaceChildren(
        el("div", { class: "notice" }, [
          el("div", {}, [
            el("strong", { text: "数据来源：" }),
            el("a", { href: up.url || "#", target: "_blank", rel: "noreferrer",
                      text: up.repo || "awesome-zhuiju-free" }),
            el("span", { text: "（" + (up.license || "CC-BY-4.0") + "，社区维护）" }),
          ]),
          el("div", { class: "tiny dim", text:
            "⚠️ 上游只检测首页是否可访问；本页「状态」列是 CineFlow 自己真搜一次的结论。" +
            "只有「可搜到资源」的站点可一键添加。" }),
        ]),
        table(
          [
            { title: "名称", render: function (row) { return row.name; } },
            {
              title: "类型",
              render: function (row) {
                return el("span", { class: "tag", text: row.category_label || row.category });
              },
            },
            {
              title: "域名",
              render: function (row) {
                return el("a", { class: "mono", href: row.url, target: "_blank",
                                 rel: "noreferrer", text: row.domain });
              },
            },
            {
              title: "状态",
              render: function (row) {
                const meta = PROBE_META[row.probe] || PROBE_META.unknown;
                return el("div", {}, [
                  el("span", { class: "tag dot " + meta.cls, text: meta.label }),
                  row.probe_note
                    ? el("div", { class: "dim tiny", text: row.probe_note })
                    : null,
                ].filter(Boolean));
              },
            },
            {
              title: "操作",
              render: function (row) {
                if (row.already_added) {
                  return el("span", { class: "tag dot ok", text: "已添加" });
                }
                if (row.probe !== "searchable") {
                  return el("span", { class: "dim tiny", text: "不建议添加" });
                }
                const add = el("button", { class: "btn sm", text: "添加" });
                add.addEventListener("click", async function () {
                  add.disabled = true;
                  try {
                    await api("/sites/catalog/" + encodeURIComponent(row.id) + "/apply", {
                      method: "POST",
                    });
                    toast("已添加「" + row.name + "」，默认未启用", "ok");
                    row.already_added = true;
                    add.replaceWith(el("span", { class: "tag dot ok", text: "已添加" }));
                    if (onDone) onDone();
                  } catch (error) {
                    toast(error.message, "err");
                    add.disabled = false;
                  }
                });
                return add;
              },
            },
          ],
          rows,
          "清单为空（可点「同步清单」拉取）"
        )
      );
    }

    async function load(refresh) {
      listBox.replaceChildren(el("div", { class: "muted", text: refresh ? "正在同步上游清单…" : "正在读取清单…" }));
      try {
        const response = await api("/sites/catalog" + (refresh ? "?refresh=true" : ""));
        render(response.data);
        if (response.data.stale && response.data.error) {
          toast(response.data.error, "warn");
        }
      } catch (error) {
        listBox.replaceChildren(el("div", { class: "notice err", text: error.message }));
      }
    }

    const syncBtn = el("button", { class: "btn" }, [
      icon("refresh", "sm"),
      el("span", { text: "同步清单" }),
    ]);
    syncBtn.addEventListener("click", async function () {
      syncBtn.disabled = true;
      await load(true);
      syncBtn.disabled = false;
    });

    const probeBtn = el("button", { class: "btn primary" }, [
      icon("radar", "sm"),
      el("span", { text: "真搜一次探测" }),
    ]);
    probeBtn.addEventListener("click", async function () {
      probeBtn.disabled = true;
      probeBtn.querySelector("span").textContent = "探测中…（约 1~3 分钟）";
      try {
        const response = await api("/sites/catalog/probe", { method: "POST" });
        toast("探测完成：可搜到 " + (response.data.stats.searchable || 0) + " 个", "ok");
        await load(false);
      } catch (error) {
        toast(error.message, "err");
      }
      probeBtn.disabled = false;
      probeBtn.querySelector("span").textContent = "真搜一次探测";
    });

    panelModal(
      "社区站点清单（awesome-zhuiju-free）",
      null,
      el("div", {}, [
        el("div", { class: "row tight", style: "margin-bottom:12px" }, [syncBtn, probeBtn]),
        summary,
        listBox,
      ]),
      true
    );
    load(false);
  }

  /**
   * 内置 AI 分析站点：analyze → verify → apply 三步走。
   *
   * 刻意不做"一键分析并添加"：模型会编造字段，直接落库等于把一个
   * 搜不到东西的站点塞进搜索链路，之后每次搜索都白等它一次超时。
   * 让用户看到「置信度 + 依据 + 试搜命中几条」再决定。
   */
  async function aiSiteDialog(onDone) {
    const config = (await api("/ai/config")).data;

    const urlInput = el("input", { class: "input", placeholder: "https://example.com" });
    const kwInput = el("input", { class: "input", placeholder: "流浪地球" });
    kwInput.value = "流浪地球";
    const resultBox = el("div", {});

    // 没配好就别让用户白点：直接把缺什么、去哪配说清楚
    if (!config.ready) {
      resultBox.replaceChildren(
        el("div", { class: "notice warn" }, [
          icon("info", "sm"),
          el("span", { text: config.reason || "内置 AI 不可用" }),
        ])
      );
    }

    let suggestion = null;

    const applyButton = el("button", { class: "btn primary", disabled: true }, [
      icon("plus", "sm"),
      el("span", { text: "添加为站点" }),
    ]);
    applyButton.addEventListener("click", () => {
      if (!suggestion) return;
      modal(
        "添加 AI 分析出的站点",
        [
          { key: "name", label: "站点名称", required: true, value: "" },
          { key: "priority", label: "优先级", type: "number", value: "50" },
        ],
        async (values) => {
          await api("/ai/apply", {
            method: "POST",
            body: {
              suggestion: suggestion,
              name: values.name,
              priority: Number(values.priority) || 50,
              enabled: false,
            },
          });
          toast("已添加（默认未启用，请先测试连通性）", "ok");
          if (onDone) onDone();
        },
        "添加"
      );
    });

    const verifyButton = el("button", { class: "btn", disabled: true }, [
      icon("check", "sm"),
      el("span", { text: "试跑验证" }),
    ]);
    verifyButton.addEventListener("click", async () => {
      if (!suggestion) return;
      verifyButton.disabled = true;
      verifyButton.querySelector("span").textContent = "试搜中…";
      try {
        const result = (await api("/ai/verify", {
          method: "POST",
          body: { suggestion: suggestion, keyword: kwInput.value.trim() || "流浪地球" },
        })).data;
        toast(result.message, result.success ? "ok" : "err");
        // 试搜没结果也允许添加（可能只是这个关键词没有），但要让用户知情
        applyButton.disabled = false;
      } catch (error) {
        toast(error.message, "err");
      }
      verifyButton.disabled = false;
      verifyButton.querySelector("span").textContent = "试跑验证";
    });

    const analyze = el("button", { class: "btn primary" }, [
      icon("radar", "sm"),
      el("span", { text: "开始分析" }),
    ]);
    analyze.addEventListener("click", async () => {
      const url = urlInput.value.trim();
      if (!url) {
        toast("请先填写站点地址", "err");
        return;
      }
      analyze.disabled = true;
      analyze.querySelector("span").textContent = "分析中…";
      resultBox.replaceChildren(el("div", { class: "muted", text: "正在抓取页面并请求模型，可能需要十几秒…" }));
      try {
        const data = (await api("/ai/analyze", {
          method: "POST",
          body: { url: url, keyword: kwInput.value.trim() || "流浪地球" },
        })).data;
        suggestion = data;
        verifyButton.disabled = false;
        applyButton.disabled = true;
        const percent = Math.round((data.confidence || 0) * 100);
        resultBox.replaceChildren(
          el("div", { class: "stack" }, [
            el("div", { class: "row tight center" }, [
              el("span", { class: "tag brand", text: data.provider_label || data.provider }),
              el("span", {
                class: "tag " + (percent >= 70 ? "ok" : "warn"),
                text: "置信度 " + percent + "%",
              }),
              (data.probes_hit || []).length
                ? el("span", { class: "tag tiny", text: "命中探测：" + data.probes_hit.join("、") })
                : null,
            ]),
            data.reason ? el("div", { class: "muted", text: "依据：" + data.reason }) : null,
            data.notes ? el("div", { class: "muted tiny", text: data.notes }) : null,
            el("div", { class: "muted tiny", text: "字段配置（可添加后再微调）：" }),
            el("pre", { class: "mono tiny pre-wrap", text: JSON.stringify(data.options || {}, null, 2) }),
            el("div", { class: "notice" }, [
              icon("info", "sm"),
              el("span", { text: "AI 只给建议。请先「试跑验证」看能不能真搜到结果，再决定是否添加。" }),
            ]),
          ])
        );
      } catch (error) {
        resultBox.replaceChildren(
          el("div", { class: "notice warn" }, [icon("info", "sm"), el("span", { text: error.message })])
        );
      }
      analyze.disabled = false;
      analyze.querySelector("span").textContent = "开始分析";
    });

    panelModal(
      "AI 分析站点接入方式",
      config.ready
        ? "模型：" + config.model + "（" + config.base_url + "）"
        : "需先到「设置 → 内置 AI」配置模型与密钥",
      el("div", {}, [
        el("div", { class: "field" }, [el("label", { text: "站点地址" }), urlInput]),
        el("div", { class: "field" }, [
          el("label", { text: "试探关键词" }),
          kwInput,
          el("div", { class: "muted tiny", text: "用来抓一页搜索结果给模型看结构，建议用一部热门片名" }),
        ]),
        el("div", { class: "row tight", style: "margin-bottom:12px" }, [analyze, verifyButton, applyButton]),
        resultBox,
      ]),
      true
    );
  }

  async function pageSites() {
    shell(loading(), "站点管理", "索引器、盘搜、下载器、媒体服务器与通知");
    const [allSites, allProviders] = await Promise.all([
      api("/sites"),
      api("/sites/providers"),
    ]);
    // 下载器已搬到设置页，这里必须一并从**计数**和**Provider 下拉**里排掉，
    // 否则页面会显示"共 8 个配置"却只列出 6 个，且能从这里新建出一个
    // 没法在本页编辑的下载器。
    const sites = allSites.filter((item) => item.kind !== "downloader");
    const providers = allProviders.filter((item) => item.kind !== "downloader");

    const actionsCell = (row) => {
      const test = el("button", { class: "btn sm" }, [
        icon("check", "sm"),
        el("span", { text: "测试" }),
      ]);
      test.addEventListener("click", async () => {
        test.disabled = true;
        test.querySelector("span").textContent = "检测中…";
        try {
          const result = await api("/sites/" + row.id + "/test", { method: "POST" });
          toast(row.name + "：" + result.message, result.success ? "ok" : "err");
          pageSites();
        } catch (error) {
          toast(error.message, "err");
          test.disabled = false;
          test.querySelector("span").textContent = "测试";
        }
      });

      const toggle = iconButton(
        row.enabled ? "禁用" : "启用",
        row.enabled ? "pause" : "play",
        async () => {
          try {
            await api("/sites/" + row.id, {
              method: "PATCH",
              body: { enabled: !row.enabled },
            });
            pageSites();
          } catch (error) {
            toast(error.message, "err");
          }
        },
        "sm"
      );

      const edit = iconButton("编辑", "edit", () => siteEditForm(row), "sm ghost");

      const remove = iconButton("删除", "trash", async () => {
        if (!confirm("确定删除站点 " + row.name + "？")) return;
        try {
          await api("/sites/" + row.id, { method: "DELETE" });
          toast("已删除", "ok");
          pageSites();
        } catch (error) {
          toast(error.message, "err");
        }
      }, "sm danger");

      return el("div", { class: "row tight" }, [test, toggle, edit, remove]);
    };

    const enabledCount = sites.filter((item) => item.enabled).length;

    const groups = Object.keys(KIND_LABELS)
      .map((kind) => {
        const rows = sites.filter((item) => item.kind === kind);
        if (!rows.length) return null;
        return el("div", { class: "card flush" }, [
          el("div", { class: "card-head" }, [
            el("h3", {}, [
              icon(KIND_ICONS[kind], "sm"),
              el("span", { text: KIND_LABELS[kind] + "（" + rows.length + "）" }),
            ]),
            el("span", { class: "tag", text: "已启用 " + rows.filter((item) => item.enabled).length }),
          ]),
          table(
            [
              {
                title: "名称",
                render: (row) =>
                  el("div", {}, [
                    el("div", { text: row.name }),
                    el("div", { class: "cell-sub mono", text: row.provider }),
                  ]),
              },
              {
                title: "地址",
                render: (row) =>
                  el("div", {
                    class: "truncate mono dim",
                    title: row.url,
                    text: row.url || "-",
                  }),
              },
              {
                title: "状态",
                render: (row) =>
                  el("span", {
                    class: "tag dot " + (row.enabled ? "ok" : ""),
                    text: row.enabled ? "已启用" : "已禁用",
                  }),
              },
              { title: "优先级", class: "num", key: "priority" },
              {
                title: "最近检测",
                render: (row) =>
                  el("div", { class: "tiny dim", text: row.last_status || "未检测" }),
              },
              { title: "操作", render: actionsCell },
            ],
            rows
          ),
        ]);
      })
      .filter(Boolean);

    shell(
      el(
        "div",
        { class: "grid" },
        groups.length
          ? groups
          : [el("div", { class: "card" }, [emptyBox("还没有配置站点", "settings")])]
      ),
      "站点管理",
      "共 " + sites.length + " 个配置 · 已启用 " + enabledCount + " 个 · 下载器请到「设置」页配置",
      [
        iconButton("实测 BT 站", "flame", () => catalogImportDialog({
          title: "实测可用的 BT / 磁力站点",
          hint: "以下站点均已当场搜出真实资源，实测产出与已知缺陷都列在下方。请先看清缺陷再导入。",
          listPath: "/sites/bt-catalog",
          importPath: "/sites/bt-catalog/import",
          onDone: pageSites,
        }), "primary"),
        iconButton("接入 Jackett", "link", () => jackettDialog(pageSites), "primary"),
        iconButton("发现站点", "radar", () => discoverDialog()),
        iconButton("社区清单", "compass", () => zhuijuDialog(pageSites)),
        iconButton("AI 分析站点", "sparkles", () => aiSiteDialog(pageSites)),
        iconButton("从模板添加", "box", () => presetPicker(providers)),
        iconButton("新增站点", "plus", () => siteForm(providers), "primary"),
        iconButton("下载器设置", "download", () => go("settings"), "ghost"),
      ]
    );
  }

  /**
   * Jackett / Prowlarr 批量接入对话框。
   *
   * 设计意图：用户心里的操作是「我 Jackett 里已经配好一堆站了，拿过来」，
   * 而不是逐个手工拼 indexers/<id>/results/torznab 地址（20 个站要填 20 次）。
   * 所以这里是「填一次地址+Key → 勾选 → 批量导入」三步。
   */
  function jackettDialog(onDone) {
    const url = el("input", { class: "input", placeholder: "http://127.0.0.1:9117" });
    const key = el("input", { class: "input", placeholder: "Jackett 界面右上角的 API Key" });
    url.value = localStorage.getItem("cf_jackett_url") || "http://127.0.0.1:9117";
    key.value = localStorage.getItem("cf_jackett_key") || "";

    const listBox = el("div", {});
    const picked = {};
    let indexers = [];

    const importBtn = el("button", { class: "btn primary" }, [
      icon("download", "sm"),
      el("span", { text: "导入所选" }),
    ]);
    importBtn.disabled = true;

    const refreshImportBtn = () => {
      const n = Object.keys(picked).filter(function (k) { return picked[k]; }).length;
      importBtn.disabled = !n;
      importBtn.querySelector("span").textContent = n ? "导入所选 " + n + " 个" : "导入所选";
    };

    const renderList = () => {
      if (!indexers.length) {
        listBox.replaceChildren(el("div", { class: "muted", text: "先填地址与 API Key，再点「读取索引器」" }));
        return;
      }
      const allBtn = el("button", { class: "btn sm ghost" }, [el("span", { text: "全选可导入" })]);
      allBtn.addEventListener("click", function () {
        indexers.forEach(function (item) {
          if (!item.already_added) picked[item.id] = true;
        });
        renderList();
      });
      const noneBtn = el("button", { class: "btn sm ghost" }, [el("span", { text: "清空" })]);
      noneBtn.addEventListener("click", function () {
        Object.keys(picked).forEach(function (k) { delete picked[k]; });
        renderList();
      });

      listBox.replaceChildren(
        el("div", { class: "row tight", style: "margin-bottom:8px" }, [
          el("span", { class: "tag", text: "共 " + indexers.length + " 个" }),
          allBtn,
          noneBtn,
        ]),
        table(
          [
            {
              title: "",
              render: function (row) {
                const box = el("input", { type: "checkbox" });
                box.checked = !!picked[row.id];
                box.disabled = !!row.already_added;
                box.addEventListener("change", function () {
                  picked[row.id] = box.checked;
                  refreshImportBtn();
                });
                return box;
              },
            },
            {
              title: "索引器",
              render: function (row) {
                return el("div", {}, [
                  el("div", { class: "row tight center" }, [
                    el("div", { text: row.name }),
                    row.already_added ? el("span", { class: "tag ok", text: "已导入" }) : null,
                    row.type ? el("span", { class: "tag", text: row.type }) : null,
                  ]),
                  el("div", { class: "cell-sub", text: row.categories.join(" · ") || row.description || "" }),
                ]);
              },
            },
            {
              title: "测试",
              render: function (row) {
                const btn = el("button", { class: "btn sm ghost" }, [el("span", { text: "测试" })]);
                btn.addEventListener("click", async function () {
                  btn.disabled = true;
                  btn.querySelector("span").textContent = "测试中…";
                  try {
                    const res = await api("/sites/jackett/test", {
                      method: "POST",
                      body: { url: url.value.trim(), api_key: key.value.trim(), indexer_id: row.id },
                    });
                    toast(row.name + "：" + res.message, res.success ? "ok" : "err");
                  } catch (error) {
                    toast(error.message, "err");
                  }
                  btn.disabled = false;
                  btn.querySelector("span").textContent = "测试";
                });
                return btn;
              },
            },
          ],
          indexers,
          "没有已配置的索引器"
        )
      );
      refreshImportBtn();
    };

    const loadBtn = el("button", { class: "btn" }, [
      icon("refresh", "sm"),
      el("span", { text: "读取索引器" }),
    ]);
    loadBtn.addEventListener("click", async function () {
      loadBtn.disabled = true;
      loadBtn.querySelector("span").textContent = "读取中…";
      listBox.replaceChildren(el("div", { class: "muted", text: "正在读取 Jackett 索引器…" }));
      try {
        const res = await api("/sites/jackett/indexers", {
          method: "POST",
          body: { url: url.value.trim(), api_key: key.value.trim() },
        });
        indexers = (res.data && res.data.items) || [];
        if (!res.success) {
          // 失败原因要原样显示：连不上 / Key 错 / 一个站都没配，
          // 三种情况的下一步动作完全不同
          listBox.replaceChildren(el("div", { class: "notice err", text: res.data.message }));
        } else {
          localStorage.setItem("cf_jackett_url", url.value.trim());
          localStorage.setItem("cf_jackett_key", key.value.trim());
          renderList();
          toast(res.data.message, "ok");
        }
      } catch (error) {
        listBox.replaceChildren(el("div", { class: "notice err", text: error.message }));
      }
      loadBtn.disabled = false;
      loadBtn.querySelector("span").textContent = "读取索引器";
    });

    importBtn.addEventListener("click", async function () {
      const ids = Object.keys(picked).filter(function (k) { return picked[k]; });
      if (!ids.length) return;
      importBtn.disabled = true;
      importBtn.querySelector("span").textContent = "导入中…";
      try {
        const res = await api("/sites/jackett/import", {
          method: "POST",
          body: { url: url.value.trim(), api_key: key.value.trim(), indexer_ids: ids },
        });
        toast(res.message, res.created_count ? "ok" : "warn");
        if (res.skipped && res.skipped.length) {
          res.skipped.forEach(function (s) { toast(s.id + "：" + s.reason, "warn"); });
        }
        if (onDone) onDone();
      } catch (error) {
        toast(error.message, "err");
      }
      importBtn.disabled = false;
      refreshImportBtn();
    });

    renderList();
    panelModal(
      "从 Jackett / Prowlarr 导入站点",
      "Jackett 已经帮你维护了几百个站点的适配。填一次地址与 API Key，把里面配好的索引器整批拿过来即可。",
      el("div", {}, [
        el("div", { class: "grid cols-2" }, [
          el("label", { class: "field" }, [el("span", { text: "Jackett 地址" }), url]),
          el("label", { class: "field" }, [el("span", { text: "API Key" }), key]),
        ]),
        el("div", { class: "notice", style: "margin:10px 0" }, [
          el("div", { text: "提示：CineFlow 跑在 Docker 里时，127.0.0.1 指的是容器自己，要填宿主机 IP（或用 host 网络）。" }),
        ]),
        el("div", { class: "row tight", style: "margin-bottom:12px" }, [loadBtn, importBtn]),
        listBox,
      ]),
      true
    );
  }

  // ---------------- 插件 ----------------
  async function pagePlugins() {
    shell(loading(), "插件", "扩展能力，无需修改核心代码");
    const data = await api("/plugins");

    const cards = data.items.map((item) => {
      const toggle = el("button", { class: "btn sm " + (item.enabled ? "" : "primary") }, [
        icon(item.enabled ? "pause" : "play", "sm"),
        el("span", { text: item.enabled ? "停用" : "启用" }),
      ]);
      toggle.addEventListener("click", async () => {
        toggle.disabled = true;
        try {
          await api("/plugins/" + item.id + "/" + (item.enabled ? "disable" : "enable"), {
            method: "POST",
          });
          toast(item.enabled ? "已停用" : "已启用", "ok");
          pagePlugins();
        } catch (error) {
          toast(error.message, "err");
          toggle.disabled = false;
        }
      });

      const configButton = iconButton("配置", "settings", () => {
        const schema = item.config_schema || [];
        if (!schema.length) {
          toast("该插件无可配置项", "warn");
          return;
        }
        const current = item.config || {};
        const fields = schema.map((entry) => {
          const value =
            current[entry.key] !== undefined ? current[entry.key] : entry.default;
          return {
            key: entry.key,
            label: entry.label || entry.key,
            type: entry.type || "text",
            value: value,
            placeholder: entry.placeholder || "",
            hint: entry.hint || "",
            options: entry.options || [],
          };
        });
        modal("配置插件：" + item.name, fields, async (values) => {
          const payload = Object.assign({}, current, values, { enabled: true });
          await api("/plugins/" + item.id + "/config", {
            method: "PUT",
            body: { config: payload },
          });
          toast("配置已保存", "ok");
          pagePlugins();
        }, "保存");
      }, "sm ghost");

      const actionButtons = (item.actions || []).map((action) => {
        const button = el("button", { class: "btn sm" }, [
          icon("play", "sm"),
          el("span", { text: action }),
        ]);
        button.addEventListener("click", async () => {
          button.disabled = true;
          try {
            const result = await api("/plugins/" + item.id + "/run", {
              method: "POST",
              body: { action: action, params: {} },
            });
            toast(item.name + " · " + action + " 执行完成", "ok");
            const detail = result && result.result;
            if (detail && detail.body) {
              panelModal(
                item.name + " · " + action,
                null,
                el("pre", { class: "logs", style: "height:auto;max-height:420px", text: detail.body })
              );
            }
          } catch (error) {
            toast(error.message, "err");
          } finally {
            button.disabled = false;
          }
        });
        return button;
      });

      return el("div", { class: "card plugin-card" }, [
        el("div", { class: "plugin-top" }, [
          el("div", { style: "flex:1" }, [
            el("div", { class: "plugin-title" }, [
              icon("plugin", "sm"),
              el("span", { text: item.name }),
              el("span", { class: "tag", text: "v" + item.version }),
              item.enabled ? el("span", { class: "tag dot ok", text: "已启用" }) : null,
            ]),
            el("div", { class: "muted tiny", style: "margin-top:6px", text: item.description || "无描述" }),
            item.author
              ? el("div", { class: "dim tiny", style: "margin-top:4px", text: "作者：" + item.author })
              : null,
            item.last_error
              ? el("div", { class: "tag err", style: "margin-top:8px", text: item.last_error })
              : null,
          ]),
        ]),
        el("div", { class: "row tight" }, [toggle, configButton, ...actionButtons]),
      ]);
    });

    shell(
      el(
        "div",
        { class: "grid cols-2" },
        cards.length
          ? cards
          : [
              el("div", { class: "card" }, [
                emptyBox("plugins/ 目录下暂无插件，放入插件目录后刷新即可。", "plugin"),
              ]),
            ]
      ),
      "插件",
      "共 " + data.total + " 个插件",
      [iconButton("刷新", "refresh", () => pagePlugins())]
    );
  }

  // ---------------- 运行日志 ----------------
  const logState = { level: "" };

  // ---------------- 更新日志 ----------------
  //: 展开的版本号集合。默认只展开最新一版——历史版本内容很长，
  //: 全展开会让页面几千像素高，找不到重点。
  const changelogState = { open: null, filter: "" };

  //: 分组名 → 标签配色，让「新增/修复」一眼可分。
  const CHANGE_TONES = {
    新增: "ok",
    变更: "brand",
    修复: "warn",
    文档: "",
    门禁数字: "",
    更新内容: "brand",
  };

  async function pageChangelog() {
    shell(loading(), "更新日志", "每个版本改了什么");
    const data = await api("/system/changelog");
    const items = data.items || [];
    if (changelogState.open === null && items.length) {
      changelogState.open = items[0].version;
    }

    const keyword = changelogState.filter.trim().toLowerCase();
    const matched = keyword
      ? items.filter((row) => JSON.stringify(row).toLowerCase().indexOf(keyword) >= 0)
      : items;

    const sectionBlock = (section) =>
      el("div", { class: "chg-section" }, [
        el("div", { class: "chg-section-head" }, [
          el("span", {
            class: "tag " + (CHANGE_TONES[section.name] || ""),
            text: section.name,
          }),
          el("span", { class: "dim tiny", text: section.items.length + " 项" }),
        ]),
        el(
          "ul",
          { class: "chg-list" },
          section.items.map((item) =>
            el("li", {}, [
              el("div", { class: "chg-item-title", text: item.title }),
              item.points.length
                ? el(
                    "ul",
                    { class: "chg-points" },
                    item.points.map((point) => el("li", { text: point }))
                  )
                : null,
            ])
          )
        ),
        section.notes.length
          ? el("div", { class: "chg-notes mono tiny dim" }, section.notes.map((line) =>
              el("div", { text: line })
            ))
          : null,
      ]);

    const releaseCard = (row) => {
      const isCurrent = row.version === data.current;
      const open = changelogState.open === row.version;
      const head = el("div", { class: "chg-head" }, [
        el("div", { class: "chg-head-main" }, [
          el("span", { class: "chg-version", text: "v" + row.version }),
          isCurrent ? el("span", { class: "tag dot ok", text: "当前版本" }) : null,
          el("span", { class: "chg-title", text: row.title }),
        ]),
        el("div", { class: "row tight center" }, [
          el("span", { class: "dim tiny", text: row.date }),
          el("span", { class: "dim tiny", text: row.item_count + " 项改动" }),
          icon(open ? "close" : "plus", "sm"),
        ]),
      ]);
      head.addEventListener("click", () => {
        changelogState.open = open ? "" : row.version;
        pageChangelog();
      });

      return el("div", { class: "card chg-card" + (open ? " open" : "") }, [
        head,
        open
          ? el("div", { class: "chg-body" }, [
              row.summary
                ? el("div", { class: "chg-summary dim", text: row.summary })
                : null,
              ...row.sections.map(sectionBlock),
            ])
          : null,
      ]);
    };

    const search = el("input", {
      class: "input",
      placeholder: "搜索改动内容…",
      value: changelogState.filter,
    });
    search.addEventListener("input", () => {
      changelogState.filter = search.value;
      // 搜索时全部展开，否则命中内容藏在折叠里看不见
      changelogState.open = search.value.trim() ? "*" : changelogState.open;
      pageChangelog();
    });

    // filter=="*" 表示"全部展开"
    const cards = matched.map((row) =>
      changelogState.open === "*"
        ? (function () {
            const saved = changelogState.open;
            changelogState.open = row.version;
            const node = releaseCard(row);
            changelogState.open = saved;
            return node;
          })()
        : releaseCard(row)
    );

    shell(
      el("div", { class: "grid" }, [
        el("div", { class: "card compact" }, [
          el("div", { class: "status-strip" }, [
            el("div", { class: "status-title" }, [
              icon("history", "sm"),
              el("span", { text: "版本历史" }),
            ]),
            el("div", { class: "status-item" }, [
              el("span", { class: "status-label", text: "当前" }),
              el("span", { class: "tag dot ok", text: "v" + data.current }),
            ]),
            el("div", { class: "status-item" }, [
              el("span", { class: "status-label", text: "累计版本" }),
              el("span", { class: "tag", text: String(data.total) }),
            ]),
            el("div", { class: "chg-search" }, [search]),
          ]),
        ]),
        cards.length
          ? el("div", { class: "grid" }, cards)
          : emptyBox("没有匹配「" + changelogState.filter + "」的改动", "history"),
      ]),
      "更新日志",
      data.total + " 个版本 · 数据来自 docs/08-变更日志.md",
      [
        iconButton("检查更新", "refresh", () => checkUpdate(false), "primary"),
        iconButton("全部展开", "layers", () => {
          changelogState.open = changelogState.open === "*" ? "" : "*";
          pageChangelog();
        }),
      ]
    );
  }

  async function pageLogs() {
    shell(loading(), "运行日志", "调度任务与最近日志");
    const [logs, jobs] = await Promise.all([
      api("/system/logs?limit=500" + (logState.level ? "&level=" + logState.level : "")),
      api("/system/jobs"),
    ]);

    const box = el(
      "div",
      { class: "logs" },
      logs.items.length
        ? logs.items.map((item) =>
            el("div", {
              class: "log-line log-" + item.level,
              text: item.time + " [" + item.level + "] " + item.logger + " - " + item.message,
            })
          )
        : [el("div", { class: "dim", text: "暂无该级别日志" })]
    );

    const jobsCard = el("div", { class: "card flush" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("clock", "sm"), el("span", { text: "调度任务" })]),
        iconButton("任务设置", "settings", () => go("schedules"), "sm ghost"),
      ]),
      table(
        [
          {
            title: "任务",
            render: (row) =>
              el("div", {}, [
                el("div", { text: row.name }),
                el("div", { class: "cell-sub mono", text: row.id }),
              ]),
          },
          {
            title: "类型",
            render: (row) =>
              row.builtin
                ? el("span", { class: "tag brand", text: "内置" })
                : el("span", { class: "tag", text: "插件" }),
          },
          {
            title: "触发规则",
            render: (row) => el("span", { class: "mono dim tiny", text: row.trigger }),
          },
          {
            title: "下次执行",
            render: (row) =>
              el("div", {}, [
                el("div", { class: "tiny", text: fmtRelative(row.next_run_time) }),
                el("div", { class: "cell-sub", text: fmtTime(row.next_run_time) }),
              ]),
          },
          {
            title: "操作",
            render: (row) =>
              iconButton("立即执行", "play", async () => {
                try {
                  await api("/system/jobs/" + encodeURIComponent(row.id) + "/run", {
                    method: "POST",
                  });
                  toast("已触发", "ok");
                } catch (error) {
                  toast(error.message, "err");
                }
              }, "sm ghost"),
          },
        ],
        jobs.items,
        "调度器未启动或暂无任务"
      ),
    ]);

    const logCard = el("div", { class: "card" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("logs", "sm"), el("span", { text: "日志" })]),
        segment(
          [
            { value: "", label: "全部" },
            { value: "INFO", label: "INFO" },
            { value: "WARNING", label: "WARN" },
            { value: "ERROR", label: "ERROR" },
          ],
          logState.level,
          (value) => {
            logState.level = value;
            pageLogs();
          }
        ),
      ]),
      box,
    ]);

    shell(
      el("div", { class: "grid" }, [jobsCard, logCard]),
      "运行日志",
      logs.total + " 条",
      [
        iconButton("刷新", "refresh", () => pageLogs()),
        iconButton("测试通知", "info", async () => {
          try {
            const result = await api("/system/notify/test", { method: "POST" });
            toast(result.message, result.success ? "ok" : "err");
          } catch (error) {
            toast(error.message, "err");
          }
        }),
      ]
    );
    box.scrollTop = box.scrollHeight;
  }

  // ---------------- 追新雷达 ----------------
  async function pageRadar() {
    shell(loading(), "追新雷达", "拉取各站点最新资源，自动匹配订阅并下载");
    const [schedules, sites] = await Promise.all([
      api("/schedules"),
      api("/sites?kind=indexer&enabled_only=true"),
    ]);
    const radar = schedules.items.filter((item) => item.key === "radar");

    const feedBox = el("div", { class: "card" }, [
      el("h3", {}, [icon("radar", "sm"), el("span", { text: "最新资源流" })]),
      el("div", { class: "muted", text: "点击「预览最新流」拉取各站点最新发布的资源。" }),
    ]);

    const renderFeed = (items) => {
      feedBox.replaceChildren(
        el("div", { class: "card-head" }, [
          el("h3", {}, [icon("radar", "sm"), el("span", { text: "最新资源流（" + items.length + " 条）" })]),
        ]),
        table(
          [
            {
              title: "资源名",
              render: (row) =>
                el("div", { class: "truncate", title: row.title, text: row.title }),
            },
            { title: "站点", render: (row) => el("span", { class: "tag", text: row.site || "-" }) },
            {
              title: "类型",
              render: (row) =>
                el("span", { class: "tag " + (row.kind === "pan" ? "brand" : "ok") }, [
                  icon(row.kind === "pan" ? "cloud" : "link", "sm"),
                  el("span", { text: row.kind === "pan" ? "网盘" : "BT" }),
                ]),
            },
            { title: "做种", class: "num", render: (row) => row.seeders || "-" },
            { title: "体积", class: "num", render: (row) => fmtSize(row.size) },
            { title: "发布", render: (row) => el("span", { class: "tiny dim", text: fmtTime(row.publish_at) }) },
            { title: "操作", render: (row) => downloadButton(row) },
          ],
          items,
          "没有获取到最新资源，请确认已启用支持最新流的站点"
        )
      );
    };

    const resultBox = el("div", { class: "card" }, [
      el("h3", {}, [icon("check", "sm"), el("span", { text: "匹配结果" })]),
      el("div", {
        class: "muted",
        text: "「预览匹配」只做匹配不下载；「立即追新」会真实投递下载任务。",
      }),
    ]);

    const renderRun = (data) => {
      const rows = data.downloads || [];
      resultBox.replaceChildren(
        el("div", { class: "card-head" }, [
          el("h3", {}, [
            icon("check", "sm"),
            el("span", { text: data.dry_run ? "预览匹配结果" : "追新执行结果" }),
          ]),
          el("div", { class: "chips" }, [
            el("span", { class: "tag", text: "资源 " + (data.resources || 0) }),
            el("span", { class: "tag", text: "活跃订阅 " + (data.subscribes || 0) }),
            el("span", { class: "tag ok", text: "命中订阅 " + (data.matched || 0) }),
            el("span", {
              class: "tag " + (data.dry_run ? "warn" : "brand"),
              text: (data.dry_run ? "待下载 " : "已投递 ") + rows.length,
            }),
            el("span", { class: "tag", text: (data.elapsed_ms || 0) + "ms" }),
          ]),
        ]),
        table(
          [
            { title: "订阅", render: (row) => row.subscribe },
            {
              title: "资源名",
              render: (row) =>
                el("div", { class: "truncate", title: row.title, text: row.title }),
            },
            { title: "集数", render: (row) => (row.episodes || []).join(",") || "-" },
            { title: "站点", render: (row) => el("span", { class: "tag", text: row.site || "-" }) },
            { title: "评分", class: "num", render: (row) => (row.score || 0).toFixed(1) },
          ],
          rows,
          "本轮没有命中任何缺集资源"
        ),
        (data.skipped || []).length
          ? el("div", { style: "margin-top:14px" }, [
              el("div", { class: "dim tiny", style: "margin-bottom:6px", text: "被过滤的订阅" }),
              table(
                [
                  { title: "订阅", render: (row) => row.title },
                  { title: "候选数", class: "num", render: (row) => row.candidates },
                  { title: "原因", render: (row) => el("span", { class: "muted", text: row.reason }) },
                ],
                data.skipped
              ),
            ])
          : null
      );
    };

    const previewFeed = el("button", { class: "btn" }, [
      icon("radar", "sm"),
      el("span", { text: "预览最新流" }),
    ]);
    previewFeed.addEventListener("click", async () => {
      previewFeed.disabled = true;
      previewFeed.querySelector("span").textContent = "拉取中…";
      try {
        const data = await api("/radar/feed?limit_per_site=30");
        renderFeed(data.data.items);
        toast("获取 " + data.data.total + " 条最新资源", "ok");
      } catch (error) {
        toast(error.message, "err");
      }
      previewFeed.disabled = false;
      previewFeed.querySelector("span").textContent = "预览最新流";
    });

    const dryRun = el("button", { class: "btn" }, [
      icon("check", "sm"),
      el("span", { text: "预览匹配" }),
    ]);
    dryRun.addEventListener("click", async () => {
      dryRun.disabled = true;
      dryRun.querySelector("span").textContent = "匹配中…";
      try {
        const data = await api("/radar/run?dry_run=true", { method: "POST" });
        renderRun(data.data);
        toast("命中 " + data.data.matched + " 个订阅", "ok");
      } catch (error) {
        toast(error.message, "err");
      }
      dryRun.disabled = false;
      dryRun.querySelector("span").textContent = "预览匹配";
    });

    const runNow = el("button", { class: "btn primary" }, [
      icon("play", "sm"),
      el("span", { text: "立即追新" }),
    ]);
    runNow.addEventListener("click", async () => {
      if (!confirm("将拉取各站点最新资源并对缺集自动投递下载，确认继续？")) return;
      runNow.disabled = true;
      runNow.querySelector("span").textContent = "执行中…";
      try {
        const data = await api("/radar/run", { method: "POST" });
        renderRun(data.data);
        toast("新增 " + (data.data.downloads || []).length + " 个下载任务", "ok");
      } catch (error) {
        toast(error.message, "err");
      }
      runNow.disabled = false;
      runNow.querySelector("span").textContent = "立即追新";
    });

    const jobCard = el("div", { class: "card" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("clock", "sm"), el("span", { text: "定时追新任务" })]),
        iconButton("任务设置", "settings", () => go("schedules"), "sm ghost"),
      ]),
      radar.length
        ? table(
            [
              { title: "任务", render: (row) => row.name },
              {
                title: "状态",
                render: (row) =>
                  row.enabled
                    ? el("span", { class: "tag dot ok", text: "已启用" })
                    : el("span", { class: "tag dot warn", text: "已关闭" }),
              },
              {
                title: "触发规则",
                render: (row) =>
                  el("span", { class: "mono", text: row.trigger === "cron" ? row.cron : "每 " + row.minutes + " 分钟" }),
              },
              {
                title: "下次执行",
                render: (row) =>
                  el("div", {}, [
                    el("div", { class: "tiny", text: fmtRelative(row.next_run_time) }),
                    el("div", { class: "cell-sub", text: fmtTime(row.next_run_time) }),
                  ]),
              },
              {
                title: "操作",
                render: (row) =>
                  el("div", { class: "row tight" }, [
                    iconButton("修改周期", "edit", () => scheduleForm(row, pageRadar), "sm"),
                    iconButton("立即执行", "play", () => runSchedule(row, pageRadar), "sm ghost"),
                  ]),
              },
            ],
            radar
          )
        : emptyBox("雷达定时任务未启用（CF_RADAR_ENABLED=false 或调度器已关闭）", "clock"),
      el("div", { class: "divider" }),
      el("div", { class: "dim tiny", text:
        "当前启用的索引站点：" + (sites.length ? sites.map((s) => s.name).join("、") : "无（请先在站点管理启用站点）") }),
    ]);

    shell(
      el("div", { class: "grid" }, [jobCard, resultBox, feedBox]),
      "追新雷达",
      "以站点最新流驱动的低延迟追新",
      [previewFeed, dryRun, runNow]
    );
  }

  // ---------------- 网盘管理 ----------------
  /** 网盘浏览器的当前位置（页面级状态，切页后保留便于来回跳转）。 */
  const panState = { siteId: null, path: "/" };

  /** 网盘账号登录入口：扫码（115/百度）或导入 Cookie（含夸克）。

      能力清单由后端 /pan/login/providers 下发，前端**不写死**哪个盘能扫码
      —— 与网盘能力位（capabilities）同一原则，将来新增网盘前端零改动。
  */
  async function panLoginDialog(onDone) {
    let providers = [];
    try {
      const response = await api("/pan/login/providers");
      providers = response.data || [];
    } catch (error) {
      toast(error.message, "err");
      return;
    }
    if (!providers.length) {
      toast("没有可用的登录方式", "err");
      return;
    }

    const body = el("div", {});
    const close = panelModal(
      "登录网盘账号",
      "扫码登录后凭据会自动写入站点并启用；不支持扫码的网盘可导入 Cookie",
      body,
      true
    );

    const renderPicker = () => {
      body.replaceChildren(
        el("div", { class: "grid" },
          providers.map((p) =>
            el("div", { class: "card" }, [
              el("div", { class: "row center between" }, [
                el("div", {}, [
                  el("div", { class: "row tight center" }, [
                    icon("cloud", "sm"),
                    el("strong", { text: p.label }),
                    p.qrcode
                      ? el("span", { class: "tag brand tiny", text: "支持扫码" })
                      : el("span", { class: "tag tiny", text: "仅 Cookie" }),
                  ]),
                  el("div", { class: "tiny dim", style: "margin-top:4px", text: p.note }),
                ]),
                el("div", { class: "row tight" }, [
                  p.qrcode
                    ? iconButton("扫码登录", "qr", () => startQr(p), "sm primary")
                    : null,
                  p.cookie
                    ? iconButton("导入 Cookie", "key", () => importCookie(p), "sm ghost")
                    : null,
                ]),
              ]),
            ])
          )
        )
      );
    };

    /** 扫码：拉二维码后轮询状态，成功即落库。 */
    const startQr = async (provider) => {
      body.replaceChildren(loading());
      let session;
      try {
        const response = await api("/pan/login/qrcode", {
          method: "POST",
          body: { provider: provider.provider },
        });
        session = response.data;
      } catch (error) {
        body.replaceChildren(
          el("div", { class: "card" }, [
            emptyBox("无法获取二维码：" + error.message, "alert"),
            el("div", { class: "row tight" }, [
              iconButton("返回", "back", renderPicker, "sm ghost"),
            ]),
          ])
        );
        return;
      }

      const statusLine = el("div", { class: "dim tiny", text: session.message || "等待扫码…" });
      // 二维码图走后端图片代理：115/百度的二维码接口都校验 Referer
      const qrImg = el("img", {
        src: API + "/images/proxy?url=" + encodeURIComponent(session.qr_image),
        alt: "登录二维码",
        style: "width:200px;height:200px;background:#fff;padding:8px;border-radius:12px",
      });
      qrImg.addEventListener("error", () => {
        qrImg.replaceWith(
          el("div", { class: "tiny dim", style: "max-width:220px;word-break:break-all" }, [
            el("div", { text: "二维码图片加载失败，请用手机打开下面的地址：" }),
            el("div", { text: session.qr_content || "-" }),
          ])
        );
      });

      let stopped = false;
      const stop = () => { stopped = true; };

      body.replaceChildren(
        el("div", { class: "card" }, [
          el("div", { class: "col center", style: "align-items:center;gap:12px" }, [
            el("strong", { text: "请用「" + provider.label + "」App 扫码" }),
            qrImg,
            statusLine,
            el("div", { class: "row tight" }, [
              iconButton("返回", "back", () => { stop(); renderPicker(); }, "sm ghost"),
              iconButton("换个二维码", "refresh", () => { stop(); startQr(provider); }, "sm ghost"),
            ]),
          ]),
        ])
      );

      // 轮询：后端对上游是长轮询，这里间隔 2 秒足够，不会打爆接口
      const poll = async () => {
        if (stopped) return;
        try {
          const response = await api("/pan/login/qrcode/" + session.token);
          const data = response.data;
          statusLine.textContent = data.message || data.status;
          if (data.status === "success") {
            stopped = true;
            const saved = await api("/pan/login/complete", {
              method: "POST",
              body: { token: session.token },
            });
            toast(saved.message || "登录成功", "ok");
            close();
            if (onDone) onDone();
            return;
          }
          if (data.status === "expired" || data.status === "failed") {
            stopped = true;
            statusLine.textContent = data.message || "二维码已失效，请重新获取";
            return;
          }
        } catch (error) {
          // 单次轮询失败不终止整个流程（可能只是网络抖动）
          statusLine.textContent = "查询状态失败，重试中…";
        }
        setTimeout(poll, 2000);
      };
      setTimeout(poll, 1500);
    };

    /** 导入 Cookie：保存前先校验，无效不写库。 */
    const importCookie = (provider) => {
      close();
      modal(
        "导入「" + provider.label + "」Cookie",
        [
          {
            key: "cookie",
            label: "完整 Cookie",
            type: "textarea",
            placeholder: "浏览器登录后 F12 → Network → 任一请求 → 复制 Cookie 请求头",
            hint: provider.note,
          },
          { key: "site_name", label: "站点名称（留空自动生成）", placeholder: provider.label },
        ],
        async (values) => {
          const result = await api("/pan/login/cookie", {
            method: "POST",
            body: {
              provider: provider.provider,
              cookie: values.cookie,
              site_name: values.site_name || null,
            },
          });
          toast(result.message || "已保存", "ok");
          if (onDone) onDone();
        },
        "校验并保存",
        { lead: "会先调接口验证有效性，校验不过就不保存——避免半夜任务才发现填错。" }
      );
    };

    renderPicker();
  }

  function quotaCard(item, onPick, active) {
    const q = item.quota || {};
    const percent = Number(q.percent) || 0;
    const card = el("div", { class: "card pan-card" + (active ? " on" : "") }, [
      el("div", { class: "pan-card-head" }, [
        el("div", { class: "row tight center", style: "flex:1" }, [
          icon("cloud", "sm"),
          el("div", {}, [
            el("div", { class: "pan-name", text: item.name }),
            el("div", { class: "cell-sub mono", text: item.provider }),
          ]),
        ]),
        el("span", {
          class: "tag" + (item.supports_save ? " brand" : ""),
          text: item.supports_save ? "可转存" : "只读",
        }),
      ]),
      el("div", { class: "progress" + (percent >= 90 ? " done" : "") }, [
        el("i", { style: "width:" + Math.max(2, Math.min(100, percent)) + "%" }),
      ]),
      el("div", { class: "row tight center between" }, [
        el("span", {
          class: "tiny dim",
          text: q.total ? fmtSize(q.used) + " / " + fmtSize(q.total) : "容量未知",
        }),
        el("span", { class: "tiny dim", text: q.total ? percent + "%" : "-" }),
      ]),
      el("div", { class: "row tight", style: "margin-top:12px" }, [
        iconButton("浏览", "library", () => onPick(item.site_id), "sm"),
        iconButton("测试", "check", async () => {
          try {
            const result = await api("/pan/" + item.site_id + "/test", { method: "POST" });
            toast(item.name + "：" + result.message + " · " + result.capacity_text, result.success ? "ok" : "err");
          } catch (error) {
            toast(error.message, "err");
          }
        }, "sm ghost"),
        item.supports_save
          ? iconButton("新建目录", "plus", () => {
              modal("新建目录", [
                { key: "path", label: "完整路径", value: (item.root_path || "/") , hint: "例如 /影视/剧集" },
              ], async (values) => {
                await api("/pan/mkdir", { method: "POST", body: { site_id: item.site_id, path: values.path } });
                toast("目录已创建", "ok");
                pageStorage();
              }, "创建");
            }, "sm ghost")
          : null,
      ]),
    ]);
    return card;
  }

  function breadcrumb(path, onGo) {
    const parts = String(path || "/").split("/").filter(Boolean);
    const nodes = [
      el("button", { class: "crumb", text: "根目录", onclick: () => onGo("/") }),
    ];
    let acc = "";
    parts.forEach((part, index) => {
      acc += "/" + part;
      const target = acc;
      nodes.push(el("span", { class: "crumb-sep", text: "/" }));
      nodes.push(
        index === parts.length - 1
          ? el("span", { class: "crumb on", text: part })
          : el("button", { class: "crumb", text: part, onclick: () => onGo(target) })
      );
    });
    return el("div", { class: "crumbs" }, nodes);
  }

  async function pageStorage() {
    shell(loading(), "网盘管理", "容量、目录浏览与分享转存");
    const [overview, pending, records] = await Promise.all([
      api("/pan"),
      api("/pan/pending?limit=50").catch(() => ({ items: [] })),
      api("/pan/records?limit=30").catch(() => ({ items: [] })),
    ]);

    const list = overview.items || [];
    if (!panState.siteId && list.length) panState.siteId = list[0].site_id;
    const current = list.find((item) => item.site_id === panState.siteId) || list[0] || null;

    const pickSite = (siteId) => {
      panState.siteId = siteId;
      panState.path = "/";
      pageStorage();
    };

    const cards = el(
      "div",
      { class: "grid cols-3" },
      list.length
        ? list.map((item) => quotaCard(item, pickSite, item.site_id === panState.siteId))
        : [
            el("div", { class: "card" }, [
              emptyBox("还没有启用网盘存储。到「站点管理」启用 AList / 夸克 / 本地目录后即可在此浏览与转存", "cloud"),
            ]),
          ]
    );

    // ---- 文件浏览 ----
    let browser = null;
    if (current) {
      let files = { items: [], path: panState.path, parent: null };
      try {
        files = await api(
          "/pan/files?site_id=" + current.site_id + "&path=" + encodeURIComponent(panState.path)
        );
      } catch (error) {
        files = { items: [], path: panState.path, parent: null, error: error.message };
      }

      const goPath = (path) => {
        panState.path = path;
        pageStorage();
      };

      // 能力位由后端下发：不支持的操作直接不渲染按钮，避免"点了才知道不支持"
      const caps = current.capabilities || {};

      browser = el("div", { class: "card flush" }, [
        el("div", { class: "card-head" }, [
          el("h3", {}, [icon("library", "sm"), el("span", { text: current.name + " · 文件管理" })]),
          el("div", { class: "row tight center" }, [
            caps.search
              ? iconButton("盘内搜索", "search", () => {
                  modal(
                    current.name + " · 盘内搜索",
                    [{ key: "keyword", label: "文件名关键词", required: true }],
                    async (values) => {
                      const result = await api(
                        "/pan/search?site_id=" + current.site_id +
                          "&keyword=" + encodeURIComponent(values.keyword) + "&limit=100"
                      );
                      const rows = result.items || [];
                      if (!rows.length) {
                        toast("没有匹配的文件", "");
                        return;
                      }
                      // 搜索结果用只读弹窗展示，点「定位」直接跳到所在目录
                      let closePanel = null;
                      closePanel = panelModal(
                        "搜索「" + values.keyword + "」",
                        "共 " + rows.length + " 条结果",
                        el("div", { class: "card flush" }, [
                          table(
                            [
                              {
                                title: "名称",
                                render: (row) =>
                                  el("div", {}, [
                                    el("div", { class: "row tight center" }, [
                                      icon(row.is_dir ? "box" : "film", "sm"),
                                      el("span", { class: "truncate", title: row.name, text: row.name }),
                                    ]),
                                    el("div", { class: "cell-sub mono", text: row.path }),
                                  ]),
                              },
                              { title: "大小", class: "num", render: (row) => (row.is_dir ? "-" : fmtSize(row.size)) },
                              {
                                title: "操作",
                                render: (row) =>
                                  iconButton("定位", "link", () => {
                                    const parent = row.is_dir
                                      ? row.path
                                      : row.path.split("/").slice(0, -1).join("/") || "/";
                                    if (closePanel) closePanel();
                                    goPath(parent);
                                  }, "sm ghost"),
                              },
                            ],
                            rows
                          ),
                        ]),
                        true
                      );
                    },
                    "搜索"
                  );
                }, "sm ghost")
              : null,
            iconButton("新建目录", "plus", () => {
              modal(
                "新建目录",
                [
                  {
                    key: "name",
                    label: "目录名",
                    required: true,
                    hint: "将创建在当前目录 " + files.path + " 下",
                  },
                ],
                async (values) => {
                  const base = files.path === "/" ? "" : files.path;
                  await api("/pan/mkdir", {
                    method: "POST",
                    body: { site_id: current.site_id, path: base + "/" + values.name },
                  });
                  toast("已创建", "ok");
                  pageStorage();
                },
                "创建"
              );
            }, "sm ghost"),
            files.parent ? iconButton("上一级", "refresh", () => goPath(files.parent), "sm ghost") : null,
            iconButton("刷新", "refresh", () => pageStorage(), "sm ghost"),
          ]),
        ]),
        el("div", { style: "padding:0 var(--sp-5) var(--sp-3)" }, [breadcrumb(files.path, goPath)]),
        files.error
          ? emptyBox("读取失败：" + files.error, "alert")
          : table(
              [
                {
                  title: "名称",
                  render: (row) =>
                    row.is_dir
                      ? el("button", { class: "link-btn", onclick: () => goPath(row.path) }, [
                          icon("box", "sm"),
                          el("span", { text: row.name }),
                        ])
                      : el("div", { class: "row tight center" }, [
                          icon("film", "sm"),
                          el("span", { class: "truncate", title: row.name, text: row.name }),
                        ]),
                },
                { title: "类型", render: (row) => (row.is_dir ? "目录" : "文件") },
                { title: "大小", class: "num", render: (row) => (row.is_dir ? "-" : fmtSize(row.size)) },
                { title: "修改时间", render: (row) => fmtTime(row.modified_at) },
                {
                  title: "操作",
                  render: (row) =>
                    el("div", { class: "row tight" }, [
                      row.is_dir
                        ? null
                        : iconButton("直链", "link", async () => {
                            try {
                              const result = await api(
                                "/pan/download-url?site_id=" + current.site_id + "&path=" + encodeURIComponent(row.path)
                              );
                              copyText(result.url);
                              toast("直链已复制到剪贴板", "ok");
                            } catch (error) {
                              toast(error.message, "err");
                            }
                          }, "sm ghost"),
                      caps.rename
                        ? iconButton("重命名", "edit", () => {
                            modal(
                              "重命名",
                              [{ key: "new_name", label: "新名称", value: row.name, required: true }],
                              async (values) => {
                                await api("/pan/rename", {
                                  method: "POST",
                                  body: {
                                    site_id: current.site_id,
                                    path: row.path,
                                    new_name: values.new_name,
                                    file_id: row.file_id || null,
                                  },
                                });
                                toast("已重命名", "ok");
                                pageStorage();
                              },
                              "保存"
                            );
                          }, "sm ghost")
                        : null,
                      caps.move
                        ? iconButton("移动", "box", () => {
                            modal(
                              "移动 / 复制",
                              [
                                {
                                  key: "target_dir",
                                  label: "目标目录",
                                  value: files.path,
                                  required: true,
                                  hint: "填网盘内的绝对路径，如 /影视/电影",
                                },
                                {
                                  key: "mode",
                                  label: "方式",
                                  type: "select",
                                  options: [
                                    { value: "move", label: "移动" },
                                    { value: "copy", label: "复制" },
                                  ],
                                },
                              ],
                              async (values) => {
                                await api("/pan/move", {
                                  method: "POST",
                                  body: {
                                    site_id: current.site_id,
                                    path: row.path,
                                    target_dir: values.target_dir,
                                    file_id: row.file_id || null,
                                    copy: values.mode === "copy",
                                  },
                                });
                                toast(values.mode === "copy" ? "已复制" : "已移动", "ok");
                                pageStorage();
                              },
                              "执行"
                            );
                          }, "sm ghost")
                        : null,
                      current.supports_delete
                        ? iconButton("删除", "trash", async () => {
                            if (!confirm("确定删除 " + row.name + "？")) return;
                            try {
                              await api(
                                "/pan/files?site_id=" + current.site_id + "&path=" + encodeURIComponent(row.path),
                                { method: "DELETE" }
                              );
                              toast("已删除", "ok");
                              pageStorage();
                            } catch (error) {
                              toast(error.message, "err");
                            }
                          }, "sm danger")
                        : null,
                    ]),
                },
              ],
              files.items,
              "该目录为空"
            ),
      ]);
    }

    // ---- 待转存队列 ----
    const pendingItems = pending.items || [];
    const pendingCard = el("div", { class: "card flush" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("inbox", "sm"), el("span", { text: "待转存队列（" + pendingItems.length + "）" })]),
        pendingItems.length
          ? iconButton("一键全部转存", "cloud", async () => {
              try {
                const result = await api("/pan/transfer?limit=50", { method: "POST" });
                toast("转存完成：成功 " + result.saved + " · 失败 " + result.failed, result.failed ? "err" : "ok");
                pageStorage();
              } catch (error) {
                toast(error.message, "err");
              }
            }, "sm primary")
          : null,
      ]),
      table(
        [
          {
            title: "标题",
            render: (row) =>
              el("div", {}, [
                el("div", { class: "truncate", title: row.title, text: row.title }),
                el("div", { class: "cell-sub", text: (row.site || "-") + " · " + typeLabel(row.media_type) }),
              ]),
          },
          { title: "网盘", render: (row) => el("span", { class: "tag", text: row.pan_type || "未知" }) },
          { title: "提取码", render: (row) => el("span", { class: "mono dim", text: row.password || "-" }) },
          { title: "登记时间", render: (row) => fmtTime(row.created_at) },
          {
            title: "操作",
            render: (row) =>
              el("div", { class: "row tight" }, [
                iconButton("转存", "cloud", async () => {
                  try {
                    const result = await api("/pan/save", {
                      method: "POST",
                      body: { share_url: row.link, password: row.password, task_id: row.id },
                    });
                    toast("已转存到 " + (result.saved_path || "网盘"), "ok");
                    pageStorage();
                  } catch (error) {
                    toast(error.message, "err");
                  }
                }, "sm"),
                iconButton("复制链接", "link", () => {
                  copyText(row.link);
                  toast("已复制", "ok");
                }, "sm ghost"),
              ]),
          },
        ],
        pendingItems,
        "没有待转存的网盘资源"
      ),
    ]);

    // ---- 转存记录 ----
    const recordCard = el("div", { class: "card flush" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("logs", "sm"), el("span", { text: "转存记录" })]),
      ]),
      table(
        [
          { title: "网盘", render: (row) => el("span", { class: "mono", text: row.storage }) },
          {
            title: "分享链接",
            render: (row) => el("div", { class: "truncate mono dim", title: row.share_url, text: row.share_url }),
          },
          { title: "落地路径", render: (row) => el("span", { class: "mono", text: row.saved_path || "-" }) },
          {
            title: "结果",
            render: (row) =>
              el("span", {
                class: "tag dot " + (row.success ? "ok" : "err"),
                text: row.success ? "成功" : "失败",
              }),
          },
          { title: "说明", render: (row) => el("div", { class: "truncate tiny dim", title: row.message, text: row.message || "-" }) },
          { title: "时间", render: (row) => fmtTime(row.created_at) },
        ],
        records.items || [],
        "还没有转存记录"
      ),
    ]);

    const saveButton = iconButton("转存分享链接", "plus", () => {
      modal(
        "转存网盘分享",
        [
          { key: "share_url", label: "分享链接", placeholder: "https://pan.quark.cn/s/xxxx", hint: "支持夸克/阿里/百度等，AList 走离线下载" },
          { key: "password", label: "提取码", placeholder: "没有则留空" },
          {
            key: "site_id",
            label: "目标网盘",
            type: "select",
            options: [{ value: "", label: "自动选择（按链接域名匹配）" }].concat(
              list.map((item) => ({ value: String(item.site_id), label: item.name }))
            ),
          },
          { key: "target_dir", label: "落地目录", placeholder: "留空用网盘默认目录" },
        ],
        async (values) => {
          const result = await api("/pan/save", {
            method: "POST",
            body: {
              share_url: values.share_url,
              password: values.password || null,
              site_id: values.site_id ? Number(values.site_id) : null,
              target_dir: values.target_dir || null,
            },
          });
          toast("已转存：" + (result.saved_path || "完成"), "ok");
          pageStorage();
        },
        "开始转存"
      );
    }, "primary");

    shell(
      el("div", { class: "grid" }, [cards, browser, pendingCard, recordCard].filter(Boolean)),
      "网盘管理",
      list.length
        ? "已启用 " + list.length + " 个网盘 · 待转存 " + pendingItems.length + " 条"
        : "尚未启用网盘存储",
      [
        saveButton,
        // 扫码登录：比让用户去浏览器抠 Cookie 友好得多
        iconButton("登录网盘", "user", () => panLoginDialog(() => pageStorage()), "ghost"),
        // 网盘 Cookie 会静默过期，提供一键巡检比等任务失败再排查高效得多
        iconButton("凭据保活巡检", "shield", async () => {
          try {
            const result = await api("/pan/keep-alive", { method: "POST" });
            const rows = result.items || [];
            panelModal(
              "网盘凭据保活",
              "共巡检 " + result.total + " 个网盘，异常 " + result.failed + " 个",
              el("div", { class: "card flush" }, [
                table(
                  [
                    { title: "网盘", render: (row) => el("span", { text: row.name }) },
                    { title: "类型", render: (row) => el("span", { class: "tag", text: row.provider }) },
                    {
                      title: "状态",
                      render: (row) =>
                        el("span", {
                          class: "tag dot " + (row.skipped ? "" : row.success ? "ok" : "err"),
                          text: row.skipped ? "跳过" : row.success ? "有效" : "失效",
                        }),
                    },
                    { title: "说明", render: (row) => el("div", { class: "tiny dim", text: row.message || "-" }) },
                  ],
                  rows,
                  "没有已启用的网盘"
                ),
              ]),
              true
            );
          } catch (error) {
            toast(error.message, "err");
          }
        }, "ghost"),
        iconButton("刷新", "refresh", () => pageStorage()),
      ]
    );
  }

  // ---------------- STRM 同步 ----------------
  /** STRM 页面的筛选状态（切页保留，便于来回对比）。 */
  const strmState = { siteId: null, aliveOnly: false };

  /** 手动同步弹窗：选盘 + 起始目录 + 链接模式。 */
  function strmSyncForm(storages, onDone) {
    const options = [{ value: "", label: "全部启用的网盘（遍历同步）" }].concat(
      (storages || []).map((item) => ({ value: String(item.site_id), label: item.name }))
    );
    modal(
      "手动同步 STRM",
      [
        { key: "site_id", label: "网盘", type: "select", value: strmState.siteId ? String(strmState.siteId) : "", options },
        { key: "pan_path", label: "起始目录", value: "/", hint: "只同步该目录及其子目录下的视频文件" },
        { key: "strm_subdir", label: "STRM 子目录", value: "", hint: "留空则直接落在 STRM 根目录；多盘并存时建议按盘名分开" },
        {
          key: "link_mode",
          label: "链接模式",
          type: "select",
          value: "",
          options: [
            { value: "", label: "跟随全局配置" },
            { value: "proxy", label: "proxy · 写 302 端点，链接永不过期（推荐）" },
            { value: "direct", label: "direct · 写网盘临时直链，会过期" },
          ],
        },
        { key: "clean", label: "清理失效 STRM", type: "checkbox", value: true, hint: "网盘上源文件已消失时，同步删掉对应的 .strm，避免媒体库出现点不开的空剧集" },
      ],
      async (values) => {
        const payload = {
          pan_path: values.pan_path || "/",
          clean: values.clean,
        };
        if (values.site_id) payload.site_id = Number(values.site_id);
        if (values.strm_subdir) payload.strm_subdir = values.strm_subdir;
        if (values.link_mode) payload.link_mode = values.link_mode;
        const result = await api("/strm/sync", { method: "POST", body: payload });
        const created = result.created !== undefined ? result.created : 0;
        const removed = result.removed !== undefined ? result.removed : 0;
        toast(result.message || "同步完成：新增 " + created + " · 清理 " + removed, "ok");
        if (onDone) onDone();
      },
      "开始同步"
    );
  }

  async function pageStrm() {
    shell(loading(), "STRM 同步", "把网盘目录映射成本地 .strm，媒体服务器秒级入库");
    const query =
      "/strm/records?limit=300" +
      (strmState.siteId ? "&site_id=" + strmState.siteId : "") +
      (strmState.aliveOnly ? "&alive_only=true" : "");
    const [overview, records, storages] = await Promise.all([
      api("/strm"),
      api(query).catch(() => ({ items: [] })),
      api("/pan").catch(() => ({ items: [] })),
    ]);

    const data = overview.data || {};
    const list = records.items || [];
    const panList = storages.items || [];
    const proxyMode = (data.link_mode || "proxy") === "proxy";

    const stats = el("div", { class: "grid cols-4" }, [
      statCard("STRM 总数", String(data.total || 0), "已生成的 .strm 文件", "film"),
      statCard("有效", String(data.alive || 0), "源文件仍在网盘上", "check"),
      statCard("失效", String(data.invalid || 0), data.invalid ? "源文件已消失，建议同步清理" : "没有失效记录", "alert"),
      statCard("覆盖体积", data.total_size_text || "-", "这些视频在网盘上的总大小", "cloud"),
    ]);

    // 链接模式说明卡：这一条最容易配错，直接把差异写在界面上
    const modeCard = el("div", { class: "card" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("link", "sm"), el("span", { text: "链接模式" })]),
        el("span", { class: "tag " + (proxyMode ? "brand" : "warn"), text: data.link_mode || "proxy" }),
      ]),
      el("div", { class: "kv" }, [
        el("div", { class: "kv-item" }, [
          el("div", { class: "kv-label", text: "STRM 目录" }),
          el("div", { class: "mono tiny", text: data.strm_dir || "-" }),
        ]),
        el("div", { class: "kv-item" }, [
          el("div", { class: "kv-label", text: "当前写入内容" }),
          el("div", {
            class: "mono tiny",
            text: proxyMode ? "/api/v1/strm/play/{记录ID}" : "网盘临时直链",
          }),
        ]),
        el("div", { class: "kv-item" }, [
          el("div", { class: "kv-label", text: "过期风险" }),
          el("div", { text: proxyMode ? "无：播放时才实时换直链" : "有：直链过期后需重新同步" }),
        ]),
        el("div", { class: "kv-item" }, [
          el("div", { class: "kv-label", text: "切换方式" }),
          el("div", { class: "mono tiny", text: "CF_STRM_LINK_MODE=proxy|direct" }),
        ]),
      ]),
      el("div", { class: "muted", style: "margin-top:12px" }, [
        el("span", {
          text: proxyMode
            ? "播放端点是匿名 302 跳转（播放器带不了登录态），只回 Location 头不代理流量。"
            : "direct 模式 NAS 零流量，但网盘直链有有效期，建议配合定时同步任务使用。",
        }),
      ]),
    ]);

    const filters = el("div", { class: "row tight center wrap" }, [
      segment(
        [{ value: "", label: "全部网盘" }].concat(
          panList.map((item) => ({ value: String(item.site_id), label: item.name }))
        ),
        strmState.siteId ? String(strmState.siteId) : "",
        (value) => {
          strmState.siteId = value ? Number(value) : null;
          pageStrm();
        }
      ),
      segment(
        [
          { value: "all", label: "全部记录" },
          { value: "alive", label: "只看有效" },
        ],
        strmState.aliveOnly ? "alive" : "all",
        (value) => {
          strmState.aliveOnly = value === "alive";
          pageStrm();
        }
      ),
    ]);

    const recordCard = el("div", { class: "card flush" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("library", "sm"), el("span", { text: "STRM 记录（" + list.length + "）" })]),
        filters,
      ]),
      table(
        [
          {
            title: "STRM 文件",
            render: (row) =>
              el("div", {}, [
                el("div", { class: "truncate", title: row.strm_path, text: baseName(row.strm_path) }),
                el("div", { class: "cell-sub mono tiny truncate", title: row.strm_path, text: row.strm_path }),
              ]),
          },
          {
            title: "网盘源文件",
            render: (row) =>
              el("span", { class: "mono tiny dim truncate", title: row.source_path, text: row.source_path }),
          },
          { title: "大小", class: "num", render: (row) => row.size_text || fmtSize(row.size) },
          {
            title: "模式",
            render: (row) => el("span", { class: "tag" + (row.link_mode === "proxy" ? " brand" : ""), text: row.link_mode }),
          },
          {
            title: "状态",
            render: (row) =>
              row.alive
                ? el("span", { class: "tag dot ok", text: "有效" })
                : el("span", { class: "tag dot warn", text: "失效" }),
          },
          { title: "最近同步", render: (row) => fmtRelative(row.last_synced_at) },
          {
            title: "操作",
            render: (row) =>
              el("div", { class: "row tight" }, [
                iconButton("测直链", "play", async () => {
                  try {
                    // 只请求不跟随跳转，用状态码判断这条 STRM 现在还能不能播
                    const response = await fetch("/api/v1/strm/play/" + row.id, { redirect: "manual" });
                    const ok = response.type === "opaqueredirect" || (response.status >= 200 && response.status < 400);
                    toast(ok ? "换取直链成功，这条 STRM 可正常播放" : "换取失败（HTTP " + response.status + "）", ok ? "ok" : "err");
                  } catch (error) {
                    toast(error.message, "err");
                  }
                }, "sm ghost"),
                iconButton("复制播放地址", "link", () => {
                  copyText(location.origin + "/api/v1/strm/play/" + row.id);
                  toast("播放地址已复制", "ok");
                }, "sm ghost"),
              ]),
          },
        ],
        list,
        panList.length
          ? "还没有生成 STRM，点右上角「手动同步」把网盘目录映射成 .strm 文件"
          : "先到「站点管理」启用一个网盘存储（AList / 夸克 / WebDAV / 本地目录）"
      ),
    ]);

    shell(
      el("div", { class: "grid" }, [stats, modeCard, recordCard]),
      "STRM 同步",
      "共 " + (data.total || 0) + " 个 STRM · " + (data.total_size_text || "0") + " 网盘内容",
      [
        iconButton("手动同步", "cloud", () => strmSyncForm(panList, pageStrm), "primary"),
        iconButton("刷新", "refresh", () => pageStrm()),
      ]
    );
  }

  // ---------------- 网盘分享追更 ----------------
  const WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

  /** 把 weekdays 数组渲染成人话。 */
  function weekdayText(days) {
    if (!days || !days.length) return "每天";
    return days
      .slice()
      .sort((a, b) => a - b)
      .map((day) => WEEKDAY_NAMES[day] || day)
      .join("、");
  }

  /** 新建/编辑分享追更任务。row 为空表示新建。 */
  async function panSubForm(row, onDone) {
    let storages = { items: [] };
    try {
      storages = await api("/pan");
    } catch (error) {
      /* 网盘列不出来也允许建任务：留空即自动挑同家网盘 */
    }
    const siteOptions = [{ value: "", label: "自动选择（按链接匹配同家网盘）" }].concat(
      (storages.items || []).map((item) => ({ value: String(item.site_id), label: item.name }))
    );
    const current = row || {};
    modal(
      row ? "编辑分享追更 · " + row.name : "新建分享追更",
      [
        { key: "name", label: "任务名称", value: current.name || "", placeholder: "如：庆余年第二季（连载）" },
        { key: "share_url", label: "分享链接", value: current.share_url || "", placeholder: "https://pan.quark.cn/s/xxxxxx" },
        { key: "password", label: "提取码", value: current.password || "", hint: "没有就留空" },
        { key: "site_id", label: "转存到", type: "select", value: current.site_id ? String(current.site_id) : "", options: siteOptions },
        { key: "target_dir", label: "落地目录", value: current.target_dir || "", placeholder: "/来自：分享/庆余年", hint: "留空用网盘根目录" },
        { key: "include_regex", label: "只要匹配（正则）", value: current.include_regex || "", placeholder: "\\.(mkv|mp4)$", hint: "留空表示全都要" },
        { key: "exclude_regex", label: "排除匹配（正则）", value: current.exclude_regex || "", placeholder: "预告|花絮|sample" },
        { key: "rename_search", label: "重命名匹配（正则）", value: current.rename_search || "", placeholder: "^第(\\d+)集.*$" },
        { key: "rename_replace", label: "重命名为", value: current.rename_replace || "", placeholder: "S02E\\1.mkv", hint: "支持 \\1 反向引用；两项都填才生效" },
        { key: "weekdays", label: "仅在这些星期几执行", value: (current.weekdays || []).join(","), placeholder: "1,3,5", hint: "0=周一 … 6=周日；留空=每天。周更剧只在更新日巡检可省大量请求" },
        row ? { key: "reset_invalid", label: "清除失效标记", type: "checkbox", value: false, hint: "换了新链接或重填 Cookie 后勾选，让任务重新开始" } : null,
        row ? { key: "reset_history", label: "清空转存历史", type: "checkbox", value: false, hint: "下次巡检会把分享里的文件重新转存一遍" } : null,
      ].filter(Boolean),
      async (values) => {
        const payload = {
          name: values.name,
          share_url: values.share_url,
          password: values.password || null,
          target_dir: values.target_dir || null,
          include_regex: values.include_regex || null,
          exclude_regex: values.exclude_regex || null,
          rename_search: values.rename_search || null,
          rename_replace: values.rename_replace || null,
          weekdays: String(values.weekdays || "")
            .split(/[,，\s]+/)
            .filter((item) => item !== "")
            .map((item) => Number(item))
            .filter((item) => !Number.isNaN(item) && item >= 0 && item <= 6),
        };
        payload.site_id = values.site_id ? Number(values.site_id) : null;
        if (row) {
          payload.reset_invalid = !!values.reset_invalid;
          payload.reset_history = !!values.reset_history;
          await api("/pan-subscribes/" + row.id, { method: "PATCH", body: payload });
          toast("已保存", "ok");
        } else {
          if (!payload.name || !payload.share_url) throw new Error("任务名称与分享链接必填");
          await api("/pan-subscribes", { method: "POST", body: payload });
          toast("分享追更已创建", "ok");
        }
        if (onDone) onDone();
      },
      row ? "保存" : "创建",
      { wide: true, lead: "盯住一个会持续更新的分享链接，每次巡检只转存新增文件——对标 quark-auto-save 的核心玩法。" }
    );
  }

  async function pagePanSub() {
    shell(loading(), "分享追更", "盯住持续更新的分享链接，增量转存");
    const [data, schedules] = await Promise.all([
      api("/pan-subscribes"),
      api("/schedules").catch(() => ({ items: [] })),
    ]);
    const list = data.items || [];
    const job = (schedules.items || []).find((item) => item.key === "pan_subscribe");

    const active = list.filter((item) => item.status === "active" && !item.invalid).length;
    const totalSaved = list.reduce((sum, item) => sum + (item.total_saved || 0), 0);

    const stats = el("div", { class: "grid cols-4" }, [
      statCard("追更任务", String(list.length), "盯住的分享链接总数", "link"),
      statCard("运行中", String(active), "会被定时巡检的任务", "play"),
      statCard("已失效", String(data.invalid || 0), data.invalid ? "连续失败达阈值，已自动停手" : "全部健康", "alert"),
      statCard("累计转存", String(totalSaved), "历史新增的文件数", "cloud"),
    ]);

    const jobCard = el("div", { class: "card" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("clock", "sm"), el("span", { text: "巡检节奏" })]),
        job && job.enabled
          ? el("span", { class: "tag dot ok", text: "已启用" })
          : el("span", { class: "tag dot warn", text: job ? "已关闭" : "未注册" }),
      ]),
      job
        ? el("div", { class: "kv" }, [
            el("div", { class: "kv-item" }, [
              el("div", { class: "kv-label", text: "当前规则" }),
              el("div", { class: "mono", text: job.trigger === "cron" ? job.cron : "每 " + job.minutes + " 分钟" }),
            ]),
            el("div", { class: "kv-item" }, [
              el("div", { class: "kv-label", text: "下次执行" }),
              el("div", {}, [
                el("div", { text: fmtRelative(job.next_run_time) }),
                el("div", { class: "cell-sub", text: fmtTime(job.next_run_time) }),
              ]),
            ]),
          ])
        : emptyBox("调度器未运行，任务只能手动巡检", "clock"),
      job
        ? el("div", { class: "row tight", style: "margin-top:16px" }, [
            iconButton("修改周期", "edit", () => scheduleForm(job, pagePanSub), "sm primary"),
            iconButton("立即执行", "play", () => runSchedule(job, pagePanSub), "sm"),
          ])
        : null,
    ]);

    const listCard = el("div", { class: "card flush" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("inbox", "sm"), el("span", { text: "追更任务（" + list.length + "）" })]),
        el("div", { class: "row tight center" }, [
          iconButton("立即巡检全部", "play", async () => {
            try {
              const result = await api("/pan-subscribes/check-all?limit=50", { method: "POST" });
              toast(result.message || "巡检完成", result.failed ? "err" : "ok");
              pagePanSub();
            } catch (error) {
              toast(error.message, "err");
            }
          }, "sm"),
          iconButton("新建", "plus", () => panSubForm(null, pagePanSub), "sm primary"),
        ]),
      ]),
      table(
        [
          {
            title: "任务",
            render: (row) =>
              el("div", {}, [
                el("div", { class: "row tight center" }, [
                  el("span", { text: row.name }),
                  row.invalid ? el("span", { class: "tag warn", text: "已失效" }) : null,
                ]),
                el("div", { class: "cell-sub mono tiny truncate", title: row.share_url, text: row.share_url }),
              ]),
          },
          {
            title: "过滤 / 重命名",
            render: (row) =>
              el("div", { class: "stack tiny" }, [
                row.include_regex ? el("div", { class: "mono dim", text: "只要 " + row.include_regex }) : null,
                row.exclude_regex ? el("div", { class: "mono dim", text: "排除 " + row.exclude_regex }) : null,
                row.rename_search ? el("div", { class: "mono dim", text: row.rename_search + " → " + (row.rename_replace || "") }) : null,
                !row.include_regex && !row.exclude_regex && !row.rename_search
                  ? el("span", { class: "dim", text: "全部转存，不改名" })
                  : null,
              ]),
          },
          { title: "执行日", render: (row) => el("span", { class: "tiny", text: weekdayText(row.weekdays) }) },
          {
            title: "已转存",
            class: "num",
            render: (row) =>
              el("div", {}, [
                el("div", { text: String(row.total_saved || 0) }),
                el("div", { class: "cell-sub", text: "记录 " + (row.saved_count || 0) }),
              ]),
          },
          {
            title: "最近巡检",
            render: (row) =>
              el("div", {}, [
                el("div", { text: fmtRelative(row.last_checked_at) }),
                row.last_message
                  ? el("div", { class: "cell-sub truncate", title: row.last_message, text: row.last_message })
                  : null,
              ]),
          },
          {
            title: "失败",
            class: "num",
            render: (row) =>
              row.failure_count
                ? el("span", { class: "tag warn", text: String(row.failure_count) })
                : el("span", { class: "dim", text: "0" }),
          },
          {
            title: "操作",
            render: (row) =>
              el("div", { class: "row tight" }, [
                iconButton("巡检", "play", async () => {
                  try {
                    const result = await api("/pan-subscribes/" + row.id + "/check", { method: "POST" });
                    toast(result.message || "巡检完成", result.success ? "ok" : "err");
                    pagePanSub();
                  } catch (error) {
                    toast(error.message, "err");
                  }
                }, "sm"),
                iconButton("编辑", "edit", () => panSubForm(row, pagePanSub), "sm ghost"),
                iconButton("删除", "trash", async () => {
                  if (!confirm("确定删除追更任务「" + row.name + "」？")) return;
                  try {
                    await api("/pan-subscribes/" + row.id, { method: "DELETE" });
                    toast("已删除", "ok");
                    pagePanSub();
                  } catch (error) {
                    toast(error.message, "err");
                  }
                }, "sm danger"),
              ]),
          },
        ],
        list,
        "还没有分享追更任务。点「新建」贴一个持续更新的网盘分享链接，之后只转存新增文件"
      ),
    ]);

    shell(
      el("div", { class: "grid" }, [stats, jobCard, listCard]),
      "分享追更",
      list.length ? "共 " + list.length + " 个任务 · 累计转存 " + totalSaved + " 个文件" : "盯住分享链接做增量转存",
      [
        iconButton("新建任务", "plus", () => panSubForm(null, pagePanSub), "primary"),
        iconButton("刷新", "refresh", () => pagePanSub()),
      ]
    );
  }

  // ---------------- 视频追更（UP 主 / 频道） ----------------
  //: 视频订阅来源站点的展示名。后端 guess_site() 只给英文 key，
  //: 这里翻成中文标签；未收录的 key 原样显示，不至于变空白。
  const VIDEO_SITE_LABEL = {
    bilibili: "B 站",
    youtube: "YouTube",
    douyin: "抖音",
    acfun: "AcFun",
    other: "其他",
  };

  /** 新建/编辑视频订阅。row 为空表示新建。

      与「分享追更」的区别：那个盯网盘分享链接做增量转存，
      这个盯一个**会持续发新作的创作者页面**（B 站空间 / YouTube 频道 / 播放列表），
      新投稿出现就交给 yt-dlp 下载。
  */
  function videoSubForm(row, onDone) {
    const current = row || {};
    modal(
      row ? "编辑视频追更 · " + row.name : "新建视频追更",
      [
        { key: "name", label: "订阅名称", value: current.name || "", placeholder: "如：影视飓风（B 站）" },
        {
          key: "url",
          label: "空间页 / 频道页 / 播放列表地址",
          value: current.url || "",
          placeholder: "https://space.bilibili.com/946974",
          hint: "要贴**列表页**而不是单个视频页；填好后可先点「预览」看能不能列出投稿",
        },
        { key: "save_path", label: "保存目录（留空用默认下载目录）", value: current.save_path || "" },
        {
          key: "max_height",
          label: "画质上限",
          type: "select",
          value: current.max_height ? String(current.max_height) : "",
          options: [
            { value: "", label: "自动（最佳画质）" },
            { value: "2160", label: "4K（2160p）" },
            { value: "1080", label: "1080p" },
            { value: "720", label: "720p" },
            { value: "480", label: "480p" },
          ],
        },
        {
          key: "include_regex",
          label: "只要匹配（正则）",
          value: current.include_regex || "",
          placeholder: "第\\d+期|测评",
          hint: "作用在标题上。B 站列表页不返回标题，标题缺失时一律放行，不会因此漏下",
        },
        { key: "exclude_regex", label: "排除匹配（正则）", value: current.exclude_regex || "", placeholder: "直播回放|预告" },
        {
          key: "check_limit",
          label: "每轮读取列表前 N 条",
          type: "number",
          value: current.check_limit || 10,
          hint: "1~50。UP 主更新越频繁就调大一点，太大会变慢也更容易触发风控",
        },
        {
          key: "max_per_run",
          label: "单轮最多下载几个",
          type: "number",
          value: current.max_per_run || 3,
          hint: "1~20。防止一次把下载器打满",
        },
        {
          key: "skip_existing",
          label: "首次巡检只记账不补历史",
          type: "checkbox",
          value: row ? !!current.skip_existing : true,
          hint: "建议保持勾选：不勾会把该 UP 主最近 N 个投稿全部下一遍",
        },
        row ? { key: "reset_failures", label: "清除失败计数", type: "checkbox", value: false, hint: "被自动暂停的订阅会一并恢复运行" } : null,
        row ? { key: "reset_history", label: "清空已处理记录", type: "checkbox", value: false, hint: "下次巡检会把列表里的投稿重新当成新的" } : null,
      ].filter(Boolean),
      async (values) => {
        const payload = {
          name: values.name,
          url: values.url,
          save_path: values.save_path || null,
          include_regex: values.include_regex || null,
          exclude_regex: values.exclude_regex || null,
          check_limit: Number(values.check_limit) || 10,
          max_per_run: Number(values.max_per_run) || 3,
          max_height: values.max_height ? Number(values.max_height) : null,
          skip_existing: !!values.skip_existing,
        };
        if (row) {
          payload.reset_failures = !!values.reset_failures;
          payload.reset_history = !!values.reset_history;
          await api("/video-subscribes/" + row.id, { method: "PATCH", body: payload });
          toast("已保存", "ok");
        } else {
          if (!payload.name || !payload.url) throw new Error("订阅名称与地址必填");
          await api("/video-subscribes", { method: "POST", body: payload });
          toast("视频追更已创建", "ok");
        }
        if (onDone) onDone();
      },
      row ? "保存" : "创建",
      {
        wide: true,
        lead: "关注的 UP 主/频道更新就自动下载。地址填 B 站空间页、YouTube 频道页或播放列表页均可。",
      }
    );
  }

  /** 预览一个地址能列出哪些投稿。

      「地址贴错了」是这类订阅最常见的失败（贴了单个视频页而不是空间页），
      建订阅前先预览一次能立刻发现，不用等定时任务跑完才知道白等一轮。
  */
  async function videoSubPreview(defaultUrl) {
    modal(
      "预览可追更的投稿",
      [
        { key: "url", label: "空间页 / 频道页 / 播放列表地址", value: defaultUrl || "", placeholder: "https://space.bilibili.com/946974" },
        { key: "limit", label: "取前几条", type: "number", value: 10 },
      ],
      async (values) => {
        if (!values.url) throw new Error("请填地址");
        const result = await api(
          "/video-subscribes/preview?url=" + encodeURIComponent(values.url) +
            "&limit=" + (Number(values.limit) || 10),
          { method: "POST" }
        );
        const items = result.items || [];
        panelModal(
          "预览结果",
          (result.site || "未知来源") + " · 列出 " + items.length + " 条",
          el("div", {}, [
            result.message ? el("div", { class: "pad" }, [emptyBox(result.message, "alert")]) : null,
            items.length
              ? el("div", { class: "list" }, items.map((item, index) =>
                  el("div", { class: "list-row" }, [
                    el("span", {}, [
                      // 标题可能为 null（B 站扁平提取实测不返回标题），
                      // 这时显示视频 ID 而不是空白，用户才知道确实列到了东西
                      el("div", { text: item.title || "（列表页未提供标题）" }),
                      el("div", { class: "cell-sub mono tiny", text: item.id || "-" }),
                    ]),
                    el("span", { class: "dim tiny", text: "#" + (index + 1) }),
                  ])
                ))
              : null,
          ].filter(Boolean)),
          true
        );
      },
      "预览",
      { lead: "先确认地址能列出投稿，再建订阅。列不出来多半是贴了单个视频页。" }
    );
  }

  async function pageVideoSub() {
    shell(loading(), "视频追更", "关注的 UP 主 / 频道更新就自动下载");
    const [data, schedules] = await Promise.all([
      api("/video-subscribes"),
      api("/schedules").catch(() => ({ items: [] })),
    ]);
    const list = data.items || [];
    const job = (schedules.items || []).find((item) => item.key === "video_subscribe");

    const active = list.filter((item) => item.status === "active").length;
    const totalDownloaded = list.reduce((sum, item) => sum + (item.total_downloaded || 0), 0);

    const stats = el("div", { class: "grid cols-4" }, [
      statCard("视频订阅", String(list.length), "盯住的创作者页面数", "video"),
      statCard("运行中", String(active), "会被定时巡检的订阅", "play"),
      statCard("已暂停", String(list.length - active), "连续失败 5 次会自动暂停", "pause"),
      statCard("累计下载", String(totalDownloaded), "历史新增的视频数", "download"),
    ]);

    const jobCard = el("div", { class: "card" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("clock", "sm"), el("span", { text: "巡检节奏" })]),
        job && job.enabled
          ? el("span", { class: "tag dot ok", text: "已启用" })
          : el("span", { class: "tag dot warn", text: job ? "已关闭" : "未注册" }),
      ]),
      job
        ? el("div", { class: "kv" }, [
            el("div", { class: "kv-item" }, [
              el("div", { class: "kv-label", text: "当前规则" }),
              el("div", { class: "mono", text: job.trigger === "cron" ? job.cron : "每 " + job.minutes + " 分钟" }),
            ]),
            el("div", { class: "kv-item" }, [
              el("div", { class: "kv-label", text: "下次执行" }),
              el("div", {}, [
                el("div", { text: fmtRelative(job.next_run_time) }),
                el("div", { class: "cell-sub", text: fmtTime(job.next_run_time) }),
              ]),
            ]),
          ])
        : emptyBox("调度器未运行，订阅只能手动巡检", "clock"),
      job
        ? el("div", { class: "row tight", style: "margin-top:16px" }, [
            iconButton("修改周期", "edit", () => scheduleForm(job, pageVideoSub), "sm primary"),
            iconButton("立即执行", "play", () => runSchedule(job, pageVideoSub), "sm"),
          ])
        : null,
    ]);

    const listCard = el("div", { class: "card flush" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("inbox", "sm"), el("span", { text: "视频订阅（" + list.length + "）" })]),
        el("div", { class: "row tight center" }, [
          iconButton("立即巡检全部", "play", async () => {
            try {
              const result = await api("/video-subscribes/check-all", { method: "POST" });
              toast("巡检完成：新增下载 " + (result.downloaded || 0) + " 个", "ok");
              pageVideoSub();
            } catch (error) {
              toast(error.message, "err");
            }
          }, "sm"),
          iconButton("预览地址", "search", () => videoSubPreview(""), "sm ghost"),
          iconButton("新建", "plus", () => videoSubForm(null, pageVideoSub), "sm primary"),
        ]),
      ]),
      table(
        [
          {
            title: "订阅",
            render: (row) =>
              el("div", {}, [
                el("div", { class: "row tight center" }, [
                  el("span", { text: row.name }),
                  el("span", { class: "tag tiny", text: VIDEO_SITE_LABEL[row.site] || row.site }),
                  row.status !== "active"
                    ? el("span", { class: "tag warn tiny", text: "已暂停" })
                    : null,
                ]),
                el("div", { class: "cell-sub mono tiny truncate", title: row.url, text: row.url }),
              ]),
          },
          {
            title: "过滤 / 画质",
            render: (row) =>
              el("div", { class: "stack tiny" }, [
                row.include_regex ? el("div", { class: "mono dim", text: "只要 " + row.include_regex }) : null,
                row.exclude_regex ? el("div", { class: "mono dim", text: "排除 " + row.exclude_regex }) : null,
                el("div", { class: "dim", text: row.max_height ? "最高 " + row.max_height + "p" : "自动最佳画质" }),
              ].filter(Boolean)),
          },
          {
            title: "每轮",
            class: "num",
            render: (row) =>
              el("div", {}, [
                el("div", { text: "读 " + row.check_limit + " 条" }),
                el("div", { class: "cell-sub", text: "最多下 " + row.max_per_run + " 个" }),
              ]),
          },
          {
            title: "已下载",
            class: "num",
            render: (row) =>
              el("div", {}, [
                el("div", { text: String(row.total_downloaded || 0) }),
                el("div", { class: "cell-sub", text: "已记 " + (row.handled_count || 0) + " 个 ID" }),
              ]),
          },
          {
            title: "最近巡检",
            render: (row) =>
              el("div", {}, [
                el("div", { text: fmtRelative(row.last_checked_at) }),
                row.last_message
                  ? el("div", { class: "cell-sub truncate", title: row.last_message, text: row.last_message })
                  : null,
              ]),
          },
          {
            title: "失败",
            class: "num",
            render: (row) =>
              row.failure_count
                ? el("span", { class: "tag warn", text: String(row.failure_count) })
                : el("span", { class: "dim", text: "0" }),
          },
          {
            title: "操作",
            render: (row) =>
              el("div", { class: "row tight" }, [
                iconButton("巡检", "play", async () => {
                  try {
                    const result = await api("/video-subscribes/" + row.id + "/check", { method: "POST" });
                    toast(result.message || "巡检完成", result.success ? "ok" : "err");
                    pageVideoSub();
                  } catch (error) {
                    toast(error.message, "err");
                  }
                }, "sm"),
                iconButton("编辑", "edit", () => videoSubForm(row, pageVideoSub), "sm ghost"),
                iconButton("删除", "trash", async () => {
                  if (!confirm("确定删除视频订阅「" + row.name + "」？")) return;
                  try {
                    await api("/video-subscribes/" + row.id, { method: "DELETE" });
                    toast("已删除", "ok");
                    pageVideoSub();
                  } catch (error) {
                    toast(error.message, "err");
                  }
                }, "sm danger"),
              ]),
          },
        ],
        list,
        "还没有视频订阅。点「新建」贴一个 B 站空间页或 YouTube 频道页，新投稿出现就自动下载"
      ),
    ]);

    shell(
      el("div", { class: "grid" }, [stats, jobCard, listCard]),
      "视频追更",
      list.length
        ? "共 " + list.length + " 条订阅 · 累计下载 " + totalDownloaded + " 个视频"
        : "盯住 UP 主 / 频道，新投稿自动下载",
      [
        iconButton("新建订阅", "plus", () => videoSubForm(null, pageVideoSub), "primary"),
        iconButton("刷新", "refresh", () => pageVideoSub()),
      ]
    );
  }

  // ---------------- RSS 追新 ----------------
  //: 方言 key → 界面短标签。RSS 各站的字段差异很大（见后端 rss_dialects），
  //: 界面上标出来用户才知道"这个站为什么没有做种数"。
  const RSS_DIALECT_LABEL = {
    mikan: "蜜柑计划",
    nyaa: "Nyaa",
    dmhy: "动漫花园",
    acgnx: "acgnx",
    generic: "通用解析",
  };

  //: RSS 地址示例。贴错地址（贴网页而不是 RSS）是这类订阅最常见的失败，
  //: 直接把可用样例摆在表单里比写一句"请填 RSS 地址"有用得多。
  const RSS_SAMPLES = [
    { label: "蜜柑「我的番组」（推荐，只含你追的番）", url: "https://mikanani.me/RSS/MyBangumi?token=你的Token" },
    { label: "蜜柑全站最新（聚合流，务必配合订阅过滤）", url: "https://mikanani.me/RSS/Classic" },
    { label: "Nyaa 动画分类（英文源，有做种数）", url: "https://nyaa.si/?page=rss&c=1_2&f=0" },
    { label: "动漫花园全站（磁力链）", url: "https://share.dmhy.org/topics/rss/rss.xml" },
  ];

  /** 新建/编辑 RSS 源。row 为空表示新建。

      与另外几种追新的分工：「订阅追新」按片名去各站**搜索**，
      「追新雷达」拉各站最新流，本页处理的是用户自己贴的 **RSS 地址** ——
      番剧站的 RSS 根本不支持关键词查询，只能靠定时拉流。
  */
  function rssFeedForm(row, onDone) {
    const current = row || {};
    modal(
      row ? "编辑 RSS 源 · " + row.name : "新建 RSS 源",
      [
        { key: "name", label: "源名称", value: current.name || "", placeholder: "如：蜜柑我的番组" },
        {
          key: "url",
          label: "RSS / Atom 地址",
          value: current.url || "",
          placeholder: "https://mikanani.me/RSS/MyBangumi?token=...",
          hint: "必须是 RSS 地址而不是网页地址。填好后先点「预览」确认能解析出条目",
        },
        {
          key: "aggregate",
          label: "这是聚合流（一条 RSS 混着多部作品）",
          type: "checkbox",
          value: row ? !!current.aggregate : true,
          hint: "开着：逐条识别再与订阅匹配，只下命中订阅的。关掉表示整条流都是同一部作品，会全量下载——判断错会把整站新番拖回来",
        },
        {
          key: "subscribe_id",
          label: "绑定订阅 ID（仅非聚合流需要，留空自动匹配）",
          type: "number",
          value: current.subscribe_id || "",
          hint: "单番 RSS 可以直接绑定到某个订阅，省掉标题匹配这一步",
        },
        { key: "save_path", label: "保存目录（留空用订阅或默认目录）", value: current.save_path || "" },
        {
          key: "include_regex",
          label: "只要匹配（正则）",
          value: current.include_regex || "",
          placeholder: "1080p|简体",
        },
        {
          key: "exclude_regex",
          label: "排除匹配（正则）",
          value: current.exclude_regex || "",
          placeholder: "生肉|繁体|720p",
          hint: "两者同时命中时判为不要 —— 排除词的意图通常更明确",
        },
        {
          key: "cookie",
          label: "Cookie（仅需要登录的 PT 个人订阅地址）",
          value: "",
          hint: current.has_cookie ? "已保存过 Cookie，留空表示不修改" : "多数公开番剧 RSS 不需要",
        },
        {
          key: "max_per_run",
          label: "单轮最多下载几个",
          type: "number",
          value: current.max_per_run || 5,
          hint: "1~50。聚合流一轮可能命中很多条，这个上限防止一次把下载器打满",
        },
        {
          key: "skip_existing",
          label: "首次拉取只记账不补历史",
          type: "checkbox",
          value: row ? !!current.skip_existing : true,
          hint: "建议保持勾选：老 RSS 里躺着几十条历史条目，不勾会立刻投出几十个下载任务",
        },
        row ? { key: "enabled", label: "启用（参与定时巡检）", type: "checkbox", value: !!current.enabled } : null,
        row ? { key: "reset_failures", label: "清除失败计数", type: "checkbox", value: false, hint: "被自动停用的源会一并恢复启用" } : null,
        row ? { key: "reset_history", label: "清空已处理记录", type: "checkbox", value: false, hint: "下轮会把流里全部条目当作新增" } : null,
      ].filter(Boolean),
      async (values) => {
        const payload = {
          name: values.name,
          url: values.url,
          aggregate: !!values.aggregate,
          save_path: values.save_path || null,
          include_regex: values.include_regex || null,
          exclude_regex: values.exclude_regex || null,
          subscribe_id: values.subscribe_id ? Number(values.subscribe_id) : null,
          max_per_run: Number(values.max_per_run) || 5,
          skip_existing: !!values.skip_existing,
        };
        // Cookie 留空表示"不修改"，不能提交 null 把已保存的擦掉
        if (values.cookie) payload.cookie = values.cookie;
        if (row) {
          payload.enabled = !!values.enabled;
          payload.reset_failures = !!values.reset_failures;
          payload.reset_history = !!values.reset_history;
          await api("/rss-feeds/" + row.id, { method: "PATCH", body: payload });
          toast("已保存", "ok");
        } else {
          if (!payload.name || !payload.url) throw new Error("源名称与地址必填");
          const result = await api("/rss-feeds", { method: "POST", body: payload });
          toast(result.message || "RSS 源已添加", "ok");
        }
        if (onDone) onDone();
      },
      row ? "保存" : "创建",
      {
        wide: true,
        lead: "番剧站的 RSS 不支持关键词搜索，只能靠定时拉流追新。聚合流会按标题匹配你的订阅，只下命中的。",
      }
    );
  }

  /** 预览一条 RSS 能解析出什么。

      贴进来的地址对不对、是不是聚合流、能否拿到体积与做种数，
      只有真拉一次才知道；不预览就只能"先存下来，等下一轮定时任务过去了才发现不对"。
  */
  async function rssPreview(defaultUrl) {
    modal(
      "预览 RSS",
      [
        { key: "url", label: "RSS 地址", value: defaultUrl || "", placeholder: "https://mikanani.me/RSS/Classic" },
        { key: "cookie", label: "Cookie（需要登录的源才填）", value: "" },
        { key: "limit", label: "取前几条", type: "number", value: 20 },
      ],
      async (values) => {
        if (!values.url) throw new Error("请填 RSS 地址");
        const result = await api("/rss-feeds/preview", {
          method: "POST",
          body: {
            url: values.url,
            cookie: values.cookie || null,
            limit: Number(values.limit) || 20,
          },
        });
        const items = result.items || [];
        const titles = result.distinct_titles || [];
        panelModal(
          "预览结果",
          (RSS_DIALECT_LABEL[result.dialect] || result.dialect || "通用解析") +
            " · " + (result.title || "未命名源") + " · " + items.length + " 条",
          el("div", {}, [
            el("div", { class: "pad" }, [
              result.success
                ? el("div", { class: "row tight center wrap" }, [
                    el("span", { class: "tag dot ok", text: "解析成功" }),
                    el("span", {
                      class: "tag " + (result.suggest_aggregate ? "warn" : ""),
                      text: result.suggest_aggregate
                        ? "建议按聚合流处理（识别出 " + titles.length + " 部作品）"
                        : "看起来是单番流",
                    }),
                    el("span", { class: "dim tiny", text: result.message || "" }),
                  ])
                : emptyBox(result.message || "解析失败", "alert"),
            ]),
            titles.length
              ? el("div", { class: "pad" }, [
                  el("div", { class: "kv-label", text: "识别出的作品" }),
                  el("div", { class: "row tight wrap" }, titles.map((name) =>
                    el("span", { class: "tag tiny", text: name })
                  )),
                ])
              : null,
            items.length
              ? el("div", { class: "list" }, items.map((item) =>
                  el("div", { class: "list-row" }, [
                    el("span", {}, [
                      el("div", { class: "truncate", title: item.title, text: item.title }),
                      el("div", { class: "cell-sub tiny" }, [
                        el("span", { text: item.parsed_title || "未识别片名" }),
                        el("span", {
                          text: " · " +
                            (item.season ? "S" + item.season : "无季") +
                            " · " +
                            ((item.episodes || []).length ? "E" + item.episodes.join(",") : "无集号"),
                        }),
                      ]),
                    ]),
                    el("span", { class: "row tight center" }, [
                      item.resolution ? el("span", { class: "tag tiny", text: item.resolution }) : null,
                      el("span", { class: "tag tiny", text: item.kind === "magnet" ? "磁力" : "种子" }),
                      // 体积/做种数为 0 多半是方言没认出来，如实显示"未提供"而不是 0
                      el("span", {
                        class: "dim tiny mono",
                        text: item.size ? fmtSize(item.size) : "体积未提供",
                      }),
                      el("span", {
                        class: "dim tiny",
                        text: item.seeders ? item.seeders + " 做种" : "无做种数",
                      }),
                    ].filter(Boolean)),
                  ])
                ))
              : null,
          ].filter(Boolean)),
          true
        );
      },
      "预览",
      { lead: "先确认能解析出条目再添加。解析不出条目通常是贴了网页地址，或该源需要登录。" }
    );
  }

  /** 各站方言的字段差异说明（帮用户理解"为什么这个站没有做种数"）。 */
  async function rssDialectHelp() {
    const data = await api("/rss-feeds/dialects").catch(() => ({ items: [] }));
    panelModal(
      "支持的 RSS 站点方言",
      "各站把有用字段放在自己的命名空间里，本项目逐站适配",
      el("div", { class: "list" }, (data.items || []).map((item) =>
        el("div", { class: "list-row" }, [
          el("span", {}, [
            el("div", { class: "row tight center" }, [
              el("span", { text: RSS_DIALECT_LABEL[item.key] || item.key }),
              el("span", { class: "tag tiny mono", text: item.key }),
            ]),
            el("div", { class: "cell-sub", text: item.note || "" }),
          ]),
        ])
      )),
      true
    );
  }

  /** 实测清单批量导入向导（BT 站点 / RSS 源共用）。

      为什么做成"先列清单再勾选"而不是直接一键全装：清单里每条都有
      已知缺陷（caveat），比如 BD电影首发站的 size 恒为 0。用户该在
      导入前看到这些，而不是装完发现列表里体积全是 0 再来怀疑是 bug。
   */
  async function catalogImportDialog(opts) {
    let data;
    try {
      data = await api(opts.listPath);
    } catch (error) {
      toast(error.message, "err");
      return;
    }
    const items = data.items || [];
    if (!items.length) {
      toast("清单为空", "warn");
      return;
    }
    const picked = {};
    items.forEach((item) => {
      // 已装过的默认不勾，避免用户以为"又装了一遍"
      picked[item.id] = !item.installed;
    });

    const rows = items.map((item) => {
      const box = el("input", { type: "checkbox" });
      box.checked = !!picked[item.id];
      if (item.installed) box.disabled = true;
      box.addEventListener("change", () => {
        picked[item.id] = box.checked;
      });
      return el("label", { class: "list-row pick" }, [
        el("span", { class: "row tight center" }, [
          box,
          el("span", {}, [
            el("div", { class: "row tight center wrap" }, [
              el("span", { text: item.name }),
              item.installed ? el("span", { class: "tag tiny ok", text: "已添加" }) : null,
              item.dialect
                ? el("span", { class: "tag tiny", text: RSS_DIALECT_LABEL[item.dialect] || item.dialect })
                : null,
              item.adult ? el("span", { class: "tag tiny warn", text: "成人向" }) : null,
            ].filter(Boolean)),
            item.measured
              ? el("div", { class: "cell-sub tiny", text: "实测：" + item.measured })
              : null,
            item.caveat ? el("div", { class: "cell-sub tiny warn-text", text: item.caveat }) : null,
            item.description ? el("div", { class: "cell-sub tiny dim", text: item.description }) : null,
          ].filter(Boolean)),
        ]),
      ]);
    });

    modal(opts.title, [
      el("p", { class: "dim tiny", text: opts.hint }),
      el("div", { class: "list" }, rows),
    ], async () => {
      const ids = Object.keys(picked).filter((key) => picked[key]);
      if (!ids.length) {
        toast("请至少勾选一项", "warn");
        return false;
      }
      try {
        const query = ids.map((id) => "ids=" + encodeURIComponent(id)).join("&");
        const result = await api(opts.importPath + "?" + query, { method: "POST" });
        toast(result.message || "已导入", "ok");
        if (opts.onDone) opts.onDone();
      } catch (error) {
        toast(error.message, "err");
        return false;
      }
    }, "导入所选");
  }

  async function pageRssFeeds() {
    shell(loading(), "RSS 追新", "贴 RSS 地址自动追新，聚合流按订阅分流");
    const [data, schedules] = await Promise.all([
      api("/rss-feeds"),
      api("/schedules").catch(() => ({ items: [] })),
    ]);
    const list = data.items || [];
    const stats = data.stats || {};
    const job = (schedules.items || []).find((item) => item.key === "rss");

    const statsRow = el("div", { class: "grid cols-4" }, [
      statCard("RSS 源", String(stats.total || 0), "已添加的追新流", "link"),
      statCard("启用中", String(stats.enabled || 0), "会被定时巡检的源", "play"),
      statCard("聚合流", String(stats.aggregate || 0), "混着多部作品、需按订阅分流", "layers"),
      statCard("累计下载", String(stats.downloaded || 0), "历史新增的下载任务", "download"),
    ]);

    const jobCard = el("div", { class: "card" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("clock", "sm"), el("span", { text: "巡检节奏" })]),
        job && job.enabled
          ? el("span", { class: "tag dot ok", text: "已启用" })
          : el("span", { class: "tag dot warn", text: job ? "已关闭" : "未注册" }),
      ]),
      job
        ? el("div", { class: "kv" }, [
            el("div", { class: "kv-item" }, [
              el("div", { class: "kv-label", text: "当前规则" }),
              el("div", { class: "mono", text: job.trigger === "cron" ? job.cron : "每 " + job.minutes + " 分钟" }),
            ]),
            el("div", { class: "kv-item" }, [
              el("div", { class: "kv-label", text: "下次执行" }),
              el("div", {}, [
                el("div", { text: fmtRelative(job.next_run_time) }),
                el("div", { class: "cell-sub", text: fmtTime(job.next_run_time) }),
              ]),
            ]),
          ])
        : emptyBox("调度器未运行，RSS 源只能手动巡检", "clock"),
      job
        ? el("div", { class: "row tight", style: "margin-top:16px" }, [
            iconButton("修改周期", "edit", () => scheduleForm(job, pageRssFeeds), "sm primary"),
            iconButton("立即执行", "play", () => runSchedule(job, pageRssFeeds), "sm"),
          ])
        : null,
    ]);

    const sampleCard = el("div", { class: "card" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("info", "sm"), el("span", { text: "常用 RSS 地址" })]),
        iconButton("字段差异说明", "layers", () => rssDialectHelp(), "sm ghost"),
      ]),
      el("div", { class: "list" }, RSS_SAMPLES.map((sample) =>
        el("div", { class: "list-row" }, [
          el("span", {}, [
            el("div", { text: sample.label }),
            el("div", { class: "cell-sub mono tiny truncate", title: sample.url, text: sample.url }),
          ]),
          el("span", { class: "row tight" }, [
            iconButton("预览", "search", () => rssPreview(sample.url), "sm ghost"),
            iconButton("添加", "plus", () => rssFeedForm({ url: sample.url, name: sample.label, aggregate: true, skip_existing: true, max_per_run: 5 }, pageRssFeeds), "sm"),
          ]),
        ])
      )),
    ]);

    const listCard = el("div", { class: "card flush" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("inbox", "sm"), el("span", { text: "RSS 源（" + list.length + "）" })]),
        el("div", { class: "row tight center" }, [
          iconButton("试运行全部", "eye", async () => {
            try {
              const result = await api("/rss-feeds/check-all?dry_run=true", { method: "POST" });
              toast("试运行完成：检查 " + (result.checked || 0) + " 个源（未实际下载）", "ok");
              pageRssFeeds();
            } catch (error) {
              toast(error.message, "err");
            }
          }, "sm ghost"),
          iconButton("立即巡检全部", "play", async () => {
            try {
              const result = await api("/rss-feeds/check-all", { method: "POST" });
              toast("巡检完成：新增下载 " + (result.downloaded || 0) + " 个", "ok");
              pageRssFeeds();
            } catch (error) {
              toast(error.message, "err");
            }
          }, "sm"),
          iconButton("预览地址", "search", () => rssPreview(""), "sm ghost"),
          iconButton("推荐源", "sparkles", () => catalogImportDialog({
            title: "实测可用的 RSS 源",
            hint: "以下源均已实测可拉通（括号内为实测条目数）。聚合流会混着多部作品，请配合订阅或标题规则过滤。",
            listPath: "/rss-feeds/presets?include_adult=true",
            importPath: "/rss-feeds/presets/import",
            onDone: pageRssFeeds,
          }), "sm ghost"),
          iconButton("新建", "plus", () => rssFeedForm(null, pageRssFeeds), "sm primary"),
        ]),
      ]),
      table(
        [
          {
            title: "RSS 源",
            render: (row) =>
              el("div", {}, [
                el("div", { class: "row tight center wrap" }, [
                  el("span", { text: row.name }),
                  el("span", { class: "tag tiny", text: RSS_DIALECT_LABEL[row.dialect] || row.dialect }),
                  row.aggregate
                    ? el("span", { class: "tag tiny", text: "聚合流" })
                    : el("span", { class: "tag tiny ok", text: "单番流" }),
                  row.has_cookie ? el("span", { class: "tag tiny", text: "带 Cookie" }) : null,
                  !row.enabled ? el("span", { class: "tag warn tiny", text: "已停用" }) : null,
                ].filter(Boolean)),
                el("div", { class: "cell-sub mono tiny truncate", title: row.url, text: row.url }),
              ]),
          },
          {
            title: "过滤 / 落地",
            render: (row) =>
              el("div", { class: "stack tiny" }, [
                row.include_regex ? el("div", { class: "mono dim", text: "只要 " + row.include_regex }) : null,
                row.exclude_regex ? el("div", { class: "mono dim", text: "排除 " + row.exclude_regex }) : null,
                row.subscribe_id ? el("div", { class: "dim", text: "绑定订阅 #" + row.subscribe_id }) : null,
                el("div", { class: "dim truncate", title: row.save_path || "", text: row.save_path || "默认目录" }),
              ].filter(Boolean)),
          },
          {
            title: "单轮上限",
            class: "num",
            render: (row) => el("div", { text: String(row.max_per_run || 5) }),
          },
          {
            title: "已下载",
            class: "num",
            render: (row) =>
              el("div", {}, [
                el("div", { text: String(row.total_downloaded || 0) }),
                el("div", { class: "cell-sub", text: "已记 " + (row.handled_count || 0) + " 条 guid" }),
              ]),
          },
          {
            title: "最近巡检",
            render: (row) =>
              el("div", {}, [
                el("div", { text: fmtRelative(row.last_checked_at) }),
                row.last_message
                  ? el("div", { class: "cell-sub truncate", title: row.last_message, text: row.last_message })
                  : null,
              ]),
          },
          {
            title: "失败",
            class: "num",
            render: (row) =>
              row.failure_count
                ? el("span", { class: "tag warn", text: String(row.failure_count) })
                : el("span", { class: "dim", text: "0" }),
          },
          {
            title: "操作",
            render: (row) =>
              el("div", { class: "row tight" }, [
                iconButton("试运行", "eye", async () => {
                  try {
                    const result = await api("/rss-feeds/" + row.id + "/check?dry_run=true", { method: "POST" });
                    toast(result.message || "试运行完成", result.success ? "ok" : "err");
                    pageRssFeeds();
                  } catch (error) {
                    toast(error.message, "err");
                  }
                }, "sm ghost"),
                iconButton("巡检", "play", async () => {
                  try {
                    const result = await api("/rss-feeds/" + row.id + "/check", { method: "POST" });
                    toast(result.message || "巡检完成", result.success ? "ok" : "err");
                    pageRssFeeds();
                  } catch (error) {
                    toast(error.message, "err");
                  }
                }, "sm"),
                iconButton("编辑", "edit", () => rssFeedForm(row, pageRssFeeds), "sm ghost"),
                iconButton("删除", "trash", async () => {
                  if (!confirm("确定删除 RSS 源「" + row.name + "」？")) return;
                  try {
                    await api("/rss-feeds/" + row.id, { method: "DELETE" });
                    toast("已删除", "ok");
                    pageRssFeeds();
                  } catch (error) {
                    toast(error.message, "err");
                  }
                }, "sm danger"),
              ]),
          },
        ],
        list,
        "还没有 RSS 源。上面「常用 RSS 地址」里挑一个添加，或点「新建」贴自己的地址"
      ),
    ]);

    shell(
      el("div", { class: "grid" }, [
        statsRow,
        el("div", { class: "grid cols-2" }, [jobCard, sampleCard]),
        listCard,
      ]),
      "RSS 追新",
      list.length
        ? "共 " + list.length + " 个源 · 累计下载 " + (stats.downloaded || 0) + " 个"
        : "贴 RSS 地址自动追新，聚合流按订阅分流",
      [
        iconButton("新建 RSS 源", "plus", () => rssFeedForm(null, pageRssFeeds), "primary"),
        iconButton("刷新", "refresh", () => pageRssFeeds()),
      ]
    );
  }

  /** 检查更新。

      结果必须带「结论怎么来的」：仓库有 Release 时按 tag 比对；一个 Release
      都没有时退回读主干版本号与最新提交。不区分这两条路的话，没发版的仓库会
      永远回答"已是最新版本" —— 一个不报错的假功能。
  */
  async function checkUpdate(force) {
    let data;
    try {
      data = await api("/system/update/check" + (force ? "?force=true" : ""));
    } catch (error) {
      toast("检查更新失败：" + error.message, "err");
      return;
    }
    const isSource = data.mode === "source";
    const rows = [
      { label: "当前版本", value: "v" + (data.current || "?") + (data.current_commit ? " (" + data.current_commit + ")" : "") },
      { label: "上游版本", value: data.latest ? "v" + data.latest + (data.latest_commit ? " (" + data.latest_commit + ")" : "") : "未知" },
      {
        label: "判定依据",
        value: data.source === "release"
          ? "GitHub Release（按 tag 比对）"
          : data.source === "branch"
            ? "主干分支（仓库暂无 Release，读 main 的版本号与最新提交）"
            : "未知",
      },
      { label: "部署形态", value: isSource ? "源码部署（可一键 git pull）" : "容器部署（需在宿主机更新镜像）" },
      data.cached ? { label: "数据来源", value: "30 分钟缓存（GitHub 未鉴权限 60 次/小时，避免连点被限流）" } : null,
    ].filter(Boolean);

    panelModal(
      data.has_update ? "发现新版本 v" + data.latest : "版本检查结果",
      data.message || "",
      el("div", {}, [
        el("div", { class: "pad" }, [
          el("div", { class: "row tight center wrap" }, [
            data.has_update
              ? el("span", { class: "tag dot warn", text: "有可用更新" })
              : el("span", { class: "tag dot ok", text: "无需更新" }),
            el("span", { class: "tag tiny", text: isSource ? "源码部署" : "容器部署" }),
          ]),
        ]),
        el("div", { class: "list" }, rows.map((row) =>
          el("div", { class: "list-row" }, [
            el("span", { class: "kv-label", text: row.label }),
            el("span", { class: "mono tiny", text: row.value }),
          ])
        )),
        data.notes
          ? el("div", { class: "pad" }, [
              el("div", { class: "kv-label", text: "上游说明" }),
              el("pre", { class: "pre-wrap mono tiny", text: String(data.notes).slice(0, 2000) }),
            ])
          : null,
        el("div", { class: "pad row tight wrap" }, [
          iconButton("重新检查（忽略缓存）", "refresh", () => checkUpdate(true), "sm ghost"),
          data.can_apply
            ? iconButton("执行更新（git pull）", "download", async () => {
                try {
                  const result = await api("/system/update/apply", { method: "POST" });
                  if (result.success) {
                    toast((result.message || "更新完成") + (result.restart_required ? "，请重启服务生效" : ""), "ok");
                  } else {
                    // 失败原因往往是"本地有未提交改动"，必须原话给出来，
                    // 我们刻意不自动 reset/merge 去替用户丢掉他的修改
                    panelModal("更新未执行", result.message || "", el("div", { class: "pad" }, [
                      result.detail ? el("pre", { class: "pre-wrap mono tiny", text: result.detail }) : null,
                      (result.commands || []).length
                        ? el("div", {}, [
                            el("div", { class: "kv-label", text: "请在宿主机执行" }),
                            el("pre", { class: "pre-wrap mono tiny", text: (result.commands || []).join("\n") }),
                          ])
                        : null,
                    ].filter(Boolean)), true);
                  }
                } catch (error) {
                  toast(error.message, "err");
                }
              }, "sm primary")
            : null,
          el("a", {
            class: "btn sm ghost",
            href: data.url || "https://github.com/wengdajie/CineFlow",
            target: "_blank",
            rel: "noreferrer",
            text: "在 GitHub 查看",
          }),
        ].filter(Boolean)),
        !data.can_apply
          ? el("div", { class: "pad" }, [
              emptyBox(
                "容器部署时程序无法替换自己的镜像（我们刻意不挂载 docker.sock —— 那等于把宿主机控制权交给本进程）。请在宿主机执行：docker compose pull && docker compose up -d",
                "info"
              ),
            ])
          : null,
      ].filter(Boolean)),
      true
    );
  }

  // ---------------- ChatOps 机器人 ----------------
  function copyText(text) {
    const value = String(text || "");
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(value).catch(() => {});
      return;
    }
    const area = el("textarea", { style: "position:fixed;opacity:0" });
    area.value = value;
    document.body.appendChild(area);
    area.select();
    try {
      document.execCommand("copy");
    } catch (err) {
      /* 忽略：老浏览器不支持时用户可手动复制 */
    }
    area.remove();
  }

  async function pageChatops() {
    shell(loading(), "机器人", "飞书 / 钉钉 / Telegram 指令控制");
    const [platforms, config, commands, audit] = await Promise.all([
      api("/chatops/platforms"),
      api("/chatops/config"),
      api("/chatops/commands"),
      api("/chatops/audit?limit=50").catch(() => ({ items: [] })),
    ]);

    const cfg = config.data || {};
    const origin = location.origin;

    // ---- 总开关 ----
    const globalCard = el("div", { class: "card" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("robot", "sm"), el("span", { text: "全局设置" })]),
        el("span", {
          class: "tag dot " + (cfg.enabled ? "ok" : "warn"),
          text: cfg.enabled ? "已启用" : "已停用",
        }),
      ]),
      el("div", { class: "muted", text: "机器人收到消息后会解析成搜索/下载/订阅等指令并执行，所有指令都会记入审计日志。" }),
      el("div", { class: "divider" }),
      el("div", { class: "kv" }, [
        el("div", { class: "kv-item" }, [
          el("div", { class: "kv-label", text: "自动下载" }),
          el("div", { text: cfg.auto_download ? "搜索后自动下最优" : "只回列表等确认" }),
        ]),
        el("div", { class: "kv-item" }, [
          el("div", { class: "kv-label", text: "回复条数" }),
          el("div", { class: "mono", text: String(cfg.result_limit) }),
        ]),
        el("div", { class: "kv-item" }, [
          el("div", { class: "kv-label", text: "白名单" }),
          el("div", { class: "mono tiny", text: (cfg.allow_users || []).join("、") || "不限制" }),
        ]),
      ]),
      el("div", { class: "row tight", style: "margin-top:16px" }, [
        iconButton("修改全局设置", "edit", () => {
          modal(
            "ChatOps 全局设置",
            [
              { key: "enabled", label: "启用机器人", type: "checkbox", value: !!cfg.enabled },
              {
                key: "auto_download",
                label: "搜索后自动下载最优资源",
                type: "checkbox",
                value: !!cfg.auto_download,
                hint: "关闭时机器人只回列表，用户回复「下载 2」再下",
              },
              { key: "result_limit", label: "搜索结果回复条数", type: "number", value: cfg.result_limit },
              {
                key: "allow_users",
                label: "用户白名单",
                value: (cfg.allow_users || []).join(","),
                hint: "各平台的用户 ID，英文逗号分隔；留空表示所有人可用",
              },
            ],
            async (values) => {
              await api("/chatops/config", {
                method: "PUT",
                body: {
                  enabled: values.enabled,
                  auto_download: values.auto_download,
                  result_limit: values.result_limit,
                  allow_users: String(values.allow_users || "")
                    .split(",")
                    .map((item) => item.trim())
                    .filter(Boolean),
                },
              });
              toast("已保存", "ok");
              pageChatops();
            },
            "保存"
          );
        }, "sm primary"),
      ]),
    ]);

    // ---- 平台配置 ----
    const platformCards = (platforms.items || []).map((item) => {
      const webhook = origin + item.webhook_path;
      const saved = (cfg.platforms || {})[item.platform] || {};
      return el("div", { class: "card" }, [
        el("div", { class: "card-head" }, [
          el("h3", {}, [icon("link", "sm"), el("span", { text: item.display_name })]),
          el("span", {
            class: "tag dot " + (item.configured ? "ok" : ""),
            text: item.configured ? "已配置" : "未配置",
          }),
        ]),
        el("div", { class: "dim tiny", text: item.setup_hint }),
        el("div", { class: "webhook-box" }, [
          el("span", { class: "mono truncate", title: webhook, text: webhook }),
          iconButton("复制", "link", () => {
            copyText(webhook);
            toast("回调地址已复制", "ok");
          }, "sm ghost"),
        ]),
        el("div", { class: "kv" }, (item.fields || []).map((field) =>
          el("div", { class: "kv-item" }, [
            el("div", { class: "kv-label", text: field.label }),
            el("div", {
              class: "mono tiny",
              text: saved[field.key] ? String(saved[field.key]) : "未填写",
            }),
          ])
        )),
        el("div", { class: "row tight", style: "margin-top:16px" }, [
          iconButton("配置密钥", "edit", () => {
            modal(
              item.display_name + " 配置",
              (item.fields || []).map((field) => ({
                key: field.key,
                label: field.label,
                value: saved[field.key] !== undefined ? saved[field.key] : "",
                hint: field.hint,
              })),
              async (values) => {
                const body = { platforms: {} };
                body.platforms[item.platform] = values;
                await api("/chatops/config", { method: "PUT", body: body });
                toast(item.display_name + " 配置已保存", "ok");
                pageChatops();
              },
              "保存",
              { lead: "密钥显示为 ****** 时表示保持原值不变；" + item.setup_hint }
            );
          }, "sm primary"),
        ]),
      ]);
    });

    // ---- 试指令 ----
    const tryInput = el("input", { class: "input", placeholder: "例如：搜索 沙丘 / 订阅 苍兰诀 第2季 / 状态" });
    const tryOut = el("pre", { class: "logs", style: "min-height:120px", text: "在上方输入指令后点「执行」，这里会显示机器人的真实回复。" });
    const tryCard = el("div", { class: "card" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("play", "sm"), el("span", { text: "指令试跑（不经过平台）" })]),
      ]),
      el("div", { class: "row" }, [
        tryInput,
        iconButton("执行", "play", async () => {
          const text = tryInput.value.trim();
          if (!text) {
            toast("请输入指令", "err");
            return;
          }
          tryOut.textContent = "执行中…";
          try {
            const result = await api("/chatops/test", { method: "POST", body: { text: text } });
            tryOut.textContent = result.reply || "（无回复）";
          } catch (error) {
            tryOut.textContent = "出错：" + error.message;
          }
        }, "primary"),
        iconButton("只解析", "info", async () => {
          const text = tryInput.value.trim();
          if (!text) return;
          try {
            const result = await api("/chatops/parse", { method: "POST", body: { text: text } });
            tryOut.textContent = JSON.stringify(result.data, null, 2);
          } catch (error) {
            tryOut.textContent = "无法识别：" + error.message;
          }
        }, "ghost"),
      ]),
      tryOut,
    ]);

    // ---- 指令表 ----
    const commandCard = el("div", { class: "card flush" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("logs", "sm"), el("span", { text: "支持的指令" })]),
      ]),
      table(
        [
          { title: "指令", render: (row) => el("span", { class: "mono", text: row.name }) },
          {
            title: "可用说法（别名）",
            render: (row) =>
              el("div", { class: "chips" }, row.aliases.map((alias) => el("span", { class: "chip", text: alias }))),
          },
        ],
        commands.commands || [],
        "无"
      ),
    ]);

    // ---- 审计 ----
    const auditCard = el("div", { class: "card flush" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("logs", "sm"), el("span", { text: "指令审计（" + (audit.items || []).length + "）" })]),
        iconButton("刷新", "refresh", () => pageChatops(), "sm ghost"),
      ]),
      table(
        [
          { title: "渠道", render: (row) => el("span", { class: "tag", text: row.source }) },
          { title: "用户", render: (row) => row.actor || row.actor_id || "-" },
          { title: "指令", render: (row) => el("span", { class: "mono", text: row.action || "-" }) },
          { title: "原文", render: (row) => el("div", { class: "truncate tiny", title: row.command, text: row.command || "-" }) },
          {
            title: "结果",
            render: (row) =>
              el("span", { class: "tag dot " + (row.success ? "ok" : "err"), text: row.success ? "成功" : "失败" }),
          },
          { title: "时间", render: (row) => fmtTime(row.created_at) },
        ],
        audit.items || [],
        "还没有指令记录"
      ),
    ]);

    shell(
      el("div", { class: "grid" }, [
        globalCard,
        el("div", { class: "grid cols-3" }, platformCards),
        tryCard,
        el("div", { class: "grid cols-2" }, [commandCard, auditCard]),
      ]),
      "机器人",
      "在飞书/钉钉/Telegram 里发指令即可搜索、下载、订阅",
      [iconButton("刷新", "refresh", () => pageChatops())]
    );
  }

  // ---------------- 站点健康 ----------------
  const HEALTH_TAGS = {
    ok: ["正常", "ok"],
    degraded: ["亚健康", "warn"],
    down: ["掉线", "err"],
    unknown: ["未探测", ""],
  };

  function healthTag(status) {
    const pair = HEALTH_TAGS[status] || [status, ""];
    return el("span", { class: "tag dot " + pair[1], text: pair[0] });
  }

  async function pageSiteHealth() {
    shell(loading(), "站点健康", "主动探测站点，提前发现 Cookie 过期与掉线");
    const [data, records] = await Promise.all([
      api("/site-health"),
      api("/site-health/records?limit=60").catch(() => ({ items: [] })),
    ]);
    const counts = data.counts || {};
    const items = data.items || [];
    const history = records.items || [];

    const stats = el("div", { class: "grid cols-4" }, [
      statCard("正常", String(counts.ok || 0), "搜索有结果、响应正常", "check"),
      statCard(
        "亚健康",
        String(counts.degraded || 0),
        counts.degraded ? "能连通但 0 结果或极慢，多半是 Cookie 过期" : "没有异常站点",
        "alert"
      ),
      statCard("掉线", String(counts.down || 0), counts.down ? "连不通或报错，检查地址与网络" : "全部可连通", "close"),
      statCard("未探测", String(counts.unknown || 0), "还没跑过巡检的站点", "info"),
    ]);

    const configCard = el("div", { class: "card" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("pulse", "sm"), el("span", { text: "巡检策略" })]),
        el("span", { class: "tag " + (data.enabled ? "brand" : "warn"), text: data.enabled ? "已启用" : "已关闭" }),
      ]),
      el("div", { class: "kv" }, [
        el("div", { class: "kv-item" }, [
          el("div", { class: "kv-label", text: "巡检间隔" }),
          el("div", { text: (data.interval_minutes || 0) + " 分钟" }),
        ]),
        el("div", { class: "kv-item" }, [
          el("div", { class: "kv-label", text: "告警阈值" }),
          el("div", { text: "连续 " + (data.fail_threshold || 3) + " 次异常才通知" }),
        ]),
        el("div", { class: "kv-item" }, [
          el("div", { class: "kv-label", text: "自动停用" }),
          el("div", { text: data.auto_disable ? "开启（连续失败自动禁用站点）" : "关闭" }),
        ]),
        el("div", { class: "kv-item" }, [
          el("div", { class: "kv-label", text: "历史记录" }),
          el("div", { text: String(data.total_records || 0) + " 条" }),
        ]),
      ]),
      el("div", { class: "muted", style: "margin-top:12px" }, [
        el("span", {
          text:
            "搜索类站点会真的搜一次（而不是只探首页）——Cookie 过期时首页照样能打开，" +
            "只有搜索结果会变成 0 条，这才是最难发现的故障。",
        }),
      ]),
    ]);

    const listCard = el("div", { class: "card flush" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("server", "sm"), el("span", { text: "站点状态（" + items.length + "）" })]),
      ]),
      table(
        [
          {
            title: "站点",
            render: (row) =>
              el("div", {}, [
                el("div", { class: "row tight center" }, [
                  el("span", { text: row.site }),
                  row.enabled ? null : el("span", { class: "tag", text: "已禁用" }),
                ]),
                el("div", { class: "cell-sub mono tiny", text: row.kind + " · " + row.provider }),
              ]),
          },
          { title: "状态", render: (row) => healthTag(row.status) },
          { title: "耗时", class: "num", render: (row) => (row.latency_ms ? row.latency_ms + " ms" : "-") },
          { title: "结果数", class: "num", render: (row) => String(row.result_count || 0) },
          {
            title: "说明",
            render: (row) => el("span", { class: "tiny truncate", title: row.message, text: row.message || "-" }),
          },
          { title: "最近探测", render: (row) => fmtRelative(row.checked_at) },
          {
            title: "操作",
            render: (row) =>
              canDo("operator")
                ? iconButton("探测", "play", async () => {
                    try {
                      const result = await api("/site-health/check/" + row.site_id, { method: "POST" });
                      toast(result.message || "探测完成", result.status === "ok" ? "ok" : "err");
                      pageSiteHealth();
                    } catch (error) {
                      toast(error.message, "err");
                    }
                  }, "sm")
                : el("span", { class: "dim tiny", text: "无权限" }),
          },
        ],
        items,
        "还没有站点。先到「站点管理」添加并启用站点"
      ),
    ]);

    const historyCard = el("div", { class: "card flush" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("logs", "sm"), el("span", { text: "探测历史（最近 " + history.length + " 条）" })]),
      ]),
      table(
        [
          { title: "时间", render: (row) => el("span", { class: "tiny", text: fmtTime(row.created_at) }) },
          { title: "站点", render: (row) => row.site },
          { title: "状态", render: (row) => healthTag(row.status) },
          { title: "耗时", class: "num", render: (row) => (row.latency_ms ? row.latency_ms + " ms" : "-") },
          {
            title: "说明",
            render: (row) => el("span", { class: "tiny truncate", title: row.message, text: row.message || "-" }),
          },
        ],
        history,
        "还没有探测记录，点右上角「立即巡检」跑一次"
      ),
    ]);

    const actions = [iconButton("刷新", "refresh", () => pageSiteHealth())];
    if (canDo("operator")) {
      actions.unshift(
        iconButton("立即巡检", "play", async () => {
          toast("正在逐站探测，可能需要十几秒…");
          try {
            const result = await api("/site-health/check", { method: "POST" });
            toast(
              "巡检完成：" + result.checked + " 个站点，异常 " + result.unhealthy + " 个",
              result.unhealthy ? "err" : "ok"
            );
            pageSiteHealth();
          } catch (error) {
            toast(error.message, "err");
          }
        }, "primary")
      );
    }

    shell(
      el("div", { class: "grid" }, [stats, configCard, listCard, historyCard]),
      "站点健康",
      items.length
        ? items.length + " 个站点 · 异常 " + ((counts.degraded || 0) + (counts.down || 0)) + " 个"
        : "主动探测站点可用性",
      actions
    );
  }

  // ---------------- 榜单自动订阅 ----------------
  function rankingForm(row, sources, onDone) {
    const current = row || {};
    const defaults = current.subscribe_defaults || {};
    modal(
      row ? "编辑榜单规则 · " + row.name : "新建榜单规则",
      [
        { key: "name", label: "规则名称", value: current.name || "", placeholder: "如：每周高分新剧" },
        {
          key: "source",
          label: "榜单来源",
          type: "select",
          value: current.source || "tmdb_trending",
          options: sources.map((item) => ({ value: item.value, label: item.label })),
          hint: "TMDB 榜需要配置 CF_TMDB_API_KEY；没配就用「本地资源热度榜」",
        },
        {
          key: "media_type",
          label: "类型",
          type: "select",
          value: current.media_type || "tv",
          options: [
            { value: "tv", label: "剧集" },
            { value: "movie", label: "电影" },
          ],
        },
        { key: "limit", label: "取榜单前 N 条", type: "number", value: current.limit || 10, hint: "1~100" },
        { key: "min_vote", label: "评分下限", type: "number", value: current.min_vote || 0, hint: "0 表示不限；TMDB 评分 0~10" },
        { key: "min_year", label: "年份下限", type: "number", value: current.min_year || "", hint: "留空不限，填 2024 就只要今年之后的" },
        { key: "include", label: "标题必须包含", value: current.include || "", placeholder: "留空不限，多个用 | 分隔" },
        { key: "exclude", label: "标题命中即跳过", value: current.exclude || "", placeholder: "真人秀|综艺" },
        {
          key: "best_version",
          label: "自动订阅时开启「最优版本」",
          type: "checkbox",
          value: !!defaults.best_version,
          hint: "会参与洗版（默认关闭，洗版会删旧文件）",
        },
        { key: "enabled", label: "启用（参与定时巡检）", type: "checkbox", value: row ? !!current.enabled : true },
        row
          ? {
              key: "reset_handled",
              label: "清空已处理记录",
              type: "checkbox",
              value: false,
              hint: "默认不会把你删掉的订阅再加回来；勾选后会重新扫一遍全榜",
            }
          : null,
      ].filter(Boolean),
      async (values) => {
        const payload = {
          name: values.name,
          source: values.source,
          media_type: values.media_type,
          limit: Number(values.limit) || 10,
          min_vote: Number(values.min_vote) || 0,
          min_year: values.min_year === null || values.min_year === "" ? null : Number(values.min_year),
          include: values.include || null,
          exclude: values.exclude || null,
          subscribe_defaults: { best_version: !!values.best_version },
          enabled: !!values.enabled,
        };
        if (row) {
          payload.reset_handled = !!values.reset_handled;
          await api("/ranking-rules/" + row.id, { method: "PATCH", body: payload });
          toast("已保存", "ok");
        } else {
          if (!payload.name) throw new Error("规则名称必填");
          await api("/ranking-rules", { method: "POST", body: payload });
          toast("榜单规则已创建", "ok");
        }
        if (onDone) onDone();
      },
      row ? "保存" : "创建",
      {
        wide: true,
        lead:
          "让「最近有什么好剧」自动变成订阅。单次最多新建的数量由 CF_RANKING_MAX_PER_RUN 限制，" +
          "避免一次刷进上百个订阅。",
      }
    );
  }

  /** 试算结果弹窗：先看清会订阅哪些，再决定要不要真的执行。 */
  function showRankingPreview(rule, result) {
    const listOf = (rows) =>
      el(
        "div",
        { class: "list" },
        rows.slice(0, 12).map((item) =>
          el("div", { class: "list-row" }, [
            el("span", { class: "tiny", text: item.title }),
            el("span", { class: "dim tiny", text: item.reason || "" }),
          ])
        )
      );

    panelModal(
      "试算 · " + rule.name,
      result.message,
      el("div", { class: "grid" }, [
        (result.items || []).length
          ? el("div", { class: "card soft" }, [
              el("div", { class: "card-head" }, [el("h3", { text: "将新增订阅（" + result.items.length + "）" })]),
              el(
                "div",
                { class: "list" },
                result.items.map((item) =>
                  el("div", { class: "list-row" }, [
                    el("span", { text: item.title }),
                    el("span", { class: "dim tiny", text: (item.year || "-") + " · 评分 " + (item.vote_average || "-") }),
                  ])
                )
              ),
            ])
          : emptyBox("这一轮不会新增订阅", "info"),
        (result.skipped || []).length
          ? el("div", { class: "card soft" }, [
              el("div", { class: "card-head" }, [el("h3", { text: "跳过（" + result.skipped.length + "）" })]),
              listOf(result.skipped),
            ])
          : null,
        (result.rejected || []).length
          ? el("div", { class: "card soft" }, [
              el("div", { class: "card-head" }, [el("h3", { text: "未通过条件（" + result.rejected.length + "）" })]),
              listOf(result.rejected),
            ])
          : null,
      ]),
      true
    );
  }

  async function pageRanking() {
    shell(loading(), "榜单订阅", "把热门榜/高分榜自动变成订阅");
    const [data, schedules] = await Promise.all([
      api("/ranking-rules"),
      api("/schedules").catch(() => ({ items: [] })),
    ]);
    const list = data.items || [];
    const sources = data.sources || [];
    const job = (schedules.items || []).find((item) => item.key === "ranking");

    const enabled = list.filter((item) => item.enabled).length;
    const created = list.reduce((sum, item) => sum + (item.created_count || 0), 0);

    const stats = el("div", { class: "grid cols-4" }, [
      statCard("榜单规则", String(list.length), "配置好的自动订阅规则", "trophy"),
      statCard("启用中", String(enabled), "会被定时任务执行", "play"),
      statCard("累计新增订阅", String(created), "由榜单自动创建的订阅数", "star"),
      statCard(
        "巡检周期",
        job ? (job.trigger === "cron" ? job.cron : (job.minutes || 0) + " 分钟") : "-",
        job && job.enabled ? "下次 " + fmtRelative(job.next_run_at) : "任务未启用",
        "clock"
      ),
    ]);

    const jobCard = el("div", { class: "card" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("clock", "sm"), el("span", { text: "定时执行" })]),
        job ? el("span", { class: "tag " + (job.enabled ? "brand" : "warn"), text: job.enabled ? "已启用" : "已关闭" }) : null,
      ]),
      el("div", { class: "muted" }, [
        el("span", {
          text: job
            ? "内置任务「" + job.name + "」按周期跑全部启用规则；周期可在这里或「定时任务」页修改。"
            : "调度信息不可用，请检查调度器状态。",
        }),
      ]),
      job && canDo("admin")
        ? el("div", { class: "row tight", style: "margin-top:12px" }, [
            iconButton("修改周期", "edit", () => scheduleForm(job, pageRanking), "sm primary"),
            iconButton("立即执行", "play", () => runSchedule(job, pageRanking), "sm"),
          ])
        : null,
    ]);

    const listCard = el("div", { class: "card flush" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("trophy", "sm"), el("span", { text: "规则（" + list.length + "）" })]),
        canDo("admin") ? iconButton("新建", "plus", () => rankingForm(null, sources, pageRanking), "sm primary") : null,
      ]),
      table(
        [
          {
            title: "规则",
            render: (row) =>
              el("div", {}, [
                el("div", { class: "row tight center" }, [
                  el("span", { text: row.name }),
                  row.enabled ? null : el("span", { class: "tag", text: "已停用" }),
                ]),
                el("div", { class: "cell-sub tiny", text: row.source_label }),
              ]),
          },
          { title: "类型", render: (row) => typeLabel(row.media_type) },
          {
            title: "条件",
            render: (row) =>
              el("div", { class: "stack tiny" }, [
                el("div", { class: "dim", text: "取前 " + row.limit + " 条" }),
                row.min_vote ? el("div", { class: "dim", text: "评分 ≥ " + row.min_vote }) : null,
                row.min_year ? el("div", { class: "dim", text: "年份 ≥ " + row.min_year }) : null,
                row.include ? el("div", { class: "dim", text: "含 " + row.include }) : null,
                row.exclude ? el("div", { class: "dim", text: "排除 " + row.exclude }) : null,
              ]),
          },
          { title: "已建订阅", class: "num", render: (row) => String(row.created_count || 0) },
          { title: "已处理", class: "num", render: (row) => String(row.handled_count || 0) },
          {
            title: "最近执行",
            render: (row) =>
              el("div", {}, [
                el("div", { text: fmtRelative(row.last_run_at) }),
                row.last_result
                  ? el("div", { class: "cell-sub truncate", title: row.last_result, text: row.last_result })
                  : null,
              ]),
          },
          {
            title: "操作",
            render: (row) =>
              el("div", { class: "row tight" }, [
                iconButton("试算", "info", async () => {
                  try {
                    const result = await api("/ranking-rules/" + row.id + "/preview", { method: "POST" });
                    showRankingPreview(row, result);
                  } catch (error) {
                    toast(error.message, "err");
                  }
                }, "sm ghost"),
                canDo("operator")
                  ? iconButton("执行", "play", async () => {
                      if (!confirm("立即执行「" + row.name + "」？会真的创建订阅。")) return;
                      try {
                        const result = await api("/ranking-rules/" + row.id + "/run", { method: "POST" });
                        toast(result.message || "执行完成", "ok");
                        pageRanking();
                      } catch (error) {
                        toast(error.message, "err");
                      }
                    }, "sm")
                  : null,
                canDo("admin") ? iconButton("编辑", "edit", () => rankingForm(row, sources, pageRanking), "sm ghost") : null,
                canDo("admin")
                  ? iconButton("删除", "trash", async () => {
                      if (!confirm("确定删除榜单规则「" + row.name + "」？已创建的订阅不受影响。")) return;
                      try {
                        await api("/ranking-rules/" + row.id, { method: "DELETE" });
                        toast("已删除", "ok");
                        pageRanking();
                      } catch (error) {
                        toast(error.message, "err");
                      }
                    }, "sm danger")
                  : null,
              ]),
          },
        ],
        list,
        "还没有榜单规则。点「新建」选一个榜单来源，之后会自动把上榜作品变成订阅"
      ),
    ]);

    const actions = [iconButton("刷新", "refresh", () => pageRanking())];
    if (canDo("operator") && list.length) {
      actions.unshift(
        iconButton("执行全部", "play", async () => {
          if (!confirm("立即执行所有启用规则？会真的创建订阅。")) return;
          try {
            const result = await api("/ranking-rules/run-all", { method: "POST" });
            toast((result.rules || 0) + " 条规则执行完成，新增 " + (result.created || 0) + " 个订阅", "ok");
            pageRanking();
          } catch (error) {
            toast(error.message, "err");
          }
        }, "primary")
      );
    }
    if (canDo("admin")) {
      actions.unshift(iconButton("新建规则", "plus", () => rankingForm(null, sources, pageRanking), "primary"));
    }

    shell(
      el("div", { class: "grid" }, [stats, jobCard, listCard]),
      "榜单订阅",
      list.length ? list.length + " 条规则 · 累计新增 " + created + " 个订阅" : "把榜单变成自动订阅",
      actions
    );
  }

  // ---------------- 过滤规则组 ----------------
  /** 层级用「每行一层」的文本编辑：字段少、可复制粘贴，比动态表单更好用也更耐改。 */
  function levelsToText(levels) {
    return (levels || [])
      .map((level) =>
        [level.name || "", level.resolution || "", level.quality || "", level.include || "", level.exclude || ""].join(" | ")
      )
      .join("\n");
  }

  function textToLevels(text) {
    return String(text || "")
      .split(/\n+/)
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        const cols = line.split("|").map((item) => item.trim());
        return {
          name: cols[0] || "",
          resolution: cols[1] || "",
          quality: cols[2] || "",
          include: cols[3] || "",
          exclude: cols[4] || "",
        };
      });
  }

  function ruleGroupForm(row, onDone) {
    const current = row || {};
    modal(
      row ? "编辑规则组 · " + row.name : "新建规则组",
      [
        { key: "name", label: "名称", value: current.name || "", placeholder: "如：1080p 中字优先" },
        { key: "description", label: "说明", value: current.description || "", placeholder: "这个规则组适合什么场景" },
        {
          key: "levels",
          label: "层级（每行一层，越靠前越优先）",
          type: "textarea",
          rows: 6,
          value: levelsToText(current.levels),
          hint: "格式：层名 | 分辨率 | 质量 | 必含 | 排除，留空即不限。示例：1080p中字 | 1080p | | 中字,简繁 |",
        },
        {
          key: "accept_unmatched",
          label: "接受未命中任何层的资源",
          type: "checkbox",
          value: row ? !!current.accept_unmatched : true,
          hint: "关掉就是「宁可不下，也不要不合规的版本」",
        },
        { key: "is_default", label: "设为默认组（全局生效）", type: "checkbox", value: !!current.is_default },
        { key: "enabled", label: "启用", type: "checkbox", value: row ? !!current.enabled : true },
      ],
      async (values) => {
        const payload = {
          name: values.name,
          description: values.description || null,
          levels: textToLevels(values.levels),
          accept_unmatched: !!values.accept_unmatched,
          is_default: !!values.is_default,
          enabled: !!values.enabled,
        };
        if (!payload.levels.length) throw new Error("至少写一行层级");
        if (row) {
          await api("/rule-groups/" + row.id, { method: "PATCH", body: payload });
          toast("已保存", "ok");
        } else {
          if (!payload.name) throw new Error("名称必填");
          await api("/rule-groups", { method: "POST", body: payload });
          toast("规则组已创建", "ok");
        }
        if (onDone) onDone();
      },
      row ? "保存" : "创建",
      {
        wide: true,
        lead:
          "全局评分只能表达「4K 比 1080p 好」这种单调偏好；规则组用有序分层表达" +
          "「宁可 1080p 中字，也不要没字幕的 4K」——层间定优先，层内仍按评分排序。",
      }
    );
  }

  /** 用固定样例试算：不联网、不跑搜索，纯看这组规则会怎么排序。 */
  async function previewRuleGroup(row) {
    const GB = 1024 * 1024 * 1024;
    const samples = [
      { title: "示例剧 S01E01.2160p.WEB-DL.H265.mkv", size: 6 * GB, seeders: 30, kind: "torrent" },
      { title: "示例剧 S01E01.1080p.BluRay.中字.mkv", size: 4 * GB, seeders: 80, kind: "torrent" },
      { title: "示例剧 S01E01.1080p.WEB-DL.mkv", size: 2 * GB, seeders: 50, kind: "torrent" },
      { title: "示例剧 S01E01.720p.HDTV.mp4", size: 0.9 * GB, seeders: 10, kind: "torrent" },
    ];
    try {
      const result = await api("/rule-groups/" + row.id + "/preview", {
        method: "POST",
        body: { resources: samples },
      });
      panelModal(
        "试算 · " + result.group,
        "用 4 个典型样例看排序结果（命中层号越小越优先）",
        el("div", { class: "grid" }, [
          el("div", { class: "card soft" }, [
            el("div", { class: "card-head" }, [el("h3", { text: "规则说明" })]),
            el(
              "div",
              { class: "stack tiny" },
              (result.summary || []).map((line) => el("div", { class: "dim", text: line }))
            ),
          ]),
          table(
            [
              { title: "排序", class: "num", render: (item, index) => String(index + 1) },
              { title: "资源", render: (item) => el("span", { class: "tiny", text: item.title }) },
              {
                title: "命中层",
                render: (item) =>
                  item.rule_level >= 9999
                    ? el("span", { class: "tag warn", text: "未命中（兜底）" })
                    : el("span", {
                        class: "tag brand",
                        text: "第 " + (item.rule_level + 1) + " 层 " + (item.rule_level_name || ""),
                      }),
              },
              { title: "评分", class: "num", render: (item) => String(item.score) },
            ],
            result.items || [],
            "全部被剔除"
          ),
          result.dropped
            ? el("div", {
                class: "muted",
                text: "有 " + result.dropped + " 个样例因未命中任何层被剔除（已关闭兜底接受）",
              })
            : null,
        ]),
        true
      );
    } catch (error) {
      toast(error.message, "err");
    }
  }

  async function pageRuleGroups() {
    shell(loading(), "过滤规则组", "有序分层的偏好，比单一评分更贴近真实需求");
    const data = await api("/rule-groups");
    const list = data.items || [];

    const stats = el("div", { class: "grid cols-4" }, [
      statCard("规则组", String(list.length), "可复用的偏好模板", "layers"),
      statCard("启用中", String(list.filter((item) => item.enabled).length), "可被订阅引用", "check"),
      statCard("默认组", data.default || "未设置", data.default ? "搜索与订阅默认套用" : "当前只按全局评分排序", "star"),
      statCard(
        "层级总数",
        String(list.reduce((sum, item) => sum + (item.level_count || 0), 0)),
        "所有规则组的层数合计",
        "chart"
      ),
    ]);

    const listCard = el("div", { class: "card flush" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("layers", "sm"), el("span", { text: "规则组（" + list.length + "）" })]),
        canDo("admin") ? iconButton("新建", "plus", () => ruleGroupForm(null, pageRuleGroups), "sm primary") : null,
      ]),
      table(
        [
          {
            title: "规则组",
            render: (row) =>
              el("div", {}, [
                el("div", { class: "row tight center" }, [
                  el("span", { text: row.name }),
                  row.is_default ? el("span", { class: "tag brand", text: "默认" }) : null,
                  row.enabled ? null : el("span", { class: "tag", text: "已停用" }),
                ]),
                row.description ? el("div", { class: "cell-sub tiny", text: row.description }) : null,
              ]),
          },
          {
            title: "层级",
            render: (row) =>
              el(
                "div",
                { class: "stack tiny" },
                (row.summary || []).slice(0, 5).map((line) => el("div", { class: "dim", text: line }))
              ),
          },
          { title: "层数", class: "num", render: (row) => String(row.level_count || 0) },
          {
            title: "兜底",
            render: (row) =>
              row.accept_unmatched
                ? el("span", { class: "tag", text: "接受其它" })
                : el("span", { class: "tag warn", text: "只要命中的" }),
          },
          {
            title: "操作",
            render: (row) =>
              el("div", { class: "row tight" }, [
                iconButton("试算", "info", () => previewRuleGroup(row), "sm ghost"),
                canDo("admin") && !row.is_default
                  ? iconButton("设为默认", "check", async () => {
                      try {
                        await api("/rule-groups/" + row.id, { method: "PATCH", body: { is_default: true } });
                        toast("已设为默认规则组", "ok");
                        pageRuleGroups();
                      } catch (error) {
                        toast(error.message, "err");
                      }
                    }, "sm")
                  : null,
                canDo("admin") ? iconButton("编辑", "edit", () => ruleGroupForm(row, pageRuleGroups), "sm ghost") : null,
                canDo("admin")
                  ? iconButton("删除", "trash", async () => {
                      if (!confirm("确定删除规则组「" + row.name + "」？引用它的订阅会自动解绑。")) return;
                      try {
                        await api("/rule-groups/" + row.id, { method: "DELETE" });
                        toast("已删除", "ok");
                        pageRuleGroups();
                      } catch (error) {
                        toast(error.message, "err");
                      }
                    }, "sm danger")
                  : null,
              ]),
          },
        ],
        list,
        "还没有规则组"
      ),
    ]);

    const helpCard = el("div", { class: "card" }, [
      el("div", { class: "card-head" }, [el("h3", {}, [icon("info", "sm"), el("span", { text: "怎么用" })])]),
      el(
        "div",
        { class: "stack" },
        [
          "1. 层级是有序的：命中靠前层的资源整体优于靠后层，层内再按既有评分排序。",
          "2. 设为「默认组」后，手动搜索与所有未单独绑定规则组的订阅都会套用它。",
          "3. 关掉「接受未命中」= 宁可这轮不下载，也不要不合规的版本。",
          "4. 点「试算」可以先看排序效果，不用真的跑一次搜索。",
        ].map((line) => el("div", { class: "muted", text: line }))
      ),
    ]);

    shell(
      el("div", { class: "grid" }, [stats, listCard, helpCard]),
      "过滤规则组",
      list.length ? list.length + " 个规则组 · 默认：" + (data.default || "未设置") : "配置有序的画质偏好",
      canDo("admin")
        ? [
            iconButton("新建规则组", "plus", () => ruleGroupForm(null, pageRuleGroups), "primary"),
            iconButton("刷新", "refresh", () => pageRuleGroups()),
          ]
        : [iconButton("刷新", "refresh", () => pageRuleGroups())]
    );
  }

  // ---------------- 用户与权限 ----------------
  function userForm(row, roles, onDone) {
    const current = row || {};
    modal(
      row ? "编辑用户 · " + row.username : "新增用户",
      [
        row ? null : { key: "username", label: "用户名", value: "", placeholder: "2~64 个字符" },
        {
          key: "password",
          label: row ? "重置密码" : "密码",
          type: "password",
          value: "",
          hint: row ? "留空表示不改密码" : "至少 6 位",
        },
        {
          key: "role",
          label: "角色",
          type: "select",
          value: current.role || "viewer",
          options: roles.map((item) => ({ value: item.value, label: item.label })),
          hint: "访客只读；操作员可搜索/订阅/下载；管理员可改配置与用户",
        },
        { key: "note", label: "备注", value: current.note || "", placeholder: "如：客厅电视用的账号" },
        { key: "is_active", label: "启用", type: "checkbox", value: row ? !!current.is_active : true },
      ].filter(Boolean),
      async (values) => {
        if (row) {
          const payload = { role: values.role, note: values.note || null, is_active: !!values.is_active };
          if (values.password) payload.password = values.password;
          await api("/users/" + row.id, { method: "PATCH", body: payload });
          toast("已保存", "ok");
        } else {
          if (!values.username || !values.password) throw new Error("用户名与密码必填");
          await api("/users", {
            method: "POST",
            body: {
              username: values.username,
              password: values.password,
              role: values.role,
              note: values.note || null,
              is_active: !!values.is_active,
            },
          });
          toast("用户已创建", "ok");
        }
        if (onDone) onDone();
      },
      row ? "保存" : "创建",
      { lead: "三档权限刻意做得很简单：管理员 / 操作员 / 访客。给家人开号建议选「操作员」。" }
    );
  }

  async function pageUsers() {
    shell(loading(), "用户权限", "多用户与三档角色");
    const data = await api("/users");
    const list = data.items || [];
    const roles = data.roles || [];

    const stats = el("div", { class: "grid cols-4" }, [
      statCard("用户", String(list.length), "本地账号总数", "users"),
      statCard("管理员", String(list.filter((item) => item.role === "admin").length), "可改配置与用户", "check"),
      statCard("操作员", String(list.filter((item) => item.role === "operator").length), "可订阅下载，不能改配置", "play"),
      statCard("访客", String(list.filter((item) => item.role === "viewer").length), "只读", "info"),
    ]);

    const listCard = el("div", { class: "card flush" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("users", "sm"), el("span", { text: "账号（" + list.length + "）" })]),
        iconButton("新增用户", "plus", () => userForm(null, roles, pageUsers), "sm primary"),
      ]),
      table(
        [
          {
            title: "用户",
            render: (row) =>
              el("div", {}, [
                el("div", { class: "row tight center" }, [
                  el("span", { text: row.username }),
                  row.username === store.username ? el("span", { class: "tag brand", text: "当前登录" }) : null,
                  row.is_active ? null : el("span", { class: "tag warn", text: "已停用" }),
                ]),
                row.note ? el("div", { class: "cell-sub tiny", text: row.note }) : null,
              ]),
          },
          {
            title: "角色",
            render: (row) =>
              el("span", {
                class: "tag " + (row.role === "admin" ? "brand" : row.role === "operator" ? "ok" : ""),
                text: row.role_label,
              }),
          },
          { title: "上次登录", render: (row) => fmtRelative(row.last_login_at) },
          { title: "创建时间", render: (row) => el("span", { class: "tiny", text: fmtTime(row.created_at) }) },
          {
            title: "操作",
            render: (row) =>
              el("div", { class: "row tight" }, [
                iconButton("编辑", "edit", () => userForm(row, roles, pageUsers), "sm ghost"),
                row.username === store.username
                  ? el("span", { class: "dim tiny", text: "不能删自己" })
                  : iconButton("删除", "trash", async () => {
                      if (!confirm("确定删除用户「" + row.username + "」？")) return;
                      try {
                        await api("/users/" + row.id, { method: "DELETE" });
                        toast("已删除", "ok");
                        pageUsers();
                      } catch (error) {
                        toast(error.message, "err");
                      }
                    }, "sm danger"),
              ]),
          },
        ],
        list,
        "没有用户"
      ),
    ]);

    const helpCard = el("div", { class: "card" }, [
      el("div", { class: "card-head" }, [el("h3", {}, [icon("info", "sm"), el("span", { text: "权限说明" })])]),
      el(
        "div",
        { class: "stack" },
        [
          "管理员：全部权限，包括改配置、管站点、管用户、改定时任务周期。",
          "操作员：搜索、订阅、下载、整理入库、转存、执行任务；不能改系统配置与用户。",
          "访客：只能查看各页面数据，任何写操作都会被服务端以 403 拒绝。",
          "自我保护：不能删除或停用自己，也不能把最后一个启用中的管理员降级。",
        ].map((line) => el("div", { class: "muted", text: line }))
      ),
    ]);

    shell(
      el("div", { class: "grid" }, [stats, listCard, helpCard]),
      "用户权限",
      list.length + " 个账号 · 三档角色（管理员/操作员/访客）",
      [
        iconButton("新增用户", "plus", () => userForm(null, roles, pageUsers), "primary"),
        iconButton("刷新", "refresh", () => pageUsers()),
      ]
    );
  }

  // ---------------- 设置（v1.5.0 起可在线编辑） ----------------
  /** 按字段元信息渲染一个控件，返回 { node, get, dirty }。 */
  function settingControl(item) {
    if (!item.editable) {
      return {
        node: el("div", { class: "row tight center" }, [
          el("span", {
            class: "mono" + (item.secret ? " dim" : ""),
            text: typeof item.value === "boolean" ? (item.value ? "开启" : "关闭") : String(item.value === "" ? "（空）" : item.value),
          }),
          el("span", { class: "tag tiny", text: "需重启" }),
        ]),
        get: () => undefined,
      };
    }

    const initial = item.raw;
    if (item.type === "bool") {
      const input = el("input", { type: "checkbox" });
      input.checked = !!initial;
      return {
        node: el("label", { class: "field-check inline" }, [input, el("span", { text: input.checked ? "开启" : "关闭" })]),
        get: () => input.checked,
        changed: () => input.checked !== !!initial,
      };
    }
    if (item.type === "choice") {
      const input = el(
        "select",
        { class: "input sm" },
        (item.choices || []).map((value) => el("option", { value: value, selected: value === initial }, value))
      );
      return { node: input, get: () => input.value, changed: () => input.value !== initial };
    }
    const input = el("input", {
      class: "input sm",
      type: item.type === "int" || item.type === "float" ? "number" : "text",
    });
    if (item.type === "int" || item.type === "float") {
      if (item.minimum !== null && item.minimum !== undefined) input.min = String(item.minimum);
      if (item.maximum !== null && item.maximum !== undefined) input.max = String(item.maximum);
      if (item.type === "float") input.step = "0.1";
    }
    input.value = initial === null || initial === undefined ? "" : String(initial);
    return {
      node: input,
      get: () => input.value.trim(),
      changed: () => input.value.trim() !== (initial === null || initial === undefined ? "" : String(initial)),
    };
  }

  /** 下载器表单：按后端下发的字段清单渲染，不再让用户手写 options JSON。

      字段元信息（类型/范围/可选值/说明）全部来自 ``/downloaders/schema``，
      所以新增下载器或新增参数时前端零改动。
  */
  function downloaderForm(schemaItems, existing) {
    const isEdit = Boolean(existing);
    // 编辑时锁定 provider：换 provider 等于换一套字段，语义上应该是删旧建新
    let provider = existing ? existing.provider : (schemaItems[0] || {}).provider;

    const open = () => {
      const spec = schemaItems.find((item) => item.provider === provider) || { fields: [] };
      const values = (existing && existing.values) || {};
      const fields = [
        {
          key: "name",
          label: "显示名",
          value: existing ? existing.name : spec.display_name,
          placeholder: "例：家里的 qBittorrent",
        },
        isEdit
          ? null
          : {
              key: "provider",
              label: "类型",
              type: "select",
              value: provider,
              options: schemaItems.map((item) => ({
                value: item.provider,
                label: item.display_name,
              })),
            },
      ].filter(Boolean);

      spec.fields.forEach((f) => {
        // 密码类字段已设置过就提示"留空不改"，避免用户以为丢了
        const isSecret = f.type === "password";
        const already = isSecret && values[f.key + "_set"];
        const raw = values[f.key];
        fields.push({
          key: f.key,
          label: f.label + (already ? "（已设置，留空不改）" : ""),
          type:
            f.type === "bool" ? "checkbox"
              : f.type === "password" ? "password"
              : f.type === "choice" ? "select"
              : f.type === "int" || f.type === "float" ? "number"
              : "text",
          value: isSecret
            ? ""
            : f.type === "bool"
            ? (raw === undefined ? Boolean(f.default) : Boolean(raw))
            : Array.isArray(raw)
            ? raw.join(",")
            : raw === undefined || raw === null
            ? (f.default === undefined ? "" : f.default)
            : raw,
          options: (f.choices || []).map((c) => ({ value: String(c), label: String(c) })),
          placeholder: f.placeholder || "",
          hint: f.hint || "",
        });
      });

      fields.push({
        key: "enabled",
        label: "启用",
        type: "checkbox",
        value: existing ? existing.enabled : false,
        hint: isEdit ? "" : "建议先保存再点「测试」验证连通性，通了再启用",
      });

      modal(
        isEdit ? "编辑下载器：" + existing.name : "添加下载器",
        fields,
        async (input) => {
          const picked = isEdit ? provider : input.provider || provider;
          const targetSpec =
            schemaItems.find((item) => item.provider === picked) || { fields: [] };
          const payloadValues = {};
          targetSpec.fields.forEach((f) => {
            const v = input[f.key];
            // 密码留空 = 不修改，别把空串提交上去把已存的密码清掉
            if (f.type === "password" && !String(v || "").trim()) return;
            if (v === undefined) return;
            payloadValues[f.key] = v;
          });
          const body = {
            name: input.name,
            provider: picked,
            enabled: Boolean(input.enabled),
            values: payloadValues,
          };
          if (isEdit) {
            await api("/downloaders/" + existing.id, { method: "PATCH", body: body });
          } else {
            await api("/downloaders", { method: "POST", body: body });
          }
          toast(isEdit ? "已保存" : "已添加，建议点「测试」验证连通性", "ok");
          pageSettings();
        },
        "保存",
        {
          lead: (schemaItems.find((item) => item.provider === provider) || {}).note || "",
        }
      );
    };
    open();
  }

  //: 限速时段的 phase → 展示文案。后端只给英文枚举，中文留在前端。
  const SPEED_PHASE = {
    peak: { label: "限速时段内", cls: "warn" },
    off_peak: { label: "限速时段外", cls: "ok" },
    disabled: { label: "未启用", cls: "" },
  };

  /** 设置页里的「下载器限速时段」卡片。

      解决的场景：白天要留带宽给家里其他人，夜里希望跑满。
      **只有下载器自己支持运行时限速时才有效**，迅雷 CGI / yt-dlp 不支持，
      下发结果里会如实标成「跳过」而不是假装成功。
  */
  function speedLimitCard(info, isAdmin) {
    const config = (info && info.config) || {};
    const current = (info && info.current) || {};
    const phase = SPEED_PHASE[current.phase] || SPEED_PHASE.disabled;
    // 0 在这里是「不限速」而不是「限到 0」——直接显示 0 会让人以为断流
    const kbText = (value) => (!value ? "不限速" : value + " KB/s");

    const body = el("div", {}, [
      el("div", { class: "kv" }, [
        el("div", { class: "kv-item" }, [
          el("div", { class: "kv-label", text: "当前状态" }),
          el("div", {}, [
            el("span", { class: "tag " + phase.cls + " tiny", text: phase.label }),
          ]),
        ]),
        el("div", { class: "kv-item" }, [
          el("div", { class: "kv-label", text: "此刻生效" }),
          el("div", { class: "tiny", text:
            "下行 " + kbText(current.download_kb) + " · 上行 " + kbText(current.upload_kb) }),
        ]),
        el("div", { class: "kv-item" }, [
          el("div", { class: "kv-label", text: "限速时段" }),
          el("div", { class: "mono tiny", text: (config.start || "-") + " ~ " + (config.end || "-") }),
        ]),
        el("div", { class: "kv-item" }, [
          el("div", { class: "kv-label", text: "下发周期" }),
          el("div", { class: "tiny", text: "每 " + (info.interval_minutes || 10) + " 分钟" }),
        ]),
      ]),
      el("div", { class: "divider" }),
      el("div", { class: "stack tiny" }, [
        el("div", { class: "dim", text:
          "时段内：下行 " + kbText(config.download_kb) + " · 上行 " + kbText(config.upload_kb) }),
        el("div", { class: "dim", text:
          "时段外：下行 " + kbText(config.off_peak_download_kb) +
          " · 上行 " + kbText(config.off_peak_upload_kb) }),
        el("div", { class: "dim", text: "时段可跨午夜，如 23:00 ~ 07:00 表示整个夜间" }),
      ]),
      isAdmin
        ? el("div", { class: "row tight", style: "margin-top:14px" }, [
            iconButton("配置时段", "edit", () => speedLimitForm(config), "sm primary"),
            iconButton("立即下发", "play", async () => {
              try {
                const result = await api("/downloaders/speed-limit/apply", { method: "POST" });
                toast(result.message || "已下发", "ok");
                pageSettings();
              } catch (error) {
                toast(error.message, "err");
              }
            }, "sm"),
          ])
        : null,
    ]);

    return el("div", { class: "card" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("clock", "sm"), el("span", { text: "下载器限速时段" })]),
        config.enabled
          ? el("span", { class: "tag dot ok", text: "已启用" })
          : el("span", { class: "tag dot", text: "已关闭" }),
      ]),
      body,
    ]);
  }

  /** 限速时段配置弹窗。 */
  function speedLimitForm(config) {
    modal(
      "配置下载器限速时段",
      [
        { key: "enabled", label: "启用限速时段", type: "checkbox", value: !!config.enabled },
        { key: "start", label: "限速开始时间", value: config.start || "08:00", placeholder: "08:00" },
        {
          key: "end",
          label: "限速结束时间",
          value: config.end || "23:00",
          placeholder: "23:00",
          hint: "可跨午夜：填 23:00 ~ 07:00 表示夜间限速、白天放开",
        },
        { key: "download_kb", label: "时段内下行限速（KB/s，0=不限）", type: "number", value: config.download_kb || 0 },
        { key: "upload_kb", label: "时段内上行限速（KB/s，0=不限）", type: "number", value: config.upload_kb || 0 },
        {
          key: "off_peak_download_kb",
          label: "时段外下行限速（KB/s，0=不限）",
          type: "number",
          value: config.off_peak_download_kb || 0,
          hint: "通常留 0：夜间跑满",
        },
        { key: "off_peak_upload_kb", label: "时段外上行限速（KB/s，0=不限）", type: "number", value: config.off_peak_upload_kb || 0 },
      ],
      async (values) => {
        await api("/downloaders/speed-limit", {
          method: "PUT",
          body: {
            enabled: !!values.enabled,
            start: values.start,
            end: values.end,
            download_kb: Number(values.download_kb) || 0,
            upload_kb: Number(values.upload_kb) || 0,
            off_peak_download_kb: Number(values.off_peak_download_kb) || 0,
            off_peak_upload_kb: Number(values.off_peak_upload_kb) || 0,
          },
        });
        toast("已保存，下一轮会自动下发（也可点「立即下发」）", "ok");
        pageSettings();
      },
      "保存",
      {
        wide: true,
        lead: "单位统一是 KB/s——各下载器内部单位不同（qB 用 B/s、TR 用 KB/s），换算由服务端完成。",
      }
    );
  }

  /** 设置页里的「下载器」卡片。 */
  function downloaderCard(list, schemaItems, isAdmin) {
    const rows = list || [];
    const body = rows.length
      ? el("div", { class: "list" }, rows.map((row) => {
          const spec = schemaItems.find((item) => item.provider === row.provider);
          const okState = String(row.last_status || "").indexOf("正常") === 0;
          return el("div", { class: "list-row" }, [
            el("span", {}, [
              el("div", { class: "row tight center", style: "gap:6px" }, [
                el("span", { text: row.name }),
                el("span", {
                  class: "tag dot tiny " + (row.enabled ? "ok" : ""),
                  text: row.enabled ? "已启用" : "已禁用",
                }),
              ]),
              el("div", { class: "cell-sub tiny" }, [
                (spec && spec.display_name) || row.provider,
                row.values && row.values.url ? " · " + row.values.url : "",
                " · 优先级 " + row.priority,
              ]),
              row.last_status
                ? el("div", { class: "tiny dim" }, [
                    okState
                      ? el("span", { text: row.last_status })
                      : el("span", { class: "tag warn tiny", text: row.last_status }),
                  ])
                : el("div", { class: "tiny dim", text: "未检测" }),
            ]),
            el("span", {}, [
              isAdmin
                ? el("div", { class: "row tight" }, [
                    (() => {
                      const test = el("button", { class: "btn sm" }, [
                        icon("check", "sm"),
                        el("span", { text: "测试" }),
                      ]);
                      test.addEventListener("click", async () => {
                        test.disabled = true;
                        test.querySelector("span").textContent = "检测中…";
                        try {
                          const r = await api("/downloaders/" + row.id + "/test", { method: "POST" });
                          toast(row.name + "：" + r.message, r.success ? "ok" : "err");
                          pageSettings();
                        } catch (error) {
                          toast(error.message, "err");
                          test.disabled = false;
                          test.querySelector("span").textContent = "测试";
                        }
                      });
                      return test;
                    })(),
                    iconButton(row.enabled ? "禁用" : "启用", row.enabled ? "pause" : "play",
                      async () => {
                        try {
                          await api("/downloaders/" + row.id, {
                            method: "PATCH",
                            body: {
                              name: row.name,
                              provider: row.provider,
                              enabled: !row.enabled,
                              values: {},
                            },
                          });
                          pageSettings();
                        } catch (error) {
                          toast(error.message, "err");
                        }
                      }, "sm"),
                    iconButton("编辑", "edit",
                      () => downloaderForm(schemaItems, row), "sm ghost"),
                    iconButton("删除", "trash", async () => {
                      if (!confirm("确定删除下载器 " + row.name + "？")) return;
                      try {
                        await api("/downloaders/" + row.id, { method: "DELETE" });
                        toast("已删除", "ok");
                        pageSettings();
                      } catch (error) {
                        toast(error.message, "err");
                      }
                    }, "sm danger"),
                  ])
                : el("span", { class: "tiny dim", text: row.enabled ? "已启用" : "已禁用" }),
            ]),
          ]);
        }))
      : el("div", { class: "pad-sm" }, [
          emptyBox("还没有配置下载器。BT 资源需要 qBittorrent/Transmission，" +
                   "榜单里的 B 站/YouTube 视频需要 yt-dlp。", "download"),
        ]);

    return el("div", { class: "card flush" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("download", "sm"), el("span", { text: "下载器" })]),
        el("div", { class: "row tight center" }, [
          el("span", { class: "tag", text: "已启用 " + rows.filter((r) => r.enabled).length + "/" + rows.length }),
          isAdmin
            ? iconButton("添加", "plus", () => downloaderForm(schemaItems, null), "sm primary")
            : null,
        ]),
      ]),
      body,
    ]);
  }

  async function pageSettings() {
    shell(loading(), "设置", "在线修改配置并立即生效");
    // 下载器数据拉不到不该让整个设置页白屏（比如老版本后端没这个接口），
    // 所以两个下载器请求都做降级：失败就当没有下载器，其余设置照常显示。
    const [data, info, me, dlList, dlSchema, speedInfo] = await Promise.all([
      api("/system/settings"),
      api("/system/info"),
      api("/auth/me").catch(() => null),
      api("/downloaders").catch(() => ({ items: [] })),
      api("/downloaders/schema").catch(() => ({ items: [] })),
      // 限速时段同样降级：老后端没这个接口时当作"未配置"，不让设置页整页失败
      api("/downloaders/speed-limit").catch(() => ({ data: null })),
    ]);
    const isAdmin = canDo("admin");
    const controls = {};

    // 把「一项都改不了」的组单独挑出来：它们（服务/目录/安全）只能改
    // .env 或 config.yaml 后重启，摆在页面上只是拉长滚动条（本轮需求 3）。
    // 不是直接删掉——用户仍需要查当前生效值，所以收进一个可展开的卡片。
    const allGroups = data.groups || [];
    const editableGroups = allGroups.filter((group) =>
      group.items.some((item) => item.editable)
    );
    const readonlyGroups = allGroups.filter(
      (group) => !group.items.some((item) => item.editable)
    );

    const groups = editableGroups.map((group) =>
      el("div", { class: "card flush" }, [
        el("div", { class: "card-head" }, [
          el("h3", {}, [icon("settings", "sm"), el("span", { text: group.title })]),
          el("span", {
            class: "tag tiny",
            text: group.items.filter((item) => item.editable).length + " 项可改",
          }),
        ]),
        table(
          [
            {
              title: "配置项",
              render: (row) =>
                el("div", {}, [
                  el("div", { text: row.label || row.key }),
                  el("div", { class: "cell-sub mono tiny", text: row.env }),
                ]),
            },
            {
              title: "值",
              render: (row) => {
                // 非管理员一律只读展示：即使前端放开了，服务端也会 403
                const control = isAdmin ? settingControl(row) : settingControl({ ...row, editable: false });
                if (row.editable && isAdmin) controls[row.key] = control;
                return control.node;
              },
            },
            {
              title: "说明",
              render: (row) =>
                el("div", { class: "stack tiny" }, [
                  row.hint ? el("div", { class: "dim", text: row.hint }) : null,
                  row.reschedule ? el("div", { class: "dim", text: "改动后会重建定时任务触发器" }) : null,
                  row.overridden ? el("span", { class: "tag brand tiny", text: "已被在线修改" }) : null,
                ]),
            },
          ],
          group.items,
          "无"
        ),
      ])
    );

    const saveBar = isAdmin
      ? el("div", { class: "card" }, [
          el("div", { class: "card-head" }, [
            el("h3", {}, [icon("check", "sm"), el("span", { text: "保存改动" })]),
            el("span", { class: "tag tiny", text: data.editable_total + " 项可在线修改" }),
          ]),
          el("div", { class: "muted", text: data.note }),
          el("div", { class: "row tight", style: "margin-top:14px" }, [
            iconButton("保存并生效", "check", async () => {
              const values = {};
              Object.keys(controls).forEach((key) => {
                const control = controls[key];
                // 只提交真正改过的项：整份提交会把「界面显示的旧值」当成用户意图写回去
                if (!control.changed || control.changed()) values[key] = control.get();
              });
              if (!Object.keys(values).length) {
                toast("没有改动");
                return;
              }
              try {
                const result = await api("/system/settings", { method: "PUT", body: { values: values } });
                toast(result.message, "ok");
                pageSettings();
              } catch (error) {
                toast(error.message, "err");
              }
            }, "primary"),
            (data.overridden || []).length
              ? iconButton("全部恢复默认", "refresh", async () => {
                  if (!confirm("把 " + data.overridden.length + " 项在线修改全部恢复为配置文件里的值？")) return;
                  try {
                    const result = await api("/system/settings/reset", { method: "POST", body: { keys: null } });
                    toast(result.message, "ok");
                    pageSettings();
                  } catch (error) {
                    toast(error.message, "err");
                  }
                }, "ghost")
              : null,
          ]),
          (data.overridden || []).length
            ? el("div", { class: "row tight wrap", style: "margin-top:12px" },
                data.overridden.map((key) => el("span", { class: "tag brand tiny", text: key })))
            : null,
        ])
      : el("div", { class: "card" }, [
          el("div", { class: "card-head" }, [el("h3", {}, [icon("info", "sm"), el("span", { text: "只读模式" })])]),
          el("div", { class: "muted", text: "当前角色（" + (ROLE_LABEL[store.role] || store.role) + "）不能修改配置，请联系管理员。" }),
        ]);

    /** 只读配置卡片：默认收起，点开才渲染表格。

        这些项（HOST/PORT/密钥/目录…）改了必须重启进程才生效，界面给输入框
        就是假功能（ADR-18），所以只提供**查看**。默认收起是因为日常配置
        用不到它们，摊开只会让页面变长（本轮需求 3）。
    */
    const readonlyCard = readonlyGroups.length
      ? (() => {
          const bodyBox = el("div", {});
          let open = false;
          const toggle = el("button", { class: "btn sm ghost" }, [
            icon("chevron-down", "sm"),
            el("span", { text: "展开查看" }),
          ]);
          const total = readonlyGroups.reduce((sum, g) => sum + g.items.length, 0);
          const render = () => {
            if (!open) {
              bodyBox.replaceChildren();
              toggle.querySelector("span").textContent = "展开查看";
              return;
            }
            toggle.querySelector("span").textContent = "收起";
            bodyBox.replaceChildren(
              ...readonlyGroups.map((group) =>
                el("div", { class: "card soft" }, [
                  el("div", { class: "card-head" }, [
                    el("h3", {}, [el("span", { text: group.title })]),
                  ]),
                  el("div", { class: "list" }, group.items.map((row) =>
                    el("div", { class: "list-row" }, [
                      el("span", {}, [
                        el("div", { text: row.label || row.key }),
                        el("div", { class: "cell-sub mono tiny", text: row.env }),
                      ]),
                      el("span", { class: "mono tiny", text:
                        typeof row.value === "boolean"
                          ? (row.value ? "开启" : "关闭")
                          : String(row.value === "" ? "（空）" : row.value) }),
                    ])
                  )),
                ])
              )
            );
          };
          toggle.addEventListener("click", () => {
            open = !open;
            render();
          });
          return el("div", { class: "card" }, [
            el("div", { class: "card-head" }, [
              el("h3", {}, [icon("info", "sm"), el("span", { text: "只读配置（需重启生效）" })]),
              el("div", { class: "row tight center" }, [
                el("span", { class: "tag tiny", text: total + " 项" }),
                toggle,
              ]),
            ]),
            el("div", { class: "muted tiny", text:
              readonlyGroups.map((g) => g.title).join("、") +
              " —— 这些项只能改 .env 或 config/config.yaml 后重启容器，界面不提供修改入口。" }),
            bodyBox,
          ]);
        })()
      : null;

    const accountCard = el("div", { class: "card" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("users", "sm"), el("span", { text: "账号" })]),
        el("span", { class: "tag brand", text: (me && me.role_label) || ROLE_LABEL[store.role] || "-" }),
      ]),
      el("div", { class: "kv" }, [
        el("div", { class: "kv-item" }, [
          el("div", { class: "kv-label", text: "用户名" }),
          el("div", { text: (me && me.username) || store.username }),
        ]),
        el("div", { class: "kv-item" }, [
          el("div", { class: "kv-label", text: "上次登录" }),
          el("div", { class: "tiny", text: fmtTime(me && me.last_login_at) }),
        ]),
        el("div", { class: "kv-item" }, [
          el("div", { class: "kv-label", text: "版本" }),
          el("div", { class: "mono", text: "v" + info.version }),
        ]),
        el("div", { class: "kv-item" }, [
          el("div", { class: "kv-label", text: "调度器" }),
          el("div", { text: info.scheduler_running ? "运行中" : "已停止" }),
        ]),
      ]),
      el("div", { class: "row tight", style: "margin-top:16px" }, [
        iconButton("修改密码", "edit", () => {
          modal(
            "修改密码",
            [
              { key: "old_password", label: "原密码", type: "password" },
              { key: "new_password", label: "新密码", type: "password", hint: "至少 6 位" },
            ],
            async (values) => {
              await api(
                "/auth/password?old_password=" +
                  encodeURIComponent(values.old_password) +
                  "&new_password=" +
                  encodeURIComponent(values.new_password),
                { method: "POST" }
              );
              toast("密码已更新，请重新登录", "ok");
              setTimeout(() => logout(true), 800);
            },
            "更新"
          );
        }, "sm primary"),
        isAdmin
          ? iconButton("测试通知渠道", "info", async () => {
              try {
                const result = await api("/system/notify/test", { method: "POST" });
                toast(result.message, result.success ? "ok" : "err");
              } catch (error) {
                toast(error.message, "err");
              }
            }, "sm")
          : null,
        isAdmin ? iconButton("用户管理", "users", () => go("users"), "sm ghost") : null,
      ]),
    ]);

    const noteCard = el("div", { class: "card" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("info", "sm"), el("span", { text: "配置优先级" })]),
      ]),
      el("div", { class: "stack" }, [
        el("div", { class: "muted", text: "1. 界面在线修改（存数据库，最高优先级，重启仍在）" }),
        el("div", { class: "muted", text: "2. 环境变量 CF_xxx / .env" }),
        el("div", { class: "muted", text: "3. config/config.yaml" }),
      ]),
      el("div", { class: "divider" }),
      el("div", { class: "kv" }, [
        el("div", { class: "kv-item" }, [
          el("div", { class: "kv-label", text: "配置文件" }),
          el("div", { class: "mono tiny", text: data.config_file }),
        ]),
        el("div", { class: "kv-item" }, [
          el("div", { class: "kv-label", text: "文件状态" }),
          el("div", { text: data.config_file_exists ? "已存在" : "不存在（走默认值/.env）" }),
        ]),
      ]),
    ]);

    shell(
      el("div", { class: "grid" }, [
        el("div", { class: "grid cols-2" }, [accountCard, noteCard]),
        // 下载器放在保存栏之前：它是独立的增删改，不受「保存并生效」影响，
        // 摆在最上面也符合"配置下载器"是新装机第一件事的使用顺序。
        downloaderCard(dlList.items || [], dlSchema.items || [], isAdmin),
        // 限速时段紧跟下载器：它配的就是下载器的行为，挨在一起才好理解
        speedInfo && speedInfo.data ? speedLimitCard(speedInfo.data, isAdmin) : null,
        saveBar,
        // 可改的配置组用多列瀑布流：原先单列纵向排 13 张卡片，页面极长
        // 需要一直滚（本轮需求 3）。cols-settings 在宽屏给 2~3 列。
        el("div", { class: "grid cols-settings" }, groups),
        readonlyCard,
      ].filter(Boolean)),
      "设置",
      editableGroups.length + " 组可改 · " + data.editable_total +
        " 项可在线改 · " + (dlList.items || []).length + " 个下载器 · 敏感项已脱敏",
      [
        iconButton("检查更新", "download", () => checkUpdate(false)),
        iconButton("刷新", "refresh", () => pageSettings()),
      ]
    );
  }

  // ---------------- 路由 ----------------
  const ROUTES = {
    dashboard: pageDashboard,
    search: pageSearch,
    trending: pageTrending,
    subscribes: pageSubscribes,
    schedules: pageSchedules,
    downloads: pageDownloads,
    library: pageLibrary,
    radar: pageRadar,
    sites: pageSites,
    sitehealth: pageSiteHealth,
    ranking: pageRanking,
    rules: pageRuleGroups,
    users: pageUsers,
    plugins: pagePlugins,
    logs: pageLogs,
    changelog: pageChangelog,
    storage: pageStorage,
    pansub: pagePanSub,
    videosub: pageVideoSub,
    rssfeeds: pageRssFeeds,
    strm: pageStrm,
    chatops: pageChatops,
    settings: pageSettings,
  };

  //: 导航代次。每次切页 +1。
  //: 页面内容的过期判定由 shell() 按标题做；这里专门管「出错了」那一屏——
  //: 它没有对应的页面标题，只能靠代次判断该不该显示。
  let navEpoch = 0;

  function go(page) {
    store.page = page;
    location.hash = page;
    render();
  }

  /** 关掉所有打开的弹窗。

      切页时必须做：弹窗挂在 #modal-root（不属于任何页面容器），
      换页只重绘页面容器，遮罩会**原地留下**。而 .modal-mask 是覆盖全屏的，
      于是新页面看得见却点不动 —— 表现为「界面卡死」，且刷新一下就好了，
      非常难联想到「上一页有个没关的弹窗」。
      实测复现：订阅页开「新增订阅」→ 点侧边栏去设置页 → 设置页任何按钮都点不到。
  */
  function closeAllModals() {
    const root = document.getElementById("modal-root");
    if (root && root.childNodes.length) root.innerHTML = "";
  }

  async function render() {
    if (!store.token) {
      renderLogin();
      return;
    }
    closeAllModals();
    const handler = ROUTES[store.page] || pageDashboard;
    const epoch = ++navEpoch; // 记下本次导航的代次，供下面的错误分支比对
    try {
      await handler();
    } catch (error) {
      // 已经切走的页面报错就别再糊到界面上了，否则用户会看到上一页的错误
      if (store.token && epoch === navEpoch) {
        shell(
          el("div", { class: "card" }, [emptyBox(error.message, "alert")]),
          "出错了",
          "请检查服务状态或重新登录"
        );
      }
    }
  }

  window.addEventListener("hashchange", () => {
    const page = location.hash.replace("#", "") || "dashboard";
    if (page !== store.page) {
      store.page = page;
      render();
    }
  });

  render();
})();
