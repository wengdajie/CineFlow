(() => {
  if (globalThis.__cineflowLoginBridge || window.top !== window) return;
  globalThis.__cineflowLoginBridge = true;
  const reply = (data) => window.postMessage({ ...data, source: "cineflow-cookie-helper" }, location.origin);
  window.addEventListener("message", async (event) => {
    const message = event.data;
    if (event.source !== window || event.origin !== location.origin ||
        message?.source !== "cineflow-cookie-login" ||
        !["probe", "start", "finish", "cancel", "retry"].includes(message.action)) return;
    try {
      const result = await chrome.runtime.sendMessage({
        scope: "cineflow-login-helper", action: message.action,
        requestId: message.requestId, provider: message.provider, accepted: message.accepted === true,
      });
      reply({ type: "response", action: message.action, requestId: message.requestId, ...result });
    } catch { reply({ type: "response", action: message.action, requestId: message.requestId, success: false, message: "登录助手连接已断开，请刷新页面" }); }
  });
  chrome.runtime.onMessage.addListener((message, sender) => {
    if (sender.id === chrome.runtime.id && message?.scope === "cineflow-login-helper") reply(message);
  });
})();
