/**
 * 雷神 API 客户端 —— 对应 Python 版 api/client.py。
 *
 * 用 Workers 的 fetch 而不是 urllib：Python Workers 明确不支持 stdlib 的
 * 裸 socket 客户端（urllib.request / http.client）。
 *
 * 重试策略与 Python 版一致，正是为了兜住登录接口那个偶发的「网络异常」：
 *   - 网络异常（fetch 抛异常）
 *   - HTTP 5xx
 *   - HTTP 2xx 但响应体不是合法 JSON（网关抖动会返回空体或 HTML 错误页）
 *   - HTTP 4xx 但响应体不是 JSON（请求没到业务接口，中间被 WAF / CDN 拦了）
 * 能解析出业务错误码的 4xx 才是确定性错误（比如密码错），那种不重试。
 */

import { signParams } from "./sign.js";

export const DEFAULT_BASE_URL = "https://webapi.leigod.com";
export const DEFAULT_TIMEOUT = 5000;
export const DEFAULT_RETRIES = 10;
export const DEFAULT_RETRY_DELAY = 1000;
export const DEFAULT_RETRY_DELAY_MAX = 3000;

const PAUSE_PATH = "/api/user/pause";
const LOGIN_PATH = "/api/auth/login/v1";

export const DEFAULT_LANG = "zh_CN";
export const DEFAULT_COUNTRY_CODE = "86";
export const DEFAULT_SRC_CHANNEL = "guanwang";
const OS_TYPE_WEB = 4;

export const CODE_OK = 0;
export const CODE_ALREADY_PAUSED = 400803;
// 「登录态失效」一族：命中任何一个都该丢弃缓存、重新登录。
// 400007 是线上实际返回过的「当前登录态已过期，请重新登录」——服务端可以
// 提前作废令牌，不等 expiry_time。
export const CODE_TOKEN_EXPIRED = 400006;
export const TOKEN_EXPIRED_CODES = new Set([400006, 400007]);

export class APIError extends Error {}

export class LoginError extends APIError {
  constructor(code, msg) {
    super(`${code} - ${msg}`);
    this.name = "LoginError";
    this.code = code;
    this.msg = msg;
  }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function randomDelay(low, high) {
  return low + Math.random() * Math.max(0, high - low);
}

export class Client {
  constructor(options = {}) {
    this.baseUrl = (options.baseUrl ?? DEFAULT_BASE_URL).replace(/\/+$/, "");
    this.retries = Math.max(1, options.retries ?? DEFAULT_RETRIES);
    this.retryDelay = Math.max(0, options.retryDelay ?? DEFAULT_RETRY_DELAY);
    this.retryDelayMax = Math.max(this.retryDelay, options.retryDelayMax ?? DEFAULT_RETRY_DELAY_MAX);
    this.timeout = options.timeout ?? DEFAULT_TIMEOUT;
    // 便于测试注入
    this.fetchImpl = options.fetchImpl ?? ((...args) => fetch(...args));
    this.sleep = options.sleep ?? sleep;
  }

  async pause(accountToken, lang) {
    const data = await this.post(PAUSE_PATH, { account_token: accountToken, lang });
    return { code: data.code ?? 0, msg: data.msg ?? "" };
  }

  async login(username, passwordMd5, options = {}) {
    const payload = signParams({
      username,
      password: passwordMd5,
      user_type: "0",
      src_channel: options.srcChannel ?? DEFAULT_SRC_CHANNEL,
      code: "",
      country_code: options.countryCode ?? DEFAULT_COUNTRY_CODE,
      lang: options.lang ?? DEFAULT_LANG,
      os_type: OS_TYPE_WEB,
    });

    const data = await this.post(LOGIN_PATH, payload);

    const code = data.code ?? 0;
    if (code !== 0) {
      throw new LoginError(code, data.msg ?? "");
    }

    const loginInfo = (data.data && data.data.login_info) || {};
    const accountToken = loginInfo.account_token ?? "";
    if (!accountToken) {
      throw new APIError("登录响应缺少 account_token");
    }

    return { accountToken, expiryTime: loginInfo.expiry_time ?? "" };
  }

  /** POST JSON 并返回解析后的响应体，对可重试的失败做重试。 */
  async post(path, payload) {
    const body = JSON.stringify(payload);
    const url = `${this.baseUrl}${path}`;
    let lastError = "发送请求失败";
    let lastCause;

    for (let attempt = 1; attempt <= this.retries; attempt += 1) {
      let response;
      try {
        response = await this.fetchImpl(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body,
          signal: AbortSignal.timeout(this.timeout),
        });
      } catch (error) {
        lastError = `发送请求失败: ${error.message ?? error}`;
        lastCause = error;
      }

      if (response) {
        if (response.status >= 400 && response.status < 500) {
          try {
            return await this.parse(response);
          } catch (error) {
            // 4xx 但响应体不是我们的 JSON —— 说明请求根本没到业务接口，中间
            // 多半是 WAF / CDN 的拦截页。那属于基础设施问题，值得重试；
            // 能解析出业务码的 4xx 才是确定性错误。
            lastError = `发送请求失败: HTTP ${response.status}（${error.message}）`;
          }
        } else if (response.status >= 500) {
          lastError = `发送请求失败: HTTP ${response.status}`;
        } else if (response.status >= 300) {
          lastError = `发送请求失败: 意外的重定向 HTTP ${response.status}`;
        } else {
          try {
            return await this.parse(response);
          } catch (error) {
            lastError = error.message;
          }
        }
      }

      if (attempt < this.retries) {
        await this.sleep(randomDelay(this.retryDelay, this.retryDelayMax));
      }
    }

    throw new APIError(`${lastError}（已重试 ${this.retries} 次）`, { cause: lastCause });
  }

  /** 解析响应体，失败时给出可诊断的信息。 */
  async parse(response) {
    let raw;
    try {
      raw = await response.text();
    } catch (error) {
      throw new APIError(`读取响应失败: ${error.message ?? error}`);
    }

    if (!raw.trim()) {
      throw new APIError("解析响应失败: 响应体为空");
    }

    let data;
    try {
      data = JSON.parse(raw);
    } catch (error) {
      const head = raw.slice(0, 120);
      throw new APIError(`解析响应失败: ${error.message}（响应前 120 字符: ${head}）`);
    }

    if (typeof data !== "object" || data === null || Array.isArray(data)) {
      throw new APIError(`解析响应失败: 期望 JSON 对象，实际为 ${Array.isArray(data) ? "数组" : typeof data}`);
    }
    return data;
  }
}
