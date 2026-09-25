# 雷神加速器自动暂停工具

![Python Version](https://img.shields.io/badge/Python-3.9%2B-blue)
![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)
![License](https://img.shields.io/badge/License-MIT-yellow)

用手机号 + 密码自动登录雷神加速器（NN加速器），换取令牌后执行暂停。
可以跑在**华为云函数 FunctionGraph** 上（定时触发），也可以跑在 GitHub Actions 或本地。

**零第三方依赖**，只用 Python 标准库——不需要 `pip install`，部署包就是源码本身。

## ✨ 特性

- 🔑 **手机号 + 密码自动登录**，令牌过期不用管，每次运行自动换新
- 👥 **支持多账户**：一次运行轮完所有账户，一个失败不影响其余
- 🔁 **失败自动重试**——登录接口偶发「网络异常」（表现为 200 但响应体为空），
  默认重试 10 次、每次间隔 1~3 秒随机
- 🌀 自动暂停加速器，重复暂停（错误码 400803）视为成功
- 🙈 **日志与返回值只含打码手机号**（`138****8000`），完整号码和密码不会进 CI / 云日志
- 📦 **零依赖**：不需要制作云函数依赖包，也不受云运行时内置库版本影响
- 🗃️ **本地运行可缓存令牌**，登录一次能用很久，不必每次都登录
- 🔔 **可选 PushPlus 推送**：可设为只在真正暂停成功时通知，失败必推

## 📁 项目结构

```
leishen-auto/
├── index.py                    # 华为云 FunctionGraph 入口（index.handler）
├── main.py                     # 本地命令行入口（带令牌缓存）
├── runner.py                   # 「登录 + 暂停」编排，各入口共用
├── token_cache.py              # 本地令牌缓存（云函数/Actions 不使用）
├── notify.py                   # PushPlus 推送（可选）
├── api/
│   ├── client.py               # API 客户端（登录、暂停、重试）
│   └── sign.py                 # 接口签名（已与页面 JS 差分验证）
├── config/
│   └── config.py               # 配置加载 + 内置 .env 解析
├── scripts/
│   ├── build_functiongraph_zip.py    # 打包 FunctionGraph 部署包
│   └── env_to_console_json.py        # 把 .env 转成控制台可粘贴的环境变量 JSON
├── cloudflare/                 # Cloudflare Worker（JavaScript 重写的那份）
│   ├── src/
│   │   ├── index.js            # Worker 入口（Cron Triggers）
│   │   ├── api.js              # 登录、暂停、重试
│   │   ├── sign.js             # 接口签名（与 Python 版同一套黄金向量）
│   │   ├── md5.js              # 自带 MD5（Web Crypto 不支持 MD5）
│   │   ├── config.js           # 从 env 读配置
│   │   ├── notify.js           # PushPlus 推送
│   │   └── cache.js            # 令牌缓存（KV，可选）
│   ├── wrangler.toml
│   └── package.json
├── Dockerfile / docker-compose.yml   # Docker 常驻定时
└── .github/workflows/auto-pause.yml  # GitHub Actions 定时任务
```

> 单元测试只保留在本地，没有随仓库推送。

---

## 🧭 五种运行方式

核心逻辑所有入口共用（Cloudflare 那份是 JS 重写，见下），区别只在**配置从哪来**、
**要不要缓存令牌**、**由谁触发**：

| | 本地命令行 | Docker | 华为云 FunctionGraph | GitHub Actions | Cloudflare Workers |
|---|---|---|---|---|---|
| 语言 | Python | Python | Python | Python | **JavaScript** |
| 配置来源 | `.env` / 环境变量 | `.env`（compose 注入） | 控制台环境变量 | 仓库 Secrets | `wrangler secret` + `[vars]` |
| 令牌缓存 | ✅ 存本地 | ✅ 存卷里 | ❌ | ❌ | ✅ KV（可选） |
| 触发方式 | 手动 / `RUN_CRON` | `RUN_CRON` | 定时触发器 | cron | Cron Triggers |

**本地、Docker、Cloudflare 会缓存令牌**——云函数和 Actions 的存储都是一次性的，
存了也带不到下一次。下面分别说明。

---

## ⚙️ 配置项

两种部署方式（以及本地运行）都用同一组配置：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PHONE` | — | 手机号（单账户写法） |
| `PHONE_1`、`PHONE_2`… | — | 手机号（多账户写法，编号从 **1** 起） |
| `PASSWORD` / `PASSWORD_1`… | — | 账户密码（**明文**），程序会按接口要求自行做 MD5 |
| `COUNTRY_CODE` | `86` | 手机号国家码（所有账户共用） |
| `SRC_CHANNEL` | `guanwang` | 渠道来源（所有账户共用） |
| `API_LANG` | `zh_CN` | 接口语言 |
| `RETRIES` | `10` | 单个请求的失败重试次数 |
| `SHOW_TOKEN` | — | 设为 `1` 时打印登录得到的 `account_token`（调试用） |
| `PUSHPLUS_TOKEN` | — | 全局 PushPlus token；留空则不推送 |
| `PUSHPLUS_TOKEN_1`、`_2`… | — | **账户专属**的推送 token，不填回落到全局那个 |
| `PUSHPLUS_TOPIC` | — | 群组编码，填了推给整个群组，留空只推给自己 |
| `PUSHPLUS_TEMPLATE` | `txt` | 消息模板（`txt` / `html` / `markdown` / `json`） |
| `NOTIFY_MODE` | `always` | 推送时机，见下文 |
| `NOTIFY_GROUPING` | `combined` | 推送条数：`combined` 汇总一条 / `per_account` 一账户一条 |
| `RUN_CRON` | — | **仅本地/Docker**：定时运行的 cron 表达式；留空则跑一次就退出 |
| `TOKEN_CACHE_FILE` | — | **仅本地/Docker**：令牌缓存文件位置；留空用默认位置 |
| `FAIL_RETRY_INTERVAL` | `1h` | **仅本地/Docker**：某一轮失败后隔多久重试；`0` = 不重试 |
| `FAIL_NOTIFY_EVERY` | `5` | **仅本地/Docker**：每失败几次推送一次（第 1 次总是立刻推） |

### 多账户

每个账户用一组 `PHONE_n` + `PASSWORD_n` 表示，编号从 1 开始：

```bash
PHONE_1=13800138000
PASSWORD_1=你的密码

PHONE_2=13900139000
PASSWORD_2=另一个密码
```

规则：

- **不需要连续编号**，`PHONE_1` 和 `PHONE_10` 可以同时存在，处理顺序按编号数值升序
- **整组留空会被忽略**，所以模板里没用到的账户留着不填即可
- **只配一半会直接报错**（比如配了 `PHONE_2` 却漏了 `PASSWORD_2`），
  并明确指出是哪一组——漏配一个账户却以为它在跑，比报错难查得多
- 不带编号的 `PHONE` 可以和多账户写法混用，它排在最前面
- `COUNTRY_CODE` / `SRC_CHANNEL` / `RETRIES` 等是全局的，所有账户共用

**某个账户失败不会影响其他账户**——多账户场景下，一个密码输错不该让其他账户
也漏掉当天的暂停。程序会把所有账户跑完，最后汇总哪些成功哪些失败
（整体退出码以是否有失败为准）。

### 🔔 PushPlus 推送（可选）

配置 `PUSHPLUS_TOKEN` 后，运行结果会推送到微信。token 在
[pushplus.plus](https://www.pushplus.plus) 登录后于「一对一推送」页面获取。

**推送时机**由 `NOTIFY_MODE` 决定：

| 值 | 行为 |
|----|------|
| `always`（默认） | 每次运行都推 |
| `on_change` | **只在真的有账户从「运行中」变成「已暂停」时才推** |

区分「真的暂停了」和「本来就是暂停状态」靠接口返回码：`0` 表示暂停这个动作
真的执行了，`400803`（账号已经停止加速）表示状态没变。所以 `on_change` 模式下，
如果所有账户本来就已暂停，你不会收到任何打扰。

### 每个账户推给不同的人

账户可以配自己的 token，实现「谁的账号出结果就通知谁」：

```bash
PHONE_1=13800138000
PASSWORD_1=...
PUSHPLUS_TOKEN_1=账户1的token

PHONE_2=13900139000
PASSWORD_2=...
PUSHPLUS_TOKEN_2=账户2的token
```

没配 `PUSHPLUS_TOKEN_n` 的账户自动回落到全局的 `PUSHPLUS_TOKEN`。**两者都没有的
账户不会被推送**（但照样会正常暂停）。

**推送条数**由 `NOTIFY_GROUPING` 决定，本质是「账户按投递目标归组」：

| 值 | 行为 |
|----|------|
| `combined`（默认） | 发往**同一个 token** 的账户合成一条 |
| `per_account` | 一个账户一条推送（即使它们共用 token） |

所以：

- 所有账户共用一个 token → `combined` 就是一条汇总
- 每个账户各自的 token → `combined` 也是每账户一条（因为目标本来就不同）
- 混合场景 → 同 token 的合并，不同 token 的分开

两个开关独立，可以组合。**`on_change` 通常最好用**：只有真的从「运行中」变成
「已暂停」的账户才会通知你，本来就暂停的账户完全不打扰；出问题的账户则各自带
自己的失败原因。配合每账户独立 token，每个人只会收到自己账号的消息。

**失败一定会推送**，与 `NOTIFY_MODE` 无关——包括重试耗尽的情况，失败原因里会带上
重试次数，例如：

```
❌ 1 个账户中 1 个失败
失败原因：登录失败: 发送请求失败: HTTP 503（已重试 10 次）
```

`combined` 的正文长这样（多账户会逐条列出）：

```
2026-09-11 13:05:00

✅ 2 个账户全部处理成功

• 账户1 138****8000：已暂停
• 账户2 139****9000：已经是暂停状态
```

账户各自有 token（或 `per_account`）时，一个账户一条，标题带上账户标识：

```
标题：雷神加速器：账户2 139****9000 已暂停

2026-09-11 13:05:00

✅ 账户2 139****9000：已暂停
```

两点说明：

- **推送失败不会影响本次运行结果**——通知是锦上添花，发不出去只会在日志里记一行。
- PushPlus 的发送接口是**异步**的，返回 `code=200` 只代表服务端受理了请求，
  不代表消息已送达（官方文档原文如此）。日志里写的是「已提交推送」，不是「已送达」。

### ⚠️ 密码是以明文保存的

配置里填的是**明文密码**，程序每次运行时会按接口要求做一次 MD5 再发出去
（接口本身收的就是 MD5，且没有加盐）。

请留意：

- 密码会以明文落在 `.env` 文件、GitHub Secrets 或云函数环境变量里。
  一旦这些地方泄漏，泄漏的是可以直接拿去别处尝试的**真密码**。
- 云函数控制台的环境变量是**明文展示**的，官方文档也写明「请不要输入敏感信息」——
  建议开启「加密参数」（见下文方式一的说明）。
- **不要复用重要密码**，给这个账号用一个独立的密码。

---

## 🚀 方式一：华为云 FunctionGraph（推荐）

### 1. 打包部署包

```bash
python scripts/build_functiongraph_zip.py
```

产出 `dist/functiongraph.zip`，里面是 `index.py`、`runner.py`、`api/`、`config/`。
**零依赖，所以不需要制作依赖包**，也不用管 EulerOS 的二进制兼容问题。

### 2. 创建函数

在 FunctionGraph 控制台创建函数：

| 配置项 | 值 |
|--------|-----|
| 函数类型 | 事件函数 |
| 运行时 | Python 3.10 或 3.12（Python 2.7 已进入终止支持计划，别选） |
| 代码上传方式 | 上传 ZIP 文件 → 选 `dist/functiongraph.zip` |
| **函数入口** | `index.handler` |
| **执行超时时间** | **改成 120 秒**（默认只有 3 秒；默认 10 次重试最坏情况约 80 秒） |

> ZIP 解压后入口文件 `index.py` 必须位于**根目录**，这是 FunctionGraph 的硬性要求；
> 上面的打包脚本已经处理好了。

### 3. 配置环境变量

函数详情页 →「设置」→「环境变量」→「编辑环境变量」，按账户成组添加：

| 键 | 值 |
|----|-----|
| `PHONE_1` | 你的手机号 |
| `PASSWORD_1` | 账户密码（明文） |
| `PHONE_2` | 第二个账户的手机号 |
| `PASSWORD_2` | 第二个账户的密码（明文） |

推送相关（**全部可选**，留空则不推送 / 用默认值）：

| 键 | 默认 | 说明 |
|----|------|------|
| `PUSHPLUS_TOKEN` | — | PushPlus 全局 token；留空则不推送 |
| `PUSHPLUS_TOKEN_1`… | — | 各账户专属的推送 token，不填回落到全局那个 |
| `PUSHPLUS_TOPIC` | — | 群组编码，填了推给整个群组 |
| `PUSHPLUS_TEMPLATE` | `txt` | 消息模板（`txt` / `html` / `markdown` / `json`） |
| `NOTIFY_MODE` | `always` | 推送时机：`always` / `on_change` |
| `NOTIFY_GROUPING` | `combined` | 推送条数：`combined` / `per_account` |

其余可选变量：`COUNTRY_CODE`、`SRC_CHANNEL`、`API_LANG`、`RETRIES`、`SHOW_TOKEN`。

**批量录入**：控制台支持 JSON 格式编辑，点「使用 JSON 格式编辑」后一次粘贴全部配置，
不用在表单里一个个加。可以先在本地 `.env` 里维护，再用脚本生成这段 JSON：

```bash
python scripts/env_to_console_json.py
```

> **账户数量**：事件函数的环境变量只能按键读取、无法列举，所以代码按编号逐个探测，
> 默认探测到 `PHONE_50`。超过 50 个账户的话，在控制台加一个 `MAX_ACCOUNTS` 环境变量
> 调大即可。另外，如果平台把配置放在 `RUNTIME_USERDATA` 里下发，代码会优先直接枚举
> 它，那种情况下没有数量限制。

```json
{
    "PHONE_1": "13800138000",
    "PASSWORD_1": "你的密码",
    "PUSHPLUS_TOKEN_1": "账户1的推送token",
    "PHONE_2": "13900139000",
    "PASSWORD_2": "另一个密码",
    "PUSHPLUS_TOKEN_2": "账户2的推送token"
}
```

两点注意：

- **密钥建议开启「加密参数」**。环境变量在控制台是明文展示的，官方文档明确
  「请不要输入敏感信息」；勾选「加密参数」后值会以 `*` 显示。
  代码里读配置**优先走 `context.getUserData()`**（其次才回落 `os.environ`），
  所以开启加密后照常能读到——只读 `os.environ` 的写法在这里会失效。
- **所有环境变量的键+值总长上限 4096 字符**，按每组约 60 字符算，放几十个账户没问题。

> 另有「传输中加密」（AES/KMS）能力，但目前仅「拉美-圣保罗一」区域支持，
> 且 AES 需要在键名前加 `_encrypt_` 前缀——前缀会改变键名，本项目的配置读取
> 认不出加前缀的键，所以**不要**用这种方式，用上面的「加密参数」即可。

### 4. 配置定时触发器

函数详情页 →「触发器」→ 创建触发器 → 类型选 **定时触发器（TIMER）**。

cron 表达式是 **6 个字段**（比 Linux 的 5 字段多一个「秒」）：

```
秒  分  时  日  月  星期(可选)
```

每天北京时间凌晨 1 点：

```
0 0 1 * * *
```

中国站 region 的默认时区就是 `Asia/Shanghai`；想显式指定可以写：

```
CRON_TZ=Asia/Shanghai 0 0 1 * * *
```

也支持 `@every 30m`、`@every 2h30m` 这种写法。

### 5. 验证

在控制台「测试」页签直接调一次，或在「监控 → 日志」里看输出。
函数返回结构（`accounts` 里是逐账户的结果，只含打码手机号）：

```json
{
  "ok": false,
  "step": "login",
  "code": 400099,
  "message": "账号或密码错误",
  "accounts": [
    {"label": "账户1 138****8000", "ok": true,  "step": "pause", "code": 0,      "message": "操作成功"},
    {"label": "账户2 139****9000", "ok": false, "step": "login", "code": 400099, "message": "账号或密码错误"}
  ]
}
```

`ok` 为 `false` 时，`step` 指明**第一个**失败的账户卡在哪一步
（`config` / `login` / `pause`），并会额外打一条 error 级别日志，
方便在 LTS 里按级别配告警。上层字段取的是第一个失败账户的结果，
逐账户细节看 `accounts`。

---

## 🔄 方式二：GitHub Actions

1. **Fork 本项目**
2. **配置 Secrets**：仓库 → Settings → Secrets and variables → Actions → New repository secret

   **账户（至少配一组）**
   - `PHONE_1` = 第一个账户的手机号
   - `PASSWORD_1` = 第一个账户的密码（明文）
   - 有多个账户就继续加 `PHONE_2` / `PASSWORD_2` …

   **PushPlus 推送（可选，全部留空则不推送）**
   - `PUSHPLUS_TOKEN` = 全局推送 token
   - `PUSHPLUS_TOKEN_1` / `PUSHPLUS_TOKEN_2` = 各账户专属 token（可选）
   - `PUSHPLUS_TOPIC` = 群组编码（可选）
   - `PUSHPLUS_TEMPLATE` = 消息模板，留空用 `txt`
   - `NOTIFY_MODE` = 推送时机，留空用 `always`
   - `NOTIFY_GROUPING` = 推送条数，留空用 `combined`

   > ⚠️ **加变量必须改两处。** GitHub Actions 不会自动把 Secrets 传进程序——
   > 每个变量都要在 `.github/workflows/auto-pause.yml` 的 `env:` 里显式映射一行。
   > **只在 Secrets 里加了、没在工作流里映射，程序是读不到的。**
   > 工作流里已经把上面这些变量都列好了，改动时照着补即可。

3. **启用工作流**：进入 Actions 标签页，选择 "Auto Pause Leishen"，
   点 "Run workflow" 手动跑一次验证
4. 之后会在每天北京时间凌晨 1 点自动运行

工作流文件在 `.github/workflows/auto-pause.yml`，改 cron 即可调整时间
（注意 GitHub 的 cron 用 **UTC**，北京时间凌晨 1 点 = `0 17 * * *`）。

因为零依赖，工作流里连 `pip install` 都不需要。

---

## 💻 方式三：本地运行（带令牌缓存）

```bash
# 1. 配置
cp .env.example .env
# 编辑 .env，填入 PHONE 与 PASSWORD

# 2. 直接运行（无需安装任何依赖）
python main.py
```

> 本地开发还可以跑测试（`tests/` 目录不随仓库推送）：
> `python -m unittest discover -s tests -t .`

环境变量与 `.env` 同时存在时，**环境变量优先**——但**凭据按账户整组取来源，不逐键混合**：

- 某个账户的 `PHONE` 出现在环境变量里，这一组就整组用环境变量（`PASSWORD` 也得在环境变量里）
- 否则这一组整组用 `.env`
- 手机号是账户的标识，所以环境变量里**只有 `PASSWORD` 而没有对应 `PHONE`** 时不参与，
  免得 shell 里一个无关的 `PASSWORD` 劫持掉 `.env` 里的整个账户

之所以这么规定：逐键混合会配出「`.env` 里的手机号 + 环境变量里残留的密码」这种组合，
表现成一个查不出原因的「账号或密码错误」，而 `.env` 明明是对的。

其余配置项（`RETRIES`、`COUNTRY_CODE` 等）仍然逐键让环境变量优先。

打算部署到 FunctionGraph 的话，本地 `.env` 可以同时当作控制台配置的来源：

```bash
python scripts/env_to_console_json.py    # 输出可粘贴到控制台 JSON 编辑框的内容
```

### 定时运行（可选）

默认 `python main.py` **只运行一次就退出**。想让它常驻、到点才跑，在 `.env` 里设
`RUN_CRON`：

```bash
RUN_CRON=0 1 * * *
```

标准 **5 段**写法（分 时 日 月 星期），例：

| 表达式 | 含义 |
|--------|------|
| `0 1 * * *` | 每天凌晨 1 点 |
| `*/30 * * * *` | 每 30 分钟 |
| `0 9 * * 1-5` | 工作日 9 点 |
| `30 4 1,15 * *` | 每月 1 号和 15 号 04:30 |
| `0 0 1 * *` | 每月 1 号零点 |

每段支持 `*`、`N`、`N-M`、`N,M`、`*/N`、`N-M/S`；月与星期也认 `JAN`-`DEC` /
`SUN`-`SAT`；星期里 `0` 和 `7` 都表示周日。日与星期**同时限定**时按 cron 惯例取
「或」（`0 0 1 * 1` = 每月 1 号**或**每周一）。

实际运行长这样：

```
⏰定时模式已开启（0 1 * * *），按 Ctrl+C 退出
[2026-09-11 22:09:44] 😴下次运行：2026-09-12 01:00:00（2 小时 50 分钟后）
[2026-09-12 01:00:00] ⌛️开始运行
[2026-09-12 01:00:00] 🔑复用本地缓存的令牌（有效期至 2026-09-18 12:50:10）
[2026-09-12 01:00:00] 👌已经暂停: 400803 - 账号已经停止加速，请不要重复操作
[2026-09-12 01:00:00] 😴下次运行：2026-09-13 01:00:00（1 天后）
```

几点说明：

- **到点才跑**，启动时不会立刻执行一次——你配的是"凌晨 1 点"，那就等凌晨 1 点
- **每行日志都带时间戳**，这个进程会跑很久，翻日志时好定位
- 每轮的令牌都走缓存，不会反复登录
- 单轮里出现意外异常**不会让进程退出**，只记一行然后等下一次
- 长睡眠切成 60 秒一段，所以系统时钟被改、机器休眠唤醒后仍能对准，`Ctrl+C`
  也能及时响应
- 时区跟随**本机时区**（不是 UTC）
- 云函数与 GitHub Actions **不看这个配置**——它们各有自己的触发机制
  （FunctionGraph 的定时触发器用的也是 cron，不过是 **6 段含秒**的写法）

#### 失败后自动重试

定时模式下，某一轮失败时**不会干等到明天的 cron 时刻**，而是按间隔重试：

```
[02:43:00] ❌暂停失败: 500 - 服务异常
[02:43:00] 📮已提交推送 1/1 条          ← 第 1 次失败立刻推
[02:43:00] 😴失败重试：02:43:02（2 秒后）
[02:43:02] ❌暂停失败: 500 - 服务异常
[02:43:02] ⏳已连续失败 2 次，本次不推送（每 3 次才推一次）
[02:43:04] ❌暂停失败: 500 - 服务异常
[02:43:04] 📮已提交推送 1/1 条          ← 第 3 次再推
```

- `FAIL_RETRY_INTERVAL`（默认 `1h`）：失败后隔多久重试。填 `0` 关掉重试
- `FAIL_NOTIFY_EVERY`（默认 `5`）：第 1 次失败**立刻推**，之后每 N 次再推一次，
  免得每小时刷屏
- 重试**不会越过下一个 cron 时刻**：cron 每天一次时最多重试 24 次；cron 本身就是
  每 5 分钟一次时，等 cron 就行，重试间隔设多大都无所谓
- 重试成功会打一行「✔️重试成功（此前已失败 N 次）」，然后回到正常 cron 节奏

### 令牌缓存

本地运行时，登录拿到的令牌会存到项目根目录的 `.token_cache.json`，下次直接复用：

```
⌛️开始运行
🔑复用本地缓存的令牌（有效期至 2026-10-01 00:00:00）    ← 不再调用登录接口
0:操作成功
✔️暂停成功
```

只有**本地运行**会缓存。云函数的 `/tmp` 是单实例临时盘、不共享也不保证持久，
GitHub Actions 的 runner 每次都是全新环境——两者存了都带不到下一次，
所以云函数入口根本不碰这个文件，Actions 用 `--no-cache` 关掉。

```bash
python main.py --no-cache     # 每次都重新登录，也不写缓存文件
```

几点说明：

- **令牌失效会自动重建**：暂停接口返回 `400006`（令牌过期）时，程序会丢弃缓存、
  重新登录、再重试一次，不需要你手工删文件。距过期不足 1 小时也会主动重新登录。
- **`.token_cache.json` 里是凭据**，已在 `.gitignore` 里排除，别提交、别分享。
  POSIX 上会把文件权限设成 `600`。
- 文件损坏或被手改坏时会退化成一没有缓存，只影响本次是否复用，不会让程序报错。
- 想强制重新登录，直接删掉 `.token_cache.json` 即可。

---

## 🐳 方式四：Docker（常驻定时）

适合让它在 NAS、小主机或云服务器上一直跑着，到点自己暂停。**容器里跑的是定时
模式**，由 `docker-compose.yml` 的 `RUN_CRON` 控制。

零第三方依赖，所以镜像里没有 `pip install` 这一步，构建很快。

### 1. 准备配置

```bash
cp .env.example .env
# 编辑 .env，填入 PHONE_1 与 PASSWORD_1（以及可选推送配置）
```

`.env` 通过 compose 的 `env_file` 注入容器，**不会被打进镜像**（`.dockerignore`
里排除了，否则密码会留在镜像层里）。

### 2. 改运行时间

编辑 `docker-compose.yml` 里的 `RUN_CRON`：

```yaml
    environment:
      RUN_CRON: "0 1 * * *"     # 每天凌晨 1 点
      TZ: Asia/Shanghai
```

**`TZ` 必须设对。** cron 是按「本机时区」算的，而容器默认是 UTC——不设的话
`0 1 * * *` 会跑在北京时间早上 9 点。

启动日志里会打出生效的时区，一眼就能确认有没有设对：

```
⏰定时模式已开启（0 1 * * *，本机时区 CST+0800），按 Ctrl+C 退出
```

如果打出的是 `UTC+0000`，说明 `TZ` 没生效（镜像里少了 tzdata，或 compose 里
`TZ` 写错了），这时 `0 1 * * *` 会按 UTC 触发。

### 3. 启动

```bash
docker compose up -d --build
docker compose logs -f          # 看日志
docker compose down             # 停止
```

跑起来长这样：

```
⏰定时模式已开启（0 1 * * *），按 Ctrl+C 退出
[2026-09-11 22:09:44] 😴下次运行：2026-09-12 01:00:00（2 小时 50 分钟后）
[2026-09-12 01:00:00] ⌛️开始运行
[2026-09-12 01:00:00] 🔑复用本地缓存的令牌（有效期至 2026-09-18 12:50:10）
[2026-09-12 01:00:00] 👌已经暂停: 400803 - 账号已经停止加速，请不要重复操作
```

### 令牌缓存在容器里怎么存

`TOKEN_CACHE_FILE` 指向 `/data/.token_cache.json`，`/data` 挂在具名卷 `token-cache`
上。所以 **`docker compose up --build` 重建容器后不用重新登录**。

想强制重新登录：

```bash
docker compose down -v          # -v 会一并删掉卷（也就是删掉缓存）
```

### 构建太慢？（国内网络）

慢通常卡在两处，**分别用不同办法治**：

**1. 拉基础镜像慢** —— 这是 Docker **守护进程**的设置，Dockerfile 管不了。

Docker Desktop：`Settings` → `Docker Engine`，在 JSON 里加：

```json
{
  "registry-mirrors": ["https://镜像加速地址"]
}
```

保存后重启 Docker。镜像加速站时有失效，用之前先确认当前可用。

**2. `apt-get` 慢** —— 这个在 Dockerfile 里，已经默认换成阿里云源了：

```dockerfile
ARG APT_MIRROR=mirrors.aliyun.com
```

想用回官方源：

```bash
docker compose build --build-arg APT_MIRROR=deb.debian.org
```

装 tzdata 那一层的存在意义只是让 `TZ` 生效。如果你能接受不带 tzdata 的精简镜像，
把 `TZ` 改成 POSIX 写法（`TZ=CST-8` 就表示 UTC+8）也能工作，那样可以整层删掉——
代价是这写法不直观，且不适用于有夏令时的时区。

几点说明：

- **`.env` 不要用 bind mount 挂进容器**——`env_file` 已经把它注入了，再挂一次
  反而会把宿主机目录暴露给容器
- 容器用非 root 用户跑；具名卷首次挂载会继承镜像里 `/data` 的属主，所以不用手动
  `chown`
- `logging` 里给日志加了 10MB × 3 的上限，常驻进程不会把磁盘写满
- 想一次性跑完就退出（不进定时模式），把 `RUN_CRON` 那行删掉即可

---

## ☁️ 方式五：Cloudflare Workers

跑在 Cloudflare 的免费额度上，由 **Cron Triggers** 定时触发。

> **这一份是用 JavaScript 重写的**，不复用 Python 代码。原因有两个：
> Workers 是 V8 运行时（Python 支持仍是 beta，且**不支持 stdlib 的
> `urllib.request` / `http.client`**，得换成 `requests` 或 JS 的 `fetch`）；
> 而 Workers 原生语言下 cron、KV 都是一等公民。签名算法用**同一套黄金向量**
> 做过差分验证，两边结果逐字节一致。

### 1. 安装并登录

```bash
cd cloudflare
npm install
npx wrangler login
```

### 2. 配置账户（用 secret，别写进文件）

```bash
npx wrangler secret put PHONE_1
npx wrangler secret put PASSWORD_1
# 多个账户继续加 PHONE_2 / PASSWORD_2 …
# 推送（可选）
npx wrangler secret put PUSHPLUS_TOKEN
```

非敏感项写在 `cloudflare/wrangler.toml` 的 `[vars]` 里，比如 `NOTIFY_MODE`。

### 3. ⚠️ 改运行时间前先换算成 UTC

**Cron Triggers 按 UTC 执行**（官方文档原文：Cron Triggers execute on UTC time）。
`wrangler.toml` 里的默认值是：

```toml
[triggers]
crons = ["0 17 * * *"]
```

`0 17 * * *` = UTC 17:00 = **北京时间次日凌晨 1 点**。想换成别的时刻，先换算再填，
别直接照抄本地时间——照抄会把时间跑偏 8 小时，而且不会有任何报错。

### 4. 部署

```bash
npx wrangler deploy
```

部署后可以直接用浏览器访问 Worker 的地址**手动触发一次**，会返回逐账户的结果：

```json
{"ok": true, "step": "done", "code": 0, "message": "",
 "accounts": [{"label": "账户1 138****8000", "ok": true, "step": "pause", "code": 0}],
 "logs": ["🔑[账户1 138****8000] 登录获取令牌中…", "..."]}
```

### 5. 令牌缓存（可选，省掉每次都登录）

Worker 没有持久文件系统，缓存走 KV：

```bash
npx wrangler kv namespace create TOKEN_CACHE
```

把返回的 id 填进 `wrangler.toml` 的 `[[kv_namespaces]]`（文件里有注释示例）。
**不绑也能跑**，只是每次运行都重新登录。

> KV 里存的是等同凭据的东西，别把命名空间设成公开可读。

---

## 🔐 登录接口说明

自动登录调用 `POST https://webapi.leigod.com/api/auth/login/v1`，请求体：

```json
{
  "username": "13800138000",
  "password": "<密码的 MD5>",
  "user_type": "0",
  "src_channel": "guanwang",
  "code": "",
  "country_code": "86",
  "lang": "zh_CN",
  "os_type": 4,
  "ts": "1757000000",
  "sign": "<签名>"
}
```

`sign` 算法（与官网前端 `chunk-common.js` 中的实现逐字节一致）：
把除 `sign` 外的所有参数按 key 升序排序，拼成 `k=v&k=v...`（值不做 URL 编码），
末尾追加 `&key=<内置密钥>`，取 MD5 小写十六进制。

其中 `user_type`、`code` 是前端对象的默认字段，业务上看似没用，但**参与签名**，
漏掉会导致服务端签名校验失败。

登录成功后从 `data.login_info.account_token` 取出令牌，有效期见
`data.login_info.expiry_time`。

### 暂停接口

`POST /api/user/pause`，请求体 `{"account_token": "...", "lang": "zh_CN"}`。

- `0`：操作成功
- `400803`：账号已经停止加速，请不要重复操作（视为成功）

---

## ❓ 常见问题

**Q: 登录时提示「网络异常」？**
A: 这是官网登录接口的老毛病，手动多点几次也能成功。程序默认对网络异常和 5xx
做重试：默认 10 次，每次间隔 1~3 秒随机。

下面这几类都会重试，它们都属于「请求没正常走完」，而不是业务上的拒绝：

- 网络异常、连接超时、连接被拒
- HTTP 5xx
- **返回 200 但响应体为空**（网关抖动）
- **返回 4xx 但响应体是 HTML 拦截页**（WAF / CDN / 代理拦的）

最后一类是实际踩过的：凌晨的定时任务收到 `403 + <!DOCTYPE html>...`，
请求根本没到业务接口。所以**判断依据是「响应体能不能解析成 JSON」**，
而不是单纯看状态码——能解析出业务错误码的 4xx（比如密码错）才是确定性错误，
那种不重试，直接报出来。

网络不稳可以调大 `RETRIES`。

**但如果错误信息里带了 `HTTP 4xx` 状态码、而且重试 10 次全被挡**，那就不是抖动——
是某个中间设备在**稳定拦截**（WAF 的机器人规则），调 `RETRIES` 没用。典型信号是
`HTTP 418`（各类 WAF 爱用这个码标记「识别为自动化请求」）+ 一个 HTML 拦截页。

这种情况的常见诱因是**请求指纹**：Python 的 `urllib` 在没给 `User-Agent` 时会自带
`Python-urllib/3.x`，那是个极显眼的「这是脚本」特征。所以程序默认已经带上网页客户端
同款的请求头（`User-Agent` / `Accept` / `Accept-Language` / `Origin` / `Referer`），
定义在 `api/client.py` 的 `DEFAULT_HEADERS`（Cloudflare 那份在 `cloudflare/src/api.js`）。
**要是哪天又被拦，先去改这里的 `User-Agent`。**

**Q: 为什么不用官方内置的 `requests`？**
A: FunctionGraph 的 Python 运行时确实内置了 `requests`，但版本是 2015 年的 2.7.0，
且官方没有按 Python 版本细分。与其赌它能不能用，不如用标准库 `urllib` 彻底绕开——
顺带还免掉了「必须在 EulerOS 上打依赖包」这一步。

**Q: 怎么加/删账户？**
A: 加账户就在配置里追加一组 `PHONE_n` / `PASSWORD_n`（GitHub Actions 的话
还要在 Secrets 里加同名条目、并在工作流里映射成环境变量）。删账户把那一组清空即可——
整组留空的会被忽略，不用改代码。

**Q: 某个账户密码错了会怎样？**
A: 只有那个账户失败，其他账户照常处理。整体退出码为 1，日志里会写明
「N 个账户中 M 个失败」，云函数返回值里每个账户的结果都能单独看到。

**Q: 日志里怎么看不到完整手机号？**
A: 故意的。GitHub Actions 的日志和云函数的 LTS 日志都可能被更广的人看到，
所以日志和返回值里只出现打码后的号码（`138****8000`）。

**Q: 本地运行会频繁登录吗？**
A: 不会。登录一次后令牌会存进 `.token_cache.json`，之后每次运行直接复用，日志里会写
「复用本地缓存的令牌」。只有缓存缺失、距过期不足 1 小时，或者接口返回 `400006`
（令牌过期）时才会重新登录。云函数和 GitHub Actions 不缓存，每次都会登录。

**Q: 怎么强制重新登录？**
A: 删掉项目根目录的 `.token_cache.json` 再跑一次即可。

**Q: `.token_cache.json` 里是什么？能提交吗？**
A: 里面是打码前的账号令牌，**等同凭据**，已在 `.gitignore` 里排除。
别提交、别分享，也不建议放进任何同步盘或云备份。

**Q: 云函数返回 `ok: false` 怎么排查？**
A: 看 `accounts` 数组里哪个账户 `ok` 为 `false`，再看它的 `step`：
`config` 是环境变量没配对（`PHONE_n` / `PASSWORD_n` 是否成对）；
`login` 是账号密码或签名问题（看 `code` 和 `message`）；`pause` 是令牌或接口问题。

**Q: 函数执行超时？**
A: 默认超时只有 3 秒。登录接口本身偶发慢，加上重试很容易超，请在「常规设置」里
把执行超时时间调到 120 秒以上——默认 10 次重试的最坏耗时约 80 秒；
不想加超时的话，把 `RETRIES` 调到 5 也能显著缩短。

**Q: 支持哪些操作？**
A: 目前只做「暂停」。开始加速等其他操作可以基于 `api/client.py` 扩展。

## 许可证

MIT，详见 LICENSE。
## 特别感谢

<img src="https://cdn3.ldstatic.com/original/3X/9/7/97ed5d6d97f4c7f3dc0670d097bf457527c375f5.png" alt="linuxDoLogo" width="150" />

感谢 [Linux DO 社区](https://linux.do/)提供交流和支持。

本项目参考 [hobk/leishen-auto](https://github.com/hobk/leishen-auto) 感谢作者付出
