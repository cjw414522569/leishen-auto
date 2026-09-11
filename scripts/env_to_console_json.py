"""把本地 .env 转成 FunctionGraph 控制台可直接粘贴的环境变量 JSON。

控制台路径：函数详情 →「设置」→「环境变量」→「编辑环境变量」→「使用 JSON 格式编辑」

用法：

    python scripts/env_to_console_json.py              # 读项目根目录的 .env
    python scripts/env_to_console_json.py path/to/.env
    python scripts/env_to_console_json.py --all        # 连空占位项也输出

输出的内容**含密码哈希**，粘贴完请清掉终端记录。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import find_env_file, parse_env_file  # noqa: E402

# 函数会读取的键：账户相关的按前缀收，其余按固定名单
ACCOUNT_PREFIXES = ("PHONE", "PASSWORD")
COMMON_KEYS = ("COUNTRY_CODE", "SRC_CHANNEL", "API_LANG", "RETRIES", "SHOW_TOKEN")


def is_function_key(name: str) -> bool:
    return name in COMMON_KEYS or name.startswith(ACCOUNT_PREFIXES)


def build(path: Path, include_empty: bool = False) -> dict[str, str]:
    values = parse_env_file(path)
    return {
        key: value
        for key, value in values.items()
        if is_function_key(key) and (include_empty or value != "")
    }


def main(argv: list[str]) -> int:
    include_empty = "--all" in argv
    args = [a for a in argv if not a.startswith("--")]

    path = Path(args[0]) if args else find_env_file()
    if path is None or not path.is_file():
        print("找不到 .env，先把 .env.example 复制成 .env 并填好", file=sys.stderr)
        return 1

    payload = build(path, include_empty)
    if not payload:
        print(f"{path} 里没有可用的配置项", file=sys.stderr)
        return 1

    print(f"# 来源: {path}", file=sys.stderr)
    print(f"# 共 {len(payload)} 项，粘贴到「使用 JSON 格式编辑」的输入框里", file=sys.stderr)
    print(json.dumps(payload, ensure_ascii=False, indent=4))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
