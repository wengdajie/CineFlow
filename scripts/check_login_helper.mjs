// 使用隔离的 Chrome API 替身验证跨站凭据桥接的边界与生命周期。
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { webcrypto } from "node:crypto";
import vm from "node:vm";

const source = readFileSync("web/assets/login-helper/background.js", "utf8");
const manifest = JSON.parse(readFileSync("web/assets/login-helper/manifest.json", "utf8"));
assert.deepEqual(manifest.host_permissions, ["https://*.quark.cn/*"]);
assert.ok(!manifest.permissions.includes("tabs"));
assert.ok(!manifest.content_scripts && !manifest.externally_connectable);
const origin = "http://127.0.0.1:6060";
const id = "1234567890abcdef1234567890abcdef";
const scope = "cineflow-login-helper";
const event = () => { const listeners = []; return { addListener: fn => listeners.push(fn), emit: (...args) => listeners.map(fn => fn(...args)) }; };
function fixture(allowed = origin) {
  const state = { local: { appOrigin: allowed }, session: {}, cookies: [], sent: [], removed: [], focused: [], reads: [], now: 1000 };
  const tabs = new Map([[1, { id: 1, url: origin + "/#storage", windowId: 3 }], [2, { id: 2, url: origin, windowId: 3 }]]);
  const storage = key => ({
    async get(name) { return structuredClone({ [name]: state[key][name] }); },
    async set(value) { Object.assign(state[key], structuredClone(value)); },
    async remove(name) { delete state[key][name]; },
  });
  const chrome = {
    runtime: { onMessage: event(), getManifest: () => ({ version: "1.0.0" }) },
    storage: { local: storage("local"), session: storage("session") },
    cookies: { onChanged: event(), async getAll(query) { state.reads.push(query); return structuredClone(state.cookies); } },
    alarms: { onAlarm: event(), create: async () => {}, clear: async () => {} },
    windows: { async update(windowId) { state.focused.push(windowId); } },
    tabs: {
      onUpdated: event(), onRemoved: event(),
      async get(tabId) { if (!tabs.has(tabId)) throw new Error("missing tab"); return tabs.get(tabId); },
      async create({ url }) { const tab = { id: 10, url, windowId: 3 }; tabs.set(10, tab); return tab; },
      async remove(tabId) { tabs.delete(tabId); state.removed.push(tabId); },
      async update(tabId, values) { Object.assign(tabs.get(tabId), values); return tabs.get(tabId); },
      async sendMessage(tabId, message, options) { state.sent.push({ tabId, message: structuredClone(message), options }); },
    },
  };
  const context = vm.createContext({ chrome, crypto: webcrypto, TextEncoder, URL, Date: class extends Date { static now() { return state.now; } } });
  vm.runInContext(source, context);
  const sender = (extra = {}) => ({ frameId: 0, url: tabs.get(1).url, tab: tabs.get(1), ...extra });
  const send = (action, extra = {}, from = sender()) => new Promise(resolve => {
    chrome.runtime.onMessage.emit({ scope, action, requestId: id, provider: "quark", ...extra }, from, resolve);
  });
  const flush = async () => { await vm.runInContext("queue", context); };
  return { state, tabs, chrome, send, sender, flush };
}
const validCookies = () => [
  { name: "__pus", value: "fixture-private-pus", httpOnly: true },
  { name: "__puus", value: "fixture-private-puus", httpOnly: true },
];
let cases = 0;
{
  const f = fixture("");
  assert.equal((await f.send("start")).success, false);
  assert.equal(f.state.reads.length, 0); cases++;
}
for (const from of [
  { frameId: 1 },
  { url: "http://evil.test/" },
  { url: "http://127.0.0.1:6061/" },
  { tab: { id: 1, url: "http://evil.test/" } },
  { tab: { id: 1, url: origin, incognito: true } },
]) {
  const f = fixture();
  assert.equal((await f.send("start", {}, f.sender(from))).success, false);
  assert.equal(f.state.reads.length, 0); cases++;
}
{
  const f = fixture();
  assert.equal((await f.send("probe")).success, true);
  assert.equal(f.state.reads.length, 0);
  assert.equal((await f.send("start", { requestId: "bad" })).success, false);
  assert.equal((await f.send("start", { provider: "evil" })).success, false);
  assert.equal((await f.send("start")).success, true);
  assert.equal(f.tabs.get(10).url, "https://pan.quark.cn/");
  assert.equal(f.state.sent.length, 0);
  assert.equal((await f.send("start")).success, false); cases++;
}
{
  const f = fixture(); f.state.cookies = validCookies();
  await f.send("start");
  const candidate = f.state.sent[0];
  assert.equal(candidate.tabId, 1); assert.equal(candidate.options.frameId, 0);
  assert.equal(candidate.message.cookie, "__pus=fixture-private-pus; __puus=fixture-private-puus");
  assert.equal(candidate.message.requestId, id);
  assert.equal(f.state.reads[0].url, "https://drive-pc.quark.cn/1/clouddrive/member");
  assert.ok(!JSON.stringify(f.state.session).includes("fixture-private"));
  assert.ok(!JSON.stringify(f.state.local).includes("fixture-private"));
  f.chrome.cookies.onChanged.emit({ cookie: { domain: ".quark.cn" } }); await f.flush();
  assert.equal(f.state.sent.length, 1);
  assert.equal((await f.send("finish", { accepted: true, requestId: "f".repeat(32) })).success, false);
  assert.equal((await f.send("finish", { accepted: true }, f.sender({ tab: f.tabs.get(2) }))).success, false);
  assert.equal(f.state.focused.length, 0);
  await f.send("finish", { accepted: false });
  assert.equal(f.state.sent.length, 1);
  f.state.cookies[1].value = "fixture-refreshed";
  f.chrome.cookies.onChanged.emit({ cookie: { domain: ".quark.cn" } }); await f.flush();
  assert.equal(f.state.sent.length, 2);
  await f.send("finish", { accepted: true });
  assert.equal(f.state.session.pending, undefined);
  assert.equal(f.tabs.get(1).active, true);
  assert.deepEqual(f.state.removed, [10]);
  assert.deepEqual(f.state.focused, [3]); cases++;
}
for (const reason of ["cancel", "expire", "navigate", "reconfigure", "close-login"]) {
  const f = fixture(); await f.send("start"); f.state.reads.length = 0;
  if (reason === "cancel") await f.send("cancel");
  if (reason === "expire") f.state.now += 600001;
  if (reason === "navigate") f.tabs.get(1).url = "https://evil.test/";
  if (reason === "reconfigure") f.state.local.appOrigin = "https://other.test";
  if (reason === "close-login") f.tabs.delete(10);
  f.state.cookies = validCookies(); f.chrome.tabs.onUpdated.emit(1); await f.flush();
  assert.equal(f.state.session.pending, undefined);
  assert.equal(f.state.reads.length, 0);
  assert.ok(!f.state.sent.some(item => item.message.cookie)); cases++;
}
{
  const f = fixture(); await f.send("start");
  f.tabs.get(10).url = "https://quark.cn.evil.test/";
  f.state.cookies = validCookies(); f.chrome.tabs.onUpdated.emit(10); await f.flush();
  assert.equal(f.state.sent.length, 0);
  assert.equal(f.state.reads.length, 1); // 仅启动时的那一次读取。
  await f.send("cancel");
  assert.deepEqual(f.state.removed, []); cases++;
}
console.log(`Login helper contracts checked: ${cases} cases`);
