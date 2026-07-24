'use strict';

const path = require('node:path');
const { ManualRequiredError } = require('../errors');

function validateVideoContent(content, { title = false } = {}) {
  if (typeof content.body !== 'string' || !content.body.trim()) throw new Error('content.body is required');
  if (title && (typeof content.title !== 'string' || !content.title.trim())) {
    throw new Error('content.title is required');
  }
  if (typeof content.media_path !== 'string' || !path.isAbsolute(content.media_path)) {
    throw new Error('content.media_path must be an absolute path');
  }
}

async function uploadFirstVideo(page, mediaPath, timeout = 30000) {
  const input = await page.waitForSelector('input[type="file"][accept*="video"], input[type="file"]', { timeout });
  if (!input) throw new ManualRequiredError('dom_changed', 'Video file input was not found');
  await input.uploadFile(mediaPath);
}

async function insertText(page, element, value, { clear = true } = {}) {
  await element.focus();
  if (clear) {
    await element.evaluate(node => {
      const selection = getSelection();
      if (node instanceof HTMLInputElement || node instanceof HTMLTextAreaElement) {
        node.select();
        return;
      }
      const range = document.createRange();
      range.selectNodeContents(node);
      selection.removeAllRanges();
      selection.addRange(range);
    });
  }
  const session = await page.createCDPSession();
  try { await session.send('Input.insertText', { text: value }); } finally { await session.detach(); }
  const stored = await element.evaluate(node => ('value' in node ? node.value : node.innerText || node.textContent || ''));
  if (!stored.includes(value.slice(0, Math.min(value.length, 40)))) {
    throw new ManualRequiredError('content_not_filled', 'The platform editor did not retain the prepared text');
  }
}

async function visibleButton(page, labels) {
  const wanted = labels.map(item => item.toLowerCase());
  const handle = await page.evaluateHandle(values => {
    const nodes = [...document.querySelectorAll('button,[role="button"]')];
    return nodes.find(node => {
      const label = (node.innerText || node.textContent || node.getAttribute('aria-label') || '').trim().toLowerCase();
      const rect = node.getBoundingClientRect();
      return values.includes(label) && rect.width > 0 && rect.height > 0
        && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
    }) || null;
  }, wanted);
  return handle.asElement();
}

module.exports = { insertText, uploadFirstVideo, validateVideoContent, visibleButton };
