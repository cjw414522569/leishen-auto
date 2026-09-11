"""雷神加速器自动暂停工具（本地命令行入口）。

与云函数入口（``index.py``）共用 ``runner.pause_all``，区别只在于本地会把登录
拿到的令牌缓存到 ``.token_cache.json``，下次直接复用，不用每次登录。
GitHub Actions 请加 ``--no-cache``：runner 每次都是全新环境，存了也带不到下一次。

默认只运行一次就退出。想让它常驻，在 ``.env`` 里设 ``RUN_INTERVAL``（如 ``24h``）。
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta

from config import ConfigError, load_run_interval
from runner import pause_all
from token_cache import TokenCache


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
    for unit, size in (("天", 86400), ("小时", 3600), ("分钟", 60)):
        if seconds >= size and seconds % size == 0:
            return f"{int(seconds // size)} {unit}"
    return f"{seconds:g} 秒"


def run_once(cache: TokenCache | None) -> int:
    print("⌛️开始运行")
    result = pause_all(print, cache=cache)
    # 保持原行为：只有成功路径才打印结束标记
    if result.ok:
        print("⌛️结束运行")
    return result.exit_code


def run_forever(interval: float, cache: TokenCache | None) -> int:
    """常驻运行：跑一轮、睡一会儿、再跑一轮，直到 Ctrl+C。

    每行日志都带时间戳——这个进程会跑很久，翻日志时没有时间戳很难定位。
    """
    print(f"⏰定时模式：每 {format_duration(interval)} 运行一次，按 Ctrl+C 退出")

    def stamped(line: str) -> None:
        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {line}")

    while True:
        stamped("⌛️开始运行")
        try:
            pause_all(stamped, cache=cache)
        except Exception as exc:  # noqa: BLE001 - 常驻进程不该被一次意外弄死
            stamped(f"❌本次运行异常: {exc}")

        nxt = datetime.now() + timedelta(seconds=interval)
        stamped(f"😴下次运行：{nxt:%Y-%m-%d %H:%M:%S}")

        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\n👋已停止定时运行")
            return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    force_utf8_output()

    cache = None if args.no_cache else TokenCache()

    try:
        interval = load_run_interval()
    except ConfigError:
        # 间隔配置读不出来时按「只跑一次」处理，让 pause_all 去统一报错并推送
        interval = 0.0

    if interval <= 0:
        return run_once(cache)
    return run_forever(interval, cache)


if __name__ == "__main__":
    sys.exit(main())
