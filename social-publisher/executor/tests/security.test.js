'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const { once } = require('node:events');
const { assertAllowedUrl, redact, timingSafeSecret } = require('../src/security');
const { validateCommand } = require('../src/schema');
const { intentUrl } = require('../src/adapters/x');
const { submitUrl } = require('../src/adapters/reddit');
const { normalizeQuoraText } = require('../src/adapters/quora');
const { selectNewPublishedUrl } = require('../src/adapters/instagram');
const { assertNoChallenge } = require('../src/challenge');
const {
  MANAGED_PAGE_PREFIX, SocialExecutor, hash, managedPageName, softTimeout,
} = require('../src/executor');
const { startStandaloneExecutor } = require('../src/server');

function requestJson(url) {
  return new Promise((resolve, reject) => {
    http.get(url, response => {
      let raw = '';
      response.on('data', chunk => { raw += chunk; });
      response.on('end', () => {
        try {
          resolve({ status: response.statusCode, body: JSON.parse(raw) });
        } catch (error) {
          reject(error);
        }
      });
    }).on('error', reject);
  });
}

test('standalone launcher serves the canonical executor health interface', async t => {
  const sink = { write: () => {} };
  const server = startStandaloneExecutor({
    env: {
      SOCIAL_EXECUTOR_HOST: '127.0.0.1',
      SOCIAL_EXECUTOR_PORT: '0',
      SOCIAL_EXECUTOR_SHARED_SECRET: 'x'.repeat(32),
    },
    stdout: sink,
    stderr: sink,
    connectBrowser: async () => {
      throw new Error('browser connection is not expected in the health test');
    },
  });
  t.after(() => new Promise(resolve => server.close(resolve)));
  await once(server, 'listening');

  const address = server.address();
  const response = await requestJson(`http://127.0.0.1:${address.port}/health`);

  assert.equal(response.status, 200);
  assert.deepEqual(response.body, { status: 'ok' });
});

test('canonical executor requires a browser connector adapter', () => {
  assert.throws(() => new SocialExecutor(), /connectBrowser adapter is required/);
});

test('canonical executor exposes only debuggingPort to the browser connector port', async () => {
  let connectInput;
  const page = {
    setDefaultTimeout: () => {},
    evaluate: async () => {},
    screenshot: async () => {},
    target: () => ({ _targetId: 'target-port-contract' }),
    url: () => 'https://x.com/intent/post',
    isClosed: () => false,
    close: async () => {},
  };
  const browser = {
    pages: async () => [],
    newPage: async () => page,
    disconnect: async () => {},
  };
  const executor = new SocialExecutor({
    artifactDir: '.',
    connectBrowser: async input => {
      connectInput = input;
      return browser;
    },
    adapterFor: () => ({
      prepare: async () => ({ target_url: 'https://x.com/intent/post' }),
    }),
  });

  const result = await executor.prepare({
    command: 'prepare',
    platform: 'x',
    job_id: 'job-port-contract',
    container_code: 'container-port-contract',
    debugging_port: 9222,
    content: { body: 'Prepared content' },
  });

  assert.deepEqual(connectInput, { debuggingPort: 9222 });
  assert.equal(result.status, 'awaiting_review');
  await page.close();
  await browser.disconnect();
});

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

test('validates Quora and TikTok through the adapter registry', () => {
  const quora = validateCommand({
    command: 'prepare', job_id: 'job-q', container_code: 'env-1',
    platform: 'quora', debugging_port: 9222, content: { body: 'Useful answer' },
  });
  assert.equal(quora.platform, 'quora');
  const tiktok = validateCommand({
    command: 'prepare', job_id: 'job-t', container_code: 'env-1',
    platform: 'tiktok', debugging_port: 9222,
    content: { body: 'Short caption', media_path: 'C:\\videos\\clip.mp4' },
  });
  assert.equal(tiktok.platform, 'tiktok');
  assert.throws(() => validateCommand({
    command: 'prepare', job_id: 'job-t2', container_code: 'env-1',
    platform: 'tiktok', debugging_port: 9222,
    content: { body: 'Short caption', media_path: 'clip.mp4' },
  }), /absolute path/);
});

test('validates every video platform through the generic adapter registry', () => {
  for (const platform of ['youtube', 'instagram', 'facebook']) {
    const value = validateCommand({
      command: 'prepare', job_id: `job-${platform}`, container_code: 'env-1',
      platform, debugging_port: 9222,
      content: {
        title: platform === 'youtube' ? 'Device care basics' : '',
        body: 'A short maintenance reminder.',
        media_path: 'C:\\videos\\unique-clip.mp4',
      },
      target_url: platform === 'youtube'
        ? 'https://www.youtube.com/upload'
        : `https://www.${platform}.com/`,
    });
    assert.equal(value.platform, platform);
  }
});

test('YouTube adapter supports the Korean Studio controls used by Hubstudio 2876', () => {
  const { FINAL_LABELS, NEXT_LABELS } = require('../src/adapters/youtube');
  assert.ok(NEXT_LABELS.includes('다음'));
  assert.ok(FINAL_LABELS.includes('저장'));
});

test('YouTube adapter waits until a localized upload is no longer in progress', async () => {
  const { waitForUploadReady } = require('../src/adapters/youtube');
  let checks = 0;
  const page = {
    evaluate: async () => {
      checks += 1;
      return checks < 2;
    },
  };
  assert.equal(await waitForUploadReady(page, 2000), true);
  assert.equal(checks, 2);
});

test('accepts macOS absolute media paths', () => {
  const value = validateCommand({
    command: 'prepare', job_id: 'job-macos', container_code: 'env-macos',
    platform: 'tiktok', debugging_port: 9222,
    content: {
      body: 'A short maintenance reminder.',
      media_path: '/Users/alice/Exdivo/social-video/clip.mp4',
    },
  });
  assert.equal(value.content.media_path, '/Users/alice/Exdivo/social-video/clip.mp4');
});

test('normalizes Markdown-like Quora copy without joining hashtags to prose', () => {
  const source = [
    'Rechargeable-device warning signs',
    '',
    '#techmaintenanceWhat are the warning signs that a small rechargeable device should be retired?',
    '',
    'Changes matter more than complete failure.',
    '',
    '#batterysafety',
    '',
    '#devicecare',
  ].join('\n');
  assert.equal(
    normalizeQuoraText(source),
    [
      'Rechargeable-device warning signs',
      '',
      'What are the warning signs that a small rechargeable device should be retired?',
      '',
      'Changes matter more than complete failure.',
    ].join('\n'),
  );
});

test('Instagram verification rejects an old profile link that existed before publishing', () => {
  const oldPost = 'https://www.instagram.com/p/DbGvdS8IDTu/';
  assert.equal(
    selectNewPublishedUrl([oldPost], [oldPost]),
    '',
  );
  assert.equal(
    selectNewPublishedUrl([oldPost], [oldPost, 'https://www.instagram.com/reel/NEW123/']),
    'https://www.instagram.com/reel/NEW123/',
  );
});

test('keeps a container locked until the actual browser operation settles', async () => {
  const executor = new SocialExecutor({ connectBrowser: async () => {} });
  let finish;
  const pending = new Promise(resolve => { finish = resolve; });
  executor.confirm = async () => pending;

  const first = executor.run({
    command: 'confirm', job_id: 'job-1', container_code: 'env-1',
    confirmation_token: 'x',
  });
  await assert.rejects(
    executor.run({
      command: 'confirm', job_id: 'job-2', container_code: 'env-1',
      confirmation_token: 'y',
    }),
    /Container is busy/,
  );
  finish({ status: 'published', post_url: 'https://x.com/example/status/1' });
  assert.equal((await first).status, 'published');
  assert.equal(executor.locks.has('env-1'), false);
});

test('confirmation foregrounds a prepared page before touching platform controls', async () => {
  const order = [];
  const executor = new SocialExecutor({
    connectBrowser: async () => {},
    adapterFor: () => ({
      confirm: async () => {
        order.push('adapter_confirm');
        return { post_url: 'https://x.com/example/status/123' };
      },
    }),
  });
  const confirmationToken = 'review-token';
  executor.prepared.set('job-foreground', {
    browser: { disconnect: async () => { order.push('disconnect'); } },
    page: {
      bringToFront: async () => { order.push('foreground'); },
      close: async () => { order.push('close'); },
      isClosed: () => false,
      target: () => ({ _targetId: 'target-1' }),
      url: () => 'https://x.com/intent/post',
    },
    platform: 'x',
    containerCode: 'env-1',
    content: { body: 'Prepared content' },
    detail: {},
    tokenHash: hash(confirmationToken),
    targetId: 'target-1',
    preparedOrigin: 'https://x.com',
    expiresAt: Date.now() + 60_000,
  });

  const result = await executor.confirm({
    command: 'confirm',
    job_id: 'job-foreground',
    container_code: 'env-1',
    confirmation_token: confirmationToken,
  });

  assert.equal(result.status, 'published');
  assert.deepEqual(order.slice(0, 2), ['foreground', 'adapter_confirm']);
});

test('managed browser pages use opaque job-bound markers', () => {
  const marker = managedPageName('job-sensitive-id');
  assert.equal(marker.startsWith(MANAGED_PAGE_PREFIX), true);
  assert.equal(marker.includes('job-sensitive-id'), false);
  assert.equal(marker, managedPageName('job-sensitive-id'));
  assert.notEqual(marker, managedPageName('another-job'));
});

test('dispose closes only the exact leased page', async () => {
  const executor = new SocialExecutor({ connectBrowser: async () => {} });
  let closed = 0;
  let disconnected = 0;
  const state = {
    page: {
      isClosed: () => false,
      close: async () => { closed += 1; },
    },
    browser: {
      disconnect: async () => { disconnected += 1; },
    },
  };
  executor.prepared.set('job-1', state);
  await assert.rejects(
    executor.dispose('job-1', state, new Error('finished')),
    /finished/,
  );
  assert.equal(closed, 1);
  assert.equal(disconnected, 1);
  assert.equal(executor.prepared.has('job-1'), false);
});

test('orphan-page inspection cannot block the container indefinitely', async () => {
  const never = new Promise(() => {});
  const started = Date.now();
  assert.equal(await softTimeout(never, 20, 'skipped'), 'skipped');
  assert.ok(Date.now() - started < 250);
});

test('orphan-page discovery cannot block preparation indefinitely', async () => {
  const executor = new SocialExecutor({
    connectBrowser: async () => {},
    pageDiscoveryTimeoutMs: 20,
  });
  const started = Date.now();

  await executor.cleanupOrphanedManagedPages({
    pages: async () => new Promise(() => {}),
  });

  assert.ok(Date.now() - started < 250);
});

test('ignores preloaded invisible CAPTCHA frames but blocks visible challenges', async () => {
  const invisiblePage = {
    evaluate: async fn => fn.toString().includes('querySelectorAll') ? false : '',
  };
  await assert.doesNotReject(assertNoChallenge(invisiblePage));

  let call = 0;
  const visiblePage = {
    evaluate: async () => (++call === 1 ? true : ''),
  };
  await assert.rejects(
    assertNoChallenge(visiblePage),
    error => error.code === 'verification_required',
  );
});

test('executor emits correlated lifecycle stages without logging content or tokens', async () => {
  const events = [];
  const executor = new SocialExecutor({
    connectBrowser: async () => {},
    log: (event, fields) => events.push({ event, fields }),
  });
  executor.prepare = async () => ({ status: 'awaiting_review' });
  const request = {
    command: 'prepare',
    platform: 'x',
    job_id: 'job-log-test',
    container_code: 'container-log-test',
    content: { text: 'sensitive draft body' },
    confirmation_token: 'sensitive-token',
  };

  const result = await executor.run(request);

  assert.equal(result.status, 'awaiting_review');
  assert.deepEqual(events.map(item => item.event), ['command_started', 'command_completed']);
  const serialized = JSON.stringify(events);
  assert.match(serialized, /job-log-test/);
  assert.doesNotMatch(serialized, /sensitive draft body|sensitive-token/);
});

test('executor failure stage includes error metadata and correlation fields', async () => {
  const events = [];
  const executor = new SocialExecutor({
    connectBrowser: async () => {},
    log: (event, fields) => events.push({ event, fields }),
  });
  executor.prepare = async () => {
    const error = new Error('adapter exploded');
    error.code = 'E_ADAPTER';
    throw error;
  };

  await assert.rejects(
    executor.run({
      command: 'prepare',
      platform: 'instagram',
      job_id: 'job-failure-test',
      container_code: 'container-failure-test',
      content: { caption: 'do not log me' },
    }),
    /adapter exploded/,
  );

  const failed = events.find(item => item.event === 'command_failed');
  assert.equal(failed.fields.platform, 'instagram');
  assert.equal(failed.fields.error_code, 'E_ADAPTER');
  assert.equal(failed.fields.error_name, 'Error');
  assert.doesNotMatch(JSON.stringify(events), /do not log me/);
});
