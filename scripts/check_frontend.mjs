// 无构建链：检查原生模块语法和相对导入是否能在静态目录解析。
import { readdirSync, readFileSync, existsSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { spawnSync } from "node:child_process";
import assert from "node:assert/strict";
import { el } from "../web/assets/ui.js";

function check(directory) {
  let count = 0;
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const file = resolve(directory, entry.name);
    if (entry.isDirectory()) {
      count += check(file);
    } else if (entry.name.endsWith(".js")) {
      const result = spawnSync(process.execPath, ["--check", file], { stdio: "inherit" });
      if (result.status !== 0) throw new Error(`模块语法检查失败：${file}`);
      for (const match of readFileSync(file, "utf8").matchAll(/from\s+["'](\.[^"']+)["']/g)) {
        if (!existsSync(resolve(dirname(file), match[1]))) throw new Error(`缺少模块：${file} → ${match[1]}`);
      }
      count += 1;
    }
  }
  return count;
}

console.log(`Frontend modules checked: ${check(resolve("web/assets"))}`);

// 检查实际 DOM 构造器，外部资源 URL 不能成为可执行链接。
globalThis.document = { createElement() {
  const attrs = new Map();
  return { setAttribute: (key, value) => attrs.set(key, String(value)),
    getAttribute: (key) => attrs.get(key) ?? null };
} };
for (const href of ["javascript:alert(1)", " \tJaVaScript:\nalert(1)", "data:text/html,<script>alert(1)</script>", "https://[invalid"]) {
  assert.equal(el("a", { href }).getAttribute("href"), null, "不安全链接应不可导航");
}
for (const href of ["https://example.test/detail", "http://127.0.0.1:6060/", "#search", "/api/v1/docs"]) {
  assert.equal(el("a", { href }).getAttribute("href"), href);
}
delete globalThis.document;
console.log("Frontend link safety checked: 8 cases");
