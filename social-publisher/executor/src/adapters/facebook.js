'use strict';

const { ManualRequiredError } = require('../errors');
const { assertNoChallenge } = require('../challenge');
const { clickByText, requireElement } = require('./dom');
const { insertText, uploadFirstVideo, validateVideoContent } = require('./video-dom');

function validateContent(content) { validateVideoContent(content); }

async function prepare(page, request) {
  await page.goto(request.target_url || 'https://www.facebook.com/', { waitUntil: 'domcontentloaded' });
  await assertNoChallenge(page);
  if (/login/i.test(page.url())) throw new ManualRequiredError('login_required', 'Facebook login is required');
  const existingReelUrls = await page.$$eval(
    'a[href*="/reel/"]',
    nodes => [...new Set(nodes.map(node => node.href.replace(/[?#].*$/, '')))],
  );
  const profileUrl = await page.evaluate(() => {
    const link = [...document.querySelectorAll('a[href]')].find(node => {
      const label = (node.getAttribute('aria-label') || node.innerText || '').toLowerCase();
      return /profile|个人主页|個人檔案/.test(label)
        && /facebook\.com\/(?:profile\.php|[^/?#]+\/?$)/i.test(node.href);
    });
    return link?.href || '';
  });
  const create = await page.waitForSelector(
    '[aria-label="Create a post"], [aria-label="创建帖子"], [aria-label="建立貼文"]',
    { timeout: 20000 },
  ).catch(() => null);
  if (create) {
    await create.click();
  } else {
    const opened = await clickByText(
      page,
      ["what's on your mind?", 'create post', '发帖', '在想些什么', '在想些什麼'],
    );
    if (!opened) throw new ManualRequiredError('dom_changed', 'Facebook post composer was not found');
  }
  const media = await page.$(
    '[aria-label="Photo/video"], [aria-label="照片/视频"], '
    + '[aria-label="相片/影片"], [aria-label="相片／影片"]',
  );
  if (media) await media.click();
  else {
    const photoVideo = await clickByText(page, ['photo/video', '照片/视频', '相片/影片', '相片／影片']);
    if (!photoVideo) throw new ManualRequiredError('dom_changed', 'Facebook photo/video control was not found');
  }
  await uploadFirstVideo(page, request.content.media_path);
  await new Promise(resolve => setTimeout(resolve, 1200));
  const editor = await requireElement(
    page, ['[role="dialog"] [contenteditable="true"][role="textbox"]', '[contenteditable="true"][role="textbox"]'],
    'dom_changed', 'Facebook post editor was not found',
  );
  await insertText(page, editor, request.content.body);
  return {
    target_url: page.url(), adapter: 'facebook_video_upload',
    profile_url: profileUrl, existing_reel_urls: existingReelUrls,
  };
}

async function confirm(page, content, detail = {}) {
  await assertNoChallenge(page);
  const handle = await page.evaluateHandle(() => {
    const dialogs = [...document.querySelectorAll('[role="dialog"]')]
      .filter(node => node.getBoundingClientRect().width > 0);
    const composer = dialogs.find(node => node.querySelector('video')
      && node.querySelector('[contenteditable="true"][role="textbox"]'));
    if (!composer) return null;
    return [...composer.querySelectorAll('button,[role="button"]')].find(node => {
      const label = (node.innerText || node.textContent || '').trim().toLowerCase();
      const rect = node.getBoundingClientRect();
      return ['post', '发布', '發佈'].includes(label) && rect.width > 0 && rect.height > 0
        && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
    }) || null;
  });
  const post = handle.asElement();
  if (!post) throw new ManualRequiredError('dom_changed', 'Facebook publish button was not found');
  await post.click();
  const profileUrl = typeof detail.profile_url === 'string' ? detail.profile_url : '';
  const existingReels = new Set(
    Array.isArray(detail.existing_reel_urls) ? detail.existing_reel_urls : [],
  );
  if (!/^https:\/\/www\.facebook\.com\//i.test(profileUrl)) {
    throw new ManualRequiredError('result_unverified', 'Facebook account profile URL was not found');
  }
  await new Promise(resolve => setTimeout(resolve, 3000));
  await page.goto(profileUrl, { waitUntil: 'domcontentloaded' });
  const deadline = Date.now() + 90000;
  while (Date.now() < deadline) {
    const url = await page.evaluate(needle => {
      for (const video of document.querySelectorAll('video')) {
        let card = video;
        for (let depth = 0; depth < 10 && card; depth += 1, card = card.parentElement) {
          if (!(card.innerText || '').includes(needle)) continue;
          const link = [...card.querySelectorAll('a[href]')].find(node =>
            /(?:permalink\.php\?.*story_fbid=|\/posts\/|\/videos\/|\/reel\/)/i.test(node.href));
          if (link) return link.href;
        }
      }
      return '';
    }, content.body.slice(0, 40));
    if (url) return { post_url: url };
    const notificationReel = await page.$$eval(
      'a[href*="/reel/"]',
      (nodes, before) => nodes.map(node => node.href.replace(/[?#].*$/, ''))
        .find(url => !before.includes(url)) || '',
      [...existingReels],
    );
    if (notificationReel) return { post_url: notificationReel };
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  throw new ManualRequiredError('result_unverified', 'Published Facebook post URL could not be verified');
}

module.exports = { confirm, prepare, validateContent };
