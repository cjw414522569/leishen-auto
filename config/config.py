"""配置加载。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path
from typing import Mapping

from api.client import (
    DEFAULT_COUNTRY_CODE,
    DEFAULT_LANG,
    DEFAULT_RETRIES,
    DEFAULT_SRC_CHANNEL,
)
from api.sign import md5_hex

INDEXED_PHONE_RE = re.compile(r"^PHONE_(\d+)$")
INDEXED_PASSWORD_RE = re.compile(r"^PASSWORD_(\d+)$")
INLINE_COMMENT_RE = re.compile(r"\s+#")
ENV_FILE_NAME = ".env"

# 找到这些标记就认为到项目根了，不再往上找 .env
PROJECT_MARKERS = (".git", "pyproject.toml", "setup.py")

# 凭据类配置项的前缀：这些键不做逐键合并，见 merge_config_sources
CREDENTIAL_PREFIXES = ("PHONE", "PASSWORD")

# 凭据不完整时的提示：把「不跨来源混合」的规则一并说清楚，
# 否则用户会以为 .env 里的密码本该补上来
_INCOMPLETE_HINT = (
    "需要同时配置 {group}。注意凭据不跨来源混合——"
    "这两项要么都在 .env 里，要么都在环境变量里"
)


class ConfigError(Exception):
    """配置加载失败。"""


def _strip_inline_comment(value: str) -> str:
    """去掉行尾注释。

    只有「空白 + #」才算注释，免得误伤 ``abc#def`` 这种本身带 # 的密码；
    真要写「 #」就给它加引号。
    """
    return INLINE_COMMENT_RE.split(value, maxsplit=1)[0].rstrip()


def _is_credential(key: str) -> bool:
    return key.startswith(CREDENTIAL_PREFIXES)


def _credential_suffixes(*sources: Mapping[str, str]) -> set[str]:
    """收集出现过的账户组后缀：``""`` 以及 ``"_1"``、``"_2"``…"""
    suffixes: set[str] = set()
    for source in sources:
        for key in source:
            key = str(key)
            if key in CREDENTIAL_PREFIXES:
                suffixes.add("")
            elif INDEXED_PHONE_RE.match(key) or INDEXED_PASSWORD_RE.match(key):
                suffixes.add(key[key.index("_") :])
    return suffixes


def merge_config_sources(
    file_values: Mapping[str, str], process_env: Mapping[str, str]
) -> dict[str, str]:
    """合并 ``.env`` 与进程环境变量。

    凭据（``PHONE*`` / ``PASSWORD*``）**以账户为单位整体取一个来源，不逐键混合**：
    某个账户的手机号出现在环境变量里，这一组就整组用环境变量，否则整组用 ``.env``。

    逐键合并（``{**file, **env}``）会出现「``.env`` 里的手机号」配上「shell 里残留的
    密码环境变量」，表现成一个查不出原因的「账号或密码错误」——``.env`` 明明是对的。

    手机号是账户的标识，所以**只出现 PASSWORD 而没有对应 PHONE 的环境变量不参与**，
    否则一个无关的 ``PASSWORD`` 就能劫持 ``.env`` 里的整个账户。

    其余配置项（``RETRIES`` 等）仍然逐键让环境变量优先。
    """
    merged = {key: value for key, value in file_values.items() if not _is_credential(key)}
    merged.update({key: value for key, value in process_env.items() if not _is_credential(key)})

    for suffix in _credential_suffixes(file_values, process_env):
        # 整组取一个来源：另一边的同名项必须丢掉，否则又混起来了
        picked = process_env if f"PHONE{suffix}" in process_env else file_values
        for name in CREDENTIAL_PREFIXES:
            key = f"{name}{suffix}"
            if key in picked:
                merged[key] = picked[key]
            else:
                merged.pop(key, None)

    return merged


def mask_phone(phone: str) -> str:
    """把手机号打码后再写进日志。

    GitHub Actions 的日志、云函数 LTS 日志都可能被更广的人看到，完整号码没必要出现在里面。

    阈值取 8：``[:3]`` 与 ``[-4:]`` 一起要 7 个字符，正好 7 位时两段相接会
    把原串一个不漏地露出来，等于没打码。
    """
    digits = phone.strip()
    if len(digits) >= 8:
        return f"{digits[:3]}****{digits[-4:]}"
    if len(digits) > 2:
        return f"{digits[0]}****{digits[-1]}"
    return "****"


@dataclass(frozen=True)
class Account:
    """一个雷神账户。"""

    phone: str
    password_md5: str
    index: int = 0  # 0 表示不带编号的单账户写法

    @property
    def label(self) -> str:
        """日志与返回值里用的标识，只含打码手机号，不含任何凭据。"""
        if self.index:
            return f"账户{self.index} {mask_phone(self.phone)}"
        return mask_phone(self.phone)


@dataclass
class Config:
    """配置结构体。"""

    accounts: list[Account] = field(default_factory=list)
    lang: str = DEFAULT_LANG
    country_code: str = DEFAULT_COUNTRY_CODE
    src_channel: str = DEFAULT_SRC_CHANNEL
    retries: int = DEFAULT_RETRIES
    show_token: bool = False


def parse_env_file(path: Path) -> dict[str, str]:
    """解析 ``KEY=VALUE`` 形式的 .env 文件。

    只支持最常见的写法：``#`` 注释、空行、可选的 ``export`` 前缀、成对引号。
    刻意不依赖 python-dotenv——云函数环境里少一个依赖就少一份打包负担。
    """
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()

        key, separator, value = line.partition("=")
        if not separator:
            continue

        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = _strip_inline_comment(value)
        if key:
            values[key] = value

    return values


def find_env_file(start: Path | None = None) -> Path | None:
    """从 ``start``（默认本文件所在目录）逐级向上找 ``.env``。

    遇到项目根（含 ``.git`` 等标记）就不再往上走——不设边界的话会一路找到盘符根，
    把别的工具放在项目外的 ``.env`` 也吸收进来。
    """
    directory = start or Path(__file__).resolve().parent
    for candidate_dir in (directory, *directory.parents):
        candidate = candidate_dir / ENV_FILE_NAME
        if candidate.is_file():
            return candidate
        if any((candidate_dir / marker).exists() for marker in PROJECT_MARKERS):
            return None
    return None


def load_env_file(env_file: str | PathLike[str] | None = None) -> dict[str, str]:
    """读取 .env 并返回键值对。

    刻意**不写入** ``os.environ``——读取文件不该有全局副作用，合并逻辑放在
    ``load_config`` 里显式完成。
    """
    path = Path(env_file) if env_file is not None else find_env_file()
    if path is None:
        return {}
    return parse_env_file(path)


def _get(source: Mapping[str, str], name: str, default: str = "") -> str:
    value = source.get(name, default)
    return ("" if value is None else str(value)).strip()


def _flag(source: Mapping[str, str], name: str) -> bool:
    return _get(source, name).lower() in {"1", "true", "yes", "on"}


def _resolve_password_md5(source: Mapping[str, str], suffix: str) -> str:
    """把明文密码 ``PASSWORD<suffix>`` 转成接口要求的 MD5。

    密码不 strip——首尾空格可能是密码本身的一部分。
    """
    plain = source.get(f"PASSWORD{suffix}", "") or ""
    return md5_hex(str(plain)) if plain else ""


def _resolve_retries(source: Mapping[str, str]) -> int:
    raw = _get(source, "RETRIES")
    if not raw:
        return DEFAULT_RETRIES
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"RETRIES 必须是整数，当前为 {raw!r}") from exc
    if value < 1:
        raise ConfigError("RETRIES 至少为 1")
    return value


def collect_accounts(source: Mapping[str, str]) -> list[Account]:
    """收集账户。

    两种写法可以混用：

    - 不带编号：``PHONE`` + ``PASSWORD``（单账户，index 为 0）
    - 带编号：``PHONE_1`` + ``PASSWORD_1``，编号从 1 起

    不带编号的排在前面，其余按编号升序。任何一组不完整都会直接报错，而不是
    悄悄跳过——漏配一个账户却以为在跑，比报错难查得多。
    """
    accounts: list[Account] = []

    phone = _get(source, "PHONE")
    if phone:
        password_md5 = _resolve_password_md5(source, "")
        if not password_md5:
            raise ConfigError(_INCOMPLETE_HINT.format(group="PHONE 与 PASSWORD"))
        accounts.append(Account(phone, password_md5, 0))

    indexes = sorted(
        int(match.group(1))
        for key in source
        if (match := INDEXED_PHONE_RE.match(str(key)))
    )

    # 只配了 PASSWORD_n 而漏了 PHONE_n 时，索引推不出来，这一组会被无声忽略。
    # 用户把 PHONE_2 写错成 PHON_2 之类的情况就是这么漏掉一个账户的。
    orphans = sorted(
        int(match.group(1))
        for key in source
        if (match := INDEXED_PASSWORD_RE.match(str(key)))
        and int(match.group(1)) not in set(indexes)
        and _get(source, str(key))  # 整组留空的占位符不算
    )
    if orphans:
        index = orphans[0]
        raise ConfigError(
            f"账户 {index} 只配了 PASSWORD_{index}，缺少 PHONE_{index}；"
            "请补全，或把这一行删掉"
        )

    for index in indexes:
        suffix = f"_{index}"
        phone = _get(source, f"PHONE{suffix}")
        password_md5 = _resolve_password_md5(source, suffix)
        if not phone and not password_md5:
            continue  # 整组留空，视为占位符
        if not phone or not password_md5:
            hint = _INCOMPLETE_HINT.format(group=f"PHONE{suffix} 与 PASSWORD{suffix}")
            raise ConfigError(f"账户 {index} 配置不完整：{hint}")
        accounts.append(Account(phone, password_md5, index))

    return accounts


def load_config(
    env_file: str | PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> Config:
    """加载配置。

    - 本地运行：``environ`` 留空，读 ``.env``（从本文件所在目录逐级向上找）与
      进程环境变量，环境变量优先。
    - 云函数：调用方传入 ``environ``（``context.getUserData()`` 的合并结果），
      此时完全不碰 ``.env``。

    每个账户用 ``PHONE*`` + ``PASSWORD*``（明文，程序会按接口要求做 MD5）自动登录
    换取令牌，无需手工维护令牌。
    """
    if environ is None:
        source: Mapping[str, str] = merge_config_sources(load_env_file(env_file), os.environ)
    else:
        source = environ

    accounts = collect_accounts(source)
    if not accounts:
        raise ConfigError(
            "没有配置任何账户：需要配置 PHONE + PASSWORD，"
            "多账户时使用 PHONE_1/PASSWORD_1、PHONE_2/PASSWORD_2…"
        )

    return Config(
        accounts=accounts,
        # 不用 LANG 这个名字：POSIX 环境（含 CI 与云函数运行时）用它表示 locale
        lang=_get(source, "API_LANG") or DEFAULT_LANG,
        country_code=_get(source, "COUNTRY_CODE") or DEFAULT_COUNTRY_CODE,
        src_channel=_get(source, "SRC_CHANNEL") or DEFAULT_SRC_CHANNEL,
        retries=_resolve_retries(source),
        show_token=_flag(source, "SHOW_TOKEN"),
    )
