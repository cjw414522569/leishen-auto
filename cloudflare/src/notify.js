/**
 * PushPlus 推送 —— 对应 Python 版 notify.py。
 *
 * 两个和 Python 版一致的要点：
 * 1. 发送接口是异步的，code=200 只代表服务端受理，不代表送达 —— 日志写「已提交」。
 * 2. 推送失败只记日志，绝不改变本次运行结果。
 *
 * 账户按投递目标归组：共用 token 的账户合成一条；各自有 token 的每账户一条。
 */

import { CODE_ALREADY_PAUSED, CODE_OK } from "./api.js";

export const PUSHPLUS_ENDPOINT = "https://www.pushplus.plus/send";
const SUCCESS_CODE = 200;

export class PushPlusNotifier {
  constructor(token, topic = "", template = "txt", options = {}) {
    this.token = token;
    this.topic = topic;
    this.template = template;
    this.fetchImpl = options.fetchImpl ?? ((...args) => fetch(...args));
    this.timeout = options.timeout ?? 5000;
  }

  /** 提交一条推送，返回是否被服务端受理。任何异常都吞掉。 */
  async send(title, content) {
    const payload = { token: this.token, title, content, template: this.template };
    if (this.topic) payload.topic = this.topic;

    try {
      const response = await this.fetchImpl(PUSHPLUS_ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(this.timeout),
      });
      if (response.status >= 400) return false;
      const body = JSON.parse(await response.text());
      return typeof body === "object" && body !== null && body.code === SUCCESS_CODE;
    } catch {
      return false;
    }
  }
}

/** 整组：失败一定推；成功时按模式决定。 */
export function shouldNotify(settings, result) {
  if (!result.ok) return true;
  if (settings.mode === "on_change") {
    return result.accounts.some((a) => a.ok && a.code === CODE_OK);
  }
  return settings.mode === "always";
}

/** 单账户：失败一定推；其余按模式决定。 */
export function shouldNotifyAccount(settings, account) {
  if (!account.ok) return true;
  if (settings.mode === "on_change") return account.code === CODE_OK;
  return settings.mode === "always";
}

function describe(account) {
  if (!account.ok) return `失败（${account.message || "未知原因"}）`;
  if (account.code === CODE_ALREADY_PAUSED) return "已经是暂停状态";
  return "已暂停";
}

function stamp(now) {
  const d = now ?? new Date();
  const p = (n) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
    `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
  );
}

export function formatTitle(result) {
  if (!result.ok) return "雷神加速器：执行失败";
  if (result.accounts.some((a) => a.ok && a.code === CODE_OK)) return "雷神加速器：已暂停";
  return "雷神加速器：无需处理";
}

export function formatContent(result, now) {
  const lines = [stamp(now), ""];
  if (result.accounts.length === 0) {
    lines.push(`❌ ${result.message || "执行失败"}`);
    return lines.join("\n");
  }

  const failed = result.accounts.filter((a) => !a.ok);
  if (failed.length > 0) {
    lines.push(`❌ ${result.accounts.length} 个账户中 ${failed.length} 个失败`);
    lines.push(`失败原因：${result.message || "未知"}`);
  } else {
    lines.push(`✅ ${result.accounts.length} 个账户全部处理成功`);
  }
  lines.push("");
  for (const account of result.accounts) {
    lines.push(`• ${account.label}：${describe(account)}`);
  }
  return lines.join("\n");
}

export function accountTitle(account) {
  if (!account.ok) return `雷神加速器：${account.label} 执行失败`;
  if (account.code === CODE_ALREADY_PAUSED) return `雷神加速器：${account.label} 无需处理`;
  return `雷神加速器：${account.label} 已暂停`;
}

export function accountContent(account, now) {
  const icon = account.ok ? "✅" : "❌";
  return `${stamp(now)}\n\n${icon} ${account.label}：${describe(account)}`;
}

/**
 * 算出这次要发哪几条推送，返回 [{token, title, content}]。
 * tokens 与 result.accounts 一一对应（账户自己的 token，空则回落全局）。
 */
export function buildMessages(settings, result, tokens = [], now) {
  const accounts = result.accounts;
  const tokenFor = (index) => (tokens[index] || "") || settings.token;

  if (accounts.length === 0) {
    if (!settings.token || !shouldNotify(settings, result)) return [];
    return [{ token: settings.token, title: formatTitle(result), content: formatContent(result, now) }];
  }

  if (settings.grouping === "per_account") {
    return accounts
      .map((account, index) => ({ account, token: tokenFor(index) }))
      .filter(({ account, token }) => token && shouldNotifyAccount(settings, account))
      .map(({ account, token }) => ({
        token,
        title: accountTitle(account),
        content: accountContent(account, now),
      }));
  }

  const grouped = new Map();
  accounts.forEach((account, index) => {
    const token = tokenFor(index);
    if (!token) return;
    if (!grouped.has(token)) grouped.set(token, []);
    grouped.get(token).push(account);
  });

  const messages = [];
  for (const [token, group] of grouped) {
    const failed = group.filter((a) => !a.ok);
    const subset = failed.length
      ? { ok: false, step: failed[0].step, code: failed[0].code, message: failed[0].message, accounts: group }
      : { ok: true, step: "done", code: 0, message: "", accounts: group };
    if (shouldNotify(settings, subset)) {
      messages.push({ token, title: formatTitle(subset), content: formatContent(subset, now) });
    }
  }
  return messages;
}
