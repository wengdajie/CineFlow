# 13 · 配置与 API 参考

> 环境变量、配置优先级、REST API 一览、项目结构。
> 运维排障请转 [07-运维手册.md](07-运维手册.md)。

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
| 站点健康巡检 | `CF_SITE_HEALTH_ENABLED` | `true` | 定期探测站点可用性总开关 |
| 健康巡检周期 | `CF_SITE_HEALTH_INTERVAL_MINUTES` | `180` | 分钟；探测会真搜一次，别调太密 |
| 健康告警阈值 | `CF_SITE_HEALTH_FAIL_THRESHOLD` | `3` | 连续失败几次才通知，避免抖动刷屏 |
| 站点自动停用 | `CF_SITE_AUTO_DISABLE` | `false` | 连续失败是否自动禁用站点；默认关闭 |
| 下载器策略 | `CF_DOWNLOADER_STRATEGY` | `priority` | `priority` / `least_tasks` / `round_robin` |
| 下载器换源 | `CF_DOWNLOADER_FAILOVER` | `true` | 投递失败时自动换下一个下载器 |
| 榜单订阅周期 | `CF_RANKING_INTERVAL_MINUTES` | `720` | 分钟，`0`=关闭 |
| 榜单单次上限 | `CF_RANKING_MAX_PER_RUN` | `5` | 一次最多自动建几个订阅，防刷爆 |
| 视频追更周期 | `CF_VIDEO_SUBSCRIBE_INTERVAL_MINUTES` | `120` | 分钟，`0`=关闭。UP 主更新频率远低于剧集，查太勤只增加被风控概率 |
| 告警去抖窗口 | `CF_NOTIFY_ALERT_COOLDOWN_MINUTES` | `360` | 分钟，`0`=不去抖。同一条告警（站点掉线/网盘失效）的最短重复间隔 |
| 限速时段周期 | `CF_SPEED_LIMIT_INTERVAL_MINUTES` | `10` | 分钟，`0`=关闭。时段切换只需分钟级精度 |
| 搜索超时 | `CF_SEARCH_TIMEOUT` | `25` | 秒。⚠️ v1.13.0 起是**单个站点的总预算**（旧版是每个关键词各一次），带季集的订阅最坏耗时不再翻 3 倍 |
| 慢站熔断 | `CF_SEARCH_BREAKER_ENABLED` | `true` | v1.15.0。聚合搜索要等最慢的站，一个连不通的站会决定所有人的等待时间（实测吃满 25s 预算返回 0 条） |
| 熔断阈值 | `CF_SEARCH_BREAKER_THRESHOLD` | `3` | 连续几次「吃满预算且零结果」才熔断。**慢但有结果的站不会被熔断**（盘搜 19s / 2529 条） |
| 熔断冷却 | `CF_SEARCH_BREAKER_COOLDOWN_MINUTES` | `10` | 分钟，`0`=只计数不熔断。到期自动半开且从零计数；状态只在内存里，重启即清空 |
| 内置 AI 开关 | `CF_AI_ENABLED` | `false` | **默认关闭**。开启后分析站点会把页面正文发给你配的模型，必须显式同意 |
| AI 接口地址 | `CF_AI_BASE_URL` | `https://api.openai.com/v1` | OpenAI 兼容的 `/chat/completions`；Ollama 填 `http://主机:11434/v1` |
| AI 密钥 | `CF_AI_API_KEY` | 空 | 本地模型可留空。设置页里**留空表示不修改**（脱敏不回显，防误清空） |
| AI 模型 | `CF_AI_MODEL` | `gpt-4o-mini` | 如 `deepseek-chat` / `qwen-plus` / `llama3.1` |
| AI 超时 | `CF_AI_TIMEOUT` | `60` | 秒。要读整页 HTML，别设太短 |
| AI 正文上限 | `CF_AI_MAX_PAGE_CHARS` | `16000` | 发给模型的正文字符上限，超出则头尾各留一半 |
| AI 温度 | `CF_AI_TEMPERATURE` | `0.0` | 分析结构要稳定输出，建议保持 0 |
| 社区清单同步 | `CF_ZHUIJU_SYNC_ENABLED` | `true` | 定时拉取 awesome-zhuiju-free 站点清单。只更新候选目录与探测结论，**不会自动增删你的站点** |
| 清单同步周期 | `CF_ZHUIJU_SYNC_INTERVAL_MINUTES` | `1440` | 分钟。上游每天更新一次，查更勤没有意义 |
| 同步时顺带探测 | `CF_ZHUIJU_PROBE_ON_SYNC` | `true` | 用真实关键词逐站真搜一次判定可用性；关掉则只更新清单 |
| 单次探测上限 | `CF_ZHUIJU_PROBE_LIMIT` | `20` | 探测是串行且带间隔的（约 20 秒/站），上限防止一次跑太久 |
| RSS 巡检周期 | `CF_RSS_INTERVAL_MINUTES` | `20` | 分钟，`0`=关闭。RSS 是追新时效性最好的源（发布即出现在流里），但拉太勤对小站是无谓压力 |
| RSS 单轮源数上限 | `CF_RSS_MAX_FEEDS_PER_RUN` | `0` | `0`=不限。源很多时防止一轮跑几分钟 |
| RSS 同站间隔 | `CF_RSS_PER_HOST_DELAY` | `2.0` | 秒。同一站点的多条 feed 一次性并发拉取会被 429 |

> 上表中**除密钥、目录、端口外的绝大多数项都能在设置页直接改并立即生效**，
> 不必改文件重启。

完整清单见 [`config/config.yaml.example`](config/config.yaml.example) 与 [`.env.example`](.env.example)。

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

主要端点分组（共 183 个端点）：

| 前缀 | 用途 |
|---|---|
| `/api/v1/auth` | 登录、当前用户、改密 |
| `/api/v1/search` | 聚合搜索（GET 快查 / POST 带完整过滤条件）；**流式搜索**（v1.17.0）：`POST /search/stream` 按站点逐批下发结果，响应是 NDJSON（每行一个 JSON），事件 `start`（要查哪些站）/ `site`（某站结果 + 该站诊断 + `received`/`total_sites` 进度）/ `done`（完整诊断 + 耗时）；流已开始后异常写成 `{"type":"error"}` 行而不是改状态码。响应带 `X-Accel-Buffering: no`，反代需自行关闭缓冲；**慢站熔断**（v1.15.0）：`GET /search/breaker` 看哪些站在冷却、还剩多久，`POST /search/breaker/reset` 手动解除（不传 `site` 则全部清空） |
| `/api/v1/trending` | 热度排行 / 发现榜：总览、资源榜、实时榜、搜索热词、站点贡献榜、**豆瓣条目查询**、豆瓣四分类、B 站分区、YouTube 地区榜、**Bangumi 放送日历** |
| `/api/v1/images` | **封面图代理**（绕过豆瓣图床防盗链，带白名单 SSRF 防护，匿名可用） |
| `/api/v1/subscribes` | 订阅 CRUD、缺集查询、单个/全部巡检 |
| `/api/v1/downloads` | 任务列表、手动添加、暂停恢复、删除、同步、**资源类型→下载方式路由**（`GET /downloads/routing`：每类资源该走哪个下载器、现在缺什么、缺了去哪儿加；v1.15.0 起还会带 `unreachable` —— 巡检显示连不上的下载器）。⚠️ v1.15.0 起 `POST /downloads` 的 `success` 表示**投递的真实结局**：下载器拒绝时为 `false` 且 `message` 写明原因（HTTP 仍是 200，任务可查可重试）；`pending` 为 `true` 但带下一步提示 |
| `/api/v1/library` | 统计、文件列表、扫描、手动整理、整理记录、刷新 |
| `/api/v1/media` | 资源名识别、TMDB 搜索/详情/分集/热榜 |
| `/api/v1/sites` | 站点 CRUD、连通性测试、Provider 清单、预设模板、导航站发现；**Jackett/Prowlarr 批量接入**（v1.17.0）：`POST /sites/jackett/indexers` 列出 Jackett 上已配置的索引器（含分类/语言/是否已导入）、`POST /sites/jackett/import` 勾选后批量落库（`indexer_ids` 传 `["all"]` 用聚合端点；已存在的同名站点**更新地址与 Key**而非报错；逐条落库不整批回滚，返回 `imported`/`skipped`）、`POST /sites/jackett/test` 单站 `t=caps` 探测（不消耗搜索配额）。⚠️ 落库地址**不带**结尾 `/api`（Provider 会自己补，带上会拼成 `/api/api` → 404）；**社区清单**（v1.14.0）：`GET /sites/catalog` 查询候选（`?refresh=true` 强制拉取、`?probe=searchable` 只看可搜索的）、`POST /sites/catalog/probe` 真搜一次做可用性探测、`POST /sites/catalog/{entry_id}/apply` 一键添加（**仅 `searchable` 允许，其余返回 400 并说明原因**） |
| `/api/v1/radar` | 追新雷达：手动追新、预览匹配、最新流预览、任务状态 |
| `/api/v1/ranking-rules` | 榜单自动订阅：规则 CRUD、试算候选、单条/全部执行 |
| `/api/v1/rule-groups` | 过滤规则组：CRUD、设为默认、样例资源试算分层 |
| `/api/v1/site-health` | 站点健康：概览、历史记录、单站/批量探测 |
| `/api/v1/pan` | 网盘管理：总览（含能力位）、目录浏览、转存、批量转存、建目录、删除、直链、记录、**改名/移动/盘内搜索/凭据保活** |
| `/api/v1/pan-subscribes` | 网盘分享追更：订阅 CRUD、单个/全部巡检 |
| `/api/v1/video-subscribes` | **UP 主/频道视频追更**：订阅 CRUD、地址预览（不落库先看能不能解析）、单个/全部巡检、失败计数重置 |
| `/api/v1/downloaders` | 下载器独立管理（从站点管理搬出）：CRUD、连通性测试、字段 schema、**限速时段读写与立即应用** |
| `/api/v1/strm` | STRM 同步：概览、记录、手动同步、`play/{id}` 匿名 302 跳转 |
| `/api/v1/chatops` | 机器人：入站 Webhook、平台清单、配置、指令试跑、解析、审计 |
| `/api/v1/schedules` | 定时任务：查看、改期（interval/cron）、重置、立即执行 |
| `/api/v1/plugins` | 列表、启停、配置、执行动作 |
| `/api/v1/users` | 用户与权限：CRUD、三档角色（仅管理员可访问） |
| `/api/v1/rss-feeds` | **RSS 追新**（v1.18.0）：源 CRUD、`POST /rss-feeds/preview` 落库前先真拉一次看解析结果（返回方言、识别出的作品名、`suggest_aggregate` 建议）、`GET /rss-feeds/dialects` 各站字段差异说明、`POST /rss-feeds/{id}/check` 与 `POST /rss-feeds/check-all` 巡检（均支持 `?dry_run=true` 只算不下**且不写回 guid**）。⚠️ 与 `/sites` 里 `provider="rss"` 的站点**分工不同**：那些参与聚合搜索，本表是纯追新流（番剧 RSS 不支持关键词查询）；`PATCH` 的 `reset_history` 清空已处理 guid、`reset_failures` 清零失败计数**并一并恢复启用** |
| `/api/v1/system` | 仪表盘、系统信息、生效配置、**在线改配置/恢复默认**、调度任务、日志、通知；**更新检测**（v1.18.0）：`GET /system/update/check`（`?force=true` 忽略 30 分钟缓存）返回 `source`=`release`/`branch` 说明结论怎么来的 —— 仓库没有 Release 时退回读主干版本号与最新提交，不谎称已是最新；`POST /system/update/apply` 仅**源码部署**可用，只执行 `git pull --ff-only`（不 merge、不 reset），容器部署返回 `can_apply=false` + 可复制的 compose 命令 |
| `/api/v1/ai` | **内置 AI 站点分析**（默认关闭，仅管理员）：`GET /ai/config` 配置状态与可选方案、`POST /ai/analyze` 出建议、`POST /ai/verify` 本地真跑一次搜索验证、`POST /ai/apply` 确认后落库 |

---

## 📂 项目结构

```
cineflow/
├── app/
│   ├── core/          config logger security exceptions meta filters organizer
│   │                  nfo categories rules version
│   ├── db/            session base models init_db
│   ├── providers/     base registry + indexer/ pan/ panstorage/ downloader/
│   │                  mediaserver/ notify/ metadata/(tmdb douban bangumi)
│   ├── services/      sites search notify download library subscribe scheduler
│   │                  radar trending discovery presets settings_store
│   │                  pan_storage scraper strm_sync pan_subscribe upgrade
│   │                  config_store site_health ranking rule_groups
│   │                  video_subscribe speed_limit changelog
│   │                  download_routing ai_site zhuiju search_breaker
│   │                  chatops/
│   ├── plugins/       base manager
│   ├── api/           deps router + routers/(24)
│   ├── schemas/       enums models
│   ├── utils/         http strings
│   └── main.py        应用入口（lifespan / CORS / 静态资源）
├── web/               index.html + assets/(app.js style.css)  ← 零依赖前端
├── plugins/           auto_cleanup pan_transfer daily_digest  ← 示例插件
├── tests/             55 个测试文件
├── scripts/           smoke_test / ui_check / demo_pipeline 验证脚本
├── docs/              16 篇维护文档（现状/架构/路线图/决策/运维/站点接入/变更日志/竞品对标…）
├── config/            config.yaml.example
├── docker/            entrypoint.sh
├── Dockerfile         多阶段构建
├── docker-compose.yml 含 Jackett/qB/PanSou/Emby 可选服务
└── requirements.txt
```

---
