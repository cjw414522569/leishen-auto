"""本地网页：短信登录换取令牌。

密码登录接口被 CloudWAF 封停后，这是重新拿到令牌的入口。用标准库
``http.server`` 实现，仍然零第三方依赖。

安全设计（都不是可选项）：

1. **URL 带随机令牌**。页面只在 ``/<随机串>/`` 下提供，其余路径一律 404。
   令牌每次启动重新生成、只打印在控制台 —— 扫端口的人拿到的是 404，
   而不是一个「给任意号码发短信」的入口。
2. **默认只绑 127.0.0.1**。绑 0.0.0.0 意味着同网段任何人都能打开这个页面、
   用你的账号登录并拿走令牌。要用 ``--host`` 显式指定才会放开，届时会打印警告。
3. **只允许给配置里的号码发验证码** —— 否则这就成了一个能给任意号码发短信的接口。
4. **校验 Host 与 Origin**：Host 必须是本机回环名（防 DNS rebinding），
   带了 Origin 就必须同源（防你浏览器里的恶意网页跨站打过来）。
5. **POST 必须声明 application/json**：堵掉用 ``text/plain`` 绕过 CORS 预检的
   简单请求（那种请求浏览器不发预检，能直接打进来）。
6. 每个号码 60 秒内只能发一次，避免误点刷短信。
7. 不提供任何文件读取，只吐一个固定的页面。

注意 **127.0.0.1 本身已经挡住了远程扫描** —— 上面第 1、4、5 条防的是另外两类：
你浏览器里的恶意网页，以及本机上的其他进程。
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping

from api import APIError, SmsLoginError
from config import ConfigError, load_cache_file, load_config
from sms_login import LoginFlowError, request_code, verify_code
from token_cache import TokenCache

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
SEND_COOLDOWN_SECONDS = 60
# URL 令牌的字节数；token_urlsafe(16) 得到 22 个 URL 安全字符
TOKEN_BYTES = 16
# Host 头必须落在这些名字里，否则拒绝（防 DNS rebinding）
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})
# 表单里最多展示多少个账户（配置里账户很多时不至于把页面撑爆）
MAX_LISTED_ACCOUNTS = 50


class _State:
    """服务端持有的状态：待校验的 smscode_key 与发送冷却。"""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.pending: dict[str, str] = {}  # phone -> smscode_key
        self.last_sent: dict[str, float] = {}  # phone -> 单调时钟

    def cooldown_left(self, phone: str) -> int:
        with self.lock:
            sent_at = self.last_sent.get(phone)
        if sent_at is None:
            return 0
        left = SEND_COOLDOWN_SECONDS - (time.monotonic() - sent_at)
        return int(left) + 1 if left > 0 else 0

    def remember_send(self, phone: str, key: str) -> None:
        with self.lock:
            self.pending[phone] = key
            self.last_sent[phone] = time.monotonic()

    def take_key(self, phone: str) -> str:
        with self.lock:
            return self.pending.get(phone, "")

    def forget(self, phone: str) -> None:
        with self.lock:
            self.pending.pop(phone, None)


class _Handler(BaseHTTPRequestHandler):
    server_version = "leishen-auto"
    state: _State  # 由 create_server() 注入
    token = ""  # URL 令牌，由 create_server() 注入
    allowed_hosts: frozenset = LOOPBACK_HOSTS
    allowed_origins: frozenset = frozenset()
    logger = print

    # ---------- 基础设施 ----------

    def log_message(self, fmt: str, *args: Any) -> None:
        self.logger(f"[web] {self.address_string()} {fmt % args}")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # 本地页面，不缓存也不允许被别处嵌走
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: Mapping[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if length <= 0:
            return {}
        try:
            parsed = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _config(self):
        """每次都重新读配置 —— 改了 .env 刷新页面就能生效。"""
        return load_config()

    # ---------- 安全校验 ----------

    def _request_ok(self) -> bool:
        """Host 与 Origin 校验。这两个头浏览器会强制带上，脚本伪造不了。"""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip().lower()
        if host and host not in self.allowed_hosts:
            # DNS rebinding：攻击者的域名解析到 127.0.0.1，Host 头就是他的域名
            return False

        origin = (self.headers.get("Origin") or "").strip()
        if origin and origin not in self.allowed_origins:
            # 你浏览器里的恶意网页跨站打过来时会带 Origin
            return False
        return True

    def _json_content_type(self) -> bool:
        """POST 必须声明 application/json。

        ``text/plain`` 属于 CORS 简单请求，浏览器不发预检就能打过来 ——
        所以不能只靠「浏览器不会让我发跨站 JSON」这一点。
        """
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        return content_type == "application/json"

    def _token_ok(self) -> bool:
        """路径第一段必须等于本次启动的随机令牌。"""
        parts = self.path.split("?", 1)[0].split("/")
        # "/<token>/..." -> ['', '<token>', ...]
        return len(parts) > 1 and parts[1] == self.token

    def _route(self) -> str:
        """去掉令牌前缀后的路径：``/<token>/api/state`` -> ``/api/state``。"""
        parts = self.path.split("?", 1)[0].split("/")
        return "/" + "/".join(parts[2:])

    # ---------- 路由 ----------

    def _reject(self) -> None:
        """统一用 404 回绝 —— 不给探测者任何「这里有个服务」的信号。"""
        self._json(404, {"ok": False, "message": "没有这个路径"})

    def _guard(self) -> bool:
        if not self._request_ok() or not self._token_ok():
            self._reject()
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802
        if not self._guard():
            return
        path = self._route()
        if path in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/api/state":
            self._state()
            return
        self._reject()

    def do_POST(self) -> None:  # noqa: N802
        if not self._guard():
            return
        if not self._json_content_type():
            self._reject()
            return
        path = self._route()
        if path == "/api/sms/send":
            self._send_code()
            return
        if path == "/api/sms/verify":
            self._verify_code()
            return
        self._reject()

    # ---------- 处理 ----------

    def _state(self) -> None:
        try:
            cfg = self._config()
        except ConfigError as exc:
            self._json(200, {"ok": True, "accounts": [], "config_error": str(exc)})
            return
        accounts = [
            {"phone": a.phone, "label": a.label} for a in cfg.accounts[:MAX_LISTED_ACCOUNTS]
        ]
        self._json(200, {"ok": True, "accounts": accounts, "config_error": None})

    def _send_code(self) -> None:
        body = self._body()
        phone = str(body.get("phone") or "").strip()

        try:
            cfg = self._config()
        except ConfigError as exc:
            self._json(400, {"ok": False, "message": str(exc)})
            return

        left = self.state.cooldown_left(phone)
        if left:
            self._json(429, {"ok": False, "message": f"请等 {left} 秒后再试"})
            return

        try:
            info = request_code(cfg, phone, log=self.logger)
        except LoginFlowError as exc:
            self._json(400, {"ok": False, "message": str(exc)})
            return
        except SmsLoginError as exc:
            self._json(400, {"ok": False, "message": f"{exc.code} - {exc.msg}"})
            return
        except APIError as exc:
            self._json(502, {"ok": False, "message": f"发送失败: {exc}"})
            return

        self.state.remember_send(phone, info.smscode_key)
        self._json(
            200,
            {
                "ok": True,
                "message": "验证码已下发，请查看手机短信",
                "cooldown": SEND_COOLDOWN_SECONDS,
                # bind_status 5 表示正常绑定；不是 5 说明账号侧有异常，值得提示
                "bind_status": info.bind_status,
            },
        )

    def _verify_code(self) -> None:
        body = self._body()
        phone = str(body.get("phone") or "").strip()
        smscode = str(body.get("smscode") or "").strip()

        try:
            cfg = self._config()
        except ConfigError as exc:
            self._json(400, {"ok": False, "message": str(exc)})
            return

        key = self.state.take_key(phone)
        if not key:
            self._json(400, {"ok": False, "message": "请先发送验证码"})
            return

        try:
            # key 从服务端取，不信客户端传来的 —— 减少可篡改的面
            info = verify_code(
                cfg,
                phone,
                key,
                smscode,
                cache=TokenCache(load_cache_file() or None),
                log=self.logger,
            )
        except LoginFlowError as exc:
            self._json(400, {"ok": False, "message": str(exc)})
            return
        except SmsLoginError as exc:
            self._json(400, {"ok": False, "message": f"{exc.code} - {exc.msg}"})
            return
        except APIError as exc:
            self._json(502, {"ok": False, "message": f"登录失败: {exc}"})
            return

        self.state.forget(phone)
        self._json(
            200,
            {"ok": True, "message": "登录成功，令牌已保存", "expiry_time": info.expiry_time},
        )


def create_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    log=print,
    token: str | None = None,
) -> ThreadingHTTPServer:
    """建好服务但不启动 —— 便于测试传 port=0 拿随机端口、传固定令牌。

    令牌默认每次启动随机生成；只有能读到控制台输出的人才知道完整地址。
    """
    auth_token = token or secrets.token_urlsafe(TOKEN_BYTES)

    allowed_hosts = set(LOOPBACK_HOSTS)
    if host not in ("0.0.0.0", "::", ""):
        allowed_hosts.add(host)

    handler = type(
        "Handler",
        (_Handler,),
        {
            "state": _State(),
            "logger": log,
            "token": auth_token,
            "allowed_hosts": frozenset(allowed_hosts),
        },
    )
    httpd = ThreadingHTTPServer((host, port), handler)
    # 绑定之后才知道真实端口（port=0 时由系统分配），
    # 所以同源白名单在这里补算 —— 否则空 Origin 能过、带 Origin 的一律被拒。
    real_port = httpd.server_address[1]
    httpd.RequestHandlerClass.allowed_origins = frozenset(
        f"http://{name}:{real_port}" for name in allowed_hosts
    )
    # 暴露给调用方：serve() 用来打印地址，测试用来拼 URL
    httpd.auth_token = auth_token
    return httpd


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, log=print) -> int:
    """启动本地网页服务；按 Ctrl+C 退出。"""
    httpd = create_server(host, port, log)

    shown = "127.0.0.1" if host in ("127.0.0.1", "localhost") else host
    port = httpd.server_address[1]
    log(f"🌐短信登录页面：http://{shown}:{port}/{httpd.auth_token}/")
    log("   地址里的随机串是本次启动生成的：别发给别人、也别截图外传")
    log("   只有这一个地址能打开页面，其它路径一律 404")
    if host not in ("127.0.0.1", "localhost"):
        log("⚠️注意：服务绑在非本机地址上，同网段的任何人都能打开这个页面、")
        log("   用你的账号登录并拿走令牌。仅在你清楚风险时才这么用。")
    log("   按 Ctrl+C 退出")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("\n👋已停止")
    finally:
        httpd.server_close()
    return 0


PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>雷神加速器 · 短信登录</title>
<style>
  :root {
    --bg: #f4f5f7; --card: #fff; --text: #1a1d21; --muted: #6b7280;
    --line: #e3e6ea; --accent: #2563eb; --ok: #15803d; --err: #b91c1c;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #14161a; --card: #1d2025; --text: #e8eaed; --muted: #9aa1ab;
      --line: #2e3238; --accent: #5b8def; --ok: #4ade80; --err: #f87171;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 24px 16px; background: var(--bg); color: var(--text);
    font: 15px/1.6 -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
    display: flex; justify-content: center;
  }
  .card {
    background: var(--card); border: 1px solid var(--line); border-radius: 12px;
    padding: 24px; width: 100%; max-width: 420px; align-self: flex-start;
    box-shadow: 0 1px 3px rgba(0,0,0,.06);
  }
  h1 { font-size: 19px; margin: 0 0 6px; }
  .sub { color: var(--muted); font-size: 13px; margin: 0 0 20px; }
  label { display: block; font-size: 13px; color: var(--muted); margin: 16px 0 6px; }
  select, input {
    width: 100%; padding: 11px 12px; font-size: 15px; color: var(--text);
    background: var(--bg); border: 1px solid var(--line); border-radius: 8px;
    font-family: inherit;
  }
  input { letter-spacing: .18em; }
  input:focus, select:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
  .row { display: flex; gap: 8px; }
  .row select, .row input { flex: 1; }
  button {
    padding: 11px 16px; font-size: 15px; font-family: inherit; font-weight: 500;
    border: 1px solid var(--line); border-radius: 8px; background: var(--card);
    color: var(--text); cursor: pointer; white-space: nowrap;
  }
  button:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
  button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
  button.primary:hover:not(:disabled) { opacity: .9; color: #fff; }
  button:disabled { opacity: .5; cursor: not-allowed; }
  .full { width: 100%; margin-top: 20px; }
  #msg { margin: 16px 0 0; font-size: 13px; min-height: 1.6em; white-space: pre-wrap; }
  #msg.ok { color: var(--ok); }
  #msg.err { color: var(--err); }
  .hint { color: var(--muted); font-size: 12px; margin-top: 18px;
          border-top: 1px solid var(--line); padding-top: 14px; }
</style>
</head>
<body>
<main class="card">
  <h1>雷神加速器 · 短信登录</h1>

  <label for="phone">账户</label>
  <div class="row">
    <select id="phone"></select>
    <button id="send" type="button">发送验证码</button>
  </div>

  <label for="code">验证码</label>
  <input id="code" inputmode="numeric" autocomplete="one-time-code"
         maxlength="6" placeholder="手机收到的 6 位数字">

  <button id="login" class="primary full" type="button">登录并保存令牌</button>

  <p id="msg"></p>

  <p class="hint">
    登录成功后令牌会写进本地缓存，定时任务下一次运行就会直接复用它。
    每天凌晨自动暂停时不会再尝试密码登录。
  </p>
</main>

<script>
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var phoneEl = $("phone"), codeEl = $("code"), sendBtn = $("send"),
      loginBtn = $("login"), msgEl = $("msg");
  var cooldownTimer = null;

  // 页面挂在 /<随机令牌>/ 下，所以接口地址必须从当前路径推导，
  // 不能写成 "/api/..." 那种绝对路径（那会绕过令牌前缀）
  var BASE = location.pathname.replace(/\/+$/, "") + "/";

  function say(text, kind) {
    msgEl.textContent = text || "";
    msgEl.className = kind || "";
  }

  function post(path, payload) {
    return fetch(BASE + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then(function (r) {
      return r.json().catch(function () {
        return { ok: false, message: "服务返回了无法解析的内容（HTTP " + r.status + "）" };
      });
    }).catch(function () {
      return { ok: false, message: "连不上本地服务，确认窗口还开着" };
    });
  }

  function startCooldown(seconds) {
    var left = seconds;
    sendBtn.disabled = true;
    sendBtn.textContent = left + " 秒后可重发";
    clearInterval(cooldownTimer);
    cooldownTimer = setInterval(function () {
      left -= 1;
      if (left <= 0) {
        clearInterval(cooldownTimer);
        sendBtn.disabled = false;
        sendBtn.textContent = "发送验证码";
        return;
      }
      sendBtn.textContent = left + " 秒后可重发";
    }, 1000);
  }

  sendBtn.addEventListener("click", function () {
    var phone = phoneEl.value;
    if (!phone) { say("请先选择账户", "err"); return; }
    say("正在发送…");
    sendBtn.disabled = true;
    post("/api/sms/send", { phone: phone }).then(function (res) {
      if (!res.ok) {
        say(res.message, "err");
        sendBtn.disabled = false;
        return;
      }
      var extra = res.bind_status && res.bind_status !== 5
        ? "\n提示：该账号绑定状态为 " + res.bind_status + "（正常应为 5），可能需要先在官网处理。"
        : "";
      say(res.message + extra, "ok");
      startCooldown(res.cooldown || 60);
      codeEl.focus();
    });
  });

  loginBtn.addEventListener("click", function () {
    var phone = phoneEl.value, smscode = codeEl.value.trim();
    if (!phone) { say("请先选择账户", "err"); return; }
    if (!smscode) { say("请填写收到的验证码", "err"); codeEl.focus(); return; }
    say("正在登录…");
    loginBtn.disabled = true;
    post("/api/sms/verify", { phone: phone, smscode: smscode }).then(function (res) {
      loginBtn.disabled = false;
      if (!res.ok) { say(res.message, "err"); return; }
      say(res.message + (res.expiry_time ? "\n有效期至 " + res.expiry_time : ""), "ok");
      codeEl.value = "";
    });
  });

  codeEl.addEventListener("keydown", function (e) {
    if (e.key === "Enter") { loginBtn.click(); }
  });

  fetch(BASE + "api/state").then(function (r) { return r.json(); }).then(function (res) {
    if (res.config_error) {
      phoneEl.innerHTML = '<option value="">（配置有问题）</option>';
      say("配置读不出来：" + res.config_error, "err");
      sendBtn.disabled = true;
      loginBtn.disabled = true;
      return;
    }
    var list = res.accounts || [];
    if (!list.length) {
      phoneEl.innerHTML = '<option value="">（没有配置任何账户）</option>';
      say("先在 .env 里配置 PHONE_1 / PASSWORD_1，再刷新本页", "err");
      sendBtn.disabled = true;
      loginBtn.disabled = true;
      return;
    }
    phoneEl.innerHTML = list.map(function (a) {
      return '<option value="' + a.phone + '">' + a.label + "</option>";
    }).join("");
    var left = res.cooldown_left;
    if (left) { startCooldown(left); }
  }).catch(function () {
    say("读不到账户列表，确认本地服务还在运行", "err");
  });
})();
</script>
</body>
</html>
"""