/**
 * 令牌缓存 —— 对应 Python 版 token_cache.py，只是把文件换成了 Workers KV。
 *
 * KV 是可选的：没绑命名空间时整个缓存自动停用，每次都重新登录。
 * 绑定方式见同目录 wrangler.toml 里的注释。
 *
 * 里面是等同凭据的东西，别把 KV 命名空间设成公开可读。
 */

const CACHE_KEY = "tokens";
const REFRESH_MARGIN_MS = 60 * 60 * 1000; // 距过期不足 1 小时就当需要重新登录

/** 解析 expiry_time，解析不了返回 null（调用方按「未知」处理）。 */
export function parseExpiry(raw) {
  const text = String(raw ?? "").trim();
  if (!text) return null;

  if (/^\d+$/.test(text)) {
    let seconds = Number(text);
    if (!Number.isFinite(seconds)) return null;
    if (seconds > 1e11) seconds = Math.floor(seconds / 1000); // 毫秒时间戳
    const date = new Date(seconds * 1000);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  const date = new Date(text.replace(" ", "T"));
  return Number.isNaN(date.getTime()) ? null : date;
}

/** 解析不出有效期时返回 true（先用着），真失效了靠 400006 兜底。 */
export function isFresh(expiryTime, marginMs = REFRESH_MARGIN_MS, now = Date.now()) {
  const expiresAt = parseExpiry(expiryTime);
  if (!expiresAt) return true;
  return expiresAt.getTime() - marginMs > now;
}

export class TokenCache {
  constructor(kv, options = {}) {
    this.kv = kv ?? null;
    this.marginMs = options.marginMs ?? REFRESH_MARGIN_MS;
  }

  get enabled() {
    return Boolean(this.kv);
  }

  async load() {
    if (!this.kv) return {};
    try {
      const raw = await this.kv.get(CACHE_KEY);
      if (!raw) return {};
      const parsed = typeof raw === "string" ? JSON.parse(raw) : raw;
      return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
    } catch {
      return {}; // 缓存坏了只降级，不影响本次运行
    }
  }

  async save(entries) {
    if (!this.kv) return;
    // 顺手清掉已过期的条目，免得 KV 里长期留着等同凭据的旧令牌
    const kept = {};
    for (const [phone, entry] of Object.entries(entries)) {
      if (entry && entry.accountToken && isFresh(entry.expiryTime, this.marginMs)) {
        kept[phone] = { accountToken: entry.accountToken, expiryTime: entry.expiryTime ?? "" };
      }
    }
    try {
      await this.kv.put(CACHE_KEY, JSON.stringify(kept));
    } catch {
      // 写不进去只是降级 —— 下次重新登录而已，不该影响本次运行
    }
  }

  async get(phone) {
    const entries = await this.load();
    const entry = entries[phone];
    return entry && isFresh(entry.expiryTime, this.marginMs) ? entry : null;
  }

  async put(phone, entry) {
    const entries = await this.load();
    entries[phone] = entry;
    await this.save(entries);
  }

  async drop(phone) {
    const entries = await this.load();
    if (!(phone in entries)) return;
    delete entries[phone];
    await this.save(entries);
  }

  /** 丢掉不在 keep 里的条目 —— 账户从配置里删掉后不该继续留在缓存里。 */
  async prune(keep) {
    const entries = await this.load();
    const keepSet = new Set(keep);
    let changed = false;
    for (const phone of Object.keys(entries)) {
      if (!keepSet.has(phone)) {
        delete entries[phone];
        changed = true;
      }
    }
    if (changed) await this.save(entries);
  }
}
