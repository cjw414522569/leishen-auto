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
from datetime import datetime

from config import ConfigError, load_cache_file, load_run_cron
from cron import CronExpr, next_run
from runner import pause_all
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


def run_forever(cron: CronExpr, cache: TokenCache | None) -> int:
    """常驻运行：等到 cron 指定的时刻跑一轮，然后接着等下一次，直到 Ctrl+C。

    每行日志都带时间戳——这个进程会跑很久，翻日志时没有时间戳很难定位。
    """

    def stamped(line: str) -> None:
        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {line}")

    # 把生效的时区打出来：容器里 TZ 没生效时会静默退回 UTC，
    # 而只看「下次运行 01:00」是看不出问题的——那可能是 UTC 的 01:00。
    zone = datetime.now().astimezone().strftime("%Z%z") or "未知"
    print(f"⏰定时模式已开启（{cron.raw}，本机时区 {zone}），按 Ctrl+C 退出")

    while True:
        target = next_run(cron, datetime.now())
        stamped(
            f"😴下次运行：{target:%Y-%m-%d %H:%M:%S}"
            f"（{format_duration((target - datetime.now()).total_seconds())}后）"
        )

        try:
            sleep_until(target)
        except KeyboardInterrupt:
            print("\n👋已停止定时运行")
            return 0

        stamped("⌛️开始运行")
        try:
            pause_all(stamped, cache=cache)
        except Exception as exc:  # noqa: BLE001 - 常驻进程不该被一次意外弄死
            stamped(f"❌本次运行异常: {exc}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    force_utf8_output()

    cache = None if args.no_cache else TokenCache(load_cache_file() or None)

    try:
        cron = load_run_cron()
    except ConfigError:
        # 定时配置读不出来时按「只跑一次」处理，让 pause_all 去统一报错并推送
        cron = None

    if cron is None:
        return run_once(cache)
    return run_forever(cron, cache)


if __name__ == "__main__":
    sys.exit(main())
