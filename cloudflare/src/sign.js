/**
 * 接口签名 —— 与 Python 版 api/sign.py 同一套算法，
 * 也已用官网前端 chunk-common.js 里的原函数做过差分验证。
 *
 * ts（秒级时间戳）+ 参数按 key 升序排序 + 拼成 k=v&k=v（值不做 URL 编码）
 * + 末尾追加 &key=<内置密钥>，取 MD5 小写十六进制。
 */

import { md5 } from "./md5.js";

// 页面里硬编码的签名密钥（chunk-common.js -> c.sign）
export const SIGN_KEY = "5C5A639C20665313622F51E93E3F2783";

/** 拼出待签名串，便于排查签名不一致时直接打印比对。 */
export function buildSignedString(params, ts) {
  const merged = { ts, ...params };
  const pairs = Object.keys(merged)
    .sort()
    .map((key) => `${key}=${merged[key]}`);
  return `${pairs.join("&")}&key=${SIGN_KEY}`;
}

/**
 * 返回带 ts 与 sign 的完整请求参数。
 * ts 参数仅用于测试固定时间戳；真实调用不传。
 */
export function signParams(params, ts) {
  const tsStr = String(ts ?? Math.floor(Date.now() / 1000));
  return {
    ...params,
    ts: tsStr,
    sign: md5(buildSignedString(params, tsStr)),
  };
}
