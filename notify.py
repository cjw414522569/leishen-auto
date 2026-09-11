"""PushPlus 推送（https://www.pushplus.plus）。

没有配置 ``PUSHPLUS_TOKEN`` 时完全停用。推送失败绝不影响主流程——通知是锦上添花。

两个必须注意的点：

1. PushPlus 的发送接口是**异步**的，响应里的 ``code=200`` 只代表服务端受理了请求，
   不代表消息已经送达（官方文档原文：「仅代表服务端收到请求了，并不表示发送消息成功了」）。
   所以这里只把它记作「已提交」，不谎报「已送达」。
2. 接口文档给的是 ``http://``，但那样 token 会明文过网，所以这里用 HTTPS
   （实测可用）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from api.client import CODE_ALREADY_PAUSED, CODE_OK, HTTPResponse, UrllibSession

PUSHPLUS_ENDPOINT = "https://www.pushplus.plus/send"
PUSHPLUS_SUCCESS_CODE = 200  # 官方定义：请求成功受理
DEFAULT_TIMEOUT = 5.0
DEFAULT_TEMPLATE = "txt"

NOTIFY_ALWAYS = "always"  # 每次运行都推
NOTIFY_ON_CHANGE = "on_change"  # 只在真的有账户从「运行中」变成「已暂停」时推
NOTIFY_MODES = (NOTIFY_ALWAYS, NOTIFY_ON_CHANGE)


@dataclass
class NotifySettings:
    """推送相关配置。"""

    token: str = ""
    topic: str = ""
    template: str = DEFAULT_TEMPLATE
    mode: str = NOTIFY_ALWAYS

    @property
    def enabled(self) -> bool:
        return bool(self.token)


class PushPlusNotifier:
    """把消息发到 PushPlus。

    只用标准库，和 API 客户端共用 ``UrllibSession``。
    """

    def __init__(
        self,
        token: str,
        topic: str = "",
        template: str = DEFAULT_TEMPLATE,
        timeout: float = DEFAULT_TIMEOUT,
        session: Any = None,
    ) -> None:
        self.token = token
        self.topic = topic
        self.template = template
        self.timeout = timeout
        self.session = session if session is not None else UrllibSession()

    def send(self, title: str, content: str) -> bool:
        """提交一条推送，返回是否被服务端受理。

        任何异常都吞掉并返回 False——通知发不出去不该让整个运行失败。
        """
        payload: dict[str, str] = {
            "token": self.token,
            "title": title,
            "content": content,
            "template": self.template,
        }
        if self.topic:
            payload["topic"] = self.topic

        try:
            response: HTTPResponse = self.session.post(
                PUSHPLUS_ENDPOINT,
                data=json.dumps(payload, ensure_ascii=False),
                headers={"Content-Type": "application/json"},
                timeout=self.timeout,
            )
        except Exception:  # noqa: BLE001 - 通知失败不影响主流程
            return False

        if response.status_code >= 400:
            return False

        try:
            body = json.loads(response.text)
        except ValueError:
            return False

        return isinstance(body, dict) and body.get("code") == PUSHPLUS_SUCCESS_CODE


def should_notify(settings: NotifySettings, result: Any) -> bool:
    """判断这次运行该不该推送。

    **失败一定推送**（这是最需要知道的情况）；成功时按模式决定：

    - ``always``：每次都推
    - ``on_change``：只有真的有账户从「运行中」变成「已暂停」才推
      （接口返回 400803「已经停止加速」说明状态没变，那种不算）
    """
    if not settings.enabled:
        return False
    if not result.ok:
        return True
    if settings.mode == NOTIFY_ON_CHANGE:
        return any(account.ok and account.code == CODE_OK for account in result.accounts)
    return settings.mode == NOTIFY_ALWAYS


def _describe(account: Any) -> str:
    if not account.ok:
        reason = account.message or "未知原因"
        return f"失败（{reason}）"
    if account.code == CODE_ALREADY_PAUSED:
        return "已经是暂停状态"
    return "已暂停"


def format_title(result: Any) -> str:
    if not result.ok:
        return "雷神加速器：执行失败"
    if any(a.ok and a.code == CODE_OK for a in result.accounts):
        return "雷神加速器：已暂停"
    return "雷神加速器：无需处理"


def format_content(result: Any, now: datetime | None = None) -> str:
    """拼出推送正文（纯文本，模板默认 txt）。"""
    stamp = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    lines = [stamp, ""]

    if not result.accounts:
        # 配置阶段的失败，没有逐账户信息
        lines.append(f"❌ {result.message or '执行失败'}")
        return "\n".join(lines)

    total = len(result.accounts)
    failed = [a for a in result.accounts if not a.ok]

    if failed:
        lines.append(f"❌ {total} 个账户中 {len(failed)} 个失败")
        lines.append(f"失败原因：{result.message or '未知'}")
    else:
        lines.append(f"✅ {total} 个账户全部处理成功")

    lines.append("")
    for account in result.accounts:
        lines.append(f"• {account.label}：{_describe(account)}")

    return "\n".join(lines)
