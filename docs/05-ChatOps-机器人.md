# 05 · ChatOps 机器人

> 目标：**不开电脑、不开网页**，在飞书 / 钉钉 / Telegram 里发一句
> 「搜索 庆余年 第二季」就能搜片，回一句「下载 2」就把第 2 条丢进下载器。

相关代码：

| 文件 | 职责 |
|---|---|
| `app/services/chatops/adapters.py` | 三平台适配器：验签、解析入站、回复 |
| `app/services/chatops/commands.py` | 指令解析（42 个别名 → 8 个规范指令） |
| `app/services/chatops/service.py` | 编排：幂等、白名单、会话上下文、执行、审计 |
| `app/api/routers/chatops.py` | 8 个端点（1 个匿名 Webhook + 7 个需登录） |
| `web/assets/app.js` → `pageChatops()` | 前端「机器人」页 |

---

## 1. 端点一览

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| POST | `/api/v1/chatops/webhook/{platform}` | **匿名**（靠验签） | 平台回调入口 |
| GET | `/api/v1/chatops/platforms` | JWT | 平台清单 + 配置字段声明 + 配置指引 |
| GET | `/api/v1/chatops/config` | JWT | 当前配置（敏感项脱敏） |
| PUT | `/api/v1/chatops/config` | JWT | 更新配置 |
| POST | `/api/v1/chatops/test` | JWT | 本地模拟一条指令并**真实执行** |
| POST | `/api/v1/chatops/parse` | JWT | 只解析不执行（调试） |
| GET | `/api/v1/chatops/commands` | JWT | 指令帮助与别名表 |
| GET | `/api/v1/chatops/audit` | JWT | 指令审计日志 |

> 为什么 Webhook 不走 JWT：见 [`04-决策记录.md`](04-决策记录.md) ADR-03。

**回调地址**形如：

```
http://<你的NAS地址>:6060/api/v1/chatops/webhook/feishu
http://<你的NAS地址>:6060/api/v1/chatops/webhook/dingtalk
http://<你的NAS地址>:6060/api/v1/chatops/webhook/telegram
```

机器人页每个平台卡片上都有一个可一键复制的回调地址框（`webhook-box`），
不用自己拼。**平台侧必须能访问到这个地址**（公网/内网穿透/反向代理任选）。

---

## 2. 三平台后台配置步骤

### 2.1 飞书（Lark）

1. [开放平台](https://open.feishu.cn) → 创建**自建应用**。
2. 「凭证与基础信息」里拿到 **App ID** / **App Secret**，填进 CineFlow 机器人页。
3. 「事件订阅」→ 请求地址填上面的 `…/webhook/feishu`；
   页面上的 **Verification Token** 复制进 CineFlow（**必填**，用于验签）。
   若开启了「加密推送」，把 **Encrypt Key** 也填上。
4. 订阅事件：**`im.message.receive_v1`**（接收消息）。
5. 「权限管理」开启：`im:message`、`im:message:send_as_bot`（否则机器人无法回复）。
6. 发布版本 → 把机器人加进一个群或直接私聊。

**验签**：校验 payload 里的 token 是否等于 Verification Token。
飞书有两代格式，两者都支持：

```
v1: payload["token"]
v2: payload["header"]["token"]
```

**URL 验证挑战**：飞书首次保存请求地址时会发
`{"type":"url_verification","challenge":"xxx"}`，CineFlow 原样回
`{"challenge":"xxx"}`，不会当成指令执行。

**加密模式**：`payload["encrypt"]` 存在时先解密再解析，算法为

```
key    = SHA256(Encrypt Key)          # 32 字节
data   = base64_decode(encrypt)
iv     = data[:16]，cipher = data[16:]
plain  = AES-256-CBC-decrypt(cipher, key, iv)，再去 PKCS7 padding
```

> 飞书国际版把 `api_base` 改成 `https://open.larksuite.com`。

### 2.2 钉钉

1. [开发者后台](https://open-dev.dingtalk.com) → 创建应用 → 添加**机器人**能力。
2. 「消息接收模式」选 **HTTP 模式**，「消息接收地址」填 `…/webhook/dingtalk`。
3. 把机器人的 **AppSecret** 填进 CineFlow（**必填**）。
4. 发布并把机器人加进群。

**验签**：读请求头 `timestamp` 与 `sign`，计算

```
sign == base64( HMAC_SHA256( key = AppSecret, msg = timestamp + "\n" + AppSecret ) )
```

用 `hmac.compare_digest` 定长比较。同时做**防重放**：`timestamp` 与当前时间
偏移超过阈值直接拒绝；`timestamp` 不是合法数字也拒绝（返回「timestamp 格式非法」）。

**回复**：优先用回调 payload 里的 `sessionWebhook`（时效性 webhook，无需额外鉴权）；
没有则退回配置里的固定 `webhook_url`。钉钉允许**直接在回调响应体里回消息**，
CineFlow 会把执行结果一并写进响应体，所以即使 `sessionWebhook` 缺失也能看到回复。

### 2.3 Telegram

1. 找 [@BotFather](https://t.me/BotFather) `/newbot` 拿 **Bot Token**。
2. 自己想一个 **Secret Token**（随机长字符串），填进 CineFlow。
3. 注册 Webhook（把尖括号内容换成你的）：

```
https://api.telegram.org/bot<BotToken>/setWebhook?url=https://<你的域名>/api/v1/chatops/webhook/telegram&secret_token=<SecretToken>
```

**验签**：比对请求头 `X-Telegram-Bot-Api-Secret-Token`
与配置里的 `secret_token`（定长比较）。这是 Telegram 官方推荐的防伪方式。

> Telegram 要求 Webhook 必须是 **HTTPS**。内网可用 Cloudflare Tunnel /
> frp + 反代解决；也可以把 `api_base` 指向自建反代来解决 Bot API 出网问题。

---

## 3. ⚠️ 未配密钥默认拒绝

从 v1.3.0 起，**没填验签密钥的平台会直接返回 401**，
错误信息里会写明「未配置 xxx，已拒绝」。

原因：这个端点不走登录鉴权，却能触发下载和删除网盘文件。
详见 ADR-04。

如果确实是纯内网、且你清楚风险，可以在该平台配置里把
**允许免验签**（`allow_unverified`）填 `1`，此时会放行并打一条 WARNING 日志。

---

## 4. 指令表（42 个别名 → 8 个规范指令）

| 规范指令 | 别名 | 示例 |
|---|---|---|
| `search` | 搜索 搜 查 查找 search s find | `搜索 庆余年 第二季` |
| `download` | 下载 下 download dl d get | `下载 2` / `下载 magnet:?xt=…` |
| `subscribe` | 订阅 追 追剧 追新 subscribe sub | `订阅 凡人修仙传 第二季` |
| `subscribes` | 订阅列表 我的订阅 subs subscribes | `订阅列表` |
| `status` | 状态 进度 任务 status st | `状态` |
| `transfer` | 转存 网盘 transfer save | `转存` |
| `trending` | 热榜 排行 热度 trending hot | `热榜` |
| `help` | 帮助 help ? ？ 菜单 | `帮助` |

### 解析细节（都有单测覆盖）

- **自动去噪**：先剥掉 `@机器人` 提及，再剥掉开头的 `/`（Telegram 习惯 `/search`）。
- **冒号写法**：`搜索:庆余年`、`搜索：庆余年` 都可以。
  只替换**紧跟指令词的第一个冒号**，所以 `下载 magnet:?xt=urn:btih:…`
  和 `下载 https://…` 不会被拆坏。
- **季集号抽取**：`第二季` / `第 2 季` / `S02` / `S01E09` / `第 9 集` / `E09`
  都能识别，识别后**从关键词里移除**，避免污染搜索词。
  中文数字支持一~十。
- **兜底 1**：整句是纯数字 → 当作 `下载 N`（承接上次搜索）。
- **兜底 2**：首词不是任何别名 → 整句当作**搜索关键词**
  （用户直接发片名即可搜，不必记指令）。
- **无法识别**（空消息）→ 回复帮助文本，**不静默丢弃**。

---

## 5. 会话上下文：先搜后下

```
用户：搜索 庆余年 第二季
机器人：找到 5 条（回复「下载 序号」即可）
        1. 庆余年.S02.2160p… ↑321
        2. 庆余年.S02.1080p… ↑188
        …
用户：下载 2
机器人：已提交下载：庆余年.S02.1080p…
```

- 会话键由 `platform + chat_id + user_id` 组合，**同群不同人互不干扰**。
- 上下文默认保留 **900 秒**（`CF_CHATOPS_SESSION_TTL`），过期后回
  「上下文已过期，请重新搜索」，不会错下别的片。
- 结果条数由 `CF_CHATOPS_RESULT_LIMIT` 控制（默认 5，聊天窗口里贴太多没人看）。
- 若打开 `CF_CHATOPS_AUTO_DOWNLOAD`，`搜索` 命中后**直接下最优一条**，
  不再返回列表；失败则自动退回列表模式让用户手选。

---

## 6. 幂等、白名单、审计

- **幂等**：`ChatAdapter.dedupe_key()` = `md5(platform:message_id:user_id)`，
  10 分钟内重复投递只执行一次。平台超时重推（飞书/钉钉都会重推）不会重复下载。
- **白名单**：`CF_CHATOPS_ALLOW_USERS` 为空表示不限制；配置后仅名单内
  用户 ID 可执行指令，其他人收到拒绝提示。各平台的用户 ID 语义不同
  （飞书 open_id、钉钉 senderStaffId、TG 数字 id），所以白名单在
  **service 层统一校验**而非适配器里。
- **审计**：每条指令写一条 `audit_logs`（来源渠道、用户、原文、结果），
  机器人页底部表格可查，也可 `GET /api/v1/chatops/audit?limit=100`。

---

## 7. 排障

| 现象 | 排查 |
|---|---|
| 平台后台保存地址就报错 | 地址是否可从公网/平台侧访问；飞书是否收到了 challenge 回包 |
| 回调 401 | 密钥没填或填错；钉钉检查服务器时间（防重放对时间敏感） |
| 回调 200 但机器人不说话 | 飞书缺 `im:message:send_as_bot` 权限；TG 的 `token` 没填 |
| 指令识别成搜索 | 首词不在别名表里就会兜底成搜索，用 `/chatops/parse` 看解析结果 |
| 重复下载 | 检查是否两个平台都配了同一个群；幂等只在同一 `message_id` 内生效 |

**最有效的排障方式**是绕开平台，直接在机器人页用「指令试跑」：

- **执行**：`POST /api/v1/chatops/test`，真实走完解析 → 执行 → 回复文本，
  唯一区别是不经过平台验签。能定位「是平台配置问题还是 CineFlow 逻辑问题」。
- **只解析**：`POST /api/v1/chatops/parse`，看清指令名/关键词/季/集/序号，
  用于确认季集号是否被正确抽取。
