const address = document.getElementById("address");
const status = document.getElementById("status");
chrome.storage.local.get("appOrigin").then(({ appOrigin }) => { address.value = appOrigin || ""; });
document.getElementById("connect").addEventListener("click", async () => {
  try {
    const url = new URL(address.value.trim());
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) {
      throw new Error("请输入有效的 HTTP/HTTPS 地址，不要包含用户名或密码");
    }
    const pattern = url.protocol + "//" + url.hostname + "/*";
    // permissions.request 必须由这次按钮点击直接触发。
    if (!await chrome.permissions.request({ origins: [pattern] })) {
      throw new Error("未授予站点权限，尚未连接");
    }
    const previous = await chrome.storage.local.get(["appOrigin", "appPattern"]);
    await chrome.scripting.unregisterContentScripts({ ids: ["cineflow-login"] }).catch(() => {});
    await chrome.scripting.registerContentScripts([{
      id: "cineflow-login", matches: [pattern], js: ["bridge.js"], runAt: "document_idle",
    }]);
    await chrome.storage.local.set({ appOrigin: url.origin, appPattern: pattern });
    if (previous.appPattern && previous.appPattern !== pattern) {
      await chrome.permissions.remove({ origins: [previous.appPattern] });
    }
    for (const tab of await chrome.tabs.query({ url: pattern })) {
      if (new URL(tab.url).origin === url.origin) {
        await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["bridge.js"] });
      }
    }
    status.textContent = "已连接 " + url.origin + "，返回 CineFlow 点击浏览器登录即可。";
  } catch (error) { status.textContent = error.message || "连接失败，请重试"; }
});
