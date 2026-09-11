/**
 * Cloudflare Worker 入口 —— 对应 Python 版的 runner.py + index.py。
 *
 * 触发方式是 Cron Triggers（见 wrangler.toml）。注意 **Cron Triggers 按 UTC 执行**，
 * 所以北京时间凌晨 1 点要写成 `0 17 * * *`。
 *
 * 另外提供了一个 fetch 处理器，部署后可以直接用浏览器访问一次来手动验证。
 */

import {
  CODE_ALREADY_PAUSED,
  CODE_OK,
  CODE_TOKEN_EXPIRED,
  APIError,
  Client,
  LoginError,
} from "./api.js";
import { ConfigError, loadConfig, loadNotifySettings } from "./config.js";
import { TokenCache } from "./cache.js";
import { PushPlusNotifier, buildMessages } from "./notify.js";

const consoleLogger = {
  info: (message) => console.log(message),
  error: (message) => console.error(message),
};

/** 取令牌：优先复用缓存，缓存失效（400006）时丢掉并重新登录。 */
async function acquireToken(client, config, account, cache, prefix, log) {
  if (cache.enabled) {
    const entry = await cache.get(account.phone);
    if (entry) {
      log(`🔑${prefix}复用缓存的令牌（有效期至 ${entry.expiryTime || "未知"}）`);
      return { token: entry.accountToken, cached: true };
    }
  }

  log(`🔑${prefix}登录获取令牌中…`);
  const info = await client.login(account.phone, account.passwordMd5, {
    countryCode: config.countryCode,
    lang: config.lang,
    srcChannel: config.srcChannel,
  });
  log(`✔️${prefix}登录成功，令牌有效期至 ${info.expiryTime}`);
  if (config.showToken) {
    log(`🔑${prefix}account_token=${info.accountToken}`);
  }
  await cache.put(account.phone, {
    accountToken: info.accountToken,
    expiryTime: info.expiryTime,
  });
  return { token: info.accountToken, cached: false };
}

function loginFailure(account, error) {
  if (error instanceof LoginError) {
    return { label: account.label, ok: false, step: "login", code: error.code, message: error.msg };
  }
  return { label: account.label, ok: false, step: "login", code: 0, message: error.message };
}

/** 处理单个账户：失败不抛异常，而是返回一条失败结果。 */
async function pauseAccount(client, config, account, cache, prefix, log) {
  let token;
  let cached;
  try {
    ({ token, cached } = await acquireToken(client, config, account, cache, prefix, log));
  } catch (error) {
    if (error instanceof APIError) {
      const failed = loginFailure(account, error);
      log(`❌${prefix}登录失败: ${failed.message}`);
      return failed;
    }
    throw error;
  }

  let response;
  try {
    response = await client.pause(token, config.lang);
  } catch (error) {
    if (error instanceof APIError) {
      log(`❌${prefix}暂停失败: ${error.message}`);
      return { label: account.label, ok: false, step: "pause", code: 0, message: error.message };
    }
    throw error;
  }

  if (response.code === CODE_TOKEN_EXPIRED && cached) {
    // 缓存里的令牌失效了：丢掉它，重新登录再试一次
    log(`⚠️${prefix}缓存令牌已失效，重新登录`);
    await cache.drop(account.phone);
    try {
      ({ token } = await acquireToken(client, config, account, cache, prefix, log));
      response = await client.pause(token, config.lang);
    } catch (error) {
      if (error instanceof APIError) {
        const failed = loginFailure(account, error);
        log(`❌${prefix}登录失败: ${failed.message}`);
        return failed;
      }
      throw error;
    }
  }

  if (response.code !== 0) {
    if (response.code === CODE_ALREADY_PAUSED) {
      log(`👌${prefix}已经暂停: ${response.code} - ${response.msg}`);
      return { label: account.label, ok: true, step: "pause", code: response.code, message: response.msg };
    }
    log(`❌${prefix}暂停失败: ${response.code} - ${response.msg}`);
    return { label: account.label, ok: false, step: "pause", code: response.code, message: response.msg };
  }

  log(`${prefix}${response.code}:${response.msg}`);
  log(`✔️${prefix}暂停成功`);
  return { label: account.label, ok: true, step: "pause", code: response.code, message: response.msg };
}

async function notify(settings, result, tokens, log) {
  const messages = buildMessages(settings, result, tokens);
  if (messages.length === 0) return;

  const notifiers = new Map();
  let accepted = 0;
  for (const message of messages) {
    if (!notifiers.has(message.token)) {
      notifiers.set(message.token, new PushPlusNotifier(message.token, settings.topic, settings.template));
    }
    // eslint-disable-next-line no-await-in-loop
    if (await notifiers.get(message.token).send(message.title, message.content)) {
      accepted += 1;
    }
  }

  if (accepted === messages.length) {
    log(`📮已提交推送 ${accepted}/${messages.length} 条（模式 ${settings.mode}）`);
  } else {
    log(`⚠️推送提交失败 ${messages.length - accepted}/${messages.length} 条（不影响本次运行结果）`);
  }
}

/** 跑一轮「登录 + 暂停」。任何情况下都返回结果对象，不抛异常。 */
export async function runOnce(env, log = consoleLogger.info, deps = {}) {
  const cache = new TokenCache(env.TOKEN_CACHE, deps);

  let config;
  try {
    config = loadConfig(env);
  } catch (error) {
    if (!(error instanceof ConfigError)) throw error;
    log(`❌错误: ${error.message}`);
    const settings = loadNotifySettings(env);
    const result = { ok: false, step: "config", code: 0, message: error.message, accounts: [] };
    await notify(settings, result, [], log);
    return result;
  }

  const client = deps.client ?? new Client({ retries: config.retries, ...deps.clientOptions });
  await cache.prune(config.accounts.map((account) => account.phone));

  const multiple = config.accounts.length > 1;
  const accounts = [];
  for (const account of config.accounts) {
    const prefix = multiple ? `[${account.label}] ` : "";
    // eslint-disable-next-line no-await-in-loop
    accounts.push(await pauseAccount(client, config, account, cache, prefix, log));
  }

  if (multiple) {
    const failed = accounts.filter((a) => !a.ok);
    log(failed.length > 0
      ? `❌${accounts.length} 个账户中 ${failed.length} 个失败`
      : `✔️${accounts.length} 个账户全部处理成功`);
  }

  const firstFailure = accounts.find((a) => !a.ok);
  const result = firstFailure
    ? { ok: false, step: firstFailure.step, code: firstFailure.code, message: firstFailure.message, accounts }
    : { ok: true, step: "done", code: 0, message: "", accounts };

  await notify(config.notify, result, config.accounts.map((a) => a.pushplusToken), log);
  return result;
}

export default {
  /** Cron Trigger 触发时调用。 */
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(
      (async () => {
        console.log(`⏰触发定时任务: ${controller.cron}`);
        const result = await runOnce(env);
        if (!result.ok) {
          console.error(`❌执行失败: step=${result.step} code=${result.code} message=${result.message}`);
        }
      })(),
    );
  },

  /** 手动触发一次，方便部署后验证。 */
  async fetch(request, env, ctx) {
    const lines = [];
    const result = await runOnce(env, (line) => lines.push(line));
    return Response.json(
      {
        ok: result.ok,
        step: result.step,
        code: result.code,
        message: result.message,
        accounts: result.accounts,
        logs: lines,
      },
      { status: result.ok ? 200 : 500 },
    );
  },
};
