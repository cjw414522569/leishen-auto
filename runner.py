"""「登录 + 暂停」的编排逻辑，本地命令行与云函数共用。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping

from api import (
    CODE_ALREADY_PAUSED,
    CODE_TOKEN_EXPIRED,
    APIError,
    Client,
    LoginError,
    LoginInfo,
)
from config import Account, Config, ConfigError, load_config
from token_cache import CacheEntry, TokenCache

Logger = Callable[[str], None]


@dataclass
class AccountResult:
    """单个账户的执行结果。

    刻意只存打码后的 ``label``，不持有 ``Account``——这个对象最终会作为云函数的
    返回值被序列化，不能让密码跟着流出去。
    """

    label: str
    ok: bool
    step: str = "done"  # login / pause / done
    code: int = 0
    message: str = ""


@dataclass
class Result:
    """一次运行的整体结果。"""

    ok: bool
    step: str = "done"  # config / login / pause / done
    code: int = 0
    message: str = ""
    accounts: list[AccountResult] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1


class _StepFailed(Exception):
    """某一步失败，携带已经构造好的 AccountResult。"""

    def __init__(self, result: AccountResult) -> None:
        super().__init__(result.message)
        self.result = result


def _login_or_fail(
    client: Client, cfg: Config, account: Account, prefix: str, log: Logger
) -> LoginInfo:
    log(f"🔑{prefix}登录获取令牌中…")
    try:
        info = client.login(
            account.phone,
            account.password_md5,
            country_code=cfg.country_code,
            lang=cfg.lang,
            src_channel=cfg.src_channel,
        )
    except LoginError as exc:
        log(f"❌{prefix}登录失败: {exc.code} - {exc.msg}")
        raise _StepFailed(AccountResult(account.label, False, "login", exc.code, exc.msg))
    except APIError as exc:
        log(f"❌{prefix}登录失败: {exc}")
        raise _StepFailed(AccountResult(account.label, False, "login", message=str(exc)))

    log(f"✔️{prefix}登录成功，令牌有效期至 {info.expiry_time}")
    if cfg.show_token:
        log(f"🔑{prefix}account_token={info.account_token}")
    return info


def _pause_or_fail(
    client: Client, cfg: Config, account: Account, token: str, prefix: str, log: Logger
):
    try:
        return client.pause(token, cfg.lang)
    except APIError as exc:
        log(f"❌{prefix}暂停失败: {exc}")
        raise _StepFailed(AccountResult(account.label, False, "pause", message=str(exc)))


def _pause_account(
    client: Client,
    cfg: Config,
    account: Account,
    prefix: str,
    log: Logger,
    cache: TokenCache | None,
) -> AccountResult:
    """处理单个账户：优先复用缓存令牌，失效则重新登录。"""
    try:
        token = ""
        cached = False

        if cache is not None:
            entry = cache.get(account.phone)
            if entry is not None:
                log(f"🔑{prefix}复用本地缓存的令牌（有效期至 {entry.expiry_time or '未知'}）")
                token, cached = entry.account_token, True

        if not token:
            info = _login_or_fail(client, cfg, account, prefix, log)
            token = info.account_token
            if cache is not None:
                cache.put(account.phone, CacheEntry(token, info.expiry_time))

        resp = _pause_or_fail(client, cfg, account, token, prefix, log)

        if resp.code == CODE_TOKEN_EXPIRED and cached:
            # 缓存里的令牌失效了：丢掉它，重新登录再试一次
            log(f"⚠️{prefix}缓存令牌已失效，重新登录")
            if cache is not None:
                cache.drop(account.phone)
            info = _login_or_fail(client, cfg, account, prefix, log)
            if cache is not None:
                cache.put(account.phone, CacheEntry(info.account_token, info.expiry_time))
            resp = _pause_or_fail(client, cfg, account, info.account_token, prefix, log)

    except _StepFailed as exc:
        return exc.result

    if resp.code != 0:
        if resp.code == CODE_ALREADY_PAUSED:  # 账号已经停止加速，请不要重复操作
            log(f"👌{prefix}已经暂停: {resp.code} - {resp.msg}")
            return AccountResult(account.label, True, "pause", resp.code, resp.msg)
        log(f"❌{prefix}暂停失败: {resp.code} - {resp.msg}")
        return AccountResult(account.label, False, "pause", resp.code, resp.msg)

    log(f"{prefix}{resp.code}:{resp.msg}")
    log(f"✔️{prefix}暂停成功")
    return AccountResult(account.label, True, "pause", resp.code, resp.msg)


def pause_all(
    log: Logger = print,
    environ: Mapping[str, str] | None = None,
    cache: TokenCache | None = None,
) -> Result:
    """逐个账户登录换取令牌并暂停加速。

    ``log`` 只是一个单参数可调用对象，命令行传 ``print``，云函数传
    ``context.getLogger().info``。``environ`` 传给 ``load_config``，云函数用它
    承接 ``context.getUserData()`` 读到的配置。``cache`` 只在本地运行时传入。

    单个账户失败不会中断其余账户——多账户场景下，一个密码输错不该让其他账户
    也漏掉当天的暂停。
    """
    try:
        cfg = load_config(environ=environ)
    except ConfigError as exc:
        log(f"❌错误: {exc}")
        return Result(False, "config", message=str(exc))

    client = Client(retries=cfg.retries)
    if cache is not None:
        # 账户从配置里删掉后，它的令牌没必要继续留在磁盘上
        cache.prune(account.phone for account in cfg.accounts)

    multiple = len(cfg.accounts) > 1
    results: list[AccountResult] = []

    for account in cfg.accounts:
        prefix = f"[{account.label}] " if multiple else ""
        results.append(_pause_account(client, cfg, account, prefix, log, cache))

    if multiple:
        failed = [r for r in results if not r.ok]
        if failed:
            log(f"❌{len(results)} 个账户中 {len(failed)} 个失败")
        else:
            log(f"✔️{len(results)} 个账户全部处理成功")

    first_failure = next((r for r in results if not r.ok), None)
    if first_failure is None:
        return Result(True, "done", 0, "", results)

    return Result(
        False, first_failure.step, first_failure.code, first_failure.message, results
    )
