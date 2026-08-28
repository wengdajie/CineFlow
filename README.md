<div align="center">

# 🎬 CineFlow

**面向 NAS 的自动化观影追剧平台**

聚合 BT 站点与网盘搜索 → 自动追新 → 择优下载 → 规范命名入库 → 通知媒体服务器

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)](#-docker-部署推荐)
[![Tests](https://img.shields.io/badge/tests-370%20passed-brightgreen)](#-测试)
[![Version](https://img.shields.io/badge/version-1.4.0-blue)](docs/08-变更日志.md)
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

### 🔥 热度排行

搜索页与「热度排行」页都能看到榜单，**不依赖任何第三方榜单接口**，
完全由你自己站点的真实搜索结果算出来：

| 榜单 | 数据来源 | 说明 |
|---|---|---|
| 资源热度榜 | 本地搜索缓存（`resources` 表） | 按「作品 + 季」归并，跨站聚合 |
| 实时热榜 | 联网拉取各站最新发布流 | 不依赖历史数据，冷启动也有榜 |
| 搜索热词 | 历史搜索关键词 | 点击热词直接发起搜索 |
| 站点贡献榜 | 各站命中条目数 | 判断哪个站真的在出货 |

热度是一个**可解释的加权分**，而不是黑盒：

```
heat = min(做种数, 5000) ^ 0.5 × 3      # 做种数（开方避免头部通吃）
     + 站点数 × 14                       # 多站都有 → 是热资源
     + min(条目数, 40) × 1.6             # 同作品资源条目越多越热
     + min(集数, 60) × 0.8               # 更新集数
     + 新鲜度                             # 24h 内满分 20，之后 96h 半衰
     + 画质加成                           # 2160p +12 / 1080p +8 / 720p +4
     + 有网盘资源 +6
```

最终线性归一化成 `heat_percent`（0~100）用于渲染热度条，
`rank` 前三名带金/银/铜徽标。榜单支持按 `media_type`（电影/剧集）与
`kind`（BT / 网盘）过滤，统计窗口 `days` 可调（默认 14 天）。
榜单里任意一项都能**一键订阅**，直接进入自动追新。

**榜单会做去碎片化归并**。发布站常把同一集打成多种封装，
若直接按标题分组，一部剧会被拆成十几条并列占满榜单：

```
师兄太稳健[第16-17集][国语配音+中文字幕].Pull.Strings.S01.2026.2160p.WEB-DL
师兄太稳健[高码版][第10-11集]...
师兄太稳健[60帧率版本][杜比视界版本][高码版][第08-09集]...
第18集 师兄太稳健[第18集][国语音轨]...
✅「师兄太稳健」全17集...
                    ↓ 归并后
           师兄太稳健 · S01 · 8 条资源 / 2 站点
```

归并规则：剥离**版本标记**（高码版 / 60帧率版本 / 杜比视界版本 / 国语配音 / 中文字幕…）、
**集数标记**（`第09集`、`第01,02集`、`第12-13集`、`全36集`、`更15集`、`EP05`）、
**季标记**（`第二季` / `S02`）与装饰符号（`✅` `★` `「」`），
再按「作品名 + 季号」聚合；季号缺失的剧集按第 1 季归并（站点常省略单季剧的季号）。
不同季仍然分开成榜。该归并**只作用于榜单展示**，不影响资源名解析、
下载命名与缺集计算。

### ⏱ 定时任务可视化设置

**9 内置任务**的触发规则都能在界面上改，**不用改配置文件、不用重启**：

| 任务 | 默认 | 作用 |
|---|---|---|
| 订阅巡检（自动追新） | 每 30 分钟 | 逐个活跃订阅去各站搜索缺失集 |
| 追新雷达 | 每 15 分钟 | 拉各站最新流再匹配订阅，延迟最低 |
| 下载状态同步与自动整理 | 每 5 分钟 | 同步进度，完成即硬链入库并刷新媒体服务器 |
| 网盘待转存队列 | 每 20 分钟 | 把命中但没转存成功的网盘资源批量重试转存 |
| 网盘分享追更 | 每 60 分钟 | 巡检订阅的分享链接，只转存新增的集 |
| 网盘 STRM 同步 | 关闭（`0`） | 把网盘目录映射成 `.strm`，媒体库直接能播 |
| 媒体库补刮（NFO + 图片） | 每天 `30 4 * * *` | 给缺 NFO 的历史文件补刮元数据与海报 |
| 洗版巡检（更优版本替换） | 关闭 | 发现明显更优版本时替换已入库文件 |
| 媒体库全量扫描 | 每天 `0 4 * * *` | 重建入库索引，用于缺集计算与去重 |

- 两种触发方式：**interval**（1 ~ 10080 分钟）与 **cron**（标准 5 段表达式）
- 非法规则**当场拒绝**并给出原因（如 `非法 cron 表达式`），不会把调度器搞坏
- 改完**立即改期**并显示新的下次执行时间，同时可单独启停某个任务
- 改动写入 `settings` 表 → **重启后依然生效**；静态配置（`.env` / `config.yaml`）
  只作为默认值，随时可「重置为默认」
- 每个任务都能**立即执行一次**，用于验证配置
- 「订阅追新」页顶部内嵌快捷卡片，可直接调周期或立即跑一次

### ☁️ 网盘管理（转存 + 浏览）

盘搜找到的是**别人的分享链接**——链接会失效、也不进你的媒体库。
所以「盘搜」和「网盘」是两件事，CineFlow 分成两类 Provider：

| 分类 | Provider | 干什么 |
|---|---|---|
| `pan`（搜索器） | `pansou` `pan_generic` | **找**分享链接 |
| `panstorage`（存储器） | `alist` `quark` `webdav` `local_dir` | **存**进你自己的盘、浏览、给直链 |

| 存储 Provider | 鉴权 | 转存方式 | 适合 |
|---|---|---|---|
| `alist` **推荐** | 账号密码或固定 `api_key` | `add_offline_download` 离线下载 | 一套接 20+ 网盘，已有 AList 的首选 |
| `quark` | 浏览器 Cookie | 官方分享转存四步流程（换 stoken → 列文件 → 提交 → 轮询任务） | 国内影视分享最多的夸克 |
| `webdav` ⭐ | Basic Auth（账号密码） | 不支持分享转存（协议无此语义），支持浏览/上传/删除/容量 | **一份实现覆盖 Nextcloud / 坚果云 / 群晖 / TeraCLOUD / AList DAV / Alist 兼容层** |
| `local_dir` | 无 | 不支持转存（只读） | 把 rclone / CloudDrive 挂载目录当网盘浏览，**零配置试用** |

- **自动转存**：订阅追新命中网盘资源时，`download` 服务会按分享域名**优先选同家网盘**
  （夸克分享给夸克盘，没有同家就退回 AList 离线下载），成功后任务直接进 `transferred`
  状态并把落地路径写进 `meta`。可用 `CF_PAN_AUTO_SAVE=false` 关掉
- **待转存队列**：转存失败（提取码错、容量不足、Cookie 过期）的任务留在队列里，
  「网盘待转存队列」定时任务每 20 分钟重试一次，也能在页面上一键全部重试
- **转存记录**：每次转存都落一条记录（成功/失败 + 原因 + 落地路径），
  专门用来回答「为什么这个没转存成功」
- **目录浏览**：容量进度条 + 面包屑导航 + 建目录 / 删除 / 换取临时直链（可喂给 STRM 或 aria2）
- **优雅降级**：不支持某个能力的网盘（如 `local_dir` 不能转存）会返回**明确提示**，
  不是 500 也不是假装成功

### 🏷 NFO 刮削与分类归档

入库之后 Emby / Jellyfin 还要**自己猜**这是什么片子——国产剧、冷门片、
纪录片经常猜错。CineFlow 直接把元数据写成媒体服务器认的 **NFO**，让它不用猜：

| 类型 | 产物 | 落地位置 |
|---|---|---|
| 电影 | `movie.nfo` + `poster.jpg` / `fanart.jpg` | 影片目录 |
| 剧集 | `tvshow.nfo` + `poster.jpg` / `fanart.jpg` | 剧目录 |
| 季 | `season.nfo` + `season{N}-poster.jpg` | `Season NN/` |
| 单集 | `<同名>.nfo`（含剧情/播出日期/演职员） | 与视频同目录同名 |

- **入库即刮**：`library.transfer` 成功后同步写 NFO（`CF_SCRAPE_ENABLED`）
- **历史补刮**：`媒体库补刮` 定时任务（默认 `30 4 * * *`）扫出缺 NFO 的文件批量补，
  媒体库页也有 **`补刮 NFO`** 按钮手动触发，单次上限 `CF_SCRAPE_BATCH=200` 条防打满 TMDB 限速
- **没配 TMDB 也能刮**：降级写一份只含本地解析结果（标题/年份/季集）的最小 NFO，
  标记 `degraded`，比让媒体服务器乱猜好
- **默认不覆盖已有 NFO**（`CF_SCRAPE_OVERWRITE=false`）：你手工修过的不会被定时任务冲掉
- **分类归档**（`CF_CATEGORY_ENABLED`，默认关闭）：按 TMDB genre 优先、
  本地关键词兜底判成 电影 / 电视剧 / 动漫 / 纪录片 / 综艺 / 儿童 六类，
  在媒体库下多一级目录。**判不出来就不归档**——宁可不分类，也不要归错以后手工搬

### ☁️ WebDAV：一份实现接一堆网盘

逐家网盘写私有 API 维护成本极高（Cookie 会过期、接口会改）。
所以除了 AList 网关与夸克直连，另外提供标准 **WebDAV** 存储 Provider：

- `PROPFIND` 列目录、`MKCOL` 建目录、`PUT`/`DELETE` 读写、`quota-available-bytes` 查容量
- 一份配置即可接 **Nextcloud / 坚果云 / 群晖 Synology Drive / TeraCLOUD / AList 自带 DAV**
- 路径做 percent 编码，中文与空格目录不会 404
- 提供 `auth_header()`，可配合 STRM 302 播放直链
- **明确声明 `supports_save = False`**：WebDAV 协议没有「转存别人分享」这个语义，
  于是老实报「不支持」，而不是假装成功

### 🎬 STRM 同步 + 302 直链播放

网盘里的片子不想占 NAS 空间，又想在 Emby 里直接点开播——这就是 STRM：

```
网盘目录 (alist/quark/webdav/local_dir)
      │  cineflow.strm_sync 巡检（CF_STRM_SYNC_INTERVAL_MINUTES）
      ▼
本地 .strm 文件（一行文本，内容是一个 URL）
      │  Emby / Jellyfin 扫描到就当成一集
      ▼
播放器请求 → GET /api/v1/strm/play/{id} → 302 跳转到网盘真实直链
```

| 链接模式 | 写进 `.strm` 的内容 | 取舍 |
|---|---|---|
| `proxy`（默认） | `CF_STRM_BASE_URL` + `/api/v1/strm/play/{id}` | **链接永不过期**，每次播放都现取新直链；CineFlow 只回 302，**不代理流量** |
| `direct` | 网盘当次给的临时直链 | NAS 完全零参与，但直链会过期、需要重同步 |

- `play` 端点**匿名可访问**（播放器带不了 JWT，同 ADR-03），但它只做跳转、不回传文件内容
- **失效清理**（`CF_STRM_CLEAN_INVALID`）：网盘上源文件消失时删掉对应 `.strm`，
  避免媒体库里留着一堆点开就报错的"幽灵剧集"
- **随行文件同步**（`CF_STRM_SYNC_METADATA`）：字幕 / NFO / 图片直接下载到本地，
  这些文件很小但媒体服务器需要它们

### 🔗 网盘分享追更（不靠 BT 也能追日更）

很多国产剧只在网盘分享里更新。给一个**分享链接**，CineFlow 会定时去看有没有新集：

- **增量转存**：记住已转存过的文件名，下次只转新增的，不会重复搬同一集
- **文件过滤**：包含/排除关键词 + 正则；**用户写错正则不会搞崩巡检**（当没填处理）
- **重命名规则**：可把 `第08集.mp4` 直接改成 `剧名 - S01E08.mp4` 再落盘，方便入库识别
- **节奏可控**：限定星期几执行、设置到期时间，日更剧不必整天轮询
- **死链自动停手**：连续失败到 `CF_PAN_SUBSCRIBE_MAX_FAILURES` 次标记 `invalid` 并停止重试
- **不支持增量列举的网盘**（只能整体转存的）用哨兵记录防止反复整体转存

### ⬆️ 洗版（更优版本替换）

订阅勾选「洗版」后，已入库的 1080p 会在出现 4K REMUX 时被替换：

- **评分阈值**：新版本得分要高出 `CF_UPGRADE_SCORE_DELTA=15` 才动手，防止在几个同档版本间反复横跳
- **次数上限**：每个文件最多洗 `CF_UPGRADE_MAX_TIMES=2` 次
- **先入库再删旧**：新文件确认入库成功后才删旧文件，中途失败也不会留下空洞
- **默认关闭**（`CF_UPGRADE_ENABLED=false`）：它会**删除已入库文件**，必须你明确开启
- 订阅页提供 **洗版试算**，只报告「哪几个文件会被什么替换、得分差多少」，不真的执行

### 🤖 ChatOps：在飞书 / 钉钉 / Telegram 里发指令

不用开网页，聊天窗口里一句话就能搜索、下载、订阅。

| 指令 | 说法举例（共 40+ 个中英别名） | 作用 |
|---|---|---|
| `search` | `搜索 沙丘 第二季` / `搜 三体` / `s Dune` / 直接发片名 | 聚合搜索，回前 N 条 |
| `download` | `下载 2` / `2` / `下载 magnet:?xt=…` | 下上一次搜索的第 N 条，或直接投链接 |
| `subscribe` | `订阅 凡人修仙传 第二季` / `追剧 苍兰诀` | 建订阅并自动追新 |
| `subscribes` | `订阅列表` / `我的订阅` / `subs` | 追剧进度与缺集 |
| `status` | `状态` / `进度` | 下载中的任务 |
| `transfer` | `转存` / `网盘` | 批量转存待处理网盘资源 |
| `trending` | `热榜` / `排行` | 资源热度榜 |
| `help` | `帮助` / `?` / `菜单` | 指令说明 |

- **会话上下文**：`搜索` 的结果按会话缓存，接着发 `下载 2` 就能选中第 2 条，
  不用复制粘贴链接（有效期 `CF_CHATOPS_SESSION_TTL`，默认 15 分钟）
- **口语容错**：自动去掉 `@机器人` 与前导 `/`，认 `搜索:片名`、中文季号（`第二季`）、
  `S02E09`；纯数字当序号；**没有指令词就当搜索**
- **验签是强制的**：Webhook 端点不能带 JWT（平台发不了），所以安全性全靠验签——
  飞书校验 `verification_token`（支持 AES 加密推送与 URL 验证挑战）、
  钉钉校验 `HMAC-SHA256(timestamp+"\n"+secret)` **并带时间戳防重放**、
  Telegram 校验 `X-Telegram-Bot-Api-Secret-Token`。
  **没配密钥默认直接拒绝**，纯内网想免验签必须显式打开 `allow_unverified`
- **幂等**：平台重投同一条消息（10 分钟内）只执行一次
- **白名单**：`CF_CHATOPS_ALLOW_USERS` 限定谁能操控你的 NAS
- **全量审计**：每条指令都写 `audit_logs`（谁、哪个渠道、什么指令、成功与否、回复内容）
- **界面自助**：机器人页展示各平台**可一键复制的回调地址**与配置字段，
  还带一个「指令试跑」输入框——不用真去建机器人也能验证指令是否被正确解析和执行

三个平台的回调地址（填到各平台后台）：

```
http://<你的地址>:6060/api/v1/chatops/webhook/feishu
http://<你的地址>:6060/api/v1/chatops/webhook/dingtalk
http://<你的地址>:6060/api/v1/chatops/webhook/telegram
```

详细配置步骤见 [`docs/05-ChatOps-机器人.md`](docs/05-ChatOps-机器人.md)。

### ⚙️ 设置页

**设置页**把当前**真正生效**的配置按 **12 组 62 项**列出来（服务 / 目录 / 整理入库 /
搜索与订阅策略 / 调度 / 网盘管理 / **刮削与分类** / **STRM 同步** /
**分享追更与洗版** / ChatOps 机器人 / 元数据与网络 / 安全），
每项都标出对应的 `CF_XXX` 环境变量名，密钥类只显示「已设置」而不回显明文。

> 这里刻意**只读**：静态配置改了必须重启才生效，做成可编辑就会出现
> 「界面上改了、重启后丢了」的假功能。需要在线改的东西（定时任务周期、
> ChatOps 配置、站点、插件）都在各自页面里，并且**持久化到数据库**。

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

### 🎨 界面与主题

**零依赖前端**：原生 JS + CSS 手写，**不加载任何 CDN / 不用构建工具**，
NAS 离线内网直接可用，整个前端只有 `index.html` + `app.js` + `style.css` 三个文件。

- **三档主题**：暗色 / 浅色 / **跟随系统**。选择写入 `localStorage.cf_theme`，
  `<head>` 内有一段极小的内联脚本在首屏渲染前就定好 `data-theme`，
  **切换与刷新都不会闪白**；选「跟随系统」时监听 `prefers-color-scheme` 实时响应
- **完整 token 化设计系统**：所有颜色走语义变量
  （`--surface-0..3` / `--text` / `--accent` / `--ok|warn|err` / `--ring` / `--shadow-1..3`），
  两套主题各自一份取值，组件代码零改动即可换肤
- **16 个功能页**分五组导航（总览 / 发现 / 追剧 / 入库 / 系统），侧边栏按组折行
- **约 35 个内联 SVG 线性图标**，无字体图标、无图片请求
- **骨架屏加载**（不再是白屏转圈）、空态插画、热度条、排名徽标、
  分段控件（segment）、标签云（chips）等组件
- **响应式**：980px 折叠侧边栏，560px 单列排布；支持
  `prefers-reduced-motion`（关闭动画）与打印样式
- **双认证**：JWT（Web）+ `X-API-Token`（外部脚本自动化）
- **优雅降级**：没配 TMDB、没配站点、没配下载器，系统都不崩，只返回空结果或明确提示
- **自带 OpenAPI 文档**：`/docs`（Swagger）、`/redoc`

#### 参考的开源设计方案

视觉体系并非凭感觉调色，而是从以下开源项目里取用了**公开的设计 token 与命名范式**
（仅参考色阶数值与命名思路，未拷贝任何代码）：

| 项目 | 取用内容 |
|---|---|
| [radix-ui/colors](https://github.com/radix-ui/colors) | 12 级色阶体系。暗/浅两套主题的 slate / indigo / violet / grass / amber / red 取值直接对齐其 `dark.ts` / `light.ts`（如 `indigo9=#3e63dd`、`grass9=#46a758`、`red9=#e5484d`） |
| [argyleink/open-props](https://github.com/argyleink/open-props) | 灰阶 HSL 分层思路，以及圆角 / 间距 / 阴影的尺度 token 命名 |
| [shadcn-ui/ui](https://github.com/shadcn-ui/ui) | 语义化 token 命名法：`surface` / `muted` / `accent` / `ring` / `destructive` |
| [feathericons/feather](https://github.com/feathericons/feather) | 线性图标风格（1.5px 描边、24 网格、圆头端点），图标路径为等价手写 |

---

## 📚 文档

项目的现状、设计、升级计划与每一步决策都写进了 [`docs/`](docs/README.md)，方便后续维护与 review：

| 文档 | 内容 |
|---|---|
| [`docs/01-项目现状.md`](docs/01-项目现状.md) | 代码规模、模块清单、能力矩阵、**当前缺口** |
| [`docs/02-架构设计.md`](docs/02-架构设计.md) | 分层铁律、主链路时序、扩展点、设计取舍 |
| [`docs/03-升级路线图.md`](docs/03-升级路线图.md) | 里程碑任务表 + **逐条验收证据** |
| [`docs/04-决策记录.md`](docs/04-决策记录.md) | ADR：为什么这么选，以及被否掉的方案 |
| [`docs/05-ChatOps-机器人.md`](docs/05-ChatOps-机器人.md) | 三平台配置步骤、验签算法、指令表 |
| [`docs/06-网盘管理.md`](docs/06-网盘管理.md) | 盘搜 vs 网盘、三种存储配置、转存流程 |
| [`docs/07-运维手册.md`](docs/07-运维手册.md) | 部署、备份、排障、验证脚本 |
| [`docs/08-变更日志.md`](docs/08-变更日志.md) | v1.0.0 → v1.4.0 逐版本记录 |
| [`docs/09-竞品对标与差距分析.md`](docs/09-竞品对标与差距分析.md) | 对标 MoviePilot / quark-auto-save / SmartStrm / MediaWarp / TgtoDrive，**差距与不做的事** |

---

## 🏗 架构

```
┌──────────────────────────────────────────────────────────────┐
│  web/  零依赖 Web 控制台（原生 JS，无 CDN）                     │
└────────────────────────────┬─────────────────────────────────┘
                             │ REST /api/v1（95 个端点）
┌────────────────────────────▼─────────────────────────────────┐
│  app/api/      16 个 router：auth search trending subscribes  │
│                 radar schedules downloads library media       │
│                 sites pan pan-subscribes strm chatops         │
│                 plugins system                                │
├──────────────────────────────────────────────────────────────┤
│  app/services/  业务编排层                                     │
│    search    多级关键词 · 并发聚合 · 去重                       │
│    subscribe 缺集计算 · 择优下载 · 巡检                         │
│    radar     站点最新流 → 匹配订阅 → 自动追新                    │
│    trending  热度加权打分 → 资源榜/实时榜/热词/站点榜             │
│    discovery 导航站解析 → 候选资源站发现                         │
│    presets   自定义站点配置模板                                 │
│    download  投递下载器 · 进度同步 · 网盘资源自动转存            │
│    pan_storage 网盘容量/浏览/转存/待转存队列                     │
│    chatops/  聊天平台适配（验签/解析/回复）+ 指令执行引擎          │
│    library   整理入库 · 扫描 · 刷新媒体服务器                    │
│    scraper   NFO 刮削 · 海报下载 · 媒体库批量补刮                │
│    strm_sync 网盘目录 → .strm · 302 直链播放                    │
│    pan_subscribe 网盘分享追更（增量转存新集）                    │
│    upgrade   洗版：更优版本替换已入库文件                        │
│    notify    事件总线 + 多渠道推送                              │
│    scheduler APScheduler（9 内置任务 + 插件任务），可视化改期      │
│    settings_store 运行期设置持久化（settings 表）               │
│    sites     DB 配置 → Provider 实例                           │
├──────────────────────────────────────────────────────────────┤
│  app/core/      纯函数内核（无 IO，易测）                        │
│    meta 解析引擎 · filters 过滤打分 · organizer 命名转移         │
│    config 三级配置 · security PBKDF2+JWT · logger 环形缓冲       │
├──────────────────────────────────────────────────────────────┤
│  app/providers/ 22 个注册 Provider + TMDB 单例，装饰器自动发现   │
│    indexer  torznab rss nyaa api_generic html_generic mukaku  │
│    pan      pansou pan_generic         ← 找分享链接（搜索器）    │
│    panstorage alist quark webdav local_dir ← 存到自己的盘（存储器）  │
│    downloader qbittorrent transmission aria2                  │
│    mediaserver emby jellyfin plex                             │
│    notify   telegram webhook bark wecom                       │
│    metadata tmdb（带 TTL 缓存，未配则全量降级）                  │
├──────────────────────────────────────────────────────────────┤
│  app/plugins/   插件基类 + 管理器（发现/热启停/配置/动作）        │
├──────────────────────────────────────────────────────────────┤
│  app/db/        SQLAlchemy 2.0 ORM · SQLite(WAL) · 16 张表     │
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
| NFO 刮削 | `CF_SCRAPE_ENABLED` | `true` | 入库后自动写 NFO，媒体服务器不用猜 |
| 刮削图片 | `CF_SCRAPE_IMAGES` | `true` | 同时下载 poster / fanart |
| 覆盖 NFO | `CF_SCRAPE_OVERWRITE` | `false` | 默认不冲掉你手工改过的 NFO |
| 补刮周期 | `CF_SCRAPE_CRON` | `30 4 * * *` | 给历史文件补 NFO；留空关闭 |
| 补刮批量 | `CF_SCRAPE_BATCH` | `200` | 单次最多刮几个文件，防打满 TMDB 限速 |
| 分类归档 | `CF_CATEGORY_ENABLED` | `false` | 开启后按 电影/电视剧/动漫/纪录片/综艺/儿童 分二级目录 |
| STRM 链接模式 | `CF_STRM_LINK_MODE` | `proxy` | `proxy`=写 302 端点（不过期）/ `direct`=写临时直链 |
| STRM 同步周期 | `CF_STRM_SYNC_INTERVAL_MINUTES` | `0` | 分钟，`0`=关闭自动同步 |
| STRM 失效清理 | `CF_STRM_CLEAN_INVALID` | `true` | 网盘源文件消失时删掉对应 `.strm` |
| STRM 随行文件 | `CF_STRM_SYNC_METADATA` | `true` | 同步字幕 / NFO / 图片到本地 |
| 分享追更周期 | `CF_PAN_SUBSCRIBE_INTERVAL_MINUTES` | `60` | 分钟，`0`=关闭 |
| 分享失效阈值 | `CF_PAN_SUBSCRIBE_MAX_FAILURES` | `5` | 连续失败几次后标记失效并停手 |
| 洗版开关 | `CF_UPGRADE_ENABLED` | `false` | ⚠️ 会**删除已入库文件**，默认关闭 |
| 洗版评分差 | `CF_UPGRADE_SCORE_DELTA` | `15` | 至少高这么多分才替换，防横跳 |
| 洗版次数上限 | `CF_UPGRADE_MAX_TIMES` | `2` | 每个文件最多洗几次 |
| 画质偏好 | `CF_PREFER_RESOLUTIONS` | `2160p,1080p,720p` | 越靠前优先级越高 |
| 关键词黑名单 | `CF_EXCLUDE_KEYWORDS` | `枪版,抢先版,CAM,…` | 支持正则 |
| TMDB | `CF_TMDB_API_KEY` | 空 | **留空也能用**，只是没海报/总集数 |
| API 令牌 | `CF_API_TOKEN` | 空 | 外部脚本用请求头 `X-API-Token` |
| 网盘自动转存 | `CF_PAN_AUTO_SAVE` | `true` | 盘搜命中后自动转存进已配置的网盘 |
| 转存重试间隔 | `CF_PAN_TRANSFER_INTERVAL_MINUTES` | `20` | 分钟，设 0 关闭该定时任务 |
| 转存批量 | `CF_PAN_TRANSFER_BATCH` | `20` | 单次最多转存多少条 |
| 机器人总开关 | `CF_CHATOPS_ENABLED` | `true` | 关掉后所有入站指令直接忽略 |
| 指令自动下载 | `CF_CHATOPS_AUTO_DOWNLOAD` | `false` | 开启后「搜索」直接下最优的一条 |
| 指令回复条数 | `CF_CHATOPS_RESULT_LIMIT` | `5` | 搜索结果回复几条 |
| 指令白名单 | `CF_CHATOPS_ALLOW_USERS` | 空 | 平台用户 ID，逗号分隔；留空=不限制 |
| 指令会话时长 | `CF_CHATOPS_SESSION_TTL` | `900` | 秒；「搜索」后能回「下载 2」的有效期 |

完整清单见 [`config/config.yaml.example`](config/config.yaml.example) 与 [`.env.example`](.env.example)。

---

## 🔌 接入你自己的站点

首次启动会写入 12 条**默认全部禁用**的示例站点（避免启动就对外发请求）。
版本升级时会**按名字补齐新增的示例站点**，已存在的同名站点不会被覆盖。
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

主要端点分组（共 95 个）：

| 前缀 | 用途 |
|---|---|
| `/api/v1/auth` | 登录、当前用户、改密 |
| `/api/v1/search` | 聚合搜索（GET 快查 / POST 带完整过滤条件） |
| `/api/v1/trending` | 热度排行：总览、资源榜、实时榜、搜索热词、站点贡献榜 |
| `/api/v1/subscribes` | 订阅 CRUD、缺集查询、单个/全部巡检 |
| `/api/v1/downloads` | 任务列表、手动添加、暂停恢复、删除、同步 |
| `/api/v1/library` | 统计、文件列表、扫描、手动整理、整理记录、刷新 |
| `/api/v1/media` | 资源名识别、TMDB 搜索/详情/分集/热榜 |
| `/api/v1/sites` | 站点 CRUD、连通性测试、Provider 清单、预设模板、导航站发现 |
| `/api/v1/radar` | 追新雷达：手动追新、预览匹配、最新流预览、任务状态 |
| `/api/v1/pan` | 网盘管理：总览、目录浏览、转存、批量转存、建目录、删除、直链、记录 |
| `/api/v1/pan-subscribes` | 网盘分享追更：订阅 CRUD、单个/全部巡检 |
| `/api/v1/strm` | STRM 同步：概览、记录、手动同步、`play/{id}` 匿名 302 跳转 |
| `/api/v1/chatops` | 机器人：入站 Webhook、平台清单、配置、指令试跑、解析、审计 |
| `/api/v1/schedules` | 定时任务：查看、改期（interval/cron）、重置、立即执行 |
| `/api/v1/plugins` | 列表、启停、配置、执行动作 |
| `/api/v1/system` | 仪表盘、系统信息、生效配置、调度任务、日志、通知 |

---

## 🧪 测试

```bash
pip install -r requirements-dev.txt
pytest -q                 # 370 passed
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
| `test_trending.py` | **热度排行 + 定时任务**：热度打分与排序、窗口过滤、热词、站点榜、**发布版本/集号归并去碎片化**、cron/interval 校验、改期持久化 |
| `test_panstorage.py` | **网盘管理**：三个存储 Provider、路径规范化与**越界防护**、夸克四步转存、AList 离线下载、选盘策略、**盘搜命中自动转存端到端** |
| `test_chatops.py` | **ChatOps**：40+ 别名解析、中文季号、三平台**验签正反用例**（含钉钉真实 HMAC、防重放）、飞书加解密与挑战、幂等去重、白名单、**搜索→下载 N 会话上下文**、Webhook 全链路 |
| `test_nfo.py` | **NFO 渲染**：四种根节点、演职员、图片命名惯例、TMDB id 回读、无 IO 纯函数 |
| `test_scraper.py` | **刮削服务**：入库即刮、TMDB 不可用降级、不覆盖已有 NFO、批量补刮上限 |
| `test_categories.py` | **分类归档**：TMDB genre 优先、关键词兜底、判不出返回 `None` 不猜 |
| `test_webdav.py` | **WebDAV**：PROPFIND 解析、MKCOL、容量、percent 编码、明确不支持分享转存 |
| `test_strm_sync.py` | **STRM 同步**：`proxy`/`direct` 两种链接、302 播放解析、失效清理、随行文件 |
| `test_pan_subscribe.py` | **分享追更**：增量文件名去重、包含/排除/正则过滤、错误正则不崩、重命名规则、星期与到期限制、失败达阈值标记失效 |
| `test_upgrade.py` | **洗版**：评分阈值防横跳、次数上限、仅 `best_version` 生效、先入库后删旧 |

全部测试使用内存假 Provider，**全程不触网**。

### 额外验证脚本

`scripts/` 下另有六个开发期验证工具（详见 [`scripts/README.md`](scripts/README.md)）：

```bash
python -m app.main &                  # 先起服务

python scripts/smoke_test.py          # 141 项真实 HTTP 接口用例
python scripts/ui_check.py            # Playwright 真浏览器逐页点检 16 个页面 + 主题切换，捕获 JS 报错
python scripts/demo_pipeline.py       # 真实文件演示解析→硬链入库→缺集收敛（无需服务）
python scripts/live_check.py          # 真实站点端到端：启用 mukaku→搜索→订阅→雷达匹配（联网）
python scripts/verify_docs.py         # 校验 README 事实声明与代码一致
python scripts/research_refs.py       # 抓取同类开源项目特性清单，产出对标差距表（联网）
```

---

## 📂 项目结构

```
cineflow/
├── app/
│   ├── core/          config logger security exceptions meta filters organizer
│   │                  nfo categories version
│   ├── db/            session base models init_db
│   ├── providers/     base registry + indexer/ pan/ panstorage/ downloader/
│   │                  mediaserver/ notify/ metadata/
│   ├── services/      sites search notify download library subscribe scheduler
│   │                  radar trending discovery presets settings_store
│   │                  pan_storage scraper strm_sync pan_subscribe upgrade
│   │                  chatops/
│   ├── plugins/       base manager
│   ├── api/           deps router + routers/(16)
│   ├── schemas/       enums models
│   ├── utils/         http strings
│   └── main.py        应用入口（lifespan / CORS / 静态资源）
├── web/               index.html + assets/(app.js style.css)  ← 零依赖前端
├── plugins/           auto_cleanup pan_transfer daily_digest  ← 示例插件
├── tests/             19 个测试文件
├── scripts/           smoke_test / ui_check / demo_pipeline 验证脚本
├── docs/              10 篇维护文档（现状/架构/路线图/决策/运维/变更日志/竞品对标…）
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
