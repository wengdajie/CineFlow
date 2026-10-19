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

function safeHref(value) {
  const href = String(value || "").trim();
  if (!href) return null;
  try {
    const parsed = new URL(href, globalThis.location?.href || "http://localhost/");
    return ["http:", "https:"].includes(parsed.protocol) ? href : null;
  } catch { return null; }
}

export { safeHref };

const el = (tag, attrs, children) => {
  const node = document.createElement(tag);
  Object.entries(attrs || {}).forEach(([key, value]) => {
    if (value === null || value === undefined || value === false) return;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "href") {
      const href = safeHref(value);
      if (href) node.setAttribute(key, href);
    }
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

const fmtCompact = (value) => {
  const n = Number(value) || 0;
  if (n >= 100000000) return (n / 100000000).toFixed(1).replace(/\.0$/, "") + "亿";
  if (n >= 10000) return (n / 10000).toFixed(1).replace(/\.0$/, "") + "万";
  return String(n);
};

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

const STATUS_MAP = {
  submitting: ["投递待确认", "warn"],
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

const resourceKindLabel = (row) => row.kind === "pan"
  ? row.pan_type || (row.meta || {}).pan_type || (row.extra || {}).pan_type || "待识别网盘"
  : kindLabel(row.kind);

const SITE_STATUS = {
  ok: ["有结果", "ok"],
  empty: ["无匹配", ""],
  filtered: ["全被过滤", "warn"],
  timeout: ["超时", "warn"],
  skipped: ["已跳过", "warn"],
  error: ["失败", "err"],
};

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

function iconButton(label, iconName, onclick, cls) {
  const button = el("button", { class: "btn " + (cls || "") }, [
    icon(iconName, "sm"),
    el("span", { text: label }),
  ]);
  button.addEventListener("click", onclick);
  return button;
}

function statCard(label, value, hint, iconName) {
  return el("div", { class: "card stat" }, [
    iconName ? el("div", { class: "stat-icon" }, [icon(iconName, "sm")]) : null,
    el("div", { class: "label", text: label }),
    el("div", { class: "value", text: value }),
    hint ? el("div", { class: "hint", text: hint }) : null,
  ]);
}

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

export { ICONS, icon, el, fmtSize, fmtSpeed, fmtDuration, fmtCompact, baseName, fmtTime, pad2, fmtRelative, toast, STATUS_MAP, statusTag, typeLabel, typeIcon, kindLabel, resourceKindLabel, SITE_STATUS, loading, emptyBox, table, segment, modal, panelModal, iconButton, statCard, heatCell, rankCell };
