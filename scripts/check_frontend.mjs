// 无构建链：检查原生模块语法和相对导入是否能在静态目录解析。
import { readdirSync, readFileSync, existsSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { spawnSync } from "node:child_process";

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
