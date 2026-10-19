const SCOPE = "cineflow-login-helper";
const MEMBER_URL = "https://drive-pc.quark.cn/1/clouddrive/member";
const TTL = 10 * 60 * 1000;
let queue = Promise.resolve();
const serialized = (action) => { const result = queue.then(action); queue = result.catch(() => {}); return result; };
const pending = async () => (await chrome.storage.session.get("pending")).pending;
const trustedQuark = (value) => {
  try { const url = new URL(value); return url.protocol === "https:" && (url.hostname === "quark.cn" || url.hostname.endsWith(".quark.cn")); }
  catch { return false; }
};
async function deliver(state, message) {
  const { appOrigin } = await chrome.storage.local.get("appOrigin");
  const tab = await chrome.tabs.get(state.appTabId);
  if (appOrigin !== state.appOrigin || new URL(tab.url).origin !== state.appOrigin) throw new Error("CineFlow 页面已变更");
  const options = { frameId: 0 };
  if (state.appDocumentId) options.documentId = state.appDocumentId;
  await chrome.tabs.sendMessage(state.appTabId, { scope: SCOPE, requestId: state.requestId, ...message }, options);
}
async function clear(state, message) {
  await chrome.storage.session.remove("pending");
  await chrome.alarms.clear("cineflow-login-expiry");
  if (!state) return;
  if (message) await deliver(state, { type: "ended", message }).catch(() => {});
  const tab = await chrome.tabs.get(state.loginTabId).catch(() => null);
  if (tab && trustedQuark(tab.url)) await chrome.tabs.remove(tab.id).catch(() => {});
}
async function check() {
  const state = await pending();
  if (!state) return;
  if (Date.now() >= state.expiresAt) return clear(state, "登录等待已超时，请重新开始");
  const { appOrigin } = await chrome.storage.local.get("appOrigin");
  const app = await chrome.tabs.get(state.appTabId).catch(() => null);
  const login = await chrome.tabs.get(state.loginTabId).catch(() => null);
  if (!app || !login || appOrigin !== state.appOrigin || new URL(app.url).origin !== state.appOrigin) {
    return clear(state, "登录窗口已关闭或连接地址已变更");
  }
  if (state.checking || !trustedQuark(login.url)) return;
  const cookies = await chrome.cookies.getAll({ url: MEMBER_URL });
  if (!["__pus", "__puus"].every(name => cookies.some(item => item.name === name && item.value))) return;
  const names = new Set();
  const cookie = cookies.filter(item => {
    if (names.has(item.name) || /[\r\n;]/.test(item.name + item.value)) return false;
    names.add(item.name); return true;
  }).map(item => item.name + "=" + item.value).join("; ");
  const hash = [...new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(cookie)))].map(x => x.toString(16).padStart(2, "0")).join("");
  if (hash === state.fingerprint) return;
  // 只保存摘要和流程状态，绝不将 Cookie 写入 storage。
  await chrome.storage.session.set({ pending: { ...state, checking: true, fingerprint: hash } });
  await deliver(state, { type: "candidate", cookie }).catch(() => clear(state, "无法连接原 CineFlow 页面，请重新开始"));
}
async function command(message, sender) {
  const { appOrigin } = await chrome.storage.local.get("appOrigin");
  if (!sender.tab || sender.frameId !== 0 || sender.tab.incognito || !appOrigin ||
      new URL(sender.url).origin !== appOrigin || new URL(sender.tab.url).origin !== appOrigin) {
    return { success: false, message: "请先在登录助手中连接当前 CineFlow 地址" };
  }
  if (!/^[a-f0-9-]{32,64}$/.test(message.requestId || "")) return { success: false, message: "登录请求无效" };
  if (message.action === "probe") return { success: true, version: chrome.runtime.getManifest().version };
  let state = await pending();
  if (state && Date.now() >= state.expiresAt) { await clear(state, "登录等待已超时"); state = null; }
  if (state && state.appTabId === sender.tab.id && state.appDocumentId && state.appDocumentId !== sender.documentId) {
    await clear(state, "控制台页面已刷新，请重新开始"); state = null;
  }
  if (message.action === "start") {
    if (message.provider !== "quark") return { success: false, message: "当前仅支持夸克浏览器登录" };
    if (state) return { success: false, message: "已有登录窗口，请先完成或取消" };
    const login = await chrome.tabs.create({ url: "https://pan.quark.cn/", active: true });
    state = { appTabId: sender.tab.id, appDocumentId: sender.documentId, appOrigin, loginTabId: login.id, requestId: message.requestId, expiresAt: Date.now() + TTL };
    await chrome.storage.session.set({ pending: state });
    await chrome.alarms.create("cineflow-login-expiry", { when: state.expiresAt });
    await check();
    return { success: true };
  }
  if (!state || state.appOrigin !== appOrigin || state.requestId !== message.requestId || state.appTabId !== sender.tab.id) return { success: false, message: "登录会话已结束" };
  if (message.action === "cancel") { await clear(state); return { success: true }; }
  if (message.action === "finish" && state.checking) {
    if (message.accepted) {
      await clear(state);
      const tab = await chrome.tabs.update(state.appTabId, { active: true });
      await chrome.windows.update(tab.windowId, { focused: true });
    } else {
      await chrome.storage.session.set({ pending: { ...state, checking: false } });
      await check();
    }
    return { success: true };
  }
  return { success: false, message: "不支持的登录操作" };
}
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (message?.scope !== SCOPE) return;
  serialized(() => command(message, sender)).then(respond, () => respond({ success: false, message: "浏览器登录处理失败，请重试" }));
  return true;
});
const schedule = () => { serialized(check).catch(() => {}); };
chrome.cookies.onChanged.addListener(({ cookie }) => {
  if (cookie.domain === "quark.cn" || cookie.domain.endsWith(".quark.cn")) schedule();
});
chrome.tabs.onUpdated.addListener((tabId, change = {}) => {
  serialized(async () => {
    const state = await pending();
    if (state && state.appTabId === tabId && change.status === "loading") return clear(state, "控制台页面已刷新，请重新开始");
    return check();
  }).catch(() => {});
});
chrome.tabs.onRemoved.addListener(schedule);
chrome.alarms.onAlarm.addListener(({ name }) => { if (name === "cineflow-login-expiry") schedule(); });
