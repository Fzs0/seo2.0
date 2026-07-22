'use strict';

const crypto = require('node:crypto');

const ALLOWED_HOSTS = new Set([
  'x.com', 'twitter.com', 'www.reddit.com', 'reddit.com'
]);

function timingSafeSecret(actual, expected) {
  if (!actual || !expected) return false;
  const a = Buffer.from(String(actual));
  const b = Buffer.from(String(expected));
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

function assertAllowedUrl(rawUrl) {
  let parsed;
  try { parsed = new URL(rawUrl); } catch { throw new Error('Invalid URL'); }
  if (parsed.protocol !== 'https:' || !ALLOWED_HOSTS.has(parsed.hostname.toLowerCase())) {
    throw new Error('URL is outside the social platform allowlist');
  }
  return parsed;
}

function redact(value) {
  if (value === null || value === undefined) return value;
  if (Array.isArray(value)) return value.map(redact);
  if (typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => {
      if (/secret|token|cookie|authorization/i.test(key)) return [key, '[REDACTED]'];
      return [key, redact(item)];
    }));
  }
  return typeof value === 'string'
    ? value.replace(/(Bearer\s+)[A-Za-z0-9._~-]+/gi, '$1[REDACTED]')
    : value;
}

module.exports = { assertAllowedUrl, redact, timingSafeSecret };
