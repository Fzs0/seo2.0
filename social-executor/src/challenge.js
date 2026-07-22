'use strict';

const { ManualRequiredError } = require('./errors');

async function assertNoChallenge(page) {
  const challenge = await page.$('iframe[src*="captcha" i], iframe[src*="challenge" i], .g-recaptcha, [data-testid*="captcha" i]');
  if (challenge) throw new ManualRequiredError('verification_required', 'Platform verification or CAPTCHA requires manual handling');
  const text = await page.evaluate(() => (document.body?.innerText || '').slice(0, 5000).toLowerCase());
  if (/verify (that )?you are human|complete the captcha|security check|unusual activity/.test(text)) {
    throw new ManualRequiredError('verification_required', 'Platform verification requires manual handling');
  }
}

module.exports = { assertNoChallenge };
