'use strict';

const { ManualRequiredError } = require('../errors');
const { assertNoChallenge } = require('../challenge');
const { clickByText, requireElement } = require('./dom');
const { insertText, uploadFirstVideo, validateVideoContent } = require('./video-dom');

function validateContent(content) { validateVideoContent(content); }

async function prepare(page, request) {
  await page.goto(request.target_url || 'https://www.instagram.com/', { waitUntil: 'domcontentloaded' });
  await assertNoChallenge(page);
  if (/accounts\/login/i.test(page.url())) throw new ManualRequiredError('login_required', 'Instagram login is required');
  const profileUrl = await page.evaluate(() => {
    const profileIcon = document.querySelector('svg[aria-label="Profile"], svg[aria-label="个人主页"]');
    const iconLink = profileIcon?.closest('a[href]');
    if (iconLink?.href) return iconLink.href;
    return [...document.querySelectorAll('a[href]')].map(node => node.href).find(url => {
      try {
        const parsed = new URL(url);
        return parsed.hostname === 'www.instagram.com'
          && /^\/[^/]+\/$/.test(parsed.pathname)
          && !/^\/(?:explore|direct|accounts|reels)\/$/i.test(parsed.pathname);
      } catch { return false; }
    }) || '';
  });
  const createIcon = await page.waitForSelector(
    '[aria-label="New post"], [aria-label="Create"]',
    { timeout: 20000 },
  ).catch(() => null);
  if (createIcon) {
    await createIcon.evaluate(node => (node.closest('a,[role="button"]') || node).click());
  } else {
    const opened = await clickByText(page, ['create', 'new post', '创建']);
    if (!opened) throw new ManualRequiredError('dom_changed', 'Instagram create control was not found');
  }
  await uploadFirstVideo(page, request.content.media_path);
  const noticeDeadline = Date.now() + 10000;
  while (Date.now() < noticeDeadline) {
    const accepted = await clickByText(page, ['ok', '好的', '确定']);
    if (accepted) {
      await new Promise(resolve => setTimeout(resolve, 600));
      break;
    }
    await new Promise(resolve => setTimeout(resolve, 300));
  }
  for (let step = 0; step < 2; step += 1) {
    let next = false;
    const deadline = Date.now() + 20000;
    while (!next && Date.now() < deadline) {
      next = await clickByText(page, ['next', '下一步']);
      if (!next) await new Promise(resolve => setTimeout(resolve, 400));
    }
    if (!next) break;
    await new Promise(resolve => setTimeout(resolve, 800));
  }
  const caption = await requireElement(
    page,
    [
      'textarea[aria-label*="caption" i]',
      '[contenteditable="true"][aria-label*="caption" i]',
      '[role="dialog"] textarea',
      '[role="dialog"] [contenteditable="true"][role="textbox"]',
      'textarea',
    ],
    'dom_changed',
    'Instagram caption editor was not found',
  );
  await insertText(page, caption, request.content.body);
  return {
    target_url: page.url(),
    adapter: 'instagram_reel_upload',
    profile_url: profileUrl,
  };
}

function selectNewPublishedUrl(before, after) {
  const existing = new Set(before);
  return after.find(url => !existing.has(url)) || '';
}

async function postUrls(page) {
  return page.$$eval(
    'a[href]',
    nodes => [...new Set(nodes.map(node => node.href).filter(
      url => /^https:\/\/www\.instagram\.com\/(?:reel|p)\/[^/?#]+\/?$/i.test(url),
    ))],
  );
}

async function confirm(page, _content, detail = {}) {
  await assertNoChallenge(page);
  const profileUrl = typeof detail.profile_url === 'string' ? detail.profile_url : '';
  if (!/^https:\/\/www\.instagram\.com\/[^/]+\/$/i.test(profileUrl)) {
    throw new ManualRequiredError('result_unverified', 'Instagram account profile URL was not found');
  }
  const handle = await page.evaluateHandle(() => {
    const dialogs = [...document.querySelectorAll('[role="dialog"]')]
      .filter(node => node.getBoundingClientRect().width > 0);
    const composer = dialogs.find(node => /new reel|new post|新建/i.test(node.innerText || ''))
      || dialogs.find(node => node.querySelector('[aria-label*="caption" i]'));
    if (!composer) return null;
    return [...composer.querySelectorAll('button,[role="button"]')].find(node => {
      const label = (node.innerText || node.textContent || '').trim().toLowerCase();
      const rect = node.getBoundingClientRect();
      return ['share', '发布', '分享'].includes(label) && rect.width > 0 && rect.height > 0
        && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
    }) || null;
  });
  const share = handle.asElement();
  if (!share) throw new ManualRequiredError('dom_changed', 'Instagram share button was not found');
  await share.click();
  await new Promise(resolve => setTimeout(resolve, 3000));
  await page.goto(profileUrl, { waitUntil: 'domcontentloaded' });
  const deadline = Date.now() + 90000;
  while (Date.now() < deadline) {
    const urls = await postUrls(page);
    for (const url of urls.slice(0, 3)) {
      const verification = await page.browser().newPage();
      try {
        await verification.goto(url, { waitUntil: 'domcontentloaded' });
        const text = await verification.$eval('body', node => node.innerText || '');
        if (text.includes(_content.body.slice(0, 40))) return { post_url: url };
      } finally {
        await verification.close();
      }
    }
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  throw new ManualRequiredError('result_unverified', 'No Instagram profile post matched the prepared caption');
}

module.exports = { confirm, prepare, selectNewPublishedUrl, validateContent };
