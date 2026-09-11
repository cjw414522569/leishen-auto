"""打包 FunctionGraph 部署包。

产出 ``dist/functiongraph.zip``，内容为：

    index.py
    runner.py
    token_cache.py
    api/
    config/

入口文件 ``index.py`` 位于 ZIP 根目录（FunctionGraph 的硬性要求）。本项目零第三方
依赖，所以不需要制作依赖包，也不需要按 EulerOS 对齐二进制。

云函数不会用到 ``token_cache.py``（没有可持久化的本地盘），但 ``runner.py`` 会
import 它，所以必须一起打包。

用法：

    python scripts/build_functiongraph_zip.py
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INCLUDE_FILES = ("index.py", "runner.py", "token_cache.py", "notify.py")
INCLUDE_DIRS = ("api", "config")
SKIP_DIRS = {"__pycache__", ".pytest_cache"}


def iter_sources():
    """产出 (磁盘路径, ZIP 内路径)。"""
    for name in INCLUDE_FILES:
        path = ROOT / name
        if not path.is_file():
            raise FileNotFoundError(f"缺少入口文件: {path}")
        yield path, name

    for dir_name in INCLUDE_DIRS:
        for path in sorted((ROOT / dir_name).rglob("*")):
            # 白名单：只收 *.py。用排除法（如"除 .pyc 外全收"）会把放错位置的
            # .env 之类明文凭据一起打进部署包，而 find_env_file 恰好优先在
            # config/ 目录里找 .env，这种误放很自然会发生。
            if path.is_dir() or path.suffix != ".py":
                continue
            if SKIP_DIRS.intersection(path.parts):
                continue
            yield path, path.relative_to(ROOT).as_posix()


def build(out_dir: Path | None = None) -> Path:
    zip_path = (out_dir or ROOT / "dist") / "functiongraph.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for source, arcname in iter_sources():
            archive.write(source, arcname)

    return zip_path


def main() -> int:
    zip_path = build()
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()

    print(f"已生成 {zip_path}")
    print(f"大小 {zip_path.stat().st_size} 字节，包含 {len(names)} 个文件")
    for name in names:
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
