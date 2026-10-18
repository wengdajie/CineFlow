# CineFlow 精简优化任务清单

状态：已按用户确认执行；勾选表示实现与本地验证完成。远端 CI 与 Docker 构建未实际运行，环境限制及证据见 [execution.md](execution.md)。背景与证据见 [plan.md](plan.md)。

以下命令使用项目虚拟环境的 Python；Windows 实际前缀为 `.venv/Scripts/python.exe`。

## A · 可靠性修复

### T01 · 限定 STRM 清理范围

- [x] 将清理范围限定为本次源目录与同步目标，按规范路径边界比较，不使用简单字符串前缀。
- [x] 覆盖“同步 A 保留 B”、同名前缀 `/A` 与 `/AB`、多同步目录的场景。
- [x] 仅删除确实属于完整扫描范围的失效 STRM，远端视频不受影响。

文件：`app/services/strm_sync.py`、`tests/test_strm_sync.py`。规模 M；依赖无。
验证：`python -m pytest tests/test_strm_sync.py`。

### T02 · 统一扫描完整性与清理判定

- [x] 将目录请求失败、递归超深、预算截断传递为不完整扫描；不完整时跳过推断性删除。
- [x] 回归覆盖部分子目录超时、全盘失败、真实空目录、深度上限与正常完整扫描。
- [x] API/通知如实说明扫描失败或不完整，并明确真实空目录的清理策略。

文件：`app/services/strm_sync.py`、`tests/test_strm_sync.py`。规模 M；依赖 T01。
验证：`python -m pytest tests/test_strm_sync.py`。

### T03 · 为 STRM 播放加入稳定凭据

- [x] 使用绑定记录的签名或高熵播放凭据；非法、缺失、已吊销凭据无法换取直链。
- [x] 保留无需 JWT 的媒体服务器播放，提供旧 STRM 重写与凭据轮换办法；不永久开放旧整数匿名入口。
- [x] 覆盖有效播放、篡改、枚举、轮换及升级后原有文件重写；如需存储变更，包含幂等迁移验证。

文件：`app/api/routers/strm.py`、`app/services/strm_sync.py`、`app/core/security.py`、`tests/test_strm_sync.py`；按方案必要时调整配置/迁移。规模 M；依赖 T01–T02。
验证：`python -m pytest tests/test_strm_sync.py tests/test_api.py`；媒体服务器或等价 HTTP 客户端播放验收。

### 检查点 A1

- [x] STRM 相关回归通过；有效文件不会因局部扫描或请求失败被清理。
- [x] 全量测试与 Ruff 通过；确认升级/回滚步骤可审查。

### T04 · 修复洗版索引与文件收尾

- [x] 新路径已登记时合并记录，不修改旧记录为冲突路径；质量和洗版次数正确保留。
- [x] 只有新文件验证成功后才处理旧文件；旧文件删除失败保留可重试信息。
- [x] 覆盖完整入库收尾、新文件缺失、相同路径和删除失败，保证新旧索引与实际文件一致。

文件：`app/services/library.py`、`app/services/upgrade.py`、`tests/test_upgrade.py`、`tests/test_library_ops.py`。规模 M；依赖无，按推荐顺序在 T03 后执行。
验证：`python -m pytest tests/test_upgrade.py tests/test_library_ops.py tests/test_automation.py`。

### T05 · 在下载服务统一自动投递幂等

- [x] 对自动订阅投递定义统一资源键，在 await 外部调用前完成原子占位；相同键并发只产生一次外部投递。
- [x] 失败/取消可重试；保留用户主动重新下载的语义，已有进行中任务不被清理。
- [x] 覆盖订阅/RSS/雷达交叉并发、不同订阅、失败重试和重启遗留占位；数据库变更包含旧库验证。

文件：`app/services/download.py`、必要的 `app/db/models.py` / `app/db/init_db.py`、`tests/test_automation.py`、相关下载测试。规模 M；依赖无。
验证：`python -m pytest tests/test_automation.py tests/test_download_routing.py tests/test_radar.py tests/test_rss_feeds.py`。

### 检查点 A2

- [x] 五项高优先级缺陷均有行为回归测试。
- [x] 全量测试、Ruff 通过；重试、失败通知和任务状态没有行为回退。

## B · 后端精简

### T06 · 调度配置只保留一个权威写入口

- [x] 周期与开关归调度服务管理；设置页与任务页显示同一真实值，旧配置优先级明确。
- [x] 一次性、幂等迁移已有 `runtime_config` 与 `schedules`，兼容重启与重置；冲突值不静默丢弃。
- [x] 旧 API 如保留则转发同一入口，不再两处独立保存。

文件：`app/services/config_store.py`、`app/services/scheduler.py`、必要的设置/调度 API、`tests/test_config_store.py`、拟新增 `tests/test_schedules.py`。规模 M；依赖 T05 后的稳定基线。
验证：`python -m pytest tests/test_config_store.py tests/test_schedules.py`；设置与调度页交叉编辑、重启验证。

### T07 · 合并影视缺集候选选择

- [x] 订阅、雷达、RSS 调用一个公共的纯选择函数，RSS 不再调用雷达私有函数。
- [x] 保留电影、单集、多集、季包、已存在集、最佳版本的行为与清晰的数据类型边界。
- [x] 删除原重复分支；不引入通用工作流引擎或强行合并网盘/视频订阅模型。

文件：`app/services/subscribe.py`、`app/services/radar.py`、`app/services/rss_feeds.py`、一个小型公共选择模块、相关测试。规模 M；依赖 T05。
验证：`python -m pytest tests/test_automation.py tests/test_radar.py tests/test_rss_feeds.py tests/test_filters.py`。

### T08 · 合并搜索准备与结果处理

- [x] 批量与流式复用别名/规则准备、元数据转换与站点结果诊断逻辑。
- [x] 保留流式逐站输出、取消清理及批量全局排序；站点配额、去重和规则组语义可解释且有测试。
- [x] 实际删除重复逻辑；不以增加抽象类、万能上下文对象替代重复代码。

文件：`app/services/search.py`、`tests/test_search_share.py`、`tests/test_search_quota.py`、`tests/test_jackett_stream.py`。规模 M；依赖 T07。
验证：`python -m pytest tests/test_search_share.py tests/test_search_quota.py tests/test_search_breaker.py tests/test_jackett_stream.py tests/test_rule_groups.py`。

### T09 · 具体站点定义改为单一数据源

- [x] 默认站点初始化与可安装站点目录引用同一具体定义，保留用户自改 URL、Cookie、启用状态和优先级。
- [x] 通用字段模板继续单独存在；失效站点标明状态与历史证据，不把历史可用当作当前保证。
- [x] 停止为已由 Torznab 覆盖的复杂站点新增重复适配；现有 Provider 只有在调用与迁移证据充分时才删除。

文件：`app/db/init_db.py`、`app/services/site_catalog.py`、`tests/test_site_add_and_dirs.py`、`tests/test_bt_coverage_v127.py`、`tests/test_seed_retire.py`。规模 M；依赖 T08。
验证：`python -m pytest tests/test_site_add_and_dirs.py tests/test_bt_coverage_v127.py tests/test_seed_retire.py tests/test_providers.py`。

### 检查点 B

- [x] 调度、影视候选选择和具体站点定义各自只有一个权威入口。
- [x] 确认减少的是生产逻辑重复；现有行为测试、兼容性与可用零配置源被保留。
- [x] 全量测试与 Ruff 通过；记录实际删减行数与仍保留的合理差异。

## C · 前端精简

### T10a · 提取公共 API 与 DOM/表单工具

- [x] 从单脚本提取 API 客户端、认证状态、公共 DOM/模态框/表单工具为原生模块。
- [x] 页面继续使用同一公共工具，不复制一套过渡工具；不引入 CDN 或新框架构建链。
- [x] 登录、退出、权限提示、主题与请求错误显示保持可用。

文件：`web/assets/app.js`、新增少量公共模块、`web/index.html`。规模 M；依赖检查点 B。
验证：真实浏览器检查登录/退出、三档权限、主题切换、请求失败；现有前端相关 API/文档测试。

### T10b · 逐页迁出搜索与设置

- [x] 搜索与设置各自成为页面模块，入口脚本只承担公共初始化与路由调度。
- [x] 保留搜索流式事件、取消/切页清理、表单配置与已有 hash 路由。
- [x] 首次加载、刷新、返回、切页无重复事件或控制台报错；其他页面不趁机全量重写。

文件：`web/assets/app.js`、搜索与设置页面模块、必要的公共模块。规模 M；依赖 T10a。
验证：真实浏览器搜索/取消/返回、设置保存/刷新；检查网络请求与控制台错误。

### T11 · 收敛导航并保留高级能力

- [x] 一级入口围绕搜索、追更、下载、媒体库、网盘和设置组织；雷达、RSS、规则、调度等作为所属流程的次级入口。
- [x] 视频追更、AI 建站、ChatOps、插件等可保留为高级入口；收起入口不会停掉已配置任务。
- [x] 旧 hash 可达；手机宽度、键盘导航与三档权限可用。

文件：前端导航/布局模块、`web/assets/style.css`、必要的页面路由。规模 M；依赖 T10b。
验证：真实浏览器桌面与手机尺寸的搜索→订阅→下载→入库流程；已有高级功能跳转检查。

## D · 持续验证与文档

### T12a · 镜像构建前增加质量门禁

- [x] PR 与主分支运行 pytest/Ruff，镜像构建依赖检查成功。
- [x] 验证 Python 3.10 最低声明版本及 Docker 使用的 3.12；不把本机通过等同于 CI 已通过。
- [x] CI 使用隔离配置与假外部服务，不依赖个人凭据或真实公网资源。

文件：`.github/workflows/build-image.yml`、必要的测试 workflow、`requirements-dev.txt`。规模 S–M；依赖检查点 A2，可提前执行。
验证：workflow 静态检查；实际 CI 结果；Docker 构建检查（具备 Docker 时执行并如实记录）。

### T12b · 固定可复现依赖快照

- [x] 在保留需求声明的基础上提供部署依赖锁定方式，CI 与 Docker 使用一致解析结果。
- [x] 快照覆盖当前支持平台/版本；yt-dlp 等易变依赖更新走明确验证流程。
- [x] 不为锁定依赖额外引入常驻服务；能从干净环境安装并通过核心测试。

文件：`requirements.txt`、必要的约束/锁定文件、`Dockerfile`、测试 workflow。规模 M；依赖 T12a。
验证：干净 Python 环境安装；相关全量测试与容器构建。

### T13 · 同步文档与可生成事实

- [x] 修正 README v1.27.0、现状文档 v1.18.0 与实际 v1.28.2 的不一致；后续按最终版本更新。
- [x] Provider/端点/配置数量等从真实模型生成或校验，移除过期数量口径；保留有效行为断言。
- [x] 文档说明新调度来源、播放链接迁移和精简后的入口；删去重复历史叙述，不删除决策证据。

文件：`README.md`、相关 `docs/`、`scripts/verify_docs.py`、必要的文档测试。单次规模限 M，涉及多篇文档时按主题分批。依赖 T06、T09、T11、T12b。
验证：文档端点测试、引用链接/版本一致性检查；需要服务的脚本只对隔离实例运行。

### 最终检查点

- [x] 全量 pytest 与 Ruff 通过，新增复现用例能防止同类回归。
- [x] 浏览器主流程、配置重启、旧库升级、STRM 重写与 Docker 构建均有实际证据或明确环境限制。
- [x] 汇报实际删减、维护入口减少、行为变化与回滚方式；未经另行授权不发布或删除现有功能。

## 暂不纳入的可选裁剪

- 短视频/UGC、AI 站点分析、导航站发现、ChatOps 或插件的整体删除。
- 迁移 Vue/React、PostgreSQL、Redis/Celery 或微服务。
- 强制安装 Jackett/Prowlarr、删除全部内置公共源。
- 按测试文件版本号或行数大批删除测试。

这些会改变产品范围或部署成本，需要在推荐计划之外单独明确选择。
