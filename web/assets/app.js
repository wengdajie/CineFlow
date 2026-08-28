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
    page: location.hash.replace("#", "") || "dashboard",
    theme: localStorage.getItem(THEME_KEY) || "auto",
  };

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
    link: '<path d="M9.5 14.5 14.5 9.5"/><path d="M11 6.5 13 4.5a4 4 0 0 1 5.7 5.7l-2 2"/><path d="M13 17.5 11 19.5a4 4 0 0 1-5.7-5.7l2-2"/>',
    film: '<rect x="3" y="4.5" width="18" height="15" rx="2"/><path d="M7.5 4.5v15M16.5 4.5v15M3 12h18"/>',
    tv: '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M8.5 21h7M12 6V3"/>',
    server: '<rect x="3.5" y="4" width="17" height="6" rx="2"/><rect x="3.5" y="14" width="17" height="6" rx="2"/><path d="M7 7h.4M7 17h.4"/>',
    chart: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    inbox: '<path d="M3.5 12.5 6 5h12l2.5 7.5v6.5h-17z"/><path d="M3.5 12.5H9l1 2.5h4l1-2.5h5.5"/>',
    robot: '<rect x="4" y="8" width="16" height="11" rx="3"/><path d="M12 4v4"/><circle cx="9" cy="13" r="1.2"/><circle cx="15" cy="13" r="1.2"/><path d="M9.5 16.5h5"/>',
    folder: '<path d="M3.5 6.5h5l2 2.5h9.5v9.5h-16.5z"/>',
    dot: '<circle cx="12" cy="12" r="4"/>',
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
    const name = kind === "ok" ? "check" : kind === "err" ? "alert" : "info";
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
        localStorage.setItem("cf_token", store.token);
        localStorage.setItem("cf_user", store.username);
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
    { key: "schedules", label: "定时任务", icon: "clock", group: "追剧" },
    { key: "downloads", label: "下载任务", icon: "download", group: "入库" },
    { key: "library", label: "媒体库", icon: "library", group: "入库" },
    { key: "storage", label: "网盘管理", icon: "cloud", group: "入库" },
    { key: "sites", label: "站点管理", icon: "server", group: "系统" },
    { key: "chatops", label: "机器人", icon: "robot", group: "系统" },
    { key: "plugins", label: "插件", icon: "plugin", group: "系统" },
    { key: "logs", label: "运行日志", icon: "logs", group: "系统" },
    { key: "settings", label: "设置", icon: "settings", group: "系统" },
  ];

  function shell(content, title, subtitle, actions) {
    const nav = [];
    let lastGroup = null;
    PAGES.forEach((page) => {
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

    document.getElementById("app").replaceChildren(
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
            el("button", { class: "nav-item", onclick: () => logout() }, [
              icon("logout"),
              el("span", { text: "退出（" + store.username + "）" }),
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

    const kv = (label, node) =>
      el("div", { class: "kv-item" }, [el("div", { class: "kv-label", text: label }), node]);

    const health = el("div", { class: "card" }, [
      el("h3", {}, [icon("server", "sm"), el("span", { text: "运行状态" })]),
      el("div", { class: "kv" }, [
        kv("调度器", badge(info.scheduler_running, "运行中", "已停止")),
        kv("TMDB 刮削", badge(info.tmdb_enabled, "已启用", "未配置")),
        kv("整理模式", el("span", { class: "tag brand", text: info.transfer_mode })),
        kv("版本", el("span", { class: "tag", text: "v" + info.version })),
      ]),
      el("div", { class: "divider" }),
      el("div", { class: "dim tiny mono" }, [
        "媒体库：" + info.directories.library,
        el("br"),
        "下载目录：" + info.directories.downloads,
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
        el("div", { class: "grid cols-2" }, [health, jobCard]),
        el("div", { class: "grid cols-2" }, [hotCard, recent]),
      ]),
      "仪表盘",
      "系统概览 · 定时任务 · 热度排行",
      [runAll]
    );
  }

  // ---------------- 资源搜索 ----------------
  const searchState = { items: [], keyword: "", sort: "score", kind: "" };

  const SORTERS = {
    score: (a, b) => (b.score || 0) - (a.score || 0),
    seeders: (a, b) => (b.seeders || 0) - (a.seeders || 0),
    size: (a, b) => (b.size || 0) - (a.size || 0),
    time: (a, b) => String(b.publish_at || "").localeCompare(String(a.publish_at || "")),
  };

  function downloadButton(row, onDone) {
    const button = el("button", { class: "btn sm primary" }, [
      icon("download", "sm"),
      el("span", { text: "下载" }),
    ]);
    button.addEventListener("click", async () => {
      button.disabled = true;
      button.querySelector("span").textContent = "提交中…";
      try {
        await api("/downloads", {
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
        button.querySelector("span").textContent = "已添加";
        toast("已加入下载队列", "ok");
        if (onDone) onDone();
      } catch (error) {
        toast(error.message, "err");
        button.disabled = false;
        button.querySelector("span").textContent = "下载";
      }
    });
    return button;
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
                    el("div", { class: "truncate", title: row.title, text: row.title }),
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
              { title: "操作", render: (row) => downloadButton(row) },
            ],
            filtered,
            "没有匹配的资源，试试更换关键词或启用更多站点"
          ),
        ])
      );
    };

    const doSearch = async (value) => {
      const text = (value || keyword.value).trim();
      if (!text) {
        toast("请输入关键词", "err");
        return;
      }
      keyword.value = text;
      searchState.keyword = text;
      results.replaceChildren(loading());
      try {
        const data = await api("/search", {
          method: "POST",
          body: {
            keyword: text,
            media_type: type.value || null,
            season: season.value ? Number(season.value) : null,
            episode: episode.value ? Number(episode.value) : null,
          },
        });
        searchState.items = data.items || [];
        renderResults();
        loadHot();
        toast("找到 " + data.total + " 条资源", data.total ? "ok" : "");
      } catch (error) {
        results.replaceChildren(el("div", { class: "card" }, [emptyBox(error.message, "alert")]));
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
  const trendingState = { tab: "resources", days: 14, mediaType: "" };

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

  function trendingDetail(row) {
    const info = (label, value) =>
      el("div", { class: "kv-item" }, [
        el("div", { class: "kv-label", text: label }),
        el("div", { text: value }),
      ]);

    panelModal(
      row.title,
      "热度构成：做种数 + 站点覆盖 + 资源条目 + 新鲜度 + 画质加成",
      el("div", {}, [
        el("div", { class: "kv" }, [
          info("类型", typeLabel(row.media_type)),
          info("季", row.season === null || row.season === undefined ? "-" : "第 " + row.season + " 季"),
          info("热度分", String(row.heat)),
          info("收录站点", String(row.site_count)),
          info("资源条目", String(row.resource_count)),
          info("累计做种", String(row.seeders)),
          info("覆盖集数", row.episode_count ? row.episode_count + " 集（至 " + row.latest_episode + "）" : "-"),
          info("最大体积", fmtSize(row.size)),
        ]),
        el("div", { class: "divider" }),
        el("div", { class: "chips" }, [
          ...(row.resolutions || []).map((item) => el("span", { class: "tag brand", text: item })),
          ...(row.kinds || []).map((item) => el("span", { class: "tag", text: kindLabel(item) })),
          ...(row.sites || []).map((item) => el("span", { class: "tag", text: item })),
        ]),
        el("div", { class: "divider" }),
        el("div", { class: "dim tiny", style: "margin-bottom:8px", text: "样例资源" }),
        table(
          [
            {
              title: "标题",
              render: (item) => el("div", { class: "truncate", title: item.title, text: item.title }),
            },
            { title: "站点", render: (item) => el("span", { class: "tag", text: item.site || "-" }) },
            { title: "大小", class: "num", render: (item) => fmtSize(item.size) },
            { title: "做种", class: "num", render: (item) => item.seeders || "-" },
            { title: "操作", render: (item) => downloadButton(item) },
          ],
          row.samples || [],
          "无样例"
        ),
      ]),
      true
    );
  }

  function rankingTable(items, opts) {
    return table(
      [
        { title: "#", render: (row) => rankCell(row.rank) },
        {
          title: "作品",
          render: (row) =>
            el("div", {}, [
              el("div", { class: "row tight center" }, [
                icon(typeIcon(row.media_type), "sm"),
                el("span", { class: "truncate", title: row.title, text: row.title }),
              ]),
              el("div", { class: "cell-sub", text:
                typeLabel(row.media_type) +
                (row.season ? " · 第 " + row.season + " 季" : "") +
                (row.latest_episode ? " · 更新至第 " + row.latest_episode + " 集" : "") }),
            ]),
        },
        {
          title: "热度",
          render: (row) => heatCell(row.heat_percent, Math.round(row.heat)),
        },
        { title: "站点", class: "num", render: (row) => row.site_count },
        { title: "资源", class: "num", render: (row) => row.resource_count },
        { title: "做种", class: "num", render: (row) => row.seeders || "-" },
        {
          title: "画质",
          render: (row) =>
            el("div", { class: "chips" },
              (row.resolutions || []).slice(0, 2).map((item) => el("span", { class: "tag", text: item }))),
        },
        {
          title: "更新",
          render: (row) => el("span", { class: "tiny dim", text: fmtRelative(row.latest_at) }),
        },
        {
          title: "操作",
          render: (row) =>
            el("div", { class: "row tight" }, [
              iconButton("详情", "info", () => trendingDetail(row), "sm ghost"),
              iconButton("订阅", "star", () => subscribeFromTrending(row), "sm"),
              iconButton("搜索", "search", () => {
                searchState.keyword = row.title;
                searchState.items = [];
                go("search");
              }, "sm ghost"),
            ]),
        },
      ],
      items,
      opts && opts.empty
    );
  }

  async function pageTrending() {
    shell(loading(), "热度排行", "多维度热度榜：资源 / 实时 / 热词 / 站点");

    const body = el("div", {});
    const meta = el("div", { class: "dim tiny" });

    const load = async () => {
      body.replaceChildren(loading());
      try {
        if (trendingState.tab === "live") {
          const response = await api(
            "/trending/live?limit=25&limit_per_site=40" +
              (trendingState.mediaType ? "&media_type=" + trendingState.mediaType : "")
          );
          const data = response.data;
          meta.textContent = "实时拉取站点最新流 " + data.feed_total + " 条，聚合出 " + data.total + " 部作品";
          body.replaceChildren(
            el("div", { class: "card flush" }, [
              el("div", { class: "card-head" }, [
                el("h3", {}, [icon("radar", "sm"), el("span", { text: "实时热榜（站点最新流）" })]),
                el("span", { class: "tag brand", text: "联网实时" }),
              ]),
              rankingTable(data.items, {
                empty: "没有启用的索引站点，或站点未返回最新流；请到站点管理启用站点",
              }),
            ])
          );
          return;
        }

        if (trendingState.tab === "sites") {
          const response = await api("/trending/sites?limit=25&days=" + trendingState.days);
          const data = response.data;
          meta.textContent = "近 " + data.window_days + " 天共 " + data.total + " 个站点有贡献";
          body.replaceChildren(
            el("div", { class: "card flush" }, [
              el("div", { class: "card-head" }, [
                el("h3", {}, [icon("server", "sm"), el("span", { text: "站点贡献榜" })]),
              ]),
              table(
                [
                  { title: "#", render: (row) => rankCell(row.rank) },
                  { title: "站点", render: (row) => row.site },
                  { title: "占比", render: (row) => heatCell(row.heat_percent, row.resources) },
                  { title: "资源数", class: "num", render: (row) => row.resources },
                  { title: "累计做种", class: "num", render: (row) => row.seeders },
                  { title: "平均评分", class: "num", render: (row) => row.avg_score },
                  { title: "最近入榜", render: (row) => el("span", { class: "tiny dim", text: fmtRelative(row.last_at) }) },
                ],
                data.items,
                "还没有站点数据，搜索一次后即可统计"
              ),
            ])
          );
          return;
        }

        if (trendingState.tab === "keywords") {
          const response = await api("/trending/keywords?limit=30&days=" + trendingState.days);
          const data = response.data;
          meta.textContent = "近 " + data.window_days + " 天共 " + data.total + " 个热词";
          body.replaceChildren(
            el("div", { class: "card flush" }, [
              el("div", { class: "card-head" }, [
                el("h3", {}, [icon("search", "sm"), el("span", { text: "搜索热词榜" })]),
              ]),
              table(
                [
                  { title: "#", render: (row) => rankCell(row.rank) },
                  { title: "关键词", render: (row) => row.keyword },
                  { title: "热度", render: (row) => heatCell(row.heat_percent, row.times) },
                  { title: "搜索次数", class: "num", render: (row) => row.times },
                  { title: "累计命中", class: "num", render: (row) => row.results },
                  { title: "最近搜索", render: (row) => el("span", { class: "tiny dim", text: fmtRelative(row.last_at) }) },
                  {
                    title: "操作",
                    render: (row) =>
                      iconButton("再搜一次", "search", () => {
                        searchState.keyword = row.keyword;
                        searchState.items = [];
                        go("search");
                      }, "sm ghost"),
                  },
                ],
                data.items,
                "还没有搜索历史"
              ),
            ])
          );
          return;
        }

        const response = await api(
          "/trending/resources?limit=30&days=" + trendingState.days +
            (trendingState.mediaType ? "&media_type=" + trendingState.mediaType : "")
        );
        const data = response.data;
        meta.textContent =
          "近 " + data.window_days + " 天扫描 " + data.scanned + " 条缓存资源，聚合出 " + data.total + " 部作品";
        body.replaceChildren(
          el("div", { class: "card flush" }, [
            el("div", { class: "card-head" }, [
              el("h3", {}, [icon("flame", "sm"), el("span", { text: "资源热度榜" })]),
              el("span", { class: "tag", text: "本地缓存聚合" }),
            ]),
            rankingTable(data.items, {
              empty: "暂无数据：热度榜来自搜索缓存，先在资源搜索里搜几次即可生成",
            }),
          ])
        );
      } catch (error) {
        body.replaceChildren(el("div", { class: "card" }, [emptyBox(error.message, "alert")]));
      }
    };

    const tabs = segment(
      [
        { value: "resources", label: "资源热榜" },
        { value: "live", label: "实时热榜" },
        { value: "keywords", label: "搜索热词" },
        { value: "sites", label: "站点贡献" },
      ],
      trendingState.tab,
      (value) => {
        trendingState.tab = value;
        pageTrending();
      }
    );

    const windows = segment(
      [
        { value: 7, label: "7 天" },
        { value: 14, label: "14 天" },
        { value: 30, label: "30 天" },
        { value: 90, label: "90 天" },
      ],
      trendingState.days,
      (value) => {
        trendingState.days = value;
        pageTrending();
      }
    );

    const types = segment(
      [
        { value: "", label: "全部" },
        { value: "tv", label: "剧集" },
        { value: "movie", label: "电影" },
        { value: "anime", label: "动漫" },
      ],
      trendingState.mediaType,
      (value) => {
        trendingState.mediaType = value;
        pageTrending();
      }
    );

    const filterBar = el("div", { class: "card" }, [
      el("div", { class: "row center" }, [
        el("div", { style: "flex:0 0 auto" }, [
          el("div", { class: "dim tiny", style: "margin-bottom:6px", text: "榜单" }),
          tabs,
        ]),
        el("div", { style: "flex:0 0 auto" }, [
          el("div", { class: "dim tiny", style: "margin-bottom:6px", text: "统计窗口" }),
          windows,
        ]),
        el("div", { style: "flex:0 0 auto" }, [
          el("div", { class: "dim tiny", style: "margin-bottom:6px", text: "类型" }),
          types,
        ]),
      ]),
      el("div", { class: "divider" }),
      meta,
    ]);

    shell(
      el("div", { class: "grid" }, [filterBar, body]),
      "热度排行",
      "热度 = 做种数 + 站点覆盖 + 资源条目 + 新鲜度 + 画质加成",
      [iconButton("刷新", "refresh", () => load())]
    );
    load();
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

      return el("div", { class: "row tight" }, [search, toggle, remove]);
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
              { subscribe: "star", radar: "radar", download: "download", library: "library" }[row.key] || "clock",
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

    shell(content, "下载任务", "共 " + items.length + " 个任务", [sync]);
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

    shell(content, "媒体库", data.files + " 个文件 · " + fmtSize(data.size), [
      iconButton("手动整理", "plus", transferForm),
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
  const KIND_LABELS = {
    indexer: "BT 索引器",
    pan: "网盘搜索",
    downloader: "下载器",
    mediaserver: "媒体服务器",
    notifier: "通知渠道",
    metadata: "元数据",
  };

  const KIND_ICONS = {
    indexer: "search",
    pan: "cloud",
    downloader: "download",
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

  async function pageSites() {
    shell(loading(), "站点管理", "索引器、盘搜、下载器、媒体服务器与通知");
    const [sites, providers] = await Promise.all([
      api("/sites"),
      api("/sites/providers"),
    ]);

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
      "共 " + sites.length + " 个配置 · 已启用 " + enabledCount + " 个",
      [
        iconButton("发现站点", "radar", () => discoverDialog()),
        iconButton("从模板添加", "box", () => presetPicker(providers)),
        iconButton("新增站点", "plus", () => siteForm(providers), "primary"),
      ]
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

      browser = el("div", { class: "card flush" }, [
        el("div", { class: "card-head" }, [
          el("h3", {}, [icon("library", "sm"), el("span", { text: current.name + " · 文件浏览" })]),
          el("div", { class: "row tight center" }, [
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
      [saveButton, iconButton("刷新", "refresh", () => pageStorage())]
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

  // ---------------- 设置 ----------------
  async function pageSettings() {
    shell(loading(), "设置", "生效配置总览与账号安全");
    const [data, info, me] = await Promise.all([
      api("/system/settings"),
      api("/system/info"),
      api("/auth/me").catch(() => null),
    ]);

    const groups = (data.groups || []).map((group) =>
      el("div", { class: "card flush" }, [
        el("div", { class: "card-head" }, [
          el("h3", {}, [icon("settings", "sm"), el("span", { text: group.title })]),
        ]),
        table(
          [
            { title: "配置项", render: (row) => el("span", { class: "mono", text: row.env }) },
            {
              title: "当前值",
              render: (row) =>
                el("span", {
                  class: "mono" + (row.secret ? " dim" : ""),
                  text: typeof row.value === "boolean" ? (row.value ? "开启" : "关闭") : String(row.value === "" ? "（空）" : row.value),
                }),
            },
          ],
          group.items,
          "无"
        ),
      ])
    );

    const accountCard = el("div", { class: "card" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("logout", "sm"), el("span", { text: "账号" })]),
        me && me.is_superuser ? el("span", { class: "tag brand", text: "管理员" }) : null,
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
        iconButton("测试通知渠道", "info", async () => {
          try {
            const result = await api("/system/notify/test", { method: "POST" });
            toast(result.message, result.success ? "ok" : "err");
          } catch (error) {
            toast(error.message, "err");
          }
        }, "sm"),
      ]),
    ]);

    const noteCard = el("div", { class: "card" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("info", "sm"), el("span", { text: "如何修改配置" })]),
      ]),
      el("div", { class: "muted", text: data.note }),
      el("div", { class: "divider" }),
      el("div", { class: "kv" }, [
        el("div", { class: "kv-item" }, [
          el("div", { class: "kv-label", text: "配置文件" }),
          el("div", { class: "mono tiny", text: data.config_file }),
        ]),
        el("div", { class: "kv-item" }, [
          el("div", { class: "kv-label", text: "文件状态" }),
          el("div", {
            text: data.config_file_exists ? "已存在（优先级低于环境变量）" : "不存在（全部走默认值/.env）",
          }),
        ]),
      ]),
    ]);

    shell(
      el("div", { class: "grid" }, [
        el("div", { class: "grid cols-2" }, [accountCard, noteCard]),
        ...groups,
      ]),
      "设置",
      "共 " + (data.groups || []).length + " 组配置 · 敏感项已脱敏",
      [iconButton("刷新", "refresh", () => pageSettings())]
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
    plugins: pagePlugins,
    logs: pageLogs,
    storage: pageStorage,
    chatops: pageChatops,
    settings: pageSettings,
  };

  function go(page) {
    store.page = page;
    location.hash = page;
    render();
  }

  async function render() {
    if (!store.token) {
      renderLogin();
      return;
    }
    const handler = ROUTES[store.page] || pageDashboard;
    try {
      await handler();
    } catch (error) {
      if (store.token) {
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
