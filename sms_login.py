"""短信登录流程 —— 网页（webui.py）和命令行（main.py --login）共用。

背景：密码登录接口 ``/api/auth/login/v1`` 已被 CloudWAF 拦死（HTTP 418），
所以短信登录是现在唯一能走的重登路径。

安全要点：**只允许给配置里的号码发验证码**。不加这条限制，本地这个网页就成了
一个「能给任意号码发短信」的接口 —— 既会被滥用，也会烧你自己的短信费。
"""

from __future__ import annotations

from typing import Callable

from api import Client, LoginInfo, SmsCodeInfo
from config import Config, load_cache_file
from token_cache import CacheEntry, TokenCache

Logger = Callable[[str], None]


class LoginFlowError(Exception):
    """短信登录流程里可以直接展示给用户的错误。"""


def find_account(cfg: Config, phone: str):
    """按手机号找配置里的账户；找不到返回 None。"""
    wanted = str(phone or "").strip()
    return next((account for account in cfg.accounts if account.phone == wanted), None)


def _require_account(cfg: Config, phone: str):
    account = find_account(cfg, phone)
    if account is None:
        raise LoginFlowError(
            f"{phone} 不在配置的账户列表里。为避免变成「能给任意号码发短信」的接口，"
            "只允许给已配置的号码发送验证码"
        )
    return account


def request_code(
    cfg: Config, phone: str, *, client: Client | None = None, log: Logger = print
) -> SmsCodeInfo:
    """给配置里的某个号码发送短信验证码。"""
    account = _require_account(cfg, phone)
    client = client or Client(retries=cfg.retries)

    log(f"📨正在向 {account.label} 发送验证码…")
    info = client.send_sms_code(account.phone, cfg.country_code)
    log("✔️验证码已下发，请查看手机短信（约 30 分钟内有效）")
    return info


def verify_code(
    cfg: Config,
    phone: str,
    smscode_key: str,
    smscode: str,
    cache: TokenCache | None = None,
    *,
    client: Client | None = None,
    log: Logger = print,
) -> LoginInfo:
    """校验短信验证码并把拿到的令牌写进缓存。"""
    account = _require_account(cfg, phone)
    if not str(smscode or "").strip():
        raise LoginFlowError("请填写收到的验证码")
    if not smscode_key:
        raise LoginFlowError("验证码已失效，请重新发送")

    client = client or Client(retries=cfg.retries)

    log("🔑正在校验验证码…")
    info = client.login_with_sms(account.phone, smscode_key, str(smscode).strip(),
                                 country_code=cfg.country_code)

    # 写进缓存 —— 定时任务下一次运行就会直接用这个令牌，不再尝试密码登录
    if cache is None:
        cache = TokenCache(load_cache_file() or None)
    cache.put(account.phone, CacheEntry(info.account_token, info.expiry_time))

    log(f"✔️登录成功，令牌有效期至 {info.expiry_time}")
    log("💾令牌已存入本地缓存，定时任务下次运行会直接复用它")
    return info
