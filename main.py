"""雷神加速器自动暂停工具（本地命令行入口）。

与云函数入口（``index.py``）共用 ``runner.pause_all``，区别只在于本地会把登录
拿到的令牌缓存到 ``.token_cache.json``，下次直接复用，不用每次登录。
GitHub Actions 请加 ``--no-cache``：runner 每次都是全新环境，存了也带不到下一次。
"""

from __future__ import annotations

import argparse
import sys

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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    force_utf8_output()
    print("⌛️开始运行")

    cache = None if args.no_cache else TokenCache()
    result = pause_all(print, cache=cache)

    # 保持原行为：只有成功路径才打印结束标记
    if result.ok:
        print("⌛️结束运行")
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
