'use strict';

const { ManualRequiredError } = require('../errors');

async function firstVisible(page, selectors, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    for (const selector of selectors) {
      const handles = await page.$$(selector);
      for (const handle of handles) {
        const visible = await handle.evaluate(node => {
          const rect = node.getBoundingClientRect();
          const style = getComputedStyle(node);
          return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
        });
        if (visible) return handle;
      }
    }
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  return null;
}

async function replaceText(element, value) {
  await element.evaluate(node => {
    node.focus();
    if ('value' in node) node.value = '';
    else node.textContent = '';
    node.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'deleteContentBackward' }));
  });
  const page = element.frame;
  await element.type(value, { delay: 1 });
}

async function clickByText(page, labels, selector = 'button,[role="button"]') {
  const wanted = labels.map(label => label.toLowerCase());
  const handles = await page.$$(selector);
  for (const handle of handles) {
    const state = await handle.evaluate(node => {
      const rect = node.getBoundingClientRect();
      const style = getComputedStyle(node);
      return {
        label: (node.innerText || node.textContent || '').trim().toLowerCase(),
        interactable: rect.width > 0 && rect.height > 0
          && style.visibility !== 'hidden' && style.display !== 'none'
          && !node.disabled && node.getAttribute('aria-disabled') !== 'true',
      };
    });
    if (state.interactable && wanted.includes(state.label)) {
      await handle.click();
      return true;
    }
  }
  return false;
}

async function requireElement(page, selectors, code, message) {
  const element = await firstVisible(page, selectors);
  if (!element) throw new ManualRequiredError(code, message);
  return element;
}

module.exports = { clickByText, firstVisible, replaceText, requireElement };
