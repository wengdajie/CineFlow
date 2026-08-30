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

主要端点分组（共 143 个）：

| 前缀 | 用途 |
|---|---|
| `/api/v1/auth` | 登录、当前用户、改密 |
| `/api/v1/search` | 聚合搜索（GET 快查 / POST 带完整过滤条件） |
| `/api/v1/trending` | 热度排行：总览、资源榜、实时榜、搜索热词、站点贡献榜、**豆瓣条目查询** |
| `/api/v1/images` | **封面图代理**（绕过豆瓣图床防盗链，带白名单 SSRF 防护，匿名可用） |
| `/api/v1/subscribes` | 订阅 CRUD、缺集查询、单个/全部巡检 |
| `/api/v1/downloads` | 任务列表、手动添加、暂停恢复、删除、同步 |
| `/api/v1/library` | 统计、文件列表、扫描、手动整理、整理记录、刷新 |
| `/api/v1/media` | 资源名识别、TMDB 搜索/详情/分集/热榜 |
| `/api/v1/sites` | 站点 CRUD、连通性测试、Provider 清单、预设模板、导航站发现 |
| `/api/v1/radar` | 追新雷达：手动追新、预览匹配、最新流预览、任务状态 |
| `/api/v1/ranking-rules` | 榜单自动订阅：规则 CRUD、试算候选、单条/全部执行 |
| `/api/v1/rule-groups` | 过滤规则组：CRUD、设为默认、样例资源试算分层 |
| `/api/v1/site-health` | 站点健康：概览、历史记录、单站/批量探测 |
| `/api/v1/pan` | 网盘管理：总览（含能力位）、目录浏览、转存、批量转存、建目录、删除、直链、记录、**改名/移动/盘内搜索/凭据保活** |
| `/api/v1/pan-subscribes` | 网盘分享追更：订阅 CRUD、单个/全部巡检 |
| `/api/v1/strm` | STRM 同步：概览、记录、手动同步、`play/{id}` 匿名 302 跳转 |
| `/api/v1/chatops` | 机器人：入站 Webhook、平台清单、配置、指令试跑、解析、审计 |
| `/api/v1/schedules` | 定时任务：查看、改期（interval/cron）、重置、立即执行 |
| `/api/v1/plugins` | 列表、启停、配置、执行动作 |
| `/api/v1/users` | 用户与权限：CRUD、三档角色（仅管理员可访问） |
| `/api/v1/system` | 仪表盘、系统信息、生效配置、**在线改配置/恢复默认**、调度任务、日志、通知 |

---

## 📂 项目结构

```
cineflow/
├── app/
│   ├── core/          config logger security exceptions meta filters organizer
│   │                  nfo categories rules version
│   ├── db/            session base models init_db
│   ├── providers/     base registry + indexer/ pan/ panstorage/ downloader/
│   │                  mediaserver/ notify/ metadata/
│   ├── services/      sites search notify download library subscribe scheduler
│   │                  radar trending discovery presets settings_store
│   │                  pan_storage scraper strm_sync pan_subscribe upgrade
│   │                  config_store site_health ranking rule_groups
│   │                  chatops/
│   ├── plugins/       base manager
│   ├── api/           deps router + routers/(20)
│   ├── schemas/       enums models
│   ├── utils/         http strings
│   └── main.py        应用入口（lifespan / CORS / 静态资源）
├── web/               index.html + assets/(app.js style.css)  ← 零依赖前端
├── plugins/           auto_cleanup pan_transfer daily_digest  ← 示例插件
├── tests/             36 个测试文件
├── scripts/           smoke_test / ui_check / demo_pipeline 验证脚本
├── docs/              11 篇维护文档（现状/架构/路线图/决策/运维/站点接入/变更日志/竞品对标…）
├── config/            config.yaml.example
├── docker/            entrypoint.sh
├── Dockerfile         多阶段构建
├── docker-compose.yml 含 Jackett/qB/PanSou/Emby 可选服务
└── requirements.txt
```

---
