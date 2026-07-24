'use strict';

const { ManualRequiredError } = require('../errors');
const { assertNoChallenge } = require('../challenge');

function intentUrl(content) {
  const url = new URL('https://x.com/intent/post');
  url.searchParams.set('text', content.body);
  if (content.url) url.searchParams.set('url', content.url);
  return url.toString();
}

async function prepare(page, request) {
  await page.goto(intentUrl(request.content), { waitUntil: 'domcontentloaded' });
  await assertNoChallenge(page);
  const current = new URL(page.url());
  if (/login|flow\/login/i.test(current.pathname)) throw new ManualRequiredError('login_required', 'X login is required');
  const composer = await page.waitForSelector('[data-testid="tweetTextarea_0"]', { timeout: 20000 });
  if (!composer) throw new ManualRequiredError('dom_changed', 'X composer was not found');
  return { target_url: page.url(), adapter: 'x_web_intent' };
}

async function confirm(page) {
  await assertNoChallenge(page);
  const handle = await page.evaluateHandle(() => {
    return [...document.querySelectorAll('[data-testid="tweetButton"], [data-testid="tweetButtonInline"]')]
      .find(node => {
        const rect = node.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0 && !node.disabled
          && node.getAttribute('aria-disabled') !== 'true';
      }) || null;
  });
  const button = handle.asElement();
  if (!button) throw new ManualRequiredError('dom_changed', 'X publish button was not found');
  await button.click();
  const postUrl = await waitForPostUrl(page, /https:\/\/x\.com\/[^/]+\/status\/\d+/i);
  return { post_url: postUrl };
}

async function waitForPostUrl(page, pattern, timeoutMs = 30000) {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    const match = page.url().match(pattern);
    if (match) return match[0];
    const anchors = await page.$$eval('a[href]', links => links.map(a => a.href));
    const found = anchors.find(link => pattern.test(link));
    if (found) return found.match(pattern)[0];
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  throw new ManualRequiredError('result_unverified', 'Published X post URL could not be verified');
}

function validateContent(content) {
  if (typeof content.body !== 'string' || !content.body.trim()) throw new Error('content.body is required');
}

module.exports = { confirm, intentUrl, prepare, validateContent, waitForPostUrl };
