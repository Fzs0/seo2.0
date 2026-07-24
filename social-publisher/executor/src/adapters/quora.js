'use strict';

const { ManualRequiredError } = require('../errors');
const { assertNoChallenge } = require('../challenge');
const { clickByText, requireElement } = require('./dom');

function validateContent(content) {
  if (typeof content.body !== 'string' || !content.body.trim()) throw new Error('content.body is required');
}

function normalizeQuoraText(value) {
  const lines = String(value || '').replace(/\r\n?/g, '\n').split('\n');
  const normalized = [];
  for (let line of lines) {
    line = line.replace(/^\s*```[^\n]*$/, '');
    line = line.replace(/^\s*#{1,6}\s+/, '');
    line = line.replace(/^\s*#[a-z0-9_]+(?=[A-Z])/, '');
    if (/^\s*(?:#[A-Za-z0-9_]+\s*)+$/.test(line)) line = '';
    line = line.replace(/(?:\s+#[A-Za-z0-9_]+)+\s*$/, '');
    line = line.trimEnd();
    if (!line && normalized.at(-1) === '') continue;
    normalized.push(line);
  }
  return normalized.join('\n').trim();
}

async function prepare(page, request) {
  await page.goto(request.target_url || 'https://www.quora.com/', { waitUntil: 'domcontentloaded' });
  await assertNoChallenge(page);
  if (/login/i.test(page.url())) throw new ManualRequiredError('login_required', 'Quora login is required');
  let opened = false;
  const deadline = Date.now() + 20000;
  while (!opened && Date.now() < deadline) {
    opened = await clickByText(page, ['post', 'create post', 'add post', '发帖', '创建帖子']);
    if (!opened) await new Promise(resolve => setTimeout(resolve, 400));
  }
  if (!opened) throw new ManualRequiredError('dom_changed', 'Quora create-post control was not found');
  const editor = await requireElement(
    page,
    ['[contenteditable="true"][role="textbox"]', '[contenteditable="true"]'],
    'dom_changed',
    'Quora post editor was not found',
  );
  await editor.focus();
  await page.keyboard.down('Control');
  await page.keyboard.press('KeyA');
  await page.keyboard.up('Control');
  await page.keyboard.press('Backspace');
  const normalized = normalizeQuoraText(request.content.body);
  const session = await page.createCDPSession();
  try {
    await session.send('Input.insertText', { text: normalized });
  } finally {
    await session.detach();
  }
  const actual = await editor.evaluate(node => (node.innerText || node.textContent || '')
    .replace(/\r\n?/g, '\n').replace(/\n{3,}/g, '\n\n').trim());
  if (actual !== normalized.replace(/\n{3,}/g, '\n\n')) {
    throw new ManualRequiredError('content_mismatch', 'Quora editor did not retain exactly the reviewed content');
  }
  return { target_url: page.url(), adapter: 'quora_web_post' };
}

async function confirm(page, content) {
  await page.bringToFront();
  await assertNoChallenge(page);
  const profileUrl = await page.evaluate(() => {
    const dialog = [...document.querySelectorAll('[role="dialog"]')]
      .filter(node => node.getBoundingClientRect().width > 0)
      .sort((a, b) => a.innerText.length - b.innerText.length)[0];
    return dialog?.querySelector('a[href*="/profile/"]')?.href || '';
  });
  const handle = await page.evaluateHandle(() => {
    const dialogs = [...document.querySelectorAll('[role="dialog"]')]
      .filter(node => node.getBoundingClientRect().width > 0)
      .sort((a, b) => a.innerText.length - b.innerText.length);
    for (const dialog of dialogs) {
      const candidates = [...dialog.querySelectorAll(
        'button.puppeteer_test_modal_submit, button,[role="button"]',
      )].filter(node => {
      const label = (node.innerText || node.textContent || '').trim().toLowerCase();
      const rect = node.getBoundingClientRect();
      return (label === 'post' || label === '发布') && rect.width > 0 && rect.height > 0
        && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
      });
      const explicit = candidates.find(node => node.classList.contains('puppeteer_test_modal_submit'));
      if (explicit) return explicit;
      if (candidates.length) return candidates.sort((a, b) =>
        b.getBoundingClientRect().y - a.getBoundingClientRect().y)[0];
    }
    return null;
  });
  const submit = handle.asElement();
  if (!submit) throw new ManualRequiredError('dom_changed', 'Quora publish button was not found');
  const submission = observeQuoraSubmission(page);
  await clickAtCenter(page, submit);
  const mutation = await submission;
  await new Promise(resolve => setTimeout(resolve, 1500));
  const stillOpenAfterRetry = await page.evaluate(() => [...document.querySelectorAll('[role="dialog"]')]
    .some(dialog => dialog.getBoundingClientRect().width > 0
      && (dialog.innerText || '').includes('Choose credential')));
  if (stillOpenAfterRetry) {
    const errorText = await visiblePlatformError(page);
    throw new ManualRequiredError(
      mutation ? 'platform_rejected' : 'submit_not_observed',
      errorText || (mutation
        ? `Quora rejected the submit request (HTTP ${mutation.status})`
        : 'Quora did not send a create-post request after the publish click'),
    );
  }
  if (!profileUrl) throw new ManualRequiredError('result_unverified', 'Quora account profile URL was not found');
  await new Promise(resolve => setTimeout(resolve, 3000));
  await page.goto(profileUrl, { waitUntil: 'domcontentloaded' });
  const needle = normalizeQuoraText(content.body).slice(0, 40);
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    const found = await page.evaluate(value => {
      for (const anchor of document.querySelectorAll('a[href]')) {
        const card = anchor.closest('[class]') || anchor.parentElement;
        if ((card?.innerText || '').includes(value) && /\/profile\/[^/]+\//.test(anchor.href)) return anchor.href;
      }
      return '';
    }, needle);
    if (found) return { post_url: found };
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  throw new ManualRequiredError('result_unverified', 'Published Quora post URL could not be verified');
}

function observeQuoraSubmission(page, timeoutMs = 10000) {
  return new Promise(resolve => {
    let done = false;
    const finish = value => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      page.off('response', onResponse);
      resolve(value);
    };
    const onResponse = response => {
      const request = response.request();
      const url = request.url();
      if (request.method() === 'POST'
          && /quora\.com\/graphql\//i.test(url)
          && /(?:create|publish).*post|post.*(?:create|publish)/i.test(`${url} ${request.postData() || ''}`)
          && !/draft/i.test(url)) {
        finish({ status: response.status(), url });
      }
    };
    const timer = setTimeout(() => finish(null), timeoutMs);
    page.on('response', onResponse);
  });
}

async function visiblePlatformError(page) {
  return page.evaluate(() => [...document.querySelectorAll(
    '[role="alert"], [aria-live="assertive"], [aria-live="polite"]',
  )].filter(node => {
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }).map(node => (node.innerText || node.textContent || '').trim())
    .find(text => text && !/saved|draft/i.test(text)) || '');
}

async function clickAtCenter(page, element) {
  await element.evaluate(node => node.scrollIntoView({ block: 'center', inline: 'center' }));
  const box = await element.boundingBox();
  if (!box) throw new ManualRequiredError('dom_changed', 'Quora publish button is not visible');
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
}

module.exports = { confirm, normalizeQuoraText, prepare, validateContent };
