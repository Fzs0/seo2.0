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
      const session = await page.createCDPSession();
      try {
        await session.send('Input.insertText', { text: value });
      } finally {
        await session.detach();
      }
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
  const bodyOk = await setFirst(
    page,
    [
      '[contenteditable="true"][name="body"]',
      'textarea[name="text"]',
      '[contenteditable="true"][data-testid*="body"]',
      '[contenteditable="true"]',
    ],
    request.content.body,
  );
  if (!bodyOk) throw new ManualRequiredError('dom_changed', 'Reddit body editor was not found');
  return { target_url: page.url(), adapter: 'reddit_web_submit' };
}

async function confirm(page, content) {
  await page.bringToFront();
  await assertNoChallenge(page);
  const findSubmit = () => page.evaluateHandle(() => {
    function find(root) {
      for (const button of root.querySelectorAll('button')) {
        const label = (button.innerText || button.textContent || '').trim().toLowerCase();
        const rect = button.getBoundingClientRect();
        if ((label === 'post' || label === 'submit') && rect.width > 0 && rect.height > 0 && !button.disabled) {
          return button;
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
  });
  let handle = await findSubmit();
  let submit = handle.asElement();
  if (!submit) throw new ManualRequiredError('dom_changed', 'Reddit publish button was not found');
  const submission = observeRedditSubmission(page);
  await clickAtCenter(page, submit);
  const mutation = await submission;
  await new Promise(resolve => setTimeout(resolve, 1500));
  if (/\/submit/i.test(page.url())) {
    const errorText = await visiblePlatformError(page);
    throw new ManualRequiredError(
      mutation ? 'platform_rejected' : 'submit_not_observed',
      errorText || (mutation
        ? `Reddit rejected the submit request (HTTP ${mutation.status})`
        : 'Reddit did not send a create-post request after the publish click'),
    );
  }
  const pattern = /https:\/\/(?:www\.)?reddit\.com\/r\/[^/]+\/comments\/[a-z0-9]+\//i;
  const end = Date.now() + 30000;
  while (Date.now() < end) {
    const match = page.url().match(pattern);
    if (match) return { post_url: match[0] };
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  await page.goto('https://www.reddit.com/api/me.json', { waitUntil: 'domcontentloaded' });
  const raw = await page.$eval('body', node => node.innerText);
  let username = '';
  try { username = JSON.parse(raw)?.data?.name || ''; } catch {}
  if (username) {
    await page.goto(`https://www.reddit.com/user/${encodeURIComponent(username)}/submitted/`, {
      waitUntil: 'domcontentloaded',
    });
    const verified = await page.evaluate(title => {
      for (const anchor of document.querySelectorAll('a[href*="/comments/"]')) {
        const card = anchor.closest('article, shreddit-post') || anchor.parentElement;
        if ((card?.innerText || '').includes(title)) {
          const removed = (card?.innerText || '').includes('removed by Reddit');
          return { post_url: anchor.href, remote_status: removed ? 'removed' : 'published' };
        }
      }
      return null;
    }, content.title);
    if (verified) return verified;
  }
  throw new ManualRequiredError('result_unverified', 'Published Reddit post URL could not be verified');
}

function observeRedditSubmission(page, timeoutMs = 10000) {
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
      const payload = request.postData() || '';
      if (request.method() === 'POST'
          && /reddit\.com\/(?:svc\/shreddit\/graphql|api\/submit)/i.test(url)
          && /createpost|submitpost|postcreate|create_post|submit/i.test(`${url} ${payload}`)) {
        finish({ status: response.status(), url });
      }
    };
    const timer = setTimeout(() => finish(null), timeoutMs);
    page.on('response', onResponse);
  });
}

async function visiblePlatformError(page) {
  return page.evaluate(() => {
    const messages = [];
    function walk(root) {
      for (const node of root.querySelectorAll(
        '[role="alert"], [aria-live="assertive"], [aria-live="polite"], [data-testid*="error"]',
      )) {
        const rect = node.getBoundingClientRect();
        const text = (node.innerText || node.textContent || '').trim();
        if (rect.width > 0 && rect.height > 0 && text) messages.push(text);
      }
      for (const node of root.querySelectorAll('*')) if (node.shadowRoot) walk(node.shadowRoot);
    }
    walk(document);
    return messages.find(text => !/notification|inbox/i.test(text)) || '';
  });
}

async function clickAtCenter(page, element) {
  await element.evaluate(node => node.scrollIntoView({ block: 'center', inline: 'center' }));
  const box = await element.boundingBox();
  if (!box) throw new ManualRequiredError('dom_changed', 'Reddit publish button is not visible');
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
}

function validateContent(content) {
  if (typeof content.title !== 'string' || !content.title.trim() || content.title.length > 300) throw new Error('content.title is required');
  if (typeof content.body !== 'string' || !content.body.trim()) throw new Error('content.body is required');
  if (typeof content.subreddit !== 'string' || !content.subreddit.trim()) throw new Error('content.subreddit is required');
}

module.exports = { confirm, prepare, submitUrl, validateContent };
