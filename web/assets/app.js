/* CineFlow 控制台：零依赖单页应用 */
(function () {
  "use strict";

  const API = "/api/v1";
  const store = {
    token: localStorage.getItem("cf_token") || "",
    username: localStorage.getItem("cf_user") || "",
    page: location.hash.replace("#", "") || "dashboard",
  };

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

  const toast = (message, kind) => {
    const node = el("div", { class: "toast " + (kind || ""), text: message });
    document.getElementById("toasts").appendChild(node);
    setTimeout(() => node.remove(), 3600);
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
    return el("span", { class: "tag " + pair[1], text: pair[0] });
  };

  const typeLabel = (value) =>
    ({ movie: "电影", tv: "剧集", anime: "动漫", unknown: "未知" }[value] ||
      value ||
      "-");

  const kindLabel = (value) =>
    ({ torrent: "种子", magnet: "磁力", pan: "网盘", direct: "直链" }[value] ||
      value);

  const seasonEpisode = (season, episode) =>
    season !== null && season !== undefined && episode !== null && episode !== undefined
      ? "S" + pad2(season) + "E" + pad2(episode)
      : "-";

  const loading = () =>
    el("div", { class: "empty" }, [el("span", { class: "spinner" }), " 加载中…"]);

  function table(columns, rows, emptyText) {
    if (!rows || !rows.length) {
      return el("div", { class: "empty", text: emptyText || "暂无数据" });
    }
    const head = el("thead", {}, [
      el("tr", {}, columns.map((col) => el("th", { text: col.title }))),
    ]);
    const body = el(
      "tbody",
      {},
      rows.map((row) =>
        el(
          "tr",
          {},
          columns.map((col) => {
            const value = col.render ? col.render(row) : row[col.key];
            const isNode = value && typeof value === "object";
            return el(
              "td",
              {},
              isNode ? value : String(value === undefined || value === null ? "-" : value)
            );
          })
        )
      )
    );
    return el("div", { class: "table-wrap" }, [el("table", {}, [head, body])]);
  }

  // ---------------- 通用弹窗表单 ----------------
  function modal(title, fields, onSubmit, submitText) {
    const root = document.getElementById("modal-root");
    const getters = {};

    const rows = fields.map((field) => {
      if (field.type === "checkbox") {
        const input = el("input", { type: "checkbox" });
        input.checked = !!field.value;
        getters[field.key] = () => input.checked;
        return el(
          "label",
          { class: "field", style: "display:flex;gap:8px;align-items:center" },
          [input, el("span", { text: field.label })]
        );
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
          rows: 3,
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
        field.hint
          ? el("div", {
              class: "muted",
              style: "font-size:11px;margin-top:4px",
              text: field.hint,
            })
          : null,
      ]);
    });

    const close = () => {
      root.innerHTML = "";
    };

    const submit = el("button", { class: "btn primary", text: submitText || "确定" });
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
      el("div", { class: "modal" }, [
        el("h3", { text: title }),
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
        el("div", { class: "login-card" }, [
          el("h1", {}, [
            el("span", {
              class: "brand-dot",
              style: "display:inline-block;margin-right:8px",
            }),
            "CineFlow",
          ]),
          el("p", { text: "自动化观影追剧平台 · BT 站点与网盘聚合" }),
          el("div", { class: "field" }, [el("label", { text: "用户名" }), user]),
          el("div", { class: "field" }, [el("label", { text: "密码" }), pass]),
          button,
          el("p", {
            class: "muted",
            style: "margin-top:16px;font-size:11px",
            text: "默认账号 admin / cineflow，登录后请及时修改密码",
          }),
        ]),
      ])
    );
  }

  // ---------------- 布局 ----------------
  const PAGES = [
    { key: "dashboard", label: "仪表盘", icon: "◈" },
    { key: "search", label: "资源搜索", icon: "⌕" },
    { key: "subscribes", label: "订阅追新", icon: "★" },
    { key: "downloads", label: "下载任务", icon: "↓" },
    { key: "library", label: "媒体库", icon: "▤" },
    { key: "sites", label: "站点管理", icon: "⚙" },
    { key: "plugins", label: "插件", icon: "✚" },
    { key: "logs", label: "运行日志", icon: "❯" },
  ];

  function shell(content, title, subtitle, actions) {
    const nav = PAGES.map((page) =>
      el(
        "button",
        {
          class: "nav-item " + (store.page === page.key ? "active" : ""),
          onclick: () => go(page.key),
        },
        [el("span", { text: page.icon }), el("span", { text: page.label })]
      )
    );

    document.getElementById("app").replaceChildren(
      el("div", { class: "layout" }, [
        el("aside", { class: "sidebar" }, [
          el("div", { class: "brand" }, [
            el("span", { class: "brand-dot" }),
            "CineFlow",
          ]),
          ...nav,
          el("div", { class: "nav-spacer" }),
          el("button", { class: "nav-item", onclick: () => logout() }, [
            el("span", { text: "⏻" }),
            el("span", { text: "退出（" + store.username + "）" }),
          ]),
        ]),
        el("main", { class: "main" }, [
          el("div", { class: "topbar" }, [
            el("div", {}, [
              el("h2", { text: title }),
              subtitle ? el("div", { class: "sub", text: subtitle }) : null,
            ]),
            el("div", { class: "row tight" }, actions || []),
          ]),
          content,
        ]),
      ])
    );
  }

  // ---------------- 仪表盘 ----------------
  function statCard(label, value, hint) {
    return el("div", { class: "card stat" }, [
      el("div", { class: "label", text: label }),
      el("div", { class: "value", text: value }),
      hint ? el("div", { class: "hint", text: hint }) : null,
    ]);
  }

  async function pageDashboard() {
    shell(loading(), "仪表盘", "系统概览与最近入库");
    const [data, info] = await Promise.all([
      api("/system/dashboard"),
      api("/system/info"),
    ]);

    const stats = el("div", { class: "grid cols-4" }, [
      statCard("追新中订阅", data.subscribes.active, "已完成 " + data.subscribes.completed + " 个"),
      statCard("进行中下载", data.downloads.running, "累计完成 " + data.downloads.finished + " 个"),
      statCard("媒体库文件", data.library.files, fmtSize(data.library.size)),
      statCard(
        "剧集 / 电影",
        data.library.series + " / " + data.library.movies,
        "共 " + data.library.episodes + " 集"
      ),
    ]);

    const badge = (ok, okText, badText) =>
      el("span", { class: "tag " + (ok ? "ok" : "warn"), text: ok ? okText : badText });

    const health = el("div", { class: "card" }, [
      el("h3", { text: "运行状态" }),
      el("div", { class: "row" }, [
        el("div", {}, [
          el("div", { class: "muted", text: "调度器" }),
          badge(info.scheduler_running, "运行中", "已停止"),
        ]),
        el("div", {}, [
          el("div", { class: "muted", text: "TMDB 刮削" }),
          badge(info.tmdb_enabled, "已启用", "未配置"),
        ]),
        el("div", {}, [
          el("div", { class: "muted", text: "整理模式" }),
          el("span", { class: "tag brand", text: info.transfer_mode }),
        ]),
        el("div", {}, [
          el("div", { class: "muted", text: "订阅间隔" }),
          el("span", { class: "tag", text: info.intervals.subscribe_minutes + " 分钟" }),
        ]),
      ]),
      el("div", { class: "muted mono", style: "margin-top:14px;font-size:11px" }, [
        "媒体库：" + info.directories.library,
        el("br"),
        "下载目录：" + info.directories.downloads,
      ]),
    ]);

    const recent = el("div", { class: "card" }, [
      el("h3", { text: "最近入库" }),
      table(
        [
          { title: "标题", render: (row) => row.title },
          { title: "类型", render: (row) => typeLabel(row.media_type) },
          { title: "季集", render: (row) => seasonEpisode(row.season, row.episode) },
          { title: "画质", render: (row) => row.resolution || "-" },
          { title: "时间", render: (row) => fmtTime(row.created_at) },
        ],
        data.recent,
        "还没有入库记录，先添加订阅或搜索下载吧"
      ),
    ]);

    const runAll = el("button", { class: "btn primary", text: "立即巡检订阅" });
    runAll.addEventListener("click", async () => {
      runAll.disabled = true;
      runAll.textContent = "巡检中…";
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
        runAll.textContent = "立即巡检订阅";
      }
    });

    shell(
      el("div", { class: "grid" }, [
        stats,
        el("div", { class: "grid cols-2" }, [health, recent]),
      ]),
      "仪表盘",
      "系统概览与最近入库",
      [runAll]
    );
  }

  // ---------------- 资源搜索 ----------------
  const searchState = { items: [], keyword: "" };

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

    const downloadButton = (row) => {
      const button = el("button", { class: "btn sm primary", text: "下载" });
      button.addEventListener("click", async () => {
        button.disabled = true;
        button.textContent = "提交中…";
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
          button.textContent = "已添加";
          toast("已加入下载队列", "ok");
        } catch (error) {
          toast(error.message, "err");
          button.disabled = false;
          button.textContent = "下载";
        }
      });
      return button;
    };

    const renderResults = () => {
      results.replaceChildren(
        el("div", { class: "card" }, [
          el("h3", { text: "搜索结果（" + searchState.items.length + "）" }),
          table(
            [
              {
                title: "资源名称",
                render: (row) =>
                  el("div", { class: "truncate", title: row.title, text: row.title }),
              },
              { title: "来源", render: (row) => el("span", { class: "tag", text: row.site || "-" }) },
              {
                title: "类型",
                render: (row) =>
                  el("span", {
                    class: "tag " + (row.kind === "pan" ? "brand" : ""),
                    text: kindLabel(row.kind),
                  }),
              },
              { title: "画质", render: (row) => (row.meta && row.meta.resolution) || "-" },
              { title: "大小", render: (row) => fmtSize(row.size) },
              { title: "做种", render: (row) => row.seeders || "-" },
              { title: "评分", render: (row) => Math.round(row.score || 0) },
              { title: "操作", render: downloadButton },
            ],
            searchState.items,
            "没有匹配的资源，试试更换关键词或启用更多站点"
          ),
        ])
      );
    };

    const doSearch = async () => {
      const value = keyword.value.trim();
      if (!value) {
        toast("请输入关键词", "err");
        return;
      }
      searchState.keyword = value;
      results.replaceChildren(loading());
      try {
        const data = await api("/search", {
          method: "POST",
          body: {
            keyword: value,
            media_type: type.value || null,
            season: season.value ? Number(season.value) : null,
            episode: episode.value ? Number(episode.value) : null,
          },
        });
        searchState.items = data.items || [];
        renderResults();
        toast("找到 " + data.total + " 条资源", data.total ? "ok" : "");
      } catch (error) {
        results.replaceChildren(el("div", { class: "empty", text: error.message }));
      }
    };

    keyword.addEventListener("keydown", (event) => {
      if (event.key === "Enter") doSearch();
    });
    if (searchState.items.length) renderResults();

    const labeled = (text, node, flex) =>
      el("div", { style: flex ? "flex:" + flex : null }, [
        el("label", { class: "muted", style: "font-size:12px", text: text }),
        node,
      ]);

    shell(
      el("div", { class: "grid" }, [
        el("div", { class: "card" }, [
          el("h3", { text: "聚合搜索（BT 站点 + 网盘）" }),
          el("div", { class: "row" }, [
            labeled("关键词", keyword, "3"),
            labeled("类型", type),
            labeled("季", season),
            labeled("集", episode),
            el("button", {
              class: "btn primary",
              text: "搜索",
              onclick: doSearch,
              style: "flex:0 0 auto",
            }),
          ]),
        ]),
        results,
      ]),
      "资源搜索",
      "并发查询所有已启用的索引器与盘搜服务"
    );
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
      "创建订阅"
    );
  }

  async function pageSubscribes() {
    shell(loading(), "订阅追新", "自动跟踪剧集更新并下载入库");
    const items = await api("/subscribes?limit=500");

    const progressCell = (row) => {
      const done = (row.downloaded_episodes || []).length;
      const total = row.total_episodes || 0;
      const percent = total ? Math.min(100, Math.round((done / total) * 100)) : 0;
      return el("div", {}, [
        el("div", {
          class: "muted",
          style: "font-size:11px",
          text: total ? done + "/" + total + " 集" : done + " 集（持续追新）",
        }),
        total
          ? el("div", { class: "progress", style: "margin-top:4px" }, [
              el("i", { style: "width:" + percent + "%" }),
            ])
          : null,
      ]);
    };

    const actionsCell = (row) => {
      const search = el("button", { class: "btn sm", text: "搜索" });
      search.addEventListener("click", async () => {
        search.disabled = true;
        search.textContent = "搜索中…";
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
          search.textContent = "搜索";
        }
      });

      const toggle = el("button", {
        class: "btn sm",
        text: row.status === "active" ? "暂停" : "启用",
      });
      toggle.addEventListener("click", async () => {
        try {
          await api("/subscribes/" + row.id, {
            method: "PATCH",
            body: { status: row.status === "active" ? "paused" : "active" },
          });
          pageSubscribes();
        } catch (error) {
          toast(error.message, "err");
        }
      });

      const remove = el("button", { class: "btn sm danger", text: "删除" });
      remove.addEventListener("click", async () => {
        if (!confirm("确定删除订阅《" + row.title + "》？")) return;
        try {
          await api("/subscribes/" + row.id, { method: "DELETE" });
          toast("已删除", "ok");
          pageSubscribes();
        } catch (error) {
          toast(error.message, "err");
        }
      });

      return el("div", { class: "row tight" }, [search, toggle, remove]);
    };

    const content = el("div", { class: "card" }, [
      table(
        [
          {
            title: "片名",
            render: (row) =>
              el("div", {}, [
                el("div", { text: row.title }),
                el("div", {
                  class: "muted",
                  style: "font-size:11px",
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
            render: (row) =>
              el("div", {
                class: "muted",
                style: "font-size:11px",
                text:
                  [row.resolution, row.include ? "含:" + row.include : "", row.exclude ? "排:" + row.exclude : ""]
                    .filter(Boolean)
                    .join(" ") || "默认",
              }),
          },
          {
            title: "最近检查",
            render: (row) =>
              el("span", { class: "muted", style: "font-size:11px", text: fmtTime(row.last_check_at) }),
          },
          { title: "操作", render: actionsCell },
        ],
        items,
        "还没有订阅，点击右上角新增即可开始自动追新"
      ),
    ]);

    const runAll = el("button", { class: "btn", text: "巡检全部" });
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

    shell(content, "订阅追新", "共 " + items.length + " 个订阅", [
      el("button", { class: "btn primary", text: "+ 新增订阅", onclick: subscribeForm }),
      runAll,
    ]);
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
        buttons.push(
          el("button", { class: "btn sm", text: "暂停", onclick: () => control(row.id, "pause") })
        );
      }
      if (row.status === "paused") {
        buttons.push(
          el("button", { class: "btn sm", text: "继续", onclick: () => control(row.id, "resume") })
        );
      }
      if (row.kind === "pan" && row.meta && row.meta.page_url) {
        buttons.push(
          el("a", {
            class: "btn sm",
            href: row.meta.page_url,
            target: "_blank",
            rel: "noreferrer",
            text: row.meta.password ? "打开(码:" + row.meta.password + ")" : "打开网盘",
          })
        );
      }
      const remove = el("button", { class: "btn sm danger", text: "删除" });
      remove.addEventListener("click", async () => {
        if (!confirm("确定删除该任务？")) return;
        try {
          await api("/downloads/" + row.id, { method: "DELETE" });
          toast("已删除", "ok");
          pageDownloads();
        } catch (error) {
          toast(error.message, "err");
        }
      });
      buttons.push(remove);
      return el("div", { class: "row tight" }, buttons);
    };

    const content = el("div", { class: "card" }, [
      table(
        [
          {
            title: "任务",
            render: (row) =>
              el("div", {}, [
                el("div", { class: "truncate", title: row.title, text: row.title }),
                el("div", {
                  class: "muted",
                  style: "font-size:11px",
                  text:
                    kindLabel(row.kind) + " · " + (row.site || "-") + " · " + fmtSize(row.size),
                }),
              ]),
          },
          {
            title: "进度",
            render: (row) => {
              const percent = Math.round((row.progress || 0) * 100);
              return el("div", {}, [
                el("div", { class: "progress" }, [el("i", { style: "width:" + percent + "%" })]),
                el("div", {
                  class: "muted",
                  style: "font-size:11px;margin-top:4px",
                  text: percent + "% · " + fmtSpeed(row.speed),
                }),
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
              el("span", { class: "muted", style: "font-size:11px", text: fmtTime(row.created_at) }),
          },
          { title: "操作", render: actionsCell },
        ],
        items,
        "暂无下载任务"
      ),
    ]);

    const sync = el("button", { class: "btn primary", text: "同步状态并整理" });
    sync.addEventListener("click", async () => {
      sync.disabled = true;
      sync.textContent = "同步中…";
      try {
        const result = await api("/downloads/sync", { method: "POST" });
        toast("检查 " + result.checked + " 个，完成 " + result.completed + " 个", "ok");
        pageDownloads();
      } catch (error) {
        toast(error.message, "err");
        sync.disabled = false;
        sync.textContent = "同步状态并整理";
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
        statCard("文件总数", data.files),
        statCard("占用空间", fmtSize(data.size)),
        statCard("剧集数", data.series, data.episodes + " 集"),
        statCard("电影数", data.movies),
      ]),
      el("div", { class: "card" }, [
        el("h3", { text: "入库文件" }),
        table(
          [
            { title: "标题", render: (row) => row.title },
            { title: "类型", render: (row) => typeLabel(row.media_type) },
            { title: "季集", render: (row) => seasonEpisode(row.season, row.episode) },
            { title: "画质", render: (row) => row.resolution || "-" },
            { title: "大小", render: (row) => fmtSize(row.size) },
            {
              title: "路径",
              render: (row) =>
                el("div", { class: "truncate mono muted", title: row.path, text: row.path }),
            },
          ],
          files.items,
          "媒体库为空，可点击扫描媒体库导入已有文件"
        ),
      ]),
    ]);

    const scan = el("button", { class: "btn", text: "扫描媒体库" });
    scan.addEventListener("click", async () => {
      scan.disabled = true;
      scan.textContent = "扫描中…";
      try {
        const result = await api("/library/scan", { method: "POST" });
        toast("扫描 " + result.scanned + " 个文件，新增 " + result.added + " 个", "ok");
        pageLibrary();
      } catch (error) {
        toast(error.message, "err");
        scan.disabled = false;
        scan.textContent = "扫描媒体库";
      }
    });

    const refresh = el("button", { class: "btn", text: "刷新媒体服务器" });
    refresh.addEventListener("click", async () => {
      try {
        const result = await api("/library/refresh", { method: "POST" });
        toast("已通知 " + result.refreshed + " 个媒体服务器", "ok");
      } catch (error) {
        toast(error.message, "err");
      }
    });

    shell(content, "媒体库", data.files + " 个文件 · " + fmtSize(data.size), [
      el("button", { class: "btn", text: "手动整理", onclick: transferForm }),
      scan,
      refresh,
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

  function siteForm(providers) {
    modal(
      "新增站点",
      [
        { key: "name", label: "站点名称", placeholder: "自定义显示名" },
        {
          key: "kind",
          label: "类别",
          type: "select",
          value: "indexer",
          options: Object.keys(KIND_LABELS).map((value) => ({
            value: value,
            label: KIND_LABELS[value],
          })),
        },
        {
          key: "provider",
          label: "Provider",
          type: "select",
          value: "torznab",
          options: providers.map((item) => ({
            value: item.name,
            label: item.display_name + "（" + item.kind + "）",
          })),
        },
        {
          key: "url",
          label: "地址 URL",
          placeholder: "如：http://127.0.0.1:9117/api/v2.0/indexers/all/results/torznab",
        },
        { key: "api_key", label: "API Key / Token（可选）" },
        { key: "username", label: "用户名（可选）" },
        { key: "password", label: "密码（可选）", type: "password" },
        { key: "cookie", label: "Cookie（可选）", type: "textarea" },
        { key: "priority", label: "优先级（越小越优先）", type: "number", value: 50 },
        { key: "enabled", label: "启用", type: "checkbox", value: true },
      ],
      async (values) => {
        if (!values.name || !values.provider) throw new Error("名称与 Provider 必填");
        await api("/sites", {
          method: "POST",
          body: Object.assign({}, values, { priority: values.priority || 50 }),
        });
        toast("站点已添加", "ok");
        pageSites();
      },
      "添加站点"
    );
  }

  async function pageSites() {
    shell(loading(), "站点管理", "索引器、盘搜、下载器、媒体服务器与通知");
    const [sites, providers] = await Promise.all([
      api("/sites"),
      api("/sites/providers"),
    ]);

    const actionsCell = (row) => {
      const test = el("button", { class: "btn sm", text: "测试" });
      test.addEventListener("click", async () => {
        test.disabled = true;
        test.textContent = "检测中…";
        try {
          const result = await api("/sites/" + row.id + "/test", { method: "POST" });
          toast(row.name + "：" + result.message, result.success ? "ok" : "err");
          pageSites();
        } catch (error) {
          toast(error.message, "err");
          test.disabled = false;
          test.textContent = "测试";
        }
      });

      const toggle = el("button", { class: "btn sm", text: row.enabled ? "禁用" : "启用" });
      toggle.addEventListener("click", async () => {
        try {
          await api("/sites/" + row.id, {
            method: "PATCH",
            body: { enabled: !row.enabled },
          });
          pageSites();
        } catch (error) {
          toast(error.message, "err");
        }
      });

      const remove = el("button", { class: "btn sm danger", text: "删除" });
      remove.addEventListener("click", async () => {
        if (!confirm("确定删除站点 " + row.name + "？")) return;
        try {
          await api("/sites/" + row.id, { method: "DELETE" });
          toast("已删除", "ok");
          pageSites();
        } catch (error) {
          toast(error.message, "err");
        }
      });

      return el("div", { class: "row tight" }, [test, toggle, remove]);
    };

    const groups = Object.keys(KIND_LABELS)
      .map((kind) => {
        const rows = sites.filter((item) => item.kind === kind);
        if (!rows.length) return null;
        return el("div", { class: "card" }, [
          el("h3", { text: KIND_LABELS[kind] + "（" + rows.length + "）" }),
          table(
            [
              {
                title: "名称",
                render: (row) =>
                  el("div", {}, [
                    el("div", { text: row.name }),
                    el("div", { class: "muted mono", style: "font-size:11px", text: row.provider }),
                  ]),
              },
              {
                title: "地址",
                render: (row) =>
                  el("div", {
                    class: "truncate mono muted",
                    title: row.url,
                    text: row.url || "-",
                  }),
              },
              {
                title: "状态",
                render: (row) =>
                  el("span", {
                    class: "tag " + (row.enabled ? "ok" : ""),
                    text: row.enabled ? "已启用" : "已禁用",
                  }),
              },
              { title: "优先级", key: "priority" },
              {
                title: "最近检测",
                render: (row) =>
                  el("div", {
                    class: "muted",
                    style: "font-size:11px",
                    text: row.last_status || "未检测",
                  }),
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
        groups.length ? groups : [el("div", { class: "card empty", text: "还没有配置站点" })]
      ),
      "站点管理",
      "共 " + sites.length + " 个配置",
      [
        el("button", {
          class: "btn primary",
          text: "+ 新增站点",
          onclick: () => siteForm(providers),
        }),
      ]
    );
  }

  // ---------------- 插件 ----------------
  async function pagePlugins() {
    shell(loading(), "插件", "扩展能力，无需修改核心代码");
    const data = await api("/plugins");

    const cards = data.items.map((item) => {
      const toggle = el("button", {
        class: "btn sm " + (item.enabled ? "" : "primary"),
        text: item.enabled ? "停用" : "启用",
      });
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

      const configButton = el("button", {
        class: "btn sm ghost",
        text: "配置",
      });
      configButton.addEventListener("click", () => {
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
      });

      const actionButtons = (item.actions || []).map((action) => {
        const button = el("button", { class: "btn sm", text: action });
        button.addEventListener("click", async () => {
          button.disabled = true;
          try {
            const result = await api("/plugins/" + item.id + "/run", {
              method: "POST",
              body: { action: action, params: {} },
            });
            toast("执行成功：" + JSON.stringify(result.result).slice(0, 80), "ok");
          } catch (error) {
            toast(error.message, "err");
          } finally {
            button.disabled = false;
          }
        });
        return button;
      });

      return el("div", { class: "card" }, [
        el("div", { class: "row", style: "align-items:flex-start" }, [
          el("div", { style: "flex:1" }, [
            el("h3", { style: "margin-bottom:6px" }, [
              item.name,
              " ",
              el("span", { class: "tag", text: "v" + item.version }),
              item.enabled
                ? el("span", { class: "tag ok", style: "margin-left:6px", text: "已启用" })
                : null,
            ]),
            el("div", {
              class: "muted",
              style: "font-size:12px",
              text: item.description || "无描述",
            }),
            item.author
              ? el("div", {
                  class: "muted",
                  style: "font-size:11px;margin-top:4px",
                  text: "作者：" + item.author,
                })
              : null,
            item.last_error
              ? el("div", { class: "tag err", style: "margin-top:8px", text: item.last_error })
              : null,
          ]),
          el("div", { class: "row tight", style: "flex:0 0 auto" }, [
            toggle,
            configButton,
            ...actionButtons,
          ]),
        ]),
      ]);
    });

    shell(
      el(
        "div",
        { class: "grid" },
        cards.length
          ? cards
          : [
              el("div", {
                class: "card empty",
                text: "plugins/ 目录下暂无插件，放入插件目录后刷新即可。",
              }),
            ]
      ),
      "插件",
      "共 " + data.total + " 个插件"
    );
  }

  // ---------------- 运行日志 ----------------
  async function pageLogs() {
    shell(loading(), "运行日志", "调度任务与最近日志");
    const [logs, jobs] = await Promise.all([
      api("/system/logs?limit=500"),
      api("/system/jobs"),
    ]);

    const box = el(
      "div",
      { class: "logs" },
      logs.items.map((item) =>
        el("div", {
          class: "log-line log-" + item.level,
          text: item.time + " [" + item.level + "] " + item.logger + " - " + item.message,
        })
      )
    );

    const jobsCard = el("div", { class: "card" }, [
      el("h3", { text: "定时任务" }),
      table(
        [
          { title: "任务", render: (row) => row.name },
          {
            title: "触发规则",
            render: (row) => el("span", { class: "mono muted", text: row.trigger }),
          },
          { title: "下次执行", render: (row) => fmtTime(row.next_run_time) },
          {
            title: "操作",
            render: (row) => {
              const button = el("button", { class: "btn sm", text: "立即执行" });
              button.addEventListener("click", async () => {
                try {
                  await api("/system/jobs/" + encodeURIComponent(row.id) + "/run", {
                    method: "POST",
                  });
                  toast("已触发", "ok");
                } catch (error) {
                  toast(error.message, "err");
                }
              });
              return button;
            },
          },
        ],
        jobs.items,
        "调度器未启动或暂无任务"
      ),
    ]);

    const testNotify = el("button", { class: "btn", text: "测试通知" });
    testNotify.addEventListener("click", async () => {
      try {
        const result = await api("/system/notify/test", { method: "POST" });
        toast(result.message, result.success ? "ok" : "err");
      } catch (error) {
        toast(error.message, "err");
      }
    });

    shell(
      el("div", { class: "grid" }, [
        jobsCard,
        el("div", { class: "card" }, [el("h3", { text: "日志" }), box]),
      ]),
      "运行日志",
      logs.total + " 条",
      [el("button", { class: "btn", text: "刷新", onclick: pageLogs }), testNotify]
    );
    box.scrollTop = box.scrollHeight;
  }

  // ---------------- 路由 ----------------
  const ROUTES = {
    dashboard: pageDashboard,
    search: pageSearch,
    subscribes: pageSubscribes,
    downloads: pageDownloads,
    library: pageLibrary,
    sites: pageSites,
    plugins: pagePlugins,
    logs: pageLogs,
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
          el("div", { class: "card empty", text: error.message }),
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
