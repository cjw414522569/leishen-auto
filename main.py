"""雷神加速器自动暂停工具（本地命令行入口）。

与云函数入口（``index.py``）共用 ``runner.pause_all``，区别只在于本地会把登录
拿到的令牌缓存到 ``.token_cache.json``，下次直接复用，不用每次登录。
GitHub Actions 请加 ``--no-cache``：runner 每次都是全新环境，存了也带不到下一次。

默认只运行一次就退出。想让它常驻、到点才跑，在 ``.env`` 里设 ``RUN_CRON``，例如
``RUN_CRON=0 1 * * *`` 就是每天凌晨 1 点跑一次。
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta

from api import APIError
from config import (
    ConfigError,
    FailRetry,
    load_cache_file,
    load_config,
    load_fail_retry,
    load_run_cron,
    load_web_settings,
)
from cron import CronExpr, next_run
from notify import PushPlusNotifier, build_login_link_message
from runner import pause_all
from sms_login import LoginFlowError, request_code, verify_code
from token_cache import TokenCache

# 长睡眠切成小段：这样系统时钟被改、机器休眠唤醒后，实际触发时刻仍跟着墙钟走，
# 按 Ctrl+C 也能及时响应
SLEEP_CHUNK = 60.0


def force_utf8_output() -> None:
    """确保输出流能编码 emoji。

    Windows 下 stdout 被重定向到管道或文件时会退回 GBK，打印 emoji 会抛
    ``UnicodeEncodeError``。
    """
    for stream in (sys.stdout, sys.stderr):
        encoding = getattr(stream, "encoding", None)
        reconfigure = getattr(stream, "reconfigure", None)
        if not encoding or reconfigure is None:
            continue
        try:
            "⌛".encode(encoding)
        except (UnicodeEncodeError, LookupError):
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="main.py", description="雷神加速器自动暂停（本地运行）"
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="不使用本地令牌缓存，每次都重新登录（GitHub Actions 用这个）",
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="短信登录：往手机发验证码、在终端里输入，换到令牌并存进缓存",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="启动本地网页做短信登录（默认只绑 127.0.0.1）",
    )
    parser.add_argument("--host", default="", help="网页监听地址；留空用 WEB_HOST")
    parser.add_argument("--port", type=int, default=0, help="网页监听端口；留空用 WEB_PORT")
    return parser.parse_args(argv)


def format_duration(seconds: float) -> str:
    """把秒数说得像人话：86400 -> ``1 天``。"""
    seconds = max(0, int(seconds))
    for unit, size in (("天", 86400), ("小时", 3600), ("分钟", 60)):
        if seconds >= size:
            rest = seconds % size
            body = f"{seconds // size} {unit}"
            if rest >= 60:
                return f"{body} {rest // 60} 分钟"
            return body
    return f"{seconds} 秒"


def interactive_login(cache: TokenCache | None = None, *, input_fn=input, log=print) -> int:
    """命令行短信登录：发码 → 输入验证码 → 令牌入库。"""
    try:
        cfg = load_config()
    except ConfigError as exc:
        log(f"❌错误: {exc}")
        return 1

    accounts = cfg.accounts
    if len(accounts) == 1:
        account = accounts[0]
        log(f"📱短信登录：{account.label}")
    else:
        log("📱短信登录，请选择账户：")
        for index, item in enumerate(accounts, 1):
            log(f"   {index}) {item.label}")
        try:
            raw = input_fn(f"请选择 [1-{len(accounts)}]（回车默认 1）: ").strip() or "1"
        except (EOFError, KeyboardInterrupt):
            log("\n👋已取消")
            return 1
        if not raw.isdigit() or not 1 <= int(raw) <= len(accounts):
            log("❌无效的选择")
            return 1
        account = accounts[int(raw) - 1]

    cache = cache or TokenCache(load_cache_file() or None)

    try:
        info = request_code(cfg, account.phone, log=log)
    except (LoginFlowError, APIError) as exc:
        log(f"❌发送失败: {exc}")
        return 1

    try:
        smscode = input_fn("请输入手机收到的验证码: ").strip()
    except (EOFError, KeyboardInterrupt):
        log("\n👋已取消")
        return 1

    try:
        verify_code(cfg, account.phone, info.smscode_key, smscode, cache=cache, log=log)
    except (LoginFlowError, APIError) as exc:
        log(f"❌登录失败: {exc}")
        return 1
    return 0


def run_once(cache: TokenCache | None) -> int:
    print("⌛️开始运行")
    result = pause_all(print, cache=cache)
    # 保持原行为：只有成功路径才打印结束标记
    if result.ok:
        print("⌛️结束运行")
    return result.exit_code


def sleep_until(target: datetime, chunk: float = SLEEP_CHUNK) -> None:
    """睡到 ``target``；中途被 Ctrl+C 打断会抛 ``KeyboardInterrupt``。"""
    while True:
        remaining = (target - datetime.now()).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, chunk))


def should_notify_failure(failures: int, notify_every: int) -> bool:
    """失败推送策略：第 1 次立刻推，之后每 ``notify_every`` 次推一次。

    ``failures`` 是本次失败的序号（从 1 开始）。
    """
    if failures <= 1:
        return True
    return notify_every > 0 and failures % notify_every == 0


def retry_target(
    scheduled: datetime, retry: FailRetry, failures: int, now: datetime
) -> datetime:
    """失败后下一次该在什么时候跑。

    取「下一个 cron 时刻」和「now + 重试间隔」里更早的那个——所以失败后会按
    间隔重试，但**不会越过下一个 cron 时刻**（否则重试会一直往后堆）。
    """
    if failures <= 0 or retry.interval <= 0:
        return scheduled
    return min(scheduled, now + timedelta(seconds=retry.interval))


class LoginGate:
    """常驻的一次性短信登录服务。

    端口是固定的，所以只起**一个**服务、需要时**轮换令牌** —— 这样避免了
    反复开关服务导致的端口冲突，也让「用过即失效」自然成立。

    只在 ``WEB_BASE_URL`` 配好时才启用：那是「手机怎么打开这个地址」的答案。
    """

    def __init__(self, httpd, settings, cache: TokenCache | None, log) -> None:
        self.httpd = httpd
        self.settings = settings
        self.cache = cache
        self.log = log

    @property
    def waiting(self) -> bool:
        """已经推过地址、还在等用户点开。"""
        return self.httpd.state.active

    def _config(self):
        """读一次配置；读不出来返回 None（比如账户配错了）。"""
        try:
            return load_config()
        except ConfigError:
            return None

    def issue(self) -> bool:
        """轮换令牌并把登录地址推出去。已经在等用户操作时不重复推。"""
        from webui import login_url

        if self.waiting:
            self.log("⏳登录地址已推送、还没被使用，这次不重复推送")
            return False

        self.httpd.state.rotate()
        url = login_url(self.httpd, self.settings.base_url)

        cfg = self._config()
        accounts = list(cfg.accounts) if cfg else []
        message = build_login_link_message(url, accounts)

        # 推给谁：全局 token 加各账户自己的 token（去重）
        targets: list[str] = []
        topic = template = ""
        if cfg:
            topic, template = cfg.notify.topic, cfg.notify.template
            targets = [cfg.notify.token] + [a.pushplus_token for a in cfg.accounts]
        targets = [t for t in dict.fromkeys(targets) if t]

        if not targets:
            # 没配推送 token 的话，只能把地址打在日志里
            self.log(f"⚠️没有配置推送 token，登录地址只写在这里：{url}")
            return True

        accepted = sum(
            1
            for target in targets
            if PushPlusNotifier(target, topic, template).send(message.title, message.content)
        )

        if accepted:
            self.log(f"📮已推送一次性登录地址（{accepted}/{len(targets)} 个目标），用过即失效")
        else:
            self.log(f"⚠️登录地址推送失败，地址是：{url}")
        return True

    def pause_now(self) -> str:
        """登录成功后顺手把暂停做了，返回一句给用户看的话。"""
        self.log("🔁登录已完成，立即执行暂停…")
        result = pause_all(self.log, cache=self.cache)
        for account in result.accounts:
            self.log(
                f"   {account.label}：{'成功' if account.ok else '失败'} - {account.message}"
            )
        if result.ok:
            return "✔️已顺便完成暂停"
        return f"⚠️暂停没成功：{result.message}"


def start_login_gate(
    settings, cache: TokenCache | None, log
) -> LoginGate | None:
    """按配置起一个常驻登录服务；没配对外地址就不起。"""
    if not settings.public:
        return None

    from webui import create_server, start_in_background

    gate_holder: dict = {}

    def on_login_success() -> str:
        return gate_holder["gate"].pause_now()

    httpd = create_server(
        settings.bind,
        settings.port,
        log=log,
        token="",  # 先不接受任何请求，等推送时再轮换出令牌
        base_url=settings.base_url,
        on_login_success=on_login_success,
        # 必须传同一个缓存实例，否则令牌会写到另一个文件里
        cache=cache,
    )
    gate = LoginGate(httpd, settings, cache, log)
    gate_holder["gate"] = gate
    start_in_background(httpd, log)
    log(f"   一次性登录地址将以 {settings.base_url} 对外提供")
    return gate


def run_forever(
    cron: CronExpr,
    cache: TokenCache | None,
    retry: FailRetry | None = None,
    web=None,
) -> int:
    """常驻运行：等到 cron 指定的时刻跑一轮，失败则按间隔重试，直到 Ctrl+C。

    每行日志都带时间戳——这个进程会跑很久，翻日志时没有时间戳很难定位。
    """
    retry = retry or FailRetry()

    def stamped(line: str) -> None:
        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {line}")

    # 把生效的时区打出来：容器里 TZ 没生效时会静默退回 UTC，
    # 而只看「下次运行 01:00」是看不出问题的——那可能是 UTC 的 01:00。
    zone = datetime.now().astimezone().strftime("%Z%z") or "未知"
    print(f"⏰定时模式已开启（{cron.raw}，本机时区 {zone}），按 Ctrl+C 退出")
    if retry.interval > 0:
        print(
            f"   失败后每 {format_duration(retry.interval)} 重试一次；"
            f"第 1 次失败即时推送，之后每 {retry.notify_every} 次才推送一次"
        )

    gate: LoginGate | None = None
    if web is not None:
        try:
            gate = start_login_gate(web, cache, stamped)
        except OSError as exc:
            # 端口被占用等：不影响定时暂停，只是没有一键重登
            stamped(f"⚠️短信登录服务起不来（{exc}），令牌失效时只能手动重登")

    failures = 0
    while True:
        now = datetime.now()
        scheduled = next_run(cron, now)
        target = retry_target(scheduled, retry, failures, now)
        label = "下次运行" if target == scheduled else "失败重试"

        stamped(
            f"😴{label}：{target:%Y-%m-%d %H:%M:%S}"
            f"（{format_duration((target - now).total_seconds())}后）"
        )

        try:
            sleep_until(target)
        except KeyboardInterrupt:
            print("\n👋已停止定时运行")
            return 0

        attempt = failures + 1
        notify_failure = should_notify_failure(attempt, retry.notify_every)
        stamped("⌛️开始运行")

        try:
            result = pause_all(stamped, cache=cache, notify_failure=notify_failure)
        except Exception as exc:  # noqa: BLE001 - 常驻进程不该被一次意外弄死
            stamped(f"❌本次运行异常: {exc}")
            result = None

        if result is not None and result.ok:
            if failures:
                stamped(f"✔️重试成功（此前已失败 {failures} 次）")
            failures = 0
            continue

        # 登录步骤失败 = 密码登录已经走不通了，需要人来短信登录一次
        if result is not None and result.step == "login" and gate is not None:
            try:
                gate.issue()
            except Exception as exc:  # noqa: BLE001 - 推送失败不该弄死常驻进程
                stamped(f"⚠️推送登录地址时出错: {exc}")

        failures = attempt
        if not notify_failure:
            stamped(
                f"⏳已连续失败 {failures} 次，本次不推送"
                f"（每 {retry.notify_every} 次才推一次）"
            )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    force_utf8_output()

    cache = None if args.no_cache else TokenCache(load_cache_file() or None)

    if args.login:
        return interactive_login(cache)

    try:
        web = load_web_settings()
    except ConfigError:
        web = None

    if args.web:
        from webui import serve

        return serve(
            args.host or (web.bind if web else "127.0.0.1"),
            args.port or (web.port if web else 8765),
            base_url=web.base_url if web else "",
            cache=cache,
        )

    try:
        cron = load_run_cron()
        retry = load_fail_retry()
    except ConfigError:
        # 定时配置读不出来时按「只跑一次」处理，让 pause_all 去统一报错并推送
        cron, retry = None, FailRetry()

    if cron is None:
        return run_once(cache)
    return run_forever(cron, cache, retry, web)


if __name__ == "__main__":
    sys.exit(main())
