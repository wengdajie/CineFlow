<div align="center">

# ⚠️ 请勿在任何国内平台宣传该项目！ ⚠️

</div>

> ### 🚨 重要声明
>
> # **本项目仅用于技术学习与交流，严禁用于任何商业用途。**
> # **请勿在任何国内平台（微信/微博/知乎/B站/贴吧/小红书/QQ群等）宣传、转载或推广本项目！**
>
> - 本项目**不提供、不存储、不分发**任何影视资源，所有内容均来自用户自行配置的第三方站点。
> - 使用者需自行确保其行为符合所在地法律法规，**因使用本项目产生的一切后果由使用者自行承担**。
> - 请在下载后 24 小时内删除，支持正版，为喜欢的作品付费。
> - 本项目与任何资源站点、网盘服务商无隶属关系。

---

<div align="center">

# 🎬 CineFlow

**面向 NAS 的自动化观影追剧平台**

聚合 BT 站点与网盘搜索 → 自动追新 → 择优下载 → 规范命名入库 → 通知媒体服务器

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)](#-docker-部署推荐)
[![Tests](https://img.shields.io/badge/tests-1294%20passed-brightgreen)](docs/14-开发指南.md)
[![Version](https://img.shields.io/badge/version-1.16.0-blue)](docs/08-变更日志.md)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

</div>

---

## 📖 这是什么

CineFlow 是一个**从零实现**的 NAS 影视自动化项目，设计上参考了
[MoviePilot](https://github.com/jxxghp/MoviePilot) 的订阅/插件体系与
[T3FAP](https://github.com/qq85423296/T3FAP) 的网盘资源思路，但代码、架构与数据模型完全独立编写。

你只需要说出**想追哪部剧**，剩下的事情它全包：

```
    你添加订阅「凡人修仙传 第二季」
              │
              ▼
    ┌─────────────────────┐
    │  ⏱ 每 30 分钟自动巡检  │
    └──────────┬──────────┘
               ▼
   ┌───────────────────────────────┐
   │ 🔍 多级关键词并发搜索所有站点     │
   │   片名 S02E05 → 片名 S02 → 片名 │
   │   BT 索引器 + 网盘盘搜 同时查     │
   └──────────┬────────────────────┘
              ▼
   ┌───────────────────────────────┐
   │ 🎯 硬过滤 + 加权打分择优         │
   │   丢弃枪版/无关剧/做种不足        │
   │   4K > 1080p，中字 +30，季包 +25 │
   └──────────┬────────────────────┘
              ▼
   ┌───────────────────────────────┐
   │ ⬇ 投递 qBittorrent / TR / aria2 │
   │   网盘资源 → 自动转存进自己的网盘  │
   │   （AList / 夸克，带提取码）      │
   └──────────┬────────────────────┘
              ▼
   ┌───────────────────────────────┐
   │ 📁 硬链接入库 + 规范命名          │
   │  凡人修仙传/Season 02/… S02E05  │
   └──────────┬────────────────────┘
              ▼
   ┌───────────────────────────────┐
   │ 🔄 通知 Emby/Jellyfin/Plex 刷新  │
   │ 📢 Telegram / 企微 / Bark 推送   │
   │ ✅ 回写进度，缺集收敛，永不重复下载 │
   └───────────────────────────────┘
```

也可以**不开界面**，直接在飞书 / 钉钉 / Telegram 里发一句话：

```
    你：搜索 沙丘 第二季
   Bot：🔍 找到 23 条，前 5 条：
        1. Dune.Part.Two.2024.2160p… 24.6 GB · 2160p · 128↑ · 站A
        2. 沙丘2.2024.1080p.中字…     8.2 GB · 1080p · 网盘 · 站B
        …
    你：下载 2
   Bot：✅ 已提交下载：沙丘2.2024.1080p.中字
        任务 #37 · 状态 transferred
        ☁️ 网盘资源已自动转存
```

---
---

## ✨ 能做什么（一句话版）

| 能力 | 一句话 |
|---|---|
| 🔍 **聚合搜索** | BT 索引器（Jackett/Prowlarr/torznab）+ 网盘盘搜 + 自定义站点 + B站/YouTube + 在线影视站（MacCMS），一次并发查全部；**每个站点都有保底名额**，不会被高分站挤掉 |
| 🔥 **当前最热** | 豆瓣电影/电视剧/动漫/综艺 + **Bilibili 分区** + **YouTube 地区** + **新番放送日历**共七个榜，画板式封面墙，首屏 30 条下拉加载；B 站/YouTube 条目**直接选画质下载**，影视条目跳搜资源并**记住回来的位置** |
| 🎯 **智能择优** | 硬过滤（枪版/做种不足/错剧）+ 加权打分（分辨率/字幕/季包/站点权重）+ 分层规则组 |
| 🔁 **自动追新** | 定时巡检、多级关键词降级、缺集收敛、洗版替换、榜单自动订阅 |
| 📺 **追番日历** | Bangumi 放送表：这周**哪天更新第几话**，今天那一列高亮（热度榜答不了这个问题） |
| 🎥 **视频追更** | 盯住 B 站 UP 主 / YouTube 频道 / 播放列表，有新投稿就自动下载（可限画质、限每轮条数） |
| ☁️ **网盘管理** | 115/百度/夸克/AList/WebDAV，**扫码登录 + Cookie 保活**、转存、在线浏览、分享追更 |
| 📁 **整理入库** | 资源名解析 → 硬链接 → 规范命名 → NFO 刮削 → 通知 Emby/Jellyfin/Plex 刷新 |
| 🎬 **免下载播放** | STRM 同步 + 302 直链，网盘里的片子不落地也能播 |
| 🤖 **ChatOps** | 飞书 / 钉钉 / Telegram 发一句话完成搜索与下载 |
| 🧩 **可扩展** | 插件系统 + 事件订阅 + 不写代码接入任意站点（`api_generic` / `html_generic`） |
| ⬇️ **按资源选下载器** | 磁力/种子 → qB/TR/迅雷，网页视频 → yt-dlp，网盘直链 → aria2；**缺对应下载器时直接说明去哪儿加什么**，而不是投给一个必然失败的下载器 |
| 🤖 **AI 分析站点** | 填个网址，AI 判断该用哪种适配器并填好字段；**只出建议，本地试搜通过后你确认才落库**（默认关闭，开启才会外发页面正文） |
| 🎨 **界面** | 暗色 / 浅色一键切换，设置页**多列布局**（59 项在线可改、改完即生效；14 项需重启的收进折叠卡片），**下载器在设置页按真实表单配置**（含**限速时段**：白天限速、夜里跑满），多用户三档权限 |

> 📖 **每一项的完整说明、字段解释与实现细节** → **[docs/12-功能特性详解.md](docs/12-功能特性详解.md)**

---

## 📚 文档

README 只保留**声明 / 介绍 / 安装**三件事，其余全部在 [`docs/`](docs/README.md)：

| 文档 | 内容 |
|---|---|
| [`docs/01-项目现状.md`](docs/01-项目现状.md) | 代码规模、模块清单、能力矩阵、**当前缺口** |
| [`docs/02-架构设计.md`](docs/02-架构设计.md) | 分层铁律、主链路时序、扩展点、设计取舍 |
| [`docs/03-升级路线图.md`](docs/03-升级路线图.md) | 里程碑任务表 + **逐条验收证据** |
| [`docs/04-决策记录.md`](docs/04-决策记录.md) | ADR：为什么这么选，以及被否掉的方案 |
| [`docs/05-ChatOps-机器人.md`](docs/05-ChatOps-机器人.md) | 三平台配置步骤、验签算法、指令表 |
| [`docs/06-网盘管理.md`](docs/06-网盘管理.md) | 盘搜 vs 网盘、五种存储配置、**扫码登录与保活**、转存流程 |
| [`docs/07-运维手册.md`](docs/07-运维手册.md) | **完整 Docker 安装（11 步 · 四平台）**、备份、排障、验证脚本 |
| [`docs/08-变更日志.md`](docs/08-变更日志.md) | v1.0.0 → v1.10.0 逐版本记录 |
| [`docs/09-竞品对标与差距分析.md`](docs/09-竞品对标与差距分析.md) | 对标 MoviePilot / T3FAP / quark-auto-save 等，**差距与不做的事** |
| [`docs/10-站点接入指南.md`](docs/10-站点接入指南.md) | **加站点完整操作指南**：torznab 地址怎么拼、逐字段说明、Cookie 从哪抠、403 怎么办 |
| [`docs/11-飞牛NAS部署指南.md`](docs/11-飞牛NAS部署指南.md) | 飞牛 fnOS 专用部署（`/vol1` 路径差异、图文步骤） |
| [`docs/12-功能特性详解.md`](docs/12-功能特性详解.md) | **全部功能的完整说明**（原 README 核心特性章节） |
| [`docs/13-配置与API参考.md`](docs/13-配置与API参考.md) | 环境变量、配置优先级、REST API 一览、项目结构 |
| [`docs/14-开发指南.md`](docs/14-开发指南.md) | 插件开发、事件订阅、测试与质量门禁 |
| [`docs/15-常见问题.md`](docs/15-常见问题.md) | FAQ + 架构速览 |

## 🚀 快速开始

### 🐳 Docker 部署（推荐）

**无需下载源码**，直接拉取 GitHub Actions 自动构建的多架构镜像（amd64 / arm64）：

```bash
# 通用版（群晖 / 威联通 / Linux / Windows）
curl -O https://raw.githubusercontent.com/wengdajie/CineFlow/main/docker-compose.yml

# 改掉挂载路径与 PUID/PGID（PUID 用 id -u 查，别猜）
docker compose up -d
docker compose logs -f cineflow
```

**飞牛 fnOS 用户用专用版**（路径是 `/vol1` 而非群晖的 `/volume1`）：

```bash
sudo mkdir -p /vol1/docker/cineflow && cd /vol1/docker/cineflow
curl -O https://raw.githubusercontent.com/wengdajie/CineFlow/main/docker-compose.fnos.yml
docker compose -f docker-compose.fnos.yml up -d
```

> 📖 飞牛完整图文教程见 **[docs/11-飞牛NAS部署指南.md](docs/11-飞牛NAS部署指南.md)**

<details>
<summary>想从源码自己编译镜像？</summary>

```bash
git clone https://github.com/wengdajie/CineFlow.git cineflow && cd cineflow
# 叠加 build override（不要手动往 docker-compose.yml 里加 build:）
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

> ⚠️ 别把 `build: .` 直接写进 `docker-compose.yml`。那样会与已有的 `image`
> 冲突：compose 静默取后一个 image（裸名 → 去 Docker Hub 拉，超时），
> 同时 `build` 又在没有源码的目录里找不到 Dockerfile。用 override 文件就不会踩这个坑。

</details>

打开 `http://<NAS_IP>:6060`，使用 **`admin` / `cineflow`** 登录（**请立即改密**）。

> ⚠️ **两个关键前提，务必满足**
> 1. **下载目录路径必须一致**：CineFlow 容器里的 `/downloads` 与 qBittorrent 容器里的 `/downloads`
>    必须映射到宿主机**同一个目录**，否则 CineFlow 找不到下载完成的文件。
> 2. **downloads 与 library 同一文件系统**：这样才能使用硬链接（秒完成、不占额外空间、保种）。
>    跨盘时 CineFlow 会自动降级为复制。

### 🐍 源码运行

```bash
git clone https://github.com/wengdajie/CineFlow.git cineflow && cd cineflow

python -m venv .venv
# Linux / macOS
source .venv/bin/activate
# Windows PowerShell
.\.venv\Scripts\Activate.ps1

pip install -r requirements.txt

# 可选：准备配置
cp config/config.yaml.example config/config.yaml
cp .env.example .env

python -m app.main
```

访问 `http://127.0.0.1:6060`（默认端口 `6060`，可用 `CF_PORT` 覆盖）。

---
## ⚖️ 免责声明

本项目仅提供**自动化调度与文件管理**能力，**不内置、不提供、不分发**任何影视资源、
站点地址或破解内容。所有资源均来自用户自行配置的第三方服务。
请遵守所在地法律法规，仅用于备份和管理你已合法拥有的媒体内容。

## 📄 License

[MIT](LICENSE)

---

## 🙏 致谢

- [MoviePilot](https://github.com/jxxghp/MoviePilot) · [T3FAP](https://github.com/qq85423296/T3FAP)
  —— 本项目的设计思路参考自这两个项目。
- [awesome-zhuiju-free](https://github.com/laoma2053/awesome-zhuiju-free)
  —— **社区站点清单**数据源（许可证 **CC-BY-4.0**）。
  「站点管理 → 社区清单」的候选站目录来自该项目。
  本项目**只使用其站点目录数据**，不分发任何影视资源；
  且不直接采用其可用性结论 —— 每个站是否可用，由 CineFlow
  用真实关键词**自己搜一次**判定（详见 [ADR-70](docs/04-决策记录.md)）。

---

<div align="center">
致敬 <a href="https://github.com/jxxghp/MoviePilot">MoviePilot</a> 与
<a href="https://github.com/qq85423296/T3FAP">T3FAP</a> 的开源思路 ✨
</div>
