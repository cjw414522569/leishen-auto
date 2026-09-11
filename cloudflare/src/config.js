/**
 * 配置 —— 对应 Python 版 config/config.py。
 *
 * Workers 的 env 是个可枚举对象，所以能直接扫出所有 PHONE_n，
 * 不像 FunctionGraph 那样得按固定上限逐个探测。
 */

import { md5 as md5Hex } from "./md5.js";

export class ConfigError extends Error {}

const NOTIFY_ALWAYS = "always";
const NOTIFY_ON_CHANGE = "on_change";
export const NOTIFY_MODES = [NOTIFY_ALWAYS, NOTIFY_ON_CHANGE];

const GROUP_COMBINED = "combined";
const GROUP_PER_ACCOUNT = "per_account";
export const NOTIFY_GROUPINGS = [GROUP_COMBINED, GROUP_PER_ACCOUNT];

const INDEXED_PHONE = /^PHONE_(\d+)$/;
const INDEXED_PASSWORD = /^PASSWORD_(\d+)$/;

export const DEFAULTS = {
  lang: "zh_CN",
  countryCode: "86",
  srcChannel: "guanwang",
  retries: 10,
  showToken: false,
  notifyMode: NOTIFY_ALWAYS,
  notifyGrouping: GROUP_COMBINED,
  notifyTemplate: "txt",
};

/** 把手机号打码后再写进日志 —— 云日志可能被更广的人看到。 */
export function maskPhone(phone) {
  const digits = String(phone ?? "").trim();
  if (digits.length >= 8) {
    return `${digits.slice(0, 3)}****${digits.slice(-4)}`;
  }
  if (digits.length > 2) {
    return `${digits[0]}****${digits.slice(-1)}`;
  }
  return "****";
}

function text(env, name) {
  const value = env[name];
  return value === undefined || value === null ? "" : String(value).trim();
}

function flag(env, name) {
  return ["1", "true", "yes", "on"].includes(text(env, name).toLowerCase());
}

function resolvePasswordMd5(env, suffix) {
  // 密码不 trim —— 首尾空格可能是密码本身的一部分
  const plain = env[`PASSWORD${suffix}`];
  return plain === undefined || plain === null || plain === "" ? "" : md5Hex(String(plain));
}

/**
 * 收集账户。两种写法可混用：不带编号的 PHONE 排在最前，其余按编号升序。
 * 任何一组不完整都直接报错，而不是悄悄跳过。
 */
export function collectAccounts(env) {
  const globalToken = text(env, "PUSHPLUS_TOKEN");
  const accounts = [];

  const phone = text(env, "PHONE");
  if (phone) {
    const passwordMd5 = resolvePasswordMd5(env, "");
    if (!passwordMd5) {
      throw new ConfigError("需要同时配置 PHONE 与 PASSWORD");
    }
    accounts.push({
      index: 0,
      phone,
      passwordMd5,
      pushplusToken: text(env, "PUSHPLUS_TOKEN") || globalToken,
      label: maskPhone(phone),
    });
  }

  const indices = Object.keys(env)
    .map((key) => INDEXED_PHONE.exec(key))
    .filter(Boolean)
    .map((match) => Number(match[1]))
    .sort((a, b) => a - b);

  const known = new Set(indices);

  // 只配了 PASSWORD_n 而漏了 PHONE_n 时索引推不出来，这一组会被无声忽略
  for (const key of Object.keys(env)) {
    const match = INDEXED_PASSWORD.exec(key);
    if (!match) continue;
    const index = Number(match[1]);
    if (!known.has(index) && text(env, key)) {
      throw new ConfigError(
        `账户 ${index} 只配了 PASSWORD_${index}，缺少 PHONE_${index}；请补全，或删掉这一项`,
      );
    }
  }

  for (const index of indices) {
    const suffix = `_${index}`;
    const phoneValue = text(env, `PHONE${suffix}`);
    const passwordMd5 = resolvePasswordMd5(env, suffix);
    if (!phoneValue && !passwordMd5) continue; // 整组留空，视为占位符
    if (!phoneValue || !passwordMd5) {
      throw new ConfigError(
        `账户 ${index} 配置不完整：需要同时配置 PHONE${suffix} 与 PASSWORD${suffix}`,
      );
    }
    accounts.push({
      index,
      phone: phoneValue,
      passwordMd5,
      pushplusToken: text(env, `PUSHPLUS_TOKEN${suffix}`) || globalToken,
      label: `账户${index} ${maskPhone(phoneValue)}`,
    });
  }

  return accounts;
}

function positiveInt(raw, fallback) {
  const value = Number.parseInt(String(raw ?? "").trim(), 10);
  return Number.isFinite(value) && value >= 1 ? value : fallback;
}

/** 只读推送配置 —— 账户配错时主流程拿不到 config，但仍要能把失败原因推出去。 */
export function loadNotifySettings(env) {
  const mode = (text(env, "NOTIFY_MODE") || DEFAULTS.notifyMode).toLowerCase();
  const grouping = (text(env, "NOTIFY_GROUPING") || DEFAULTS.notifyGrouping).toLowerCase();
  return {
    token: text(env, "PUSHPLUS_TOKEN"),
    topic: text(env, "PUSHPLUS_TOPIC"),
    template: text(env, "PUSHPLUS_TEMPLATE") || DEFAULTS.notifyTemplate,
    mode: NOTIFY_MODES.includes(mode) ? mode : DEFAULTS.notifyMode,
    grouping: NOTIFY_GROUPINGS.includes(grouping) ? grouping : DEFAULTS.notifyGrouping,
  };
}

/** 读取全部配置；账户缺失直接抛 ConfigError。 */
export function loadConfig(env) {
  const accounts = collectAccounts(env);
  if (accounts.length === 0) {
    throw new ConfigError(
      "没有配置任何账户：需要配置 PHONE + PASSWORD，多账户时使用 " +
        "PHONE_1/PASSWORD_1、PHONE_2/PASSWORD_2…",
    );
  }

  const mode = (text(env, "NOTIFY_MODE") || DEFAULTS.notifyMode).toLowerCase();
  if (!NOTIFY_MODES.includes(mode)) {
    throw new ConfigError(`NOTIFY_MODE 只能是 ${NOTIFY_MODES.join(" 或 ")}，当前为 ${mode}`);
  }

  const grouping = (text(env, "NOTIFY_GROUPING") || DEFAULTS.notifyGrouping).toLowerCase();
  if (!NOTIFY_GROUPINGS.includes(grouping)) {
    throw new ConfigError(
      `NOTIFY_GROUPING 只能是 ${NOTIFY_GROUPINGS.join(" 或 ")}，当前为 ${grouping}`,
    );
  }

  return {
    accounts,
    lang: text(env, "API_LANG") || DEFAULTS.lang,
    countryCode: text(env, "COUNTRY_CODE") || DEFAULTS.countryCode,
    srcChannel: text(env, "SRC_CHANNEL") || DEFAULTS.srcChannel,
    retries: positiveInt(env.RETRIES, DEFAULTS.retries),
    showToken: flag(env, "SHOW_TOKEN"),
    notify: {
      token: text(env, "PUSHPLUS_TOKEN"),
      topic: text(env, "PUSHPLUS_TOPIC"),
      template: text(env, "PUSHPLUS_TEMPLATE") || DEFAULTS.notifyTemplate,
      mode,
      grouping,
    },
  };
}
