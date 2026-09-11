"""登录令牌的本地缓存（仅本地运行启用）。

登录接口偶发「网络异常」，而令牌本身有效期较长，所以本地运行时把令牌存下来，
下次直接复用，不必每次都登录。

只有「本地运行」会用到它：

- GitHub Actions 的 runner 每次都是全新环境，存了也带不到下一次
- 云函数的 ``/tmp`` 是单实例临时盘，不共享也不保证持久

缓存文件里有令牌（等同凭据），已在 .gitignore 里排除，请勿提交或分享。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

CACHE_FILE_NAME = ".token_cache.json"
CACHE_VERSION = 1

# 距过期不足这个时长就当作需要重新登录
DEFAULT_REFRESH_MARGIN = timedelta(hours=1)


def parse_expiry(raw: object) -> datetime | None:
    """尽力解析接口返回的 ``expiry_time``，解析不了返回 None。

    接口没给出格式说明，页面代码里出现过 ``YYYY-MM-DD HH:MM:SS``（见
    ``chunk-common.js`` 的 ``formatDateTime``），也可能直接是时间戳。
    不带时区的字符串按本机时区理解。

    这里刻意不用 ``str.isdigit()`` 判断是不是时间戳——它比 ``int()`` 宽，
    上标数字（``²``）、带圈数字（``⑨``）都会返回 True 却让 ``int()`` 抛异常。
    直接试 ``int()``，失败就落到 ISO 分支。
    """
    text = str(raw or "").strip()
    if not text:
        return None

    try:
        seconds = int(text)
    except ValueError:
        pass
    else:
        if seconds > 10**11:  # 毫秒时间戳
            seconds //= 1000
        try:
            return datetime.fromtimestamp(seconds).astimezone()
        except (OverflowError, OSError, ValueError):
            return None

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00").replace("/", "-"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed


def is_fresh(
    expiry_time: object,
    margin: timedelta = DEFAULT_REFRESH_MARGIN,
    now: datetime | None = None,
) -> bool:
    """判断缓存里的令牌是否还能用。

    解析不出有效期时**返回 True**（先用着）——真失效了暂停接口会返回 400006，
    那时再重新登录兜底。
    """
    expires_at = parse_expiry(expiry_time)
    if expires_at is None:
        return True
    return expires_at - margin > (now or datetime.now().astimezone())


@dataclass
class CacheEntry:
    """一个账户的缓存条目。"""

    account_token: str
    expiry_time: str = ""


class TokenCache:
    """按手机号索引的令牌缓存。

    文件损坏或格式不认识时退化为「没有缓存」，只影响本次是否复用，
    不会让程序崩掉。
    """

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        margin: timedelta = DEFAULT_REFRESH_MARGIN,
    ) -> None:
        self.path = Path(path) if path is not None else Path(__file__).resolve().parent / CACHE_FILE_NAME
        self.margin = margin
        self._entries = self._load()

    def _load(self) -> dict[str, CacheEntry]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(payload, dict) or payload.get("version") != CACHE_VERSION:
            return {}

        tokens = payload.get("tokens")
        if not isinstance(tokens, dict):
            return {}

        entries: dict[str, CacheEntry] = {}
        for phone, item in tokens.items():
            if isinstance(item, dict) and item.get("account_token"):
                # JSON 里的 null 要落成空串，不能 str(None) 变成字面量 "None"
                raw_expiry = item.get("expiry_time")
                entries[str(phone)] = CacheEntry(
                    account_token=str(item["account_token"]),
                    expiry_time="" if raw_expiry is None else str(raw_expiry),
                )
        return entries

    def _save(self) -> None:
        # 顺手清掉已过期的条目，免得文件里长期留着等同凭据的旧令牌
        self._entries = {
            phone: entry
            for phone, entry in self._entries.items()
            if is_fresh(entry.expiry_time, self.margin)
        }

        payload = {
            "version": CACHE_VERSION,
            "tokens": {
                phone: {"account_token": e.account_token, "expiry_time": e.expiry_time}
                for phone, e in sorted(self._entries.items())
            },
        }
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # 先写临时文件再原子替换，避免中途失败留下半个文件
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            try:
                tmp.chmod(0o600)  # Windows 上无效，POSIX 上收紧权限
            except OSError:
                pass
            os.replace(tmp, self.path)
        except OSError:
            # 写不进去只是降级——下次重新登录而已，不该影响本次运行
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def get(self, phone: str) -> CacheEntry | None:
        """取还有效的缓存条目；没有或已过期返回 None。"""
        entry = self._entries.get(phone)
        if entry is None:
            return None
        if not is_fresh(entry.expiry_time, self.margin):
            return None
        return entry

    def put(self, phone: str, entry: CacheEntry) -> None:
        self._entries[phone] = entry
        self._save()

    def drop(self, phone: str) -> None:
        if self._entries.pop(phone, None) is not None:
            self._save()

    def prune(self, keep: Iterable[str]) -> None:
        """丢掉不在 ``keep`` 里的条目。

        账户从配置里删掉后，它的令牌没必要继续留在磁盘上——那是等同凭据的东西。
        """
        keep_set = set(keep)
        dropped = [phone for phone in self._entries if phone not in keep_set]
        if dropped:
            for phone in dropped:
                del self._entries[phone]
            self._save()
