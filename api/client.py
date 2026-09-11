"""雷神加速器 API 客户端。"""

from __future__ import annotations

import http.client
import json
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from api.sign import sign_params

DEFAULT_BASE_URL = "https://webapi.leigod.com"
DEFAULT_TIMEOUT = 5.0
DEFAULT_RETRIES = 10
DEFAULT_RETRY_DELAY = 1.0  # 两次重试之间的最小等待（秒）
DEFAULT_RETRY_DELAY_MAX = 3.0  # 最大等待（秒），在这个区间内随机

PAUSE_PATH = "/api/user/pause"
LOGIN_PATH = "/api/auth/login/v1"

# 页面上的接口常量：语言、默认渠道、网页端 os_type
DEFAULT_LANG = "zh_CN"
DEFAULT_COUNTRY_CODE = "86"
DEFAULT_SRC_CHANNEL = "guanwang"
OS_TYPE_WEB = 4

# 页面上的错误码（chunk-common.js -> c.HTTP_*）
CODE_OK = 0  # 操作成功——暂停这个动作是真的执行了，账号状态发生了变化
CODE_TOKEN_EXPIRED = 400006  # 令牌过期，页面据此跳回登录
CODE_ALREADY_PAUSED = 400803  # 账号已经停止加速，请不要重复操作


class APIError(Exception):
    """调用雷神 API 过程中的失败（序列化、网络、解析等）。"""


class LoginError(APIError):
    """登录接口返回了非 0 错误码。"""

    def __init__(self, code: int, msg: str) -> None:
        super().__init__(f"{code} - {msg}")
        self.code = code
        self.msg = msg


class TransportError(Exception):
    """底层传输失败（连接被拒、DNS、超时等）。

    页面上约一半概率弹出的“网络异常”就是这一层抛异常被 JS 捕获后转成的
    ``code=-50000``，重试通常即可成功。
    """


@dataclass
class HTTPResponse:
    """最小化的 HTTP 响应。"""

    status_code: int
    text: str


def _decode(payload: bytes) -> str:
    return payload.decode("utf-8", errors="replace")


def _reason(exc: BaseException) -> str:
    reason = getattr(exc, "reason", None)
    return str(reason) if reason is not None else str(exc)


class UrllibSession:
    """基于标准库的 HTTP 会话。

    刻意不用 requests：云函数环境里第三方库要额外打包，而内置的 requests 版本
    太旧不可靠；标准库没有这些负担。只实现 Client 需要的 POST。
    """

    def post(
        self,
        url: str,
        data: Any = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> HTTPResponse:
        body = data.encode("utf-8") if isinstance(data, str) else data
        try:
            request = urllib.request.Request(
                url, data=body, headers=dict(headers or {}), method="POST"
            )
        except ValueError as exc:
            raise TransportError(f"创建请求失败: {exc}") from exc

        try:
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return HTTPResponse(response.status, _decode(response.read()))
            except urllib.error.HTTPError as exc:
                # 4xx/5xx 也按响应返回，交给 Client 统一判断是否值得重试
                return HTTPResponse(exc.code, _decode(exc.read()))
        except (
            urllib.error.URLError,
            OSError,
            http.client.HTTPException,
        ) as exc:
            # HTTPException 不是 OSError 子类，但同样属于传输层故障：
            # 服务端声明了 Content-Length 却中途断流时，read() 抛的就是
            # IncompleteRead。漏掉它会让异常直接穿过重试逻辑。
            # 外层再套一层 try，是为了让 HTTPError 分支里 exc.read() 的失败也被接住。
            raise TransportError(_reason(exc)) from exc


@dataclass
class PauseRequest:
    """暂停请求结构体。"""

    account_token: str
    lang: str

    def to_dict(self) -> dict[str, str]:
        return {"account_token": self.account_token, "lang": self.lang}


@dataclass
class PauseResponse:
    """暂停响应结构体。"""

    code: int
    msg: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PauseResponse":
        # 与 Go 的 json.Unmarshal 保持一致：字段缺失时取零值
        return cls(code=data.get("code", 0), msg=data.get("msg", ""))


@dataclass
class LoginInfo:
    """登录成功后拿到的令牌信息。"""

    account_token: str
    expiry_time: str = ""


class HTTPSession(Protocol):
    """HTTP 会话接口，便于测试时注入假实现。"""

    def post(
        self,
        url: str,
        data: Any = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> HTTPResponse: ...


class Client:
    """雷神加速器客户端。"""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        session: HTTPSession | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
        retry_delay_max: float = DEFAULT_RETRY_DELAY_MAX,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = session if session is not None else UrllibSession()
        self.timeout = timeout
        self.retries = max(1, retries)
        # 负数会让 time.sleep 抛 ValueError，这里兜住
        self.retry_delay = max(0.0, retry_delay)
        self.retry_delay_max = max(self.retry_delay, retry_delay_max)

    def pause(self, account_token: str, lang: str) -> PauseResponse:
        """暂停加速器。"""
        data = self._post(PAUSE_PATH, PauseRequest(account_token, lang).to_dict())
        return PauseResponse.from_dict(data)

    def login(
        self,
        username: str,
        password_md5: str,
        *,
        country_code: str = DEFAULT_COUNTRY_CODE,
        lang: str = DEFAULT_LANG,
        src_channel: str = DEFAULT_SRC_CHANNEL,
    ) -> LoginInfo:
        """手机号 + 密码登录，拿到 ``account_token``。

        请求体字段与页面 ``onPhoneLogin`` 构造的对象逐一对齐：``user_type`` 默认
        ``"0"``、``code`` 默认空串，这两个字段会参与签名，漏掉会导致签名校验失败。
        """
        payload = sign_params(
            {
                "username": username,
                "password": password_md5,
                "user_type": "0",
                "src_channel": src_channel,
                "code": "",
                "country_code": country_code,
                "lang": lang,
                "os_type": OS_TYPE_WEB,
            }
        )

        data = self._post(LOGIN_PATH, payload)

        code = data.get("code", 0)
        if code != 0:
            raise LoginError(code, data.get("msg", ""))

        login_info = (data.get("data") or {}).get("login_info") or {}
        account_token = login_info.get("account_token", "")
        if not account_token:
            raise APIError("登录响应缺少 account_token")

        return LoginInfo(
            account_token=account_token,
            expiry_time=login_info.get("expiry_time", ""),
        )

    def _post(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """POST JSON 并返回解析后的响应体，对可重试的失败做重试。

        可重试的三种情况，正好覆盖登录接口偶发的「网络异常」：

        - 传输层异常（连接被拒、超时等）
        - HTTP 5xx
        - **HTTP 2xx 但响应体不是合法 JSON**——网关抖动时会返回空体或 HTML 错误页
        - **HTTP 4xx 但响应体不是 JSON**——请求没到业务接口，中间被 WAF / CDN
          拦了（线上真实踩到过：403 + 一个 HTML 拦截页）

        能解析出业务错误码的 4xx 才是确定性错误（比如密码错），那种不重试，
        直接交给上层判断。
        """
        try:
            body = json.dumps(payload)
        except (TypeError, ValueError) as exc:
            raise APIError(f"序列化请求数据失败: {exc}") from exc

        url = f"{self.base_url}{path}"
        last_error = "发送请求失败"
        last_exc: BaseException | None = None

        for attempt in range(1, self.retries + 1):
            try:
                resp = self.session.post(
                    url,
                    data=body,
                    headers={"Content-Type": "application/json"},
                    timeout=self.timeout,
                )
            except TransportError as exc:
                last_error, last_exc = f"发送请求失败: {exc}", exc
            else:
                if 400 <= resp.status_code < 500:
                    try:
                        return self._parse(resp)
                    except APIError as exc:
                        # 4xx 但响应体不是我们的 JSON —— 说明请求根本没到业务接口，
                        # 中间多半是 WAF / CDN / 代理的拦截页（往往是 HTML）。那属于
                        # 基础设施问题，值得重试；能解析出业务码的 4xx 才是确定性错误。
                        last_error = f"发送请求失败: HTTP {resp.status_code}（{exc}）"
                elif resp.status_code >= 500:
                    last_error = f"发送请求失败: HTTP {resp.status_code}"
                elif resp.status_code >= 300:
                    # urllib 已经尽力跟随重定向了，还落在这一档说明跟不动——
                    # 不能当成业务响应解析，否则一个恰好像 JSON 的 302 会被判成成功
                    last_error = f"发送请求失败: 意外的重定向 HTTP {resp.status_code}"
                else:
                    try:
                        return self._parse(resp)
                    except APIError as exc:
                        last_error = str(exc)

            if attempt < self.retries:
                time.sleep(self._next_delay())

        raise APIError(f"{last_error}（已重试 {self.retries} 次）") from last_exc

    def _next_delay(self) -> float:
        """两次重试之间的等待，在 [retry_delay, retry_delay_max] 内随机。"""
        return random.uniform(self.retry_delay, self.retry_delay_max)

    def _parse(self, resp: HTTPResponse) -> dict[str, Any]:
        """解析响应体，失败时给出可诊断的信息。"""
        try:
            raw = resp.text
        except Exception as exc:  # 读取响应体失败
            raise APIError(f"读取响应失败: {exc}") from exc

        if not raw.strip():
            raise APIError("解析响应失败: 响应体为空")

        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise APIError(
                f"解析响应失败: {exc}（响应前 120 字符: {raw[:120]!r}）"
            ) from exc

        if not isinstance(data, dict):
            raise APIError(f"解析响应失败: 期望 JSON 对象，实际为 {type(data).__name__}")
        return data
