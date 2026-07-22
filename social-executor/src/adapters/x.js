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
  const composer = await page.$('[data-testid="tweetTextarea_0"]');
  if (!composer) throw new ManualRequiredError('dom_changed', 'X composer was not found');
  return { target_url: page.url(), adapter: 'x_web_intent' };
}

async function confirm(page) {
  await assertNoChallenge(page);
  const button = await page.$('[data-testid="tweetButton"], [data-testid="tweetButtonInline"]');
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

module.exports = { confirm, intentUrl, prepare, waitForPostUrl };
