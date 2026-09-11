"""华为云 FunctionGraph 入口。

部署要点：

- 入口配置填 ``index.handler``
- ``index.py`` 必须位于部署包 ZIP 的根目录
- 定时触发器 cron 是 **6 字段（含秒）**，每天北京时间凌晨 1 点：
  ``0 0 1 * * *``（中国站 region 默认就是 Asia/Shanghai，也可显式写
  ``CRON_TZ=Asia/Shanghai 0 0 1 * * *``）
- 默认执行超时只有 3 秒，需在「设置 > 常规设置」里上调（多账户轮询 + 重试会更久）
- 本项目零第三方依赖，部署包只含源码，不需要制作依赖包

多账户：环境变量用 ``PHONE_1``/``PASSWORD_1``、``PHONE_2``/``PASSWORD_2``
这样成组配置。``context.getUserData()`` 只能按键取值、无法列举，所以这里按
``MAX_ACCOUNTS`` 逐个探测，取到非空值才用。
"""

from __future__ import annotations

import json
import os
from typing import Any, Mapping

from runner import Result, pause_all

# 按键探测时假设的最大账户数；超出请用环境变量 MAX_ACCOUNTS 调大
MAX_ACCOUNTS = 50

# 不带编号的键 + 与账户无关的通用键
BASE_KEYS = ("PHONE", "PASSWORD")
COMMON_KEYS = (
    "COUNTRY_CODE",
    "SRC_CHANNEL",
    "API_LANG",
    "RETRIES",
    "SHOW_TOKEN",
    "PUSHPLUS_TOKEN",
    "PUSHPLUS_TOPIC",
    "PUSHPLUS_TEMPLATE",
    "NOTIFY_MODE",
    "NOTIFY_GROUPING",
)


class _PrintLogger:
    """拿不到 ``context.getLogger()`` 时的兜底（本地调试用）。

    FunctionGraph 会采集 stdout/stderr，所以 print 也能进日志。
    """

    def info(self, message: str) -> None:
        print(message)

    def error(self, message: str) -> None:
        print(message)


def config_keys(max_accounts: int = MAX_ACCOUNTS) -> tuple[str, ...]:
    """所有需要从函数配置里读取的键。"""
    keys = list(BASE_KEYS)
    for index in range(1, max_accounts + 1):
        keys += [f"PHONE_{index}", f"PASSWORD_{index}", f"PUSHPLUS_TOKEN_{index}"]
    return (*keys, *COMMON_KEYS)


def get_logger(context: Any) -> Any:
    """优先用官方推荐的 ``context.getLogger()``，否则退回 print。"""
    getter = getattr(context, "getLogger", None)
    if callable(getter):
        try:
            logger = getter()
        except Exception:
            logger = None
        if logger is not None and callable(getattr(logger, "info", None)):
            return logger
    return _PrintLogger()


def _positive_int(raw: Any, default: int) -> int:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value >= 1 else default


def _userdata_blob(environ: Mapping[str, str], getter: Any) -> dict[str, str]:
    """尽力从 ``RUNTIME_USERDATA`` 里解析出全部用户配置。

    官方文档把它描述为「用户通过环境变量传入的值」，加密环境变量也经由它下发。
    真能解析成 dict 的话，就不受 ``MAX_ACCOUNTS`` 探测上限的约束了。

    解析不出来就忽略——这只是锦上添花，不依赖它也能正常工作。
    """
    raw = environ.get("RUNTIME_USERDATA")
    if not raw and callable(getter):
        raw = getter("RUNTIME_USERDATA")
    if not isinstance(raw, str):
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): str(value) for key, value in parsed.items() if value is not None}


def build_environ(context: Any) -> Mapping[str, str]:
    """合并函数配置与进程环境变量。

    事件函数的环境变量官方推荐用 ``context.getUserData(key)`` 读取；这里同时保留
    ``os.environ`` 兜底，方便本地用环境变量或 ``.env`` 调试。

    注意：FunctionGraph 的环境变量在控制台是明文展示的，正式环境建议给函数配
    「加密参数」或改用 KMS 托管密码。
    """
    environ = dict(os.environ)
    getter = getattr(context, "getUserData", None)

    # 一次性拿到全部配置（如果有），可以突破下面的按键探测上限
    environ.update(_userdata_blob(environ, getter))

    if callable(getter):
        limit = _positive_int(environ.get("MAX_ACCOUNTS") or getter("MAX_ACCOUNTS"), MAX_ACCOUNTS)
        for key in config_keys(limit):
            value = getter(key)
            if value not in (None, ""):
                environ[key] = value
    return environ


def handler(event: Any, context: Any) -> dict[str, Any]:
    """FunctionGraph 入口：定时触发器每次触发把所有账户轮一遍。"""
    logger = get_logger(context)
    logger.info(f"⏰触发事件: {event}")

    result: Result = pause_all(logger.info, environ=build_environ(context))

    if not result.ok:
        # 额外打一条 error 级别的日志，便于在 LTS 里按级别过滤告警
        logger.error(
            f"❌执行失败: step={result.step} code={result.code} message={result.message}"
        )

    return {
        "ok": result.ok,
        "step": result.step,
        "code": result.code,
        "message": result.message,
        # 只含打码手机号，不含任何凭据
        "accounts": [
            {
                "label": account.label,
                "ok": account.ok,
                "step": account.step,
                "code": account.code,
                "message": account.message,
            }
            for account in result.accounts
        ],
    }
