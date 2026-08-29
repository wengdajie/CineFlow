# 11 · 飞牛 NAS（fnOS）部署指南

> 面向 **飞牛私有云 fnOS** 的 CineFlow 完整部署手册。
> 通用 Docker 说明见 [07-运维手册 §1.1](07-运维手册.md)，本文只讲**飞牛与其它 NAS 不一样的地方**。
> 加 BT 站点见 [10-站点接入指南](10-站点接入指南.md)。

---

## 0. 先读这一段（省你半小时）

fnOS 基于 **Debian**，所以它的 Docker 就是标准 Docker——网上绝大多数 `docker compose`
教程都能直接用。但有 **4 个坑**和群晖/威联通不一样，踩中会浪费很多时间：

| 坑 | 飞牛的情况 | 后果 |
|---|---|---|
| **存储路径** | 是 `/vol1/...`，**不是**群晖的 `/volume1/...` | 抄群晖教程会挂载出一个空目录，表现为"能启动但看不到文件" |
| **端口 5666** | fnOS 自己的管理界面占用 | 拿它做映射会打不开 NAS 后台 |
| **硬链接** | 必须 `downloads` 与 `library` 在**同一个存储空间**（同一个 `/volN`） | 跨空间会报 `Invalid cross-device link`，整理入库失败 |
| **文件归属** | 默认管理员通常是 `uid=1000`，但**不保证** | PUID 填错 → fnOS 文件管理器里看不到/删不掉新文件 |

**本文两条路径，选一条即可**：

- **路径 A（推荐）· SSH + docker compose** —— 最可控，出问题有日志可查，升级一条命令。
- **路径 B · fnOS 图形界面 Docker** —— 不碰命令行，但排障时信息少。

> ⚠️ fnOS 各版本的界面菜单名会变（如「应用中心」/「应用」、「终端」/「SSH」）。
> 本文凡涉及界面位置都给了**验证命令**，界面对不上时以命令结果为准。

---

## 1. 路径 A：SSH + docker compose（推荐）

### ① 开启 SSH

fnOS 桌面 → **设置** → **系统** → **终端与 SNMP**（部分版本叫「远程访问」/「终端」）
→ 勾选 **开启 SSH 服务**，端口默认 `22`。

在 Windows PowerShell 连接（把 `bigjie` 换成你的 fnOS 用户名、IP 换成内网 IP）：

```powershell
ssh bigjie@192.168.1.10
```

> **为什么建议用内网 IP 而不是 `bigjie.fnos.net`**：那个域名是飞牛的远程穿透，
> 走中转、延迟高，`docker build` 拉依赖容易超时。部署用内网，装完再用域名访问。

### ② 确认 Docker 已装好

fnOS 需要先在 **应用中心** 里安装 **Docker**（有的版本叫「Docker 管理器」）。
装完回到 SSH 验证：

```bash
docker version
docker compose version
```

两条都出版本号才算成功。若 `docker compose` 报 `not found` 但 `docker-compose`（带横线）可用，
后文所有 `docker compose` 都换成 `docker-compose`。

若提示 `permission denied ... /var/run/docker.sock`，把自己加进 docker 组后**重新登录 SSH**：

```bash
sudo usermod -aG docker $USER
```

### ③ 查出 PUID / PGID（**别猜**）

```bash
id -u    # → PUID
id -g    # → PGID
```

记下这两个数字。多数 fnOS 首个管理员是 `1000/1000`，但**装过其它套件或建过用户就可能不是**，
所以一定实际跑一遍。填错的典型症状：CineFlow 下载的文件在 fnOS 文件管理器里显示灰色、无法删除。

### ④ 找到你的存储路径

```bash
ls /vol1        # 看看有哪些共享文件夹
df -h | grep vol   # 确认有几个存储空间、各自剩余容量
```

典型输出里会有你在 fnOS 里建的共享文件夹名，例如 `/vol1/media`、`/vol1/downloads`。

**关键判断 —— 硬链接能不能用**：`downloads` 和 `library` 必须在**同一个 `/volN`** 下。

```bash
# 两条命令输出的第一列（设备号）必须一致，一致才能用硬链接
stat -c %d /vol1/downloads
stat -c %d /vol1/media
```

- **一致** → `CF_TRANSFER_MODE: link`（推荐：秒完成、不占额外空间、原文件可继续做种）
- **不一致** → 改成 `copy`，否则整理入库会报 `Invalid cross-device link`

没有这两个目录就先建：

```bash
sudo mkdir -p /vol1/downloads /vol1/media
sudo chown -R $(id -u):$(id -g) /vol1/downloads /vol1/media
```

### ⑤ 拉取镜像（推荐）或上传源码

> ✅ **推荐：直接用预构建镜像，跳过本步的源码上传**
>
> 项目已通过 GitHub Actions 自动构建多架构镜像（amd64/arm64）并推送到 GHCR，
> 飞牛无需编译（低功耗机型本地 build 要 5~10 分钟，还常因网络失败）：
>
> ```bash
> sudo mkdir -p /vol1/docker/cineflow && sudo chown $(id -u):$(id -g) /vol1/docker/cineflow
> cd /vol1/docker/cineflow
> curl -O https://raw.githubusercontent.com/wengdajie/CineFlow/main/docker-compose.fnos.yml
> ```
>
> 拿到 compose 文件后**直接跳到步骤 ⑦**（该文件已是飞牛专用配置，只需改标注 ← 的值）。
> 下面的源码方式只在你想改代码或自行编译时才需要。

#### 源码方式（想自己改代码时才用）

CineFlow 目前是**源码构建**（compose 里 `build: .`），所以 NAS 上需要有代码。

**方式 1 · git clone（NAS 能联外网时最省事）**

```bash
sudo mkdir -p /vol1/docker && sudo chown $(id -u):$(id -g) /vol1/docker
cd /vol1/docker
git clone https://github.com/wengdajie/CineFlow.git cineflow
cd cineflow
```

`git` 不存在就 `sudo apt update && sudo apt install -y git`。

**方式 2 · 从 Windows 直接传（无外网/私有仓库）**

在 **Windows PowerShell** 里执行（注意源路径是本机的项目目录）：

```powershell
# 打包时排除本地虚拟环境、数据库、缓存，避免把几百 MB 垃圾传上去
$exclude = @('.venv','data','__pycache__','.git','.pytest_cache')
$tmp = "$env:TEMP\cineflow_ship"
Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $tmp | Out-Null
Get-ChildItem -LiteralPath "D:\NAS媒体库自动化" -Force |
    Where-Object { $exclude -notcontains $_.Name } |
    ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $tmp -Recurse -Force }
tar -czf "$env:TEMP\cineflow.tar.gz" -C $tmp .
scp "$env:TEMP\cineflow.tar.gz" bigjie@192.168.1.10:/vol1/docker/
```

再回到 **SSH**：

```bash
mkdir -p /vol1/docker/cineflow && cd /vol1/docker/cineflow
tar -xzf ../cineflow.tar.gz && rm ../cineflow.tar.gz
```

### ⑥ 写 fnOS 专用的 compose 配置

若你走的是步骤 ⑤ 的推荐路径，`docker-compose.fnos.yml` 已经下载好了，
直接 `nano docker-compose.fnos.yml` 改掉标注 ← 的值即可，**本步的 YAML 不用手抄**。

若你走源码路径：仓库自带的 `docker-compose.yml` 是**通用/群晖示例**，飞牛请用
`docker-compose.fnos.yml`（仓库已内置）。想自己写一份也可以照抄下面这段：

```bash
cd /vol1/docker/cineflow
cat > docker-compose.fnos.yml <<'YAML'
services:
  cineflow:
    image: cineflow:latest
    build: .
    container_name: cineflow
    restart: unless-stopped
    ports:
      - "6060:6060"          # 左边是 NAS 端口，被占用只改左边
    environment:
      PUID: 1000             # ← 换成 id -u 的结果
      PGID: 1000             # ← 换成 id -g 的结果
      TZ: Asia/Shanghai
      CF_SUPERUSER: admin
      CF_SUPERUSER_PASSWORD: cineflow          # ← 登录后立刻改
      CF_SECRET_KEY: CHANGE_ME_RANDOM_STRING   # ← 必改，见下方生成命令
      CF_TRANSFER_MODE: link                   # 跨存储空间就改 copy
      CF_SUBSCRIBE_INTERVAL_MINUTES: 30
    volumes:
      - ./data:/app/data           # 数据库+日志，务必持久化
      - ./config:/app/config
      - ./plugins:/app/plugins
      - ./strm:/strm
      - /vol1/downloads:/downloads # ← 必须与下载器容器内路径完全一致
      - /vol1/media:/library       # ← fnOS「影视」App 扫描的目录
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://127.0.0.1:6060/api/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 20s
YAML
```

生成随机密钥并填进去（**别用默认值**，它决定登录 token 的签名）：

```bash
openssl rand -hex 32
```

用 `nano docker-compose.fnos.yml` 修改上面 4 处标注 ← 的值（`Ctrl+O` 保存、`Ctrl+X` 退出）。

> **两条铁律**（踩了必翻车）
> 1. `/downloads` 在 CineFlow 和下载器（qBittorrent 等）容器里**必须是同一个路径字符串**。
>    否则 CineFlow 拿到下载器报告的路径后，在自己的文件系统里找不到文件。
> 2. `library` 与 `downloads` 同一个 `/volN`，硬链接才成立（见步骤 ④）。

### ⑦ 启动

```bash
cd /vol1/docker/cineflow
docker compose -f docker-compose.fnos.yml up -d
```

用预构建镜像时首次启动约 **1~3 分钟**（取决于下载速度），无需编译。
若你改了源码想自行编译，加 `--build`（约 3~8 分钟）。
若卡在 `pip install` 不动，是网络问题，见 [§4.1](#41-构建阶段卡住或拉不到依赖)。

### ⑧ 验证

```bash
docker compose -f docker-compose.fnos.yml ps      # STATUS 应为 Up (healthy)
curl http://127.0.0.1:6060/api/health
```

期望输出：

```json
{"status":"ok","version":"1.8.0","scheduler":true}
```

三个字段的含义：`status=ok` 服务活着；`version` 与你部署的代码一致；
**`scheduler=true` 定时任务已起**——追新/订阅全靠它，false 说明调度没启动。

浏览器访问 `http://<NAS内网IP>:6060`，用 `admin / cineflow` 登录后**立即改密**。

---

## 2. 路径 B：fnOS 图形界面部署

不想用命令行时走这条。**但步骤 ③④ 的 `id -u` 和存储路径你仍然需要知道**——
可以在 fnOS 的「文件管理器」里右键共享文件夹看属性确认路径。

1. fnOS 桌面 → **应用中心** → 安装 **Docker**
2. 打开 Docker → **项目**（Project / Compose）→ **新建**
3. 项目名填 `cineflow`，路径选 `/vol1/docker/cineflow`
4. 把 [§1⑥](#-写-fnos-专用的-compose-配置) 那段 YAML 粘进编辑框，改掉 4 处标注 ← 的值
5. 点 **构建并启动**，等状态变成 **运行中 / healthy**
6. 浏览器开 `http://<NAS内网IP>:6060`

> 图形界面**仍需要代码在 `/vol1/docker/cineflow`**（因为 `build: .`）。
> 先用 fnOS「文件管理器」把项目文件夹上传上去，再做上面的步骤。

---

## 3. 与 fnOS 生态联动

### 3.1 让 fnOS「影视」App 刮到 CineFlow 整理好的片子

CineFlow 负责**搜索→下载→重命名归档**，fnOS 自带的「影视」负责**播放**，两者通过目录对接：

1. CineFlow 的 `library` 挂载到 `/vol1/media`
2. fnOS「影视」App → 添加媒体库 → 选 `/vol1/media`
3. CineFlow 整理出的目录结构本身就符合 Emby/Jellyfin/影视 App 的命名规范：

```
/vol1/media/电影/沙丘 (2021)/沙丘 (2021) - 2160p.mkv
/vol1/media/电视剧/繁花 (2024)/Season 01/繁花 - S01E01.mkv
```

命名模板可在 CineFlow **设置** 里改（`movie_template` / `tv_template`）。

### 3.2 装下载器（qBittorrent）

CineFlow 只做"决定下什么"，实际下载交给下载器。在同一个 compose 文件里加：

```yaml
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    container_name: qbittorrent
    restart: unless-stopped
    ports:
      - "8080:8080"
      - "6881:6881"
      - "6881:6881/udp"
    environment:
      PUID: 1000            # ← 与 cineflow 保持一致
      PGID: 1000
      TZ: Asia/Shanghai
      WEBUI_PORT: 8080
    volumes:
      - ./qbittorrent:/config
      - /vol1/downloads:/downloads   # ← 必须与 cineflow 完全一致！
```

然后在 CineFlow **设置 → 下载器** 里添加，地址填 **`http://qbittorrent:8080`**。

> 🔴 **最常见的错误**：地址填 `http://127.0.0.1:8080`。
> 在容器里 `127.0.0.1` 指**容器自己**，不是 NAS。同一 compose 网络内**必须用服务名**。
> 详见 [10 §1.2](10-站点接入指南.md)。

### 3.3 远程访问（`bigjie.fnos.net`）

fnOS 的远程穿透默认只代理它自己的后台。想用 `https://bigjie.fnos.net` 访问 CineFlow，
在 fnOS 的**反向代理 / 域名管理**里加一条规则，把某个路径或子域指向 `127.0.0.1:6060`
（**这里的 127.0.0.1 是宿主机视角，是对的**——反代进程跑在 NAS 上，不在容器里）。

> ⚠️ **安全提醒**：CineFlow 默认口令是公开的 `admin/cineflow`。
> **暴露到公网前必须先改密码**，并建议只在内网或走 VPN 使用。

---

## 4. 飞牛专属排障

### 4.1 构建阶段卡住或拉不到依赖

现象：`docker compose up --build` 长时间停在 `pip install` 或 `FROM python:3.12-slim`。

```bash
# 单独重试构建，看具体卡在哪一层
docker compose -f docker-compose.fnos.yml build --progress=plain
```

多数是国内网络访问 Docker Hub / PyPI 慢。在 fnOS Docker 设置里配置**镜像加速器**后重试。

### 4.2 容器起来了但网页打不开

```bash
docker compose -f docker-compose.fnos.yml logs --tail=80 cineflow
ss -tlnp | grep 6060        # NAS 上有没有监听
```

- 日志有 `Address already in use` → 端口被占，见 §4.3
- 日志正常但外部打不开 → fnOS 防火墙未放行 6060（设置 → 安全 → 防火墙）

### 4.3 6060 被占用

**只改左边**（宿主机侧），右边容器端口保持 6060：

```yaml
    ports:
      - "6070:6060"     # 之后用 http://<NAS_IP>:6070 访问
```

改完 `docker compose -f docker-compose.fnos.yml up -d`。
右边一起改会导致健康检查失败（它固定探 6060）。

### 4.4 fnOS 里看不到/删不掉 CineFlow 下载的文件

PUID/PGID 与共享文件夹归属不一致。重查并修正：

```bash
id -u; id -g                      # 你的真实 uid/gid
stat -c '%u %g' /vol1/downloads   # 目录当前归属
```

不一致就改 compose 里的 `PUID/PGID` 重启，并修正历史文件归属：

```bash
sudo chown -R $(id -u):$(id -g) /vol1/downloads /vol1/media
```

### 4.5 整理入库报 `Invalid cross-device link`

`downloads` 与 `library` 不在同一存储空间，硬链接不可能成立（内核限制，不是 bug）。
二选一：

- 把两个目录挪到同一个 `/volN`（推荐，省空间）
- 或改 `CF_TRANSFER_MODE: copy`（占双份空间，但一定能work）

### 4.6 榜单封面部分显示灰块

见 [07 §8.4](07-运维手册.md)：豆瓣 `img9` 镜像损坏，v1.8.0 已自动规避。
确认版本 ≥ 1.8.0 即可（`curl http://127.0.0.1:6060/api/health`）。

### 4.7 其它通用问题

发现榜空、网盘扫码扫不出、登录成功但网盘操作失败 → [07 §8](07-运维手册.md)。
站点搜不到结果 → [10 §6 验证四步法](10-站点接入指南.md)。

---

## 5. 日常运维

```bash
cd /vol1/docker/cineflow
C="docker compose -f docker-compose.fnos.yml"

$C ps                      # 状态
$C logs -f --tail=100      # 实时日志（Ctrl+C 退出）
$C restart                 # 重启
$C down                    # 停止并删容器（数据在 ./data，不会丢）
$C up -d --build            # 改完代码/配置后重建
docker stats cineflow      # 看 CPU/内存占用
```

### 5.1 升级

```bash
cd /vol1/docker/cineflow
cp -r data "data.bak.$(date +%F)"     # 先备份数据库
git pull                              # 或重新上传代码
docker compose -f docker-compose.fnos.yml up -d --build
curl http://127.0.0.1:6060/api/health # 确认 version 变了
```

### 5.2 备份（只需这两个目录）

```bash
tar -czf /vol1/backup/cineflow-$(date +%F).tar.gz \
    -C /vol1/docker/cineflow data config
```

`data/` 是 SQLite 数据库（站点、订阅、下载记录、用户），`config/` 是 `config.yaml`。
**媒体文件不用备份进这里**，它们在 `/vol1/media`。

---

## 6. 安装后自检清单

逐条打勾，全过才算装好：

- [ ] `docker compose -f docker-compose.fnos.yml ps` → `Up (healthy)`
- [ ] `curl http://127.0.0.1:6060/api/health` → `status=ok` 且 **`scheduler=true`**
- [ ] 浏览器能开 `http://<NAS内网IP>:6060`
- [ ] **已修改默认密码** `admin/cineflow`
- [ ] **`CF_SECRET_KEY` 已换成随机串**（不是 `CHANGE_ME_...`）
- [ ] `PUID/PGID` 与 `id -u`/`id -g` 一致，fnOS 文件管理器里能正常删除新文件
- [ ] `stat -c %d` 确认 downloads 与 media 同一存储空间（用 link 模式时）
- [ ] 下载器地址用的是**服务名**（`http://qbittorrent:8080`），不是 `127.0.0.1`
- [ ] 「设置 → 站点管理」里至少 1 个站点**诊断通过**（见 [10 §6](10-站点接入指南.md)）
- [ ] 「发现榜」能出封面和条目
- [ ] 重启 NAS 后容器自动起来（`restart: unless-stopped` 生效）
