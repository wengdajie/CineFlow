const SCOPE = "cineflow-login-helper";
const MEMBER_URL = "https://drive-pc.quark.cn/1/clouddrive/member";
const TTL = 10 * 60 * 1000;
let queue = Promise.resolve();
const serialized = (action) => { const result = queue.then(() => action()); queue = result.catch(() => {}); return result; };
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
async function focusApp(state) {
  const tab = await chrome.tabs.update(state.appTabId, { active: true });
  await chrome.windows.update(tab.windowId, { focused: true });
}
async function progress(state, message) {
  if (state.lastMessage === message) return;
  await chrome.storage.session.set({ pending: { ...state, lastMessage: message } });
  await deliver(state, { type: "progress", message });
}
async function check(force = false) {
  const state = await pending();
  if (!state) return;
  if (Date.now() >= state.expiresAt) return clear(state, "登录等待已超时，请重新开始");
  const { appOrigin } = await chrome.storage.local.get("appOrigin");
  const app = await chrome.tabs.get(state.appTabId).catch(() => null);
  const login = await chrome.tabs.get(state.loginTabId).catch(() => null);
  if (!app || !login || appOrigin !== state.appOrigin || new URL(app.url).origin !== state.appOrigin) {
    return clear(state, "登录窗口已关闭或连接地址已变更");
  }
  if (!trustedQuark(login.url)) return;
  if (state.checking && (!force || Date.now() - state.checkingAt < 30000)) return;
  let cookies;
  try { cookies = await chrome.cookies.getAll({ url: MEMBER_URL }); }
  catch { return progress(state, "读取夸克 Cookie 失败，请检查扩展的夸克站点权限，再点击重新检测并回填。"); }
  // __pus 并非所有网页登录态都有；凭据是否有效交给服务端校验。
  if (!cookies.some(item => ["__pus", "__puus"].includes(item.name) && item.value)) {
    return progress(state, "尚未检测到夸克登录凭据。请在助手打开的官网完成登录；已登录可刷新官网后点击重新检测并回填。");
  }
  const names = new Set();
  const selected = cookies.filter(item => {
    if (names.has(item.name) || /[\r\n;]/.test(item.name + item.value)) return false;
    names.add(item.name); return true;
  });
  const cookie = selected.map(item => item.name + "=" + item.value).join("; ");
  // 统计等非登录 Cookie 的变化不能反复抢走官网焦点。
  const credentials = selected.filter(item => ["__pus", "__puus"].includes(item.name)).map(item => item.name + "=" + item.value).sort().join("; ");
  const hash = [...new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(credentials)))].map(x => x.toString(16).padStart(2, "0")).join("");
  if (!force && hash === state.fingerprint) return;
  // 只保存摘要和流程状态，绝不将 Cookie 写入 storage。
  await chrome.storage.session.set({ pending: { ...state, checking: true, checkingAt: Date.now(), fingerprint: hash, lastMessage: "已获取 Cookie，正在回填并校验…" } });
  try { await deliver(state, { type: "candidate", cookie }); }
  catch { return clear(state, "无法连接原 CineFlow 页面，请刷新控制台后重新开始"); }
  // 获取成功即返回，让校验失败的原因也能在控制台被看见。
  await focusApp(state).catch(() => {});
}
async function command(message, sender) {
  if (sender.id === chrome.runtime.id && sender.url === chrome.runtime.getURL("popup.html")) {
    if (!["status", "retry"].includes(message.action)) return { success: false, message: "不支持的助手操作" };
    if (message.action === "retry") await check(true);
    const state = await pending();
    return { success: true, active: !!state, message: state ? state.lastMessage || "等待官网登录完成，可点击重新检测并回填。" : "没有等待中的登录，请回到 CineFlow 发起浏览器登录。" };
  }
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
  if (message.action === "retry") { await check(true); return { success: true }; }
  if (message.action === "finish" && state.checking) {
    if (message.accepted) {
      await clear(state);
      await focusApp(state).catch(() => {});
    } else {
      await chrome.storage.session.set({ pending: { ...state, checking: false, lastMessage: "Cookie 已回填，但校验未通过。原因见 CineFlow；可完成登录后重新检测并回填。" } });
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
