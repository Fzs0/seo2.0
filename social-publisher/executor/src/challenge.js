'use strict';

const { ManualRequiredError } = require('./errors');

async function assertNoChallenge(page) {
  const challengeVisible = await page.evaluate(() => [
    ...document.querySelectorAll(
      'iframe[src*="captcha" i], iframe[src*="challenge" i], .g-recaptcha, [data-testid*="captcha" i]',
    ),
  ].some(node => {
    const rect = node.getBoundingClientRect();
    const style = getComputedStyle(node);
    return rect.width > 1 && rect.height > 1
      && style.display !== 'none' && style.visibility !== 'hidden'
      && Number(style.opacity || 1) > 0;
  }));
  // Reddit and Quora preload invisible CAPTCHA/Turnstile iframes on normal
  // composer pages. Presence in the DOM is not a challenge; only a visible,
  // actionable verification surface should block automation.
  if (challengeVisible) throw new ManualRequiredError('verification_required', 'Platform verification or CAPTCHA requires manual handling');
  const text = await page.evaluate(() => (document.body?.innerText || '').slice(0, 5000).toLowerCase());
  if (/verify (that )?you are human|complete the captcha|security check|unusual activity/.test(text)) {
    throw new ManualRequiredError('verification_required', 'Platform verification requires manual handling');
  }
}

module.exports = { assertNoChallenge };
