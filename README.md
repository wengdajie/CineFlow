<div align="center">

# 🎬 CineFlow

**面向 NAS 的自动化观影追剧平台**

聚合 BT 站点与网盘搜索 → 自动追新 → 择优下载 → 规范命名入库 → 通知媒体服务器

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)](#-docker-部署推荐)
[![Tests](https://img.shields.io/badge/tests-136%20passed-brightgreen)](#-测试)
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
   │   网盘资源 → 登记待转存（带提取码）│
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

---

## ✨ 核心特性

### 🔍 搜索：BT + 网盘双通道

| 类型 | 内置 Provider | 说明 |
|---|---|---|
| **BT 索引器** | `torznab` | 对接 Jackett / Prowlarr，一个地址接入上百个 PT 与公开站点 |
| | `rss` | 通用 RSS 订阅，URL 支持 `{keyword}` 占位符实现搜索 |
| | `nyaa` | Nyaa 动漫站（继承 RSS，预置分类过滤） |
| **自定义站点** | `api_generic` | **字段映射式，不写代码接入任意 JSON 资源站**；支持「列表直出链接」与「列表+详情两阶段」 |
| | `html_generic` | **正则映射式，接入没有 API 的网页站**；也可一键抓取页面内全部磁力 |
| | `mukaku` | 内置预设站点，一次请求同时拿到全部磁力 + 网盘分享，支持最新流追新 |
| **网盘盘搜** | `pansou` | 对接 PanSou 系 API，聚合夸克/阿里/百度/迅雷/天翼/115/UC/PikPak |
| | `pan_generic` | **字段映射式，不写代码就能接入任意第三方盘搜 JSON API** |

- **多级关键词降级**：`片名 S02E05` 命中不到就退到 `片名 S02`，再退到 `片名`
- **并发搜索**：Semaphore 控制并发度，跨站结果按 magnet infohash 去重
- **相关性校验**：自动剔除标题不匹配的"搭车"资源

### 🎯 择优：打分而非死规则

不是简单的"只要 1080p"，而是加权评分后排序：

```
分辨率（按你的偏好顺序）  +  质量（REMUX/BluRay/WEB-DL…）
+ 特效（DV/HDR10+/HDR）   +  编码（H265/AV1）
+ 音轨（Atmos/TrueHD/DTS-HD）
+ 中文字幕 +30   网盘资源 +40   季度合集 +25
+ 做种数、体积合理性、站点优先级
- 硬过滤：枪版/预告/Sample/做种不足/关键词黑名单
```

### 🧠 资源名解析引擎

纯本地正则 + 词典，**不依赖 TMDB 也能正常工作**。已验证样本：

| 输入 | 解析结果 |
|---|---|
| `工作细胞.S01E05.2160p.WEB-DL.H265.DDP-OurTV.mkv` | 工作细胞 · S01E05 · 2160p · WEB-DL · H265 |
| `[喵萌奶茶屋] 葬送的芙莉莲 / Sousou no Frieren [12][1080p][简繁日内封字幕]` | 葬送的芙莉莲 · E12 · 1080p · 中字 |
| `凡人修仙传 第二季 第105集 4K HDR 国语中字` | 凡人修仙传 · S02E105 · 2160p · HDR |
| `庆余年第二季全36集 1080p WEB-DL H264 国语中字` | 庆余年 · S02 · 全 36 集（季包） |
| `The.Last.of.Us.S02E01-E03.1080p.WEB-DL.DDP5.1.H.264-NTb` | The Last of Us · S02E01–E03 |
| `Oppenheimer.2023.2160p.UHD.BluRay.REMUX.DV.HDR.TrueHD.Atmos-FraMeSToR` | Oppenheimer · 2023 · REMUX · DV/HDR · Atmos |

支持：`SxxExx`、中文季集、`[12]` 括号集号、`E01-E03` 范围集、`全N集`、中文数字转阿拉伯、中英标题分离。

### 📁 整理入库

- **5 种转移模式**：硬链接（默认，秒完成不占空间）/ 复制 / 移动 / 软链接 / **STRM**
- **跨盘自动降级**：硬链失败时自动退化为复制，不中断流程
- **字幕跟随**：`.srt/.ass/.ssa/.sub/.sup` 自动同名迁移
- **模板化命名**，可用占位符：

  ```
  {title} {en_title} {cn_title} {year} {season} {episode}
  {resolution} {quality} {video_codec} {audio_codec} {effect} {group} {ext}
  ```

  默认：
  - 电影 `{title} ({year})/{title} ({year}) - {quality}{ext}`
  - 剧集 `{title} ({year})/Season {season:02d}/{title} - S{season:02d}E{episode:02d}{ext}`

### 🔁 追新与去重

**两条互补的自动追新链路：**

| | 订阅巡检 | 追新雷达 ⭐ |
|---|---|---|
| 驱动方式 | 以订阅为主，逐个订阅去各站**搜索**关键词 | 以站点为主，拉一次各站**最新流**再匹配订阅 |
| 请求量 | 订阅数 × 站点数 | 站点数（与订阅数无关） |
| 发现延迟 | 取决于巡检间隔 | 更低，适合日更剧/新番 |
| 擅长 | 补全历史缺集 | 抢首发、追当天更新 |
| 默认间隔 | 30 分钟 | 15 分钟 |

两者共用同一套过滤打分与缺集计算，都会写入同一份下载任务，不会重复下载。
「追新雷达」页可手动 `预览最新流` / `预览匹配`（只匹配不下载）/ `立即追新`。

- **缺集判断双来源**：数据库 `downloaded_episodes` ∪ 媒体库实际扫描到的文件
  → 手动放进去的文件也认，**永不重复下载**
- **季包优先**：一个季度合集能补齐多集时优先选它
- **只统计已播出集**：新番不会因为"总集数 12 集"而一直提示缺集
- **跨站去重**：磁力链按 infohash 归一，同一资源在多站出现只处理一次

### 🧩 插件系统

三种能力任选，放进 `plugins/<id>/` 目录即可被发现，Web 端可视化启停与配置：

| 能力 | 声明方式 | 用途 |
|---|---|---|
| 事件订阅 | `event_handlers()` | 监听 `download.completed`、`transfer.completed` 等 10 种事件 |
| 定时任务 | `scheduled_jobs()` | 支持 `interval` 与 5 段 `cron` |
| 手动动作 | `actions()` | 在 Web 界面/API 上一键触发 |

**内置 3 个示例插件**（默认关闭）：

- **`auto_cleanup` 自动清理** — 定时清理已入库/失败任务、过期通知、失效媒体库索引
- **`pan_transfer` 网盘转存助手** — 汇总待转存网盘资源，可对接自建转存 Webhook 实现全自动
- **`daily_digest` 每日追剧日报** — 每天 cron 推送新入库、订阅进度、缺集与下载状态

### 🖥 其他

- **Web 控制台零依赖**：原生 JS + CSS，**不加载任何 CDN**，NAS 离线内网可用
- **8 个功能页**：仪表盘 / 资源搜索 / 订阅追新 / 下载任务 / 媒体库 / 站点管理 / 插件 / 运行日志
- **双认证**：JWT（Web）+ `X-API-Token`（外部脚本自动化）
- **优雅降级**：没配 TMDB、没配站点、没配下载器，系统都不崩，只返回空结果或明确提示
- **自带 OpenAPI 文档**：`/docs`（Swagger）、`/redoc`

---

## 🏗 架构

```
┌──────────────────────────────────────────────────────────────┐
│  web/  零依赖 Web 控制台（原生 JS，无 CDN）                     │
└────────────────────────────┬─────────────────────────────────┘
                             │ REST /api/v1（53 个端点）
┌────────────────────────────▼─────────────────────────────────┐
│  app/api/      10 个 router：auth search subscribes downloads │
│                 library media sites plugins system radar      │
├──────────────────────────────────────────────────────────────┤
│  app/services/  业务编排层                                     │
│    search    多级关键词 · 并发聚合 · 去重                       │
│    subscribe 缺集计算 · 择优下载 · 巡检                         │
│    radar     站点最新流 → 匹配订阅 → 自动追新                    │
│    discovery 导航站解析 → 候选资源站发现                         │
│    presets   自定义站点配置模板                                 │
│    download  投递下载器 · 进度同步                             │
│    library   整理入库 · 扫描 · 刷新媒体服务器                    │
│    notify    事件总线 + 多渠道推送                              │
│    scheduler APScheduler（4 内置任务 + 插件任务）                │
│    sites     DB 配置 → Provider 实例                           │
├──────────────────────────────────────────────────────────────┤
│  app/core/      纯函数内核（无 IO，易测）                        │
│    meta 解析引擎 · filters 过滤打分 · organizer 命名转移         │
│    config 三级配置 · security PBKDF2+JWT · logger 环形缓冲       │
├──────────────────────────────────────────────────────────────┤
│  app/providers/ 18 个注册 Provider + TMDB 单例，装饰器自动发现   │
│    indexer  torznab rss nyaa api_generic html_generic mukaku  │
│    pan      pansou pan_generic                                │
│    downloader qbittorrent transmission aria2                  │
│    mediaserver emby jellyfin plex                             │
│    notify   telegram webhook bark wecom                       │
│    metadata tmdb（带 TTL 缓存，未配则全量降级）                  │
├──────────────────────────────────────────────────────────────┤
│  app/plugins/   插件基类 + 管理器（发现/热启停/配置/动作）        │
├──────────────────────────────────────────────────────────────┤
│  app/db/        SQLAlchemy 2.0 ORM · SQLite(WAL) · 12 张表     │
└──────────────────────────────────────────────────────────────┘
```

---

## 🚀 快速开始

### 🐳 Docker 部署（推荐）

```bash
git clone <your-repo-url> cineflow && cd cineflow

# 修改 docker-compose.yml 里的两个挂载路径与 PUID/PGID
docker compose up -d

# 查看日志
docker compose logs -f cineflow
```

打开 `http://<NAS_IP>:6060`，使用 **`admin` / `cineflow`** 登录（**请立即改密**）。

> ⚠️ **两个关键前提，务必满足**
> 1. **下载目录路径必须一致**：CineFlow 容器里的 `/downloads` 与 qBittorrent 容器里的 `/downloads`
>    必须映射到宿主机**同一个目录**，否则 CineFlow 找不到下载完成的文件。
> 2. **downloads 与 library 同一文件系统**：这样才能使用硬链接（秒完成、不占额外空间、保种）。
>    跨盘时 CineFlow 会自动降级为复制。

### 🐍 源码运行

```bash
git clone <your-repo-url> cineflow && cd cineflow

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

## ⚙️ 配置

三级优先级，**从低到高**：

```
代码默认值  <  config/config.yaml  <  环境变量 / .env（前缀 CF_）
```

YAML 里的二级 key 会拍平成 `CF_<父>_<子>`，例如 `tmdb.api_key` → `CF_TMDB_API_KEY`。

常用项速查：

| 配置 | 环境变量 | 默认 | 说明 |
|---|---|---|---|
| 端口 | `CF_PORT` | `6060` | |
| 管理员 | `CF_SUPERUSER` / `CF_SUPERUSER_PASSWORD` | `admin` / `cineflow` | 仅首次启动生效 |
| 密钥 | `CF_SECRET_KEY` | — | **务必改成随机串** |
| 转移模式 | `CF_TRANSFER_MODE` | `link` | `link`/`copy`/`move`/`softlink`/`strm` |
| 订阅巡检 | `CF_SUBSCRIBE_INTERVAL_MINUTES` | `30` | 分钟 |
| 追新雷达 | `CF_RADAR_ENABLED` | `true` | 站点最新流巡检总开关 |
| 雷达间隔 | `CF_RADAR_INTERVAL_MINUTES` | `15` | 分钟，设 0 亦可关闭 |
| 雷达取量 | `CF_RADAR_LIMIT_PER_SITE` | `100` | 每站最多取多少条最新资源 |
| 下载同步 | `CF_DOWNLOAD_CHECK_INTERVAL_MINUTES` | `5` | 分钟 |
| 媒体库扫描 | `CF_LIBRARY_SCAN_CRON` | `0 4 * * *` | 5 段 cron |
| 画质偏好 | `CF_PREFER_RESOLUTIONS` | `2160p,1080p,720p` | 越靠前优先级越高 |
| 关键词黑名单 | `CF_EXCLUDE_KEYWORDS` | `枪版,抢先版,CAM,…` | 支持正则 |
| TMDB | `CF_TMDB_API_KEY` | 空 | **留空也能用**，只是没海报/总集数 |
| API 令牌 | `CF_API_TOKEN` | 空 | 外部脚本用请求头 `X-API-Token` |

完整清单见 [`config/config.yaml.example`](config/config.yaml.example) 与 [`.env.example`](.env.example)。

---

## 🔌 接入你自己的站点

首次启动会写入 8 条**默认全部禁用**的示例站点（避免启动就对外发请求）。
在 **站点管理** 页填好地址后点「启用」即可。

### BT 站点（Jackett / Prowlarr）

最省事的方式是先部署 Jackett 或 Prowlarr，把所有站点聚合成 Torznab 接口：

| 字段 | 填写内容 |
|---|---|
| 类型 | `indexer` |
| Provider | `torznab` |
| 地址 | `http://jackett:9117/api/v2.0/indexers/all/results/torznab` |
| API Key | Jackett 首页右上角的 API Key |
| 优先级 | 数字越小越优先 |

> 用 `indexers/all` 可一次查询 Jackett 里配置的所有站点；也可换成单个 indexer 的 id。
> Prowlarr 同理，地址形如 `http://prowlarr:9696/<n>/api`。

### RSS 站点（含私站）

| 字段 | 填写内容 |
|---|---|
| Provider | `rss` |
| 地址 | RSS 地址，支持 `{keyword}` 占位符，例如 `https://site.org/rss?search={keyword}` |
| Cookie | 私站需要登录时填 |

不带 `{keyword}` 时按纯订阅流处理（取最新条目再本地过滤）。

### 网盘盘搜

**方式一：PanSou（推荐）**

| 字段 | 填写内容 |
|---|---|
| 类型 | `pan` |
| Provider | `pansou` |
| 地址 | `http://pansou:8888` |

**方式二：通用盘搜（接入任意第三方 JSON API，不用写代码）**

Provider 选 `pan_generic`，在站点的 `options` 里描述字段映射：

```json
{
  "method": "GET",
  "query_key": "kw",
  "list_path": "data.list",
  "params": { "page": 1 },
  "field_map": {
    "title": "name",
    "link": "url",
    "password": "pwd",
    "size": "filesize",
    "publish_at": "created_at"
  }
}
```

`list_path` 与 `field_map` 的值都支持 `a.b.c` 路径写法。

> 💡 网盘资源**不会**进 BT 下载器，而是登记为 `pending` 任务并保留分享链接与提取码。
> 前端「下载任务」页提供 `打开网盘(码:xxxx)` 按钮；想全自动可启用 `pan_transfer` 插件对接转存服务。

### 自定义资源站点（不写代码接入任意站点）⭐

除了 Torznab / RSS，CineFlow 提供三个**字段映射式**通用 Provider，
让你把任意资源站描述成配置即可接入，无需改动代码：

| Provider | 适用站点 | 描述方式 |
|---|---|---|
| `api_generic` | 返回 JSON 的资源站 | 接口路径 + 字段映射 |
| `html_generic` | 只有网页、没有 API 的站点 | 正则规则 |
| `pan_generic` | 第三方盘搜 API | 字段映射 |

站点管理页提供三个入口：

- **`▤ 从模板添加`** —— 选预设模板，接口路径与字段映射已预填
- **`◎ 发现站点`** —— 从导航站抓取候选资源站清单（见下文）
- **`+ 新增站点`** —— 完全手工填写

#### 内置预设：Mukaku 影视站（开箱可用）

Provider 选 `mukaku`，地址填 `https://web5.mukaku.com`，其余留空即可。
该站点的字段映射已内置并验证：**一次详情请求即可同时拿到该片的全部
BT 磁力与网盘分享链接**，并支持「最新流」用于追新雷达。

| 能力 | 说明 |
|---|---|
| 搜索 | `getVideoList` → `getVideoDetail`，返回磁力 + 夸克/百度/迅雷等网盘分享 |
| 追新 | `getTList`（电影/剧集最新种子流），供追新雷达低延迟发现新集 |
| 注意 | 中文站，搜索请用**中文片名**（英文原名命中率极低） |

> 实测：搜索《师兄太稳健》返回 66 条资源（60 磁力 + 6 网盘）；
> 换域名（如 `web9.mukaku.com`）时 API 路径会自动跟随。

#### `api_generic`：JSON API 站点

支持两种形态。**形态一**，列表接口直接带下载链接：

```json
{
  "api_base": "https://example.com/api/v1",
  "fixed_params": { "app_id": "xxx", "identity": "yyy" },
  "success_key": "code",
  "success_value": 200,
  "search_path": "search",
  "query_key": "keyword",
  "page_key": "page",
  "page_base": 1,
  "limit_key": "limit",
  "limit": 20,
  "list_path": "data.list",
  "item_map": {
    "title": "name",
    "link": "magnet",
    "size": "size",
    "seeders": "seeders",
    "publish_at": "created_at",
    "page_url": "detail_url"
  }
}
```

**形态二**，列表只有影视条目、链接要再请求详情（很常见）：

```json
{
  "api_base": "https://example.com/api/v1",
  "search_path": "getVideoList",
  "query_key": "sb",
  "list_path": "data.data",
  "item_map": { "title": "title", "alias": "alias", "detail_id": "idcode" },

  "detail_path": "getVideoDetail",
  "detail_query_key": "id",
  "max_detail_items": 3,
  "detail_extract": [
    {
      "list_path": "data.all_seeds",
      "kind": "magnet",
      "label": "BT",
      "map": { "title": "zname", "link": "zlink", "size": "zsize", "publish_at": "ezt" }
    },
    {
      "list_path": "data.movies_online_seed",
      "kind": "pan",
      "label": "网盘",
      "map": { "title": "seed_name", "link": "link", "password": "code" }
    }
  ]
}
```

要点：

- **`list_path` / `map` 的值都支持 `a.b.c` 路径**，也能取列表下标（`data.list.0.name`）
- `detail_extract` 的 `list_path` 若指向**字典套列表**（如网盘按 `quark`/`baidu` 分组），会自动压平
- `max_detail_items` 限制二次请求数量；候选条目会**先按标题相关性排序**，
  避免搜「沙丘」时把请求浪费在「沙丘战将」上
- `kind` 可显式声明 `magnet`/`torrent`/`pan`/`direct`，不写则按链接特征自动推断
- 追新流用 `latest_path` + `latest_params`（多组参数各请求一次）+ `latest_map`

#### `html_generic`：网页站点（正则）

```json
{
  "search_url": "https://example.com/search?q={keyword}&page={page}",
  "latest_url": "https://example.com/latest",
  "row_pattern": "<tr class=\"item\">(.*?)</tr>",
  "field_patterns": {
    "title": "title=\"([^\"]+)\"",
    "link": "href=\"(magnet:[^\"]+)\"",
    "size": "<td class=\"size\">([^<]+)</td>",
    "seeders": "<td class=\"se\">(\\d+)</td>"
  },
  "max_rows": 100
}
```

`row_pattern` 先切出每一行，`field_patterns` 再从行内取字段（取第一个捕获组）。
偷懒方案：只设 `"magnet_only": true`，会直接抓取页面内所有磁力链并按 infohash 去重
（磁力链 `dn` 参数里的资源名会被还原为标题）。写错的正则只会让该站返回空，不会影响其他站点。

#### 从导航站发现站点

`◎ 发现站点` 会抓取导航站收录的站点清单，供你挑选后添加。内置
[硬核指南](https://yinghezhinan.com/)（WordPress + OneNav 主题）。

> ⚠️ **导航站本身不提供影视资源与磁力链接**，它只是资源站入口的集合。
> 因此它不能作为搜索源，只用于「发现」——实测可解析出 123 个站点、
> 其中 60 个标记为影视相关。发现结果需你自行判断并配置为上面的自定义
> 站点后才能参与搜索与追新。已配置过的域名会标记「已添加」。

也可以在弹窗里填任意其他导航站地址；非 OneNav 结构会退化为抓取页面外链。

### 下载器

| Provider | 地址示例 | 备注 |
|---|---|---|
| `qbittorrent` | `http://qbittorrent:8080` | 填 WebUI 用户名密码，兼容 qB 5.x |
| `transmission` | `http://transmission:9091` | RPC，自动处理 409 令牌 |
| `aria2` | `http://aria2:6800` | JSON-RPC，API Key 填 secret；直链下载走它 |

`options` 可设 `{"category": "CineFlow", "tags": "CineFlow"}` 便于在下载器里归类。

### 媒体服务器

| Provider | 地址示例 | 凭据 |
|---|---|---|
| `emby` | `http://emby:8096` | API Key |
| `jellyfin` | `http://jellyfin:8096` | API Key |
| `plex` | `http://plex:32400` | X-Plex-Token；`options.section` 可指定库 |

入库成功后自动触发刷新，无需等它自己扫。

### 通知渠道

| Provider | 关键字段 |
|---|---|
| `telegram` | API Key = Bot Token，`options.chat_id` |
| `wecom` | 地址 = 企业微信机器人 Webhook URL |
| `bark` | 地址 = `https://api.day.app`，API Key = 设备码 |
| `webhook` | 任意 URL；`options` 可设 `method` / `headers` / `template` |

---

## 🧩 插件开发

### 目录结构

```
plugins/
└── my_plugin/
    ├── plugin.json     # 清单
    └── __init__.py     # 导出一个 PluginBase 子类
```

### `plugin.json`

```json
{
  "id": "my_plugin",
  "name": "我的插件",
  "version": "1.0.0",
  "description": "做点有用的事",
  "author": "you",
  "enabled_by_default": false,
  "default_config": { "enabled": true, "interval_minutes": 60 },
  "config_schema": [
    { "key": "interval_minutes", "label": "间隔（分钟）", "type": "number", "default": 60 }
  ]
}
```

`config_schema` 会被 Web 端自动渲染成配置表单，`type` 支持
`text` / `number` / `checkbox` / `select` / `textarea`。

### `__init__.py`

```python
from typing import Any, ClassVar
from app.plugins.base import PluginBase


class MyPlugin(PluginBase):
    plugin_id = "my_plugin"
    plugin_name = "我的插件"
    plugin_version = "1.0.0"
    plugin_desc = "做点有用的事"

    config_schema: ClassVar[list[dict[str, Any]]] = [
        {"key": "interval_minutes", "label": "间隔（分钟）", "type": "number", "default": 60},
    ]

    # ---- 生命周期 ----
    async def on_load(self) -> None: ...
    async def on_unload(self) -> None: ...
    async def on_config_change(self, config): self.config = config

    # ---- 能力 1：定时任务 ----
    def scheduled_jobs(self):
        return [{
            "id": "tick",
            "name": "我的定时任务",
            "func": self.tick,
            "trigger": "interval",              # 或 "cron"
            "minutes": int(self.get_config("interval_minutes", 60)),
            # "cron": "0 9 * * *",
        }]

    # ---- 能力 2：事件订阅 ----
    def event_handlers(self):
        return {"transfer.completed": self.on_transferred}

    # ---- 能力 3：手动动作（Web 上出现按钮）----
    def actions(self):
        return {"tick": self.tick}

    async def tick(self):
        return {"ok": True}

    async def on_transferred(self, payload: dict):
        print("入库完成", payload.get("task_id"))
```

### 可订阅事件

| 事件 | 触发时机 |
|---|---|
| `subscribe.added` | 新增订阅 |
| `subscribe.completed` | 订阅集齐 |
| `resource.matched` | 巡检命中资源 |
| `download.added` | 创建下载任务 |
| `download.completed` | 下载完成 |
| `transfer.completed` | 整理入库成功 |
| `transfer.failed` | 整理失败 |
| `library.refreshed` | 媒体服务器已刷新 |
| `plugin.action` | 插件动作被执行 |
| `system.error` | 系统异常 |

> 参考实现见 `plugins/auto_cleanup/`、`plugins/pan_transfer/`、`plugins/daily_digest/`。
> 三者分别演示了「事件 + 定时 + 动作」「Webhook 外联」「cron 报表」。

---

## 📡 API

- Swagger UI：`http://127.0.0.1:6060/docs`
- ReDoc：`http://127.0.0.1:6060/redoc`
- 健康检查：`GET /api/health`（无需认证，供 Docker / 反代使用）

外部脚本推荐用固定令牌，免登录：

```bash
# 配置 CF_API_TOKEN=your-token 后
curl -H "X-API-Token: your-token" \
     "http://127.0.0.1:6060/api/v1/search?keyword=庆余年&media_type=tv&season=2"

curl -X POST -H "X-API-Token: your-token" -H "Content-Type: application/json" \
     -d '{"title":"凡人修仙传","media_type":"tv","season":2}' \
     http://127.0.0.1:6060/api/v1/subscribes

curl -X POST -H "X-API-Token: your-token" \
     http://127.0.0.1:6060/api/v1/subscribes/run-all
```

主要端点分组（共 53 个）：

| 前缀 | 用途 |
|---|---|
| `/api/v1/auth` | 登录、当前用户、改密 |
| `/api/v1/search` | 聚合搜索（GET 快查 / POST 带完整过滤条件） |
| `/api/v1/subscribes` | 订阅 CRUD、缺集查询、单个/全部巡检 |
| `/api/v1/downloads` | 任务列表、手动添加、暂停恢复、删除、同步 |
| `/api/v1/library` | 统计、文件列表、扫描、手动整理、整理记录、刷新 |
| `/api/v1/media` | 资源名识别、TMDB 搜索/详情/分集/热榜 |
| `/api/v1/sites` | 站点 CRUD、连通性测试、Provider 清单、预设模板、导航站发现 |
| `/api/v1/radar` | 追新雷达：手动追新、预览匹配、最新流预览、任务状态 |
| `/api/v1/plugins` | 列表、启停、配置、执行动作 |
| `/api/v1/system` | 仪表盘、系统信息、调度任务、日志、通知 |

---

## 🧪 测试

```bash
pip install -r requirements-dev.txt
pytest -q                 # 136 passed
ruff check app tests      # 静态检查
```

| 测试文件 | 覆盖 |
|---|---|
| `test_meta.py` | 资源名解析（中英/季集/范围/合集/中文数字） |
| `test_filters.py` | 硬过滤与加权打分排序 |
| `test_organizer.py` | 命名模板、5 种转移模式、字幕跟随、跨盘降级 |
| `test_providers.py` | Provider 注册表、Torznab/RSS/PanSou 解析 |
| `test_api.py` | 端点鉴权与响应结构 |
| `test_automation.py` | **端到端**：订阅→巡检→下载→同步→入库→缺集收敛→重复巡检不重下 |
| `test_plugins.py` | 插件发现、启停、配置、动作、事件、cron/interval 注册 |
| `test_custom_sites.py` | **自定义站点**：JSON 字段映射、两阶段详情抓取、正则解析、导航站发现、预设 |
| `test_radar.py` | **追新雷达**：标题匹配、跨站去重、缺集命中、过滤规则、dry-run |

全部测试使用内存假 Provider，**全程不触网**。

### 额外验证脚本

`scripts/` 下另有五个开发期验证工具（详见 [`scripts/README.md`](scripts/README.md)）：

```bash
python -m app.main &                  # 先起服务

python scripts/smoke_test.py          # 67 项真实 HTTP 接口用例
python scripts/ui_check.py            # Playwright 真浏览器逐页点检 9 个页面，捕获 JS 报错
python scripts/demo_pipeline.py       # 真实文件演示解析→硬链入库→缺集收敛（无需服务）
python scripts/live_check.py          # 真实站点端到端：启用 mukaku→搜索→订阅→雷达匹配（联网）
python scripts/verify_docs.py         # 校验 README 事实声明与代码一致
```

---

## 📂 项目结构

```
cineflow/
├── app/
│   ├── core/          config logger security exceptions meta filters organizer version
│   ├── db/            session base models init_db
│   ├── providers/     base registry + indexer/ pan/ downloader/ mediaserver/ notify/ metadata/
│   ├── services/      sites search notify download library subscribe scheduler
│   │                  radar discovery presets
│   ├── plugins/       base manager
│   ├── api/           deps router + routers/(10)
│   ├── schemas/       enums models
│   ├── utils/         http strings
│   └── main.py        应用入口（lifespan / CORS / 静态资源）
├── web/               index.html + assets/(app.js style.css)  ← 零依赖前端
├── plugins/           auto_cleanup pan_transfer daily_digest  ← 示例插件
├── tests/             9 个测试文件
├── scripts/           smoke_test / ui_check / demo_pipeline 验证脚本
├── config/            config.yaml.example
├── docker/            entrypoint.sh
├── Dockerfile         多阶段构建
├── docker-compose.yml 含 Jackett/qB/PanSou/Emby 可选服务
└── requirements.txt
```

---

## ❓ 常见问题

<details>
<summary><b>下载完成了但没有入库？</b></summary>

99% 是**路径不一致**。CineFlow 需要能直接访问下载器落盘的文件。
请确认 CineFlow 容器的 `/downloads` 与下载器容器的 `/downloads` 指向宿主机同一目录。
可在「运行日志」页搜 `未找到下载文件` 确认。
</details>

<details>
<summary><b>硬链接失败 / 提示已降级为复制？</b></summary>

硬链接要求源和目标在**同一文件系统**。请把 `downloads` 与 `library` 放在同一个卷
（如群晖同一个 `/volume1`）。做不到就设 `CF_TRANSFER_MODE=copy`。
</details>

<details>
<summary><b>没有 TMDB API Key 能用吗？</b></summary>

能。解析引擎是纯本地的，搜索、过滤、下载、命名、入库全部正常，
只是没有海报、简介，且「总集数」需要你在订阅时手填。
</details>

<details>
<summary><b>搜索结果为空？</b></summary>

依次检查：① 站点管理里有没有**启用**的索引器/盘搜（示例站点默认全禁用）；
② 点「测试」看连通性；③ 看「运行日志」页；④ 是否被 `EXCLUDE_KEYWORDS` 或 `MIN_SEEDERS` 过滤光了。
</details>

<details>
<summary><b>网盘资源怎么下载？</b></summary>

网盘分享链接无法交给 BT 下载器。CineFlow 会登记为待处理任务并保留提取码，
你可以在「下载任务」页点按钮手动转存；或启用 `pan_transfer` 插件，
配置一个转存 Webhook（如自建 alist / cloud-saver 服务）实现全自动。
</details>

<details>
<summary><b>忘记密码了？</b></summary>

删掉 `data/cineflow.db` 里的 users 表记录，或直接删库重启（会丢历史数据）；
也可以设 `CF_SUPERUSER` 为一个新用户名后重启，会自动创建新管理员。
</details>

<details>
<summary><b>群晖 / 威联通文件权限报错？</b></summary>

在 compose 里把 `PUID` / `PGID` 改成你的实际用户（SSH 执行 `id` 查看），
并确保该用户对挂载目录有读写权限。
</details>

---

## ⚖️ 免责声明

本项目仅提供**自动化调度与文件管理**能力，**不内置、不提供、不分发**任何影视资源、
站点地址或破解内容。所有资源均来自用户自行配置的第三方服务。
请遵守所在地法律法规，仅用于备份和管理你已合法拥有的媒体内容。

## 📄 License

[MIT](LICENSE)

---

<div align="center">
致敬 <a href="https://github.com/jxxghp/MoviePilot">MoviePilot</a> 与
<a href="https://github.com/qq85423296/T3FAP">T3FAP</a> 的开源思路 ✨
</div>
