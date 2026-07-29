'use strict';

const { ManualRequiredError } = require('../errors');
const { assertNoChallenge } = require('../challenge');
const { insertText, uploadFirstVideo, validateVideoContent, visibleButton } = require('./video-dom');

const NEXT_LABELS = ['next', '다음'];
const FINAL_LABELS = ['save', 'publish', 'done', '저장', '게시', '완료'];

function validateContent(content) {
  validateVideoContent(content, { title: true });
  if (content.title.length > 100) throw new Error('content.title exceeds 100 characters');
}

async function prepare(page, request) {
  await page.goto(request.target_url || 'https://www.youtube.com/upload', { waitUntil: 'domcontentloaded' });
  await assertNoChallenge(page);
  if (/accounts\.google\.com|signin/i.test(page.url())) {
    throw new ManualRequiredError('login_required', 'YouTube login is required');
  }
  await uploadFirstVideo(page, request.content.media_path, 45000);
  const title = await page.waitForSelector(
    'ytcp-social-suggestions-textbox#title-textarea #textbox, #title-textarea [contenteditable="true"]',
    { timeout: 30000 },
  );
  await insertText(page, title, request.content.title);
  const description = await page.waitForSelector(
    'ytcp-social-suggestions-textbox#description-textarea #textbox, #description-textarea [contenteditable="true"]',
    { timeout: 15000 },
  );
  await insertText(page, description, request.content.body);
  const audience = await page.$('tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]');
  if (audience) await audience.click();
  for (let step = 0; step < 3; step += 1) {
    const next = await waitForVisibleButton(page, NEXT_LABELS, 30000);
    if (!next) {
      throw new ManualRequiredError('dom_changed', 'YouTube next button was not found');
    }
    await next.click();
    await new Promise(resolve => setTimeout(resolve, 700));
  }
  const publicVisibility = await page.$('tp-yt-paper-radio-button[name="PUBLIC"]');
  if (publicVisibility) await publicVisibility.click();
  const uploadReady = await waitForUploadReady(page, 240000);
  if (!uploadReady) {
    throw new ManualRequiredError('upload_incomplete', 'YouTube upload did not finish');
  }
  const finalButton = await waitForVisibleButton(page, FINAL_LABELS, 180000);
  if (!finalButton) {
    throw new ManualRequiredError('upload_incomplete', 'YouTube upload did not become ready to publish');
  }
  return { target_url: page.url(), adapter: 'youtube_studio_upload' };
}

async function confirm(page) {
  await assertNoChallenge(page);
  const done = await visibleButton(page, FINAL_LABELS);
  if (!done) throw new ManualRequiredError('dom_changed', 'YouTube final publish button was not found');
  await done.click();
  const deadline = Date.now() + 90000;
  while (Date.now() < deadline) {
    const url = await page.evaluate(() => {
      const input = document.querySelector('input[value*="youtu.be/"], input[value*="youtube.com/watch"]');
      if (input?.value) return input.value;
      const link = [...document.querySelectorAll('a[href]')].find(a => /youtu\.be\/|youtube\.com\/watch\?v=/.test(a.href));
      return link?.href || '';
    });
    if (url) return { post_url: url };
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  throw new ManualRequiredError('result_unverified', 'Published YouTube video URL could not be verified');
}

async function waitForVisibleButton(page, labels, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const button = await visibleButton(page, labels);
    if (button) return button;
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  return null;
}

async function waitForUploadReady(page, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const uploading = await page.evaluate(() => {
      const text = document.body?.innerText || '';
      return /\b\d+%\s*(?:uploading|업로드 중)/i.test(text);
    });
    if (!uploading) return true;
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  return false;
}

module.exports = {
  FINAL_LABELS,
  NEXT_LABELS,
  confirm,
  prepare,
  validateContent,
  waitForUploadReady,
};
