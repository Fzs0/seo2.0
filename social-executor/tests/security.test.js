'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { assertAllowedUrl, redact, timingSafeSecret } = require('../src/security');
const { validateCommand } = require('../src/schema');
const { intentUrl } = require('../src/adapters/x');
const { submitUrl } = require('../src/adapters/reddit');

test('allows only HTTPS X and Reddit URLs', () => {
  assert.equal(assertAllowedUrl('https://x.com/intent/post').hostname, 'x.com');
  assert.throws(() => assertAllowedUrl('http://x.com/intent/post'));
  assert.throws(() => assertAllowedUrl('https://x.com.evil.test/intent/post'));
});

test('redacts nested secrets and bearer values', () => {
  assert.deepEqual(redact({ token: 'abc', nested: { app_secret: 'def' } }), { token: '[REDACTED]', nested: { app_secret: '[REDACTED]' } });
  assert.equal(redact('Bearer abc.def'), 'Bearer [REDACTED]');
});

test('compares shared secrets safely', () => {
  assert.equal(timingSafeSecret('a'.repeat(32), 'a'.repeat(32)), true);
  assert.equal(timingSafeSecret('a', 'b'), false);
});

test('builds encoded X intent without browser activity', () => {
  const parsed = new URL(intentUrl({ body: 'hello & 世界', url: 'https://example.com/a' }));
  assert.equal(parsed.origin, 'https://x.com');
  assert.equal(parsed.searchParams.get('text'), 'hello & 世界');
});

test('builds and validates Reddit submission', () => {
  assert.equal(submitUrl({ subreddit: 'r/test_community' }), 'https://www.reddit.com/r/test_community/submit');
  assert.throws(() => submitUrl({ subreddit: '../evil' }));
  const value = validateCommand({ command: 'prepare', job_id: 'job-1', container_code: 'env-1', platform: 'reddit', debugging_port: 9222, content: { title: 'Title', body: 'Body', subreddit: 'test' } });
  assert.equal(value.platform, 'reddit');
});

test('confirm requires one-time confirmation token', () => {
  assert.throws(() => validateCommand({ command: 'confirm', job_id: 'job-1', container_code: 'env-1' }), /confirmation_token/);
});
