"""校验 YAML 与容器部署文件的语法及语义一致性。

为什么单独一个脚本：YAML 语法过 != 能跑。真正会咬人的是语义层——
挂载点与 Dockerfile 环境变量对不上、healthcheck 端口与 EXPOSE 不一致、
可选服务取消注释后缩进塌了、模板文件被 bind mount 遮蔽等。
这些都不会报语法错，只会在用户 NAS 上部署失败时才暴露。

用法（无需启动服务）：
    python scripts/verify_yaml.py
"""
import pathlib
import re
import shutil
import sys
import tempfile

import yaml

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")

ROOT = pathlib.Path(".")
COMPOSE_FILES = ["docker-compose.yml", "docker-compose.fnos.yml"]
YAML_FILES = [
    ".github/workflows/build-image.yml",
    "config/config.yaml.example",
    *COMPOSE_FILES,
]

checks = []


def check(name, ok, detail=""):
    checks.append((ok, name, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))


class DupKeyLoader(yaml.SafeLoader):
    """PyYAML 默认允许重复键并静默取后者 —— compose 里最阴的坑：
    两个 image 键同时存在时不报错，用户以为在用 A 其实跑的是 B。"""


def _no_dup(loader, node, deep=False):
    seen, mapping = set(), {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise yaml.YAMLError(
                f"重复键 {key!r} @ 行 {key_node.start_mark.line + 1}")
        seen.add(key)
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


DupKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_dup)


# ---- 1. 语法 / 重复键 / Tab 缩进 / 行尾 ----
for rel in YAML_FILES:
    path = ROOT / rel
    check(f"{rel} 存在", path.exists())
    if not path.exists():
        continue
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    check(f"{rel} 无 BOM", not raw.startswith(b"\xef\xbb\xbf"))
    check(f"{rel} 无孤立 CR", raw.count(b"\r") - raw.count(b"\r\n") == 0)
    # YAML 规范明确禁止 Tab 做缩进
    tabs = [i for i, line in enumerate(text.split("\n"), 1)
            if line.lstrip(" ").startswith("\t")]
    check(f"{rel} 无 Tab 缩进", not tabs, f"行 {tabs[:5]}" if tabs else "")
    try:
        list(yaml.load_all(text, Loader=DupKeyLoader))
        check(f"{rel} 语法合法且无重复键", True)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        loc = f"行 {mark.line + 1}" if mark else ""
        check(f"{rel} 语法合法且无重复键", False,
              f"{loc} {getattr(exc, 'problem', exc)}")


# ---- 2. GitHub workflow：on 必须是字符串键 ----
wf_path = ROOT / ".github/workflows/build-image.yml"
if wf_path.exists():
    wf = yaml.safe_load(wf_path.read_text(encoding="utf-8"))
    # YAML 1.1 把裸 on 当布尔，键名会变成 True，yq / actionlint 读不到
    check("workflow 的 on 是字符串键而非布尔", "on" in wf and True not in wf)
    check("workflow 有 push 与手动触发",
          bool(wf.get("on", {}).get("push")) and "workflow_dispatch" in wf.get("on", {}))
    job = (wf.get("jobs") or {}).get("build") or {}
    check("workflow 声明 packages: write 权限",
          (job.get("permissions") or {}).get("packages") == "write")


# ---- 3. compose 与 Dockerfile / entrypoint 的语义自洽 ----
dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
entrypoint = (ROOT / "docker/entrypoint.sh").read_text(encoding="utf-8")
expose = re.findall(r"EXPOSE\s+(\d+)", dockerfile)
hc_ports = re.findall(r"HEALTHCHECK.*?127\.0\.0\.1:(\d+)", dockerfile, re.S)
df_dirs = dict(re.findall(r"(CF_\w*DIR)=(\S+)", dockerfile))

from app.core.config import Settings  # noqa: E402

FIELDS = {k.upper() for k in Settings.model_fields}

SVC_RE = re.compile(r"^  # ([a-z][\w-]*):\s*$")
SUB_RE = re.compile(r"^  #(\s{3,})(\S.*)$")


def enable_optional(lines):
    """精确模拟用户「取消注释启用可选服务」的操作。
    服务名行是 '  # name:'（# 后恰好 1 空格），子键行是 '  #   key:'。"""
    start = next(i for i, ln in enumerate(lines) if ln.startswith("services:"))
    out, i = list(lines), start + 1
    while i < len(lines):
        m = SVC_RE.match(lines[i])
        if not m:
            i += 1
            continue
        out[i] = f"  {m.group(1)}:"
        j = i + 1
        while j < len(lines):
            m2 = SUB_RE.match(lines[j])
            if not m2:
                break
            out[j] = "  " + " " * (len(m2.group(1)) - 1) + m2.group(2)
            j += 1
        i = j
    return "\n".join(out)


for rel in COMPOSE_FILES:
    path = ROOT / rel
    if not path.exists():
        continue
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    # 现代 compose 规范已废弃 version 键
    check(f"{rel} 不含废弃的 version 键", "version" not in data)
    cf = (data.get("services") or {}).get("cineflow")
    check(f"{rel} 有 cineflow 服务", isinstance(cf, dict))
    if not isinstance(cf, dict):
        continue
    check(f"{rel} cineflow 有 image 或 build",
          bool(cf.get("image") or cf.get("build")))

    # 容器端口必须与 EXPOSE、容器内 HEALTHCHECK 三方一致，
    # 否则健康检查永远失败 → 容器反复重启
    for p in cf.get("ports") or []:
        cont = str(p).partition(":")[2].split("/")[0]
        check(f"{rel} 容器端口 {cont} 与 EXPOSE 一致", cont in expose)
        check(f"{rel} 容器端口 {cont} 与 HEALTHCHECK 一致",
              not hc_ports or cont == hc_ports[0])

    # healthcheck 依赖的命令必须真的装在镜像里
    test = (cf.get("healthcheck") or {}).get("test")
    if isinstance(test, list) and len(test) > 1:
        install = re.search(r"apt-get install(?:[^\n]|\\\n)*", dockerfile)
        check(f"{rel} healthcheck 的 {test[1]} 已装进镜像",
              bool(install) and test[1] in install.group(0))

    targets = [str(v).split(":")[1] for v in cf.get("volumes") or []
               if str(v).count(":") >= 1]
    # Dockerfile 声明的数据目录都必须被挂载，否则容器重建即丢数据
    for key, dirpath in df_dirs.items():
        check(f"{rel} 已挂载 {key}={dirpath}", dirpath in targets)
    check(f"{rel} 已挂载 /app/config", "/app/config" in targets)

    # CF_ 环境变量必须是 Settings 真认的字段（extra='ignore' 会静默丢弃拼错的）
    env = cf.get("environment") or {}
    bad = [k for k in env if str(k).startswith("CF_")
           and str(k)[3:].upper() not in FIELDS]
    check(f"{rel} 所有 CF_ 环境变量都有效", not bad, str(bad))
    # 非 CF_ 的变量由 entrypoint 消费，必须真被用到
    for k in env:
        if not str(k).startswith("CF_"):
            check(f"{rel} {k} 被 entrypoint 使用", f"${{{k}" in entrypoint)

    # 取消注释启用可选服务后，仍须是合法 YAML 且结构完整
    try:
        enabled = yaml.safe_load(enable_optional(text.split("\n")))
        svcs = enabled.get("services") or {}
        check(f"{rel} 启用可选服务后仍可解析", True, f"{len(svcs)} 个服务")
        for name, cfg in svcs.items():
            check(f"{rel} 启用后 {name} 是完整映射", isinstance(cfg, dict))
            if isinstance(cfg, dict):
                check(f"{rel} 启用后 {name} 有镜像来源",
                      bool(cfg.get("image") or cfg.get("build")))
        # 宿主端口不能撞（撞了 compose up 直接失败）
        seen = {}
        for name, cfg in svcs.items():
            for p in (cfg or {}).get("ports") or []:
                key = str(p).split(":")[0] + ("/udp" if "/udp" in str(p) else "/tcp")
                check(f"{rel} 宿主端口 {key} 不冲突", key not in seen,
                      f"{name} vs {seen.get(key)}")
                seen[key] = name
        # 硬链接铁律：所有服务的 /downloads 必须指向同一宿主路径
        hosts = {str(v).split(":")[0] for cfg in svcs.values()
                 for v in (cfg or {}).get("volumes") or []
                 if ":/downloads" in str(v)}
        check(f"{rel} 各服务 /downloads 宿主路径一致", len(hosts) <= 1, str(hosts))
    except yaml.YAMLError as exc:
        check(f"{rel} 启用可选服务后仍可解析", False, str(exc)[:160])


# ---- 4. 配置模板不能被 bind mount 遮蔽 ----
m = re.search(r"COPY config/config\.yaml\.example (\S+)", dockerfile)
check("Dockerfile 复制了配置模板", bool(m))
if m:
    img_tpl = m.group(1)
    m2 = re.search(r"CF_TEMPLATE=(\S+)", entrypoint)
    check("entrypoint 定义了 CF_TEMPLATE", bool(m2))
    if m2:
        check("模板路径与 entrypoint 一致", img_tpl == m2.group(1),
              f"{img_tpl} vs {m2.group(1)}")
    # 关键：放在 /app/config/ 下会被 compose 的 bind mount 整体遮蔽，
    # entrypoint 的 [ -f ... ] 恒为假，首次生成配置的逻辑永远不触发
    check("模板不在会被挂载遮蔽的 /app/config/ 内",
          not img_tpl.startswith("/app/config/"), img_tpl)
# set -e 下若目录不存在，cp 会直接终止容器 → 必须先 mkdir 再 cp
check("entrypoint 先建目录再复制配置",
      entrypoint.index("mkdir -p") < entrypoint.index("CF_TEMPLATE="))
check("entrypoint 不覆盖已有 config.yaml",
      "[ ! -f /app/config/config.yaml ]" in entrypoint)


# ---- 5. 模板生成后必须真能被读取（端到端） ----
tmp = pathlib.Path(tempfile.mkdtemp())
try:
    target = tmp / "config.yaml"
    shutil.copy(ROOT / "config/config.yaml.example", target)
    body = target.read_text(encoding="utf-8").replace(
        "subscribe_interval_minutes: 30", "subscribe_interval_minutes: 77")
    target.write_text(body, encoding="utf-8")
    import os

    saved = os.environ.get("CF_CONFIG_FILE")
    dropped = {k: os.environ.pop(k) for k in list(os.environ)
               if k.startswith("CF_") and k != "CF_CONFIG_FILE"}
    os.environ["CF_CONFIG_FILE"] = str(target)
    from app.core.config import _yaml_source

    flat = _yaml_source()
    check("生成的 config.yaml 能被解析", len(flat) > 50, f"{len(flat)} 键")
    check("config.yaml 的值真正生效",
          Settings(**flat).SUBSCRIBE_INTERVAL_MINUTES == 77)
    os.environ["CF_SUBSCRIBE_INTERVAL_MINUTES"] = "99"
    check("环境变量优先级高于 config.yaml",
          Settings(**_yaml_source()).SUBSCRIBE_INTERVAL_MINUTES == 99)
    os.environ.pop("CF_SUBSCRIBE_INTERVAL_MINUTES", None)
    os.environ.update(dropped)
    if saved is None:
        os.environ.pop("CF_CONFIG_FILE", None)
    else:
        os.environ["CF_CONFIG_FILE"] = saved
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# example 的键必须都被 Settings 认识，否则用户改了没效果
raw_cfg = yaml.safe_load((ROOT / "config/config.yaml.example").read_text(encoding="utf-8"))


def flatten(d, prefix=""):
    out = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        out.update(flatten(v, f"{key}_") if isinstance(v, dict) else {key: v})
    return out


flat_cfg = flatten(raw_cfg)
unknown = sorted(k for k in flat_cfg if k.upper() not in FIELDS)
check("config.yaml.example 所有键都被 Settings 接收", not unknown, str(unknown[:6]))
try:
    Settings(**flat_cfg)
    check("config.yaml.example 所有值类型合法", True)
except Exception as exc:
    check("config.yaml.example 所有值类型合法", False, str(exc)[:160])


print()
print("=" * 60)
failed = [c for c in checks if not c[0]]
print(f"YAML 与部署文件校验：{len(checks) - len(failed)}/{len(checks)} 通过")
for _, name, detail in failed:
    print(f"  FAIL {name} {detail}")
sys.exit(1 if failed else 0)
