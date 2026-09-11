/**
 * MD5 —— Cloudflare Workers 的 crypto.subtle 不支持 MD5，只能自带一份。
 *
 * 输入按 UTF-8 编码（和 Python 的 hashlib.md5(s.encode("utf-8")) 一致，
 * 也和后端接口的要求一致），输出 32 位小写十六进制。
 */

const SHIFTS = [
  7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22,
  5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20,
  4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23,
  6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21,
];

// K[i] = floor(abs(sin(i + 1)) * 2^32)，标准定义
const K = new Uint32Array(64);
for (let i = 0; i < 64; i += 1) {
  K[i] = Math.floor(Math.abs(Math.sin(i + 1)) * 4294967296);
}

function rotl(value, bits) {
  return ((value << bits) | (value >>> (32 - bits))) >>> 0;
}

function toLittleEndianHex(word) {
  let out = "";
  for (let i = 0; i < 4; i += 1) {
    out += ((word >>> (i * 8)) & 0xff).toString(16).padStart(2, "0");
  }
  return out;
}

/** 对一段字节算 MD5，返回 32 位小写十六进制。 */
export function md5Bytes(bytes) {
  const length = bytes.length;
  const bitLength = length * 8;

  // 补一个 0x80，再用 0 填到 length ≡ 56 (mod 64)，最后 8 字节放位长度
  const totalLength = (((length + 8) >>> 6) + 1) << 6;
  const buffer = new Uint8Array(totalLength);
  buffer.set(bytes);
  buffer[length] = 0x80;

  const view = new DataView(buffer.buffer);
  view.setUint32(totalLength - 8, bitLength >>> 0, true);
  view.setUint32(totalLength - 4, Math.floor(bitLength / 4294967296), true);

  let a0 = 0x67452301;
  let b0 = 0xefcdab89;
  let c0 = 0x98badcfe;
  let d0 = 0x10325476;

  const chunk = new Uint32Array(16);

  for (let offset = 0; offset < totalLength; offset += 64) {
    for (let i = 0; i < 16; i += 1) {
      chunk[i] = view.getUint32(offset + i * 4, true);
    }

    let a = a0;
    let b = b0;
    let c = c0;
    let d = d0;

    for (let i = 0; i < 64; i += 1) {
      let f;
      let g;
      if (i < 16) {
        f = (b & c) | (~b & d);
        g = i;
      } else if (i < 32) {
        f = (d & b) | (~d & c);
        g = (5 * i + 1) % 16;
      } else if (i < 48) {
        f = b ^ c ^ d;
        g = (3 * i + 5) % 16;
      } else {
        f = c ^ (b | ~d);
        g = (7 * i) % 16;
      }

      f = (f + a + K[i] + chunk[g]) >>> 0;
      a = d;
      d = c;
      c = b;
      b = (b + rotl(f, SHIFTS[i])) >>> 0;
    }

    a0 = (a0 + a) >>> 0;
    b0 = (b0 + b) >>> 0;
    c0 = (c0 + c) >>> 0;
    d0 = (d0 + d) >>> 0;
  }

  return (
    toLittleEndianHex(a0) + toLittleEndianHex(b0) +
    toLittleEndianHex(c0) + toLittleEndianHex(d0)
  );
}

/** 对字符串算 MD5（按 UTF-8 编码）。 */
export function md5(text) {
  return md5Bytes(new TextEncoder().encode(text));
}
