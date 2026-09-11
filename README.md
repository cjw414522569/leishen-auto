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
└── .github/workflows/auto-pause.yml # GitHub Actions 定时任务
```

> 单元测试只保留在本地，没有随仓库推送。

---

## 🧭 三种运行方式

核心逻辑（`runner.py` + `api/` + `config/`）三个入口共用，区别只在**配置从哪来**和
**要不要缓存令牌**：

| | 本地命令行 | 华为云 FunctionGraph | GitHub Actions |
|---|---|---|---|
| 入口 | `main.py` | `index.py`（`index.handler`） | `main.py --no-cache` |
| 配置来源 | `.env` / 环境变量 | 控制台环境变量 | 仓库 Secrets |
| 令牌缓存 | ✅ 存本地 `.token_cache.json` | ❌ 无持久盘 | ❌ runner 每次全新 |
| 触发方式 | 手动 / 系统计划任务 | 定时触发器 | cron |

**只有本地运行会缓存令牌**——云函数和 Actions 的存储都是一次性的，存了也带不到
下一次。下面分别说明。

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
| `PUSHPLUS_TOKEN` | — | PushPlus 的 token；**留空则不推送** |
| `PUSHPLUS_TOPIC` | — | 群组编码，填了推给整个群组，留空只推给自己 |
| `PUSHPLUS_TEMPLATE` | `txt` | 消息模板（`txt` / `html` / `markdown` / `json`） |
| `NOTIFY_MODE` | `always` | 推送时机，见下文 |
| `NOTIFY_GROUPING` | `combined` | 推送条数：`combined` 汇总一条 / `per_account` 一账户一条 |

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

**推送条数**由 `NOTIFY_GROUPING` 决定：

| 值 | 行为 |
|----|------|
| `combined`（默认） | 所有账户合成一条推送 |
| `per_account` | 一个账户一条推送 |

两个开关是独立的，可以组合。**`on_change` + `per_account` 通常最好用**：只有真的
从「运行中」变成「已暂停」的账户才会单独通知你，本来就暂停的账户完全不打扰；
出问题的账户则各自带自己的失败原因。

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

`per_account` 则是一个账户一条，标题带上账户标识：

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
    "PASSWORD_1": "2ab96390c7dbe3439de74d0c9b0b1767",
    "PHONE_2": "13900139000",
    "PASSWORD_2": "c56a0664e6b1041c5d819e6531335813"
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
   - `PHONE_1` = 第一个账户的手机号
   - `PASSWORD_1` = 第一个账户的密码（明文）
   - 有多个账户就继续加 `PHONE_2` / `PASSWORD_2` …（加完记得在 workflow 的 `env:` 里也补一行）
   - 工作流里需要把用到的变量逐个传给程序（见 `.github/workflows/auto-pause.yml`）
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
注意它有时表现为**返回 200 但响应体为空**（网关抖动），这种也会重试。
网络不稳可以调大 `RETRIES`。

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
