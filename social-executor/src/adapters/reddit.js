'use strict';

const { ManualRequiredError } = require('../errors');
const { assertNoChallenge } = require('../challenge');

function submitUrl(content) {
  const subreddit = content.subreddit.replace(/^r\//i, '');
  if (!/^[A-Za-z0-9_]{2,64}$/.test(subreddit)) throw new Error('Invalid subreddit');
  return `https://www.reddit.com/r/${subreddit}/submit`;
}

async function setFirst(page, selectors, value) {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    for (const selector of selectors) {
      const handle = await page.evaluateHandle(candidate => {
        function find(root) {
          for (const element of root.querySelectorAll(candidate)) {
            const rect = element.getBoundingClientRect();
            const style = getComputedStyle(element);
            if (rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none') {
              return element;
            }
          }
          for (const element of root.querySelectorAll('*')) {
            if (element.shadowRoot) {
              const nested = find(element.shadowRoot);
              if (nested) return nested;
            }
          }
          return null;
        }
        return find(document);
      }, selector);
      const node = handle.asElement();
      if (!node) continue;
      await node.evaluate(element => {
        element.focus();
        if ('value' in element) element.value = '';
        else element.textContent = '';
        element.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'deleteContentBackward' }));
      });
      await page.keyboard.type(value);
      return true;
    }
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  return false;
}

async function prepare(page, request) {
  await page.goto(submitUrl(request.content), { waitUntil: 'domcontentloaded' });
  await assertNoChallenge(page);
  if (/login/i.test(new URL(page.url()).pathname)) throw new ManualRequiredError('login_required', 'Reddit login is required');
  const titleOk = await setFirst(page, ['textarea[name="title"]', 'input[name="title"]', '[placeholder*="Title"]'], request.content.title);
  if (!titleOk) throw new ManualRequiredError('dom_changed', 'Reddit title input was not found');
  if (request.content.url) {
    const urlOk = await setFirst(page, ['input[name="url"]', 'textarea[name="url"]', '[placeholder*="Url"]', '[placeholder*="URL"]'], request.content.url);
    if (!urlOk) throw new ManualRequiredError('dom_changed', 'Reddit URL input was not found');
  }
  const bodyOk = await setFirst(page, ['textarea[name="text"]', '[contenteditable="true"]'], request.content.body);
  if (!bodyOk) throw new ManualRequiredError('dom_changed', 'Reddit body editor was not found');
  return { target_url: page.url(), adapter: 'reddit_web_submit' };
}

async function confirm(page) {
  await assertNoChallenge(page);
  const buttons = await page.$$('button');
  let submit = null;
  for (const button of buttons) {
    const label = await button.evaluate(node => (node.innerText || node.textContent || '').trim().toLowerCase());
    if (label === 'post' || label === 'submit') { submit = button; break; }
  }
  if (!submit) throw new ManualRequiredError('dom_changed', 'Reddit publish button was not found');
  await submit.click();
  const pattern = /https:\/\/(?:www\.)?reddit\.com\/r\/[^/]+\/comments\/[a-z0-9]+\//i;
  const end = Date.now() + 30000;
  while (Date.now() < end) {
    const match = page.url().match(pattern);
    if (match) return { post_url: match[0] };
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  throw new ManualRequiredError('result_unverified', 'Published Reddit post URL could not be verified');
}

module.exports = { confirm, prepare, submitUrl };
