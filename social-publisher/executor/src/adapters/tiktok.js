'use strict';

const path = require('node:path');
const { ManualRequiredError } = require('../errors');
const { assertNoChallenge } = require('../challenge');
const { clickByText, requireElement } = require('./dom');

function validateContent(content) {
  if (typeof content.body !== 'string' || !content.body.trim()) throw new Error('content.body is required');
  if (typeof content.media_path !== 'string' || !path.isAbsolute(content.media_path)) {
    throw new Error('content.media_path must be an absolute path');
  }
}

async function prepare(page, request) {
  await page.goto(request.target_url || 'https://www.tiktok.com/tiktokstudio/upload', { waitUntil: 'domcontentloaded' });
  await assertNoChallenge(page);
  if (/login/i.test(page.url())) throw new ManualRequiredError('login_required', 'TikTok login is required');
  const input = await page.waitForSelector('input[type="file"]', { timeout: 20000 });
  if (!input) throw new ManualRequiredError('dom_changed', 'TikTok video input was not found');
  await input.uploadFile(request.content.media_path);
  const editor = await requireElement(
    page,
    ['[contenteditable="true"]', 'textarea[placeholder*="caption" i]', 'textarea'],
    'dom_changed',
    'TikTok caption editor was not found',
  );
  await editor.focus();
  await page.keyboard.down('Control');
  await page.keyboard.press('A');
  await page.keyboard.up('Control');
  const session = await page.createCDPSession();
  try { await session.send('Input.insertText', { text: request.content.body }); } finally { await session.detach(); }
  return { target_url: page.url(), adapter: 'tiktok_studio_upload' };
}

async function confirm(page) {
  await assertNoChallenge(page);
  if (await clickByText(page, ['got it'])) {
    await new Promise(resolve => setTimeout(resolve, 750));
  }
  const submit = await requireElement(
    page, ['[data-e2e="post_video_button"]'], 'dom_changed', 'TikTok publish button was not found',
  );
  await submit.click();
  await new Promise(resolve => setTimeout(resolve, 5000));
  const platformError = await page.evaluate(() => {
    const candidates = [...document.querySelectorAll('[role="alert"], [aria-live], [class*="status-error"]')];
    return candidates.map(node => (node.innerText || node.textContent || '').trim())
      .find(text => /something went wrong|try again|failed|error/i.test(text)) || '';
  });
  if (platformError) {
    throw new ManualRequiredError('platform_rejected', `TikTok rejected the publish action: ${platformError}`);
  }
  const stillReady = await submit.evaluate(node => {
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0 && !node.disabled
      && node.getAttribute('aria-disabled') !== 'true';
  }).catch(() => false);
  if (stillReady) {
    await submit.click();
  }
  const deadline = Date.now() + 90000;
  while (Date.now() < deadline) {
    const links = await page.$$eval('a[href]', nodes => nodes.map(node => node.href));
    const found = links.find(url => /^https:\/\/(?:www\.)?tiktok\.com\/@[^/]+\/video\/\d+/i.test(url));
    if (found) return { post_url: found };
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  throw new ManualRequiredError('result_unverified', 'Published TikTok video URL could not be verified');
}

module.exports = { confirm, prepare, validateContent };
