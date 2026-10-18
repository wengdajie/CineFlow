import { store } from './state.js';
export const API = '/api/v1';

export function createApi(logout) {
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
return { api, apiStream };
}
