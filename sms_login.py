"""短信登录流程 —— 网页（webui.py）和命令行（main.py --login）共用。

背景：密码登录接口 ``/api/auth/login/v1`` 已被 CloudWAF 拦死（HTTP 418），
所以短信登录是现在唯一能走的重登路径。

**号码不要求在配置里** —— 网页上可以直接输一个新号码，登录成功后由调用方决定
要不要把它存进配置（见 ``config.add_account_phone``）。所以这里不做「必须在配置
里」的限制；防滥用交给上层：网页层有随机令牌、每号码冷却、总量上限三道，
命令行则天然只有本机在用。
"""

from __future__ import annotations

from typing import Callable

from api import Client, LoginInfo, SmsCodeInfo
from config import Config, load_cache_file, mask_phone
from token_cache import CacheEntry, TokenCache

Logger = Callable[[str], None]


class LoginFlowError(Exception):
    """短信登录流程里可以直接展示给用户的错误。"""


def find_account(cfg: Config | None, phone: str):
    """按手机号找配置里的账户；找不到返回 None。"""
    if cfg is None:
        return None
    wanted = str(phone or "").strip()
    return next((account for account in cfg.accounts if account.phone == wanted), None)


def label_for(cfg: Config | None, phone: str) -> str:
    """展示用的标签：配置里有就用它（带「账户N」前缀），没有就现打码一个。"""
    account = find_account(cfg, phone)
    return account.label if account is not None else mask_phone(phone)


def request_code(
    cfg: Config | None, phone: str, *, client: Client | None = None, log: Logger = print
) -> SmsCodeInfo:
    """给某个号码发送短信验证码。号码不必在配置里。"""
    number = str(phone or "").strip()
    if not number:
        raise LoginFlowError("请先填写手机号")

    client = client or Client(retries=cfg.retries if cfg else 10)
    country_code = cfg.country_code if cfg else "86"

    log(f"📨正在向 {label_for(cfg, number)} 发送验证码…")
    info = client.send_sms_code(number, country_code)
    log("✔️验证码已下发，请查看手机短信")
    return info


def verify_code(
    cfg: Config | None,
    phone: str,
    smscode_key: str,
    smscode: str,
    cache: TokenCache | None = None,
    *,
    client: Client | None = None,
    log: Logger = print,
) -> LoginInfo:
    """校验短信验证码并把拿到的令牌写进缓存。号码不必在配置里。"""
    number = str(phone or "").strip()
    if not number:
        raise LoginFlowError("请先填写手机号")
    if not str(smscode or "").strip():
        raise LoginFlowError("请填写收到的验证码")
    if not smscode_key:
        raise LoginFlowError("验证码已失效，请重新发送")

    client = client or Client(retries=cfg.retries if cfg else 10)
    country_code = cfg.country_code if cfg else "86"

    log("🔑正在校验验证码…")
    info = client.login_with_sms(
        number, smscode_key, str(smscode).strip(), country_code=country_code
    )

    # 写进缓存 —— 定时任务下一次运行就会直接用这个令牌
    if cache is None:
        cache = TokenCache(load_cache_file() or None)
    cache.put(number, CacheEntry(info.account_token, info.expiry_time))

    log(f"✔️登录成功，令牌有效期至 {info.expiry_time}")
    log("💾令牌已存入本地缓存，定时任务下次运行会直接复用它")
    return info