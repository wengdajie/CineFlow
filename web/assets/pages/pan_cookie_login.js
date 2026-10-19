import { el, iconButton, panelModal, toast } from "../ui.js";
import { API } from "../api.js";
import { store } from "../state.js";

export function showBrowserCookieLogin({ api, provider, onDone, autoStart = false }) {
  const requestId = [...crypto.getRandomValues(new Uint8Array(16))].map(x => x.toString(16).padStart(2, "0")).join("");
  const replies = new Map();
  let active = false, closed = false, busy = false;
  const cookie = el("textarea", { class: "input", rows: 3, placeholder: "登录助手自动获取，也可在这里手动粘贴完整 Cookie", autocomplete: "off" });
  const siteName = el("input", { class: "input", placeholder: provider.label });
  const status = el("div", { class: "muted", role: "status", "aria-live": "polite", text: "首次使用请安装并连接登录助手，之后登录成功即可自动返回并保存。" });
  const content = el("div", {});
  const close = panelModal("登录「" + provider.label + "」并获取 Cookie", "在官网完成登录后自动校验并保存，无需打开 F12。", content, false);
  const post = (action, extra = {}) => window.postMessage({ source: "cineflow-cookie-login", requestId, action, provider: provider.provider, ...extra }, location.origin);
  const request = (action, extra = {}, timeout = 4000) => new Promise((resolve, reject) => {
    const timer = setTimeout(() => { replies.delete(action); reject(new Error("未连接登录助手，请先下载扩展并连接当前地址")); }, timeout);
    replies.set(action, { resolve, reject, timer });
    post(action, extra);
  });
  const save = async (value, automatic) => {
    busy = true; saveButton.disabled = true; cookie.disabled = true;
    status.textContent = "正在校验 Cookie 并保存账号…";
    try {
      const result = await api("/pan/login/cookie", { method: "POST", body: {
        provider: provider.provider, cookie: value, verify: true, site_name: siteName.value.trim() || null,
      } });
      toast(result.message || "登录成功，凭据已保存", "ok");
      return true;
    } catch (error) {
      status.textContent = automatic ? error.message + "；请在夸克页面完成登录，助手会继续检测。" : error.message;
      if (!automatic) toast(error.message, "err");
      return false;
    } finally { busy = false; saveButton.disabled = false; cookie.disabled = false; }
  };
  const listener = async (event) => {
    const message = event.data;
    if (event.source !== window || event.origin !== location.origin || message?.source !== "cineflow-cookie-helper" || message.requestId !== requestId || closed) return;
    if (message.type === "response") {
      const reply = replies.get(message.action);
      if (!reply) return;
      clearTimeout(reply.timer); replies.delete(message.action);
      if (message.success) reply.resolve(message);
      else reply.reject(new Error(message.message || "登录助手未连接"));
    } else if (message.type === "ended" && active) {
      active = false; startButton.disabled = false; status.textContent = message.message;
    } else if (message.type === "candidate" && active && !busy && typeof message.cookie === "string") {
      cookie.value = message.cookie;
      const accepted = await save(message.cookie, true);
      if (closed) return;
      await request("finish", { accepted }).catch(() => {});
      if (accepted) { active = false; cookie.value = ""; close(); if (onDone) onDone(); }
    }
  };
  window.addEventListener("message", listener);
  const start = async () => {
    if (active || busy || closed) return;
    startButton.disabled = true;
    try {
      await request("probe");
      active = true;
      status.textContent = "已打开夸克官网，请完成登录。成功后会自动返回本页。";
      await request("start", {}, 10000);
    } catch (error) { active = false; startButton.disabled = false; status.textContent = error.message; }
  };
  const startButton = iconButton("跳转登录并自动获取", "user", start, "primary");
  const saveButton = iconButton("校验并保存", "check", async () => {
    if (busy) return;
    if (!cookie.value.trim()) { status.textContent = "请先登录获取 Cookie，或手动粘贴"; return; }
    if (active) { active = false; await request("cancel").catch(() => {}); startButton.disabled = false; }
    if (await save(cookie.value.trim(), false)) { cookie.value = ""; close(); if (onDone) onDone(); }
  }, "primary");
  const download = iconButton("下载登录助手", "download", async () => {
    try {
      const response = await fetch(API + "/pan/login/browser-extension", { headers: { Authorization: "Bearer " + store.token } });
      if (!response.ok) throw new Error("下载失败，请确认已登录 CineFlow");
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a"); link.href = url; link.download = "cineflow-login-helper.zip"; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { status.textContent = error.message; }
  }, "ghost");
  content.append(
    el("div", { class: "row wrap", style: "margin-bottom:12px" }, [startButton, download]),
    el("details", { style: "margin-bottom:16px" }, [el("summary", { text: "首次配置登录助手" }),
      el("p", { class: "tiny dim", text: "下载并解压扩展，在 chrome://extensions 或 edge://extensions 开启开发者模式，加载已解压的扩展。点击工具栏助手，填入下面的地址并连接。" }),
      el("code", { text: location.origin }),
      el("p", { class: "tiny dim", text: "支持桌面 Chrome/Edge；NAS/Docker 无需安装浏览器。没有扩展时可继续手动导入。" }),
    ]),
    el("div", { class: "field" }, [el("label", { text: "完整 Cookie" }), cookie]),
    el("div", { class: "field" }, [el("label", { text: "站点名称（留空自动生成）" }), siteName]),
    status, el("div", { class: "modal-actions" }, [saveButton]),
  );
  const observer = new MutationObserver(() => {
    if (content.isConnected) return;
    closed = true; if (active) post("cancel"); active = false;
    cookie.value = ""; window.removeEventListener("message", listener);
    for (const reply of replies.values()) { clearTimeout(reply.timer); reply.reject(new Error("登录窗口已关闭")); }
    replies.clear(); observer.disconnect();
  });
  observer.observe(document.getElementById("modal-root"), { childList: true });
  if (autoStart) start();
}
