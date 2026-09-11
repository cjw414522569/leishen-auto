"""雷神 webapi 接口签名。

算法取自页面 ``chunk-common.js`` 中 ``c.sign``，已把页面原函数抽出来在 Node 里
跑，与本实现做跨语言差分验证：相同输入下签名逐字节一致（详见 tests/test_sign.py
里的黄金向量）。

签名步骤：

1. ``ts`` = 当前 Unix 秒级时间戳（字符串形式）
2. 与业务参数合并后，按 key 升序排序
3. 序列化成 ``k=v`` 并以 ``&`` 连接，**值不做 URL 编码**
4. 末尾追加 ``&key=<SIGN_KEY>``
5. 取 MD5 小写十六进制作为 ``sign``
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Mapping

# 页面里硬编码的签名密钥（chunk-common.js -> c.sign）
SIGN_KEY = "5C5A639C20665313622F51E93E3F2783"


def md5_hex(text: str) -> str:
    """MD5 小写十六进制，对应页面上用的 blueimp-md5。"""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def build_signed_string(params: Mapping[str, Any], ts: str) -> str:
    """拼出待签名串。

    单独暴露出来是为了排查签名不一致时能直接打印比对。
    """
    merged: dict[str, Any] = {"ts": ts, **params}
    joined = "&".join(f"{key}={merged[key]}" for key in sorted(merged))
    return f"{joined}&key={SIGN_KEY}"


def sign_params(params: Mapping[str, Any], ts: int | None = None) -> dict[str, Any]:
    """返回带 ``ts`` 与 ``sign`` 的完整请求参数。

    ``ts`` 参数仅用于测试固定时间戳；真实调用不传。
    """
    ts_str = str(int(time.time()) if ts is None else int(ts))
    return {**params, "ts": ts_str, "sign": md5_hex(build_signed_string(params, ts_str))}
