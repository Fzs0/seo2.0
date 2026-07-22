'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs/promises');
const path = require('node:path');
const puppeteer = require('puppeteer-core');
const x = require('./adapters/x');
const reddit = require('./adapters/reddit');
const { ManualRequiredError } = require('./errors');
const { assertAllowedUrl, redact } = require('./security');

const adapters = { x, reddit };

class SocialExecutor {
  constructor(options = {}) {
    this.artifactDir = path.resolve(options.artifactDir || './artifacts');
    this.timeoutMs = options.timeoutMs || 45000;
    this.reviewTtlMs = options.reviewTtlMs || 1800000;
    this.locks = new Set();
    this.prepared = new Map();
  }

  async run(request) {
    if (this.locks.has(request.container_code)) throw Object.assign(new Error('Container is busy'), { statusCode: 409 });
    this.locks.add(request.container_code);
    try {
      return await this.withTimeout(request.command === 'prepare' ? this.prepare(request) : this.confirm(request));
    } catch (error) {
      if (error instanceof ManualRequiredError) return { status: 'manual_required', reason_code: error.code, message: error.message };
      throw error;
    } finally {
      this.locks.delete(request.container_code);
    }
  }

  async prepare(request) {
    const browser = await puppeteer.connect({ browserURL: `http://127.0.0.1:${request.debugging_port}`, defaultViewport: null });
    try {
      const page = await browser.newPage();
      page.setDefaultTimeout(this.timeoutMs);
      const detail = await adapters[request.platform].prepare(page, request);
      assertAllowedUrl(detail.target_url);
      await fs.mkdir(this.artifactDir, { recursive: true });
      const safeId = crypto.createHash('sha256').update(request.job_id).digest('hex').slice(0, 24);
      const screenshot = path.join(this.artifactDir, `${safeId}-prepared.png`);
      await page.screenshot({ path: screenshot, fullPage: false });
      const confirmationToken = crypto.randomBytes(32).toString('base64url');
      this.prepared.set(request.job_id, {
        browser, page, platform: request.platform, containerCode: request.container_code,
        tokenHash: hash(confirmationToken), expiresAt: Date.now() + this.reviewTtlMs
      });
      return {
        status: 'awaiting_review', confirmation_token: confirmationToken,
        artifact: path.basename(screenshot), detail: redact(detail),
        logs: [{ event: 'content_prepared', platform: request.platform, final_publish_clicked: false }]
      };
    } catch (error) {
      await browser.disconnect();
      throw error;
    }
  }

  async confirm(request) {
    const state = this.prepared.get(request.job_id);
    if (!state || state.containerCode !== request.container_code) throw new ManualRequiredError('preparation_missing', 'Prepared browser session was not found');
    if (Date.now() > state.expiresAt) return this.dispose(request.job_id, state, new ManualRequiredError('review_expired', 'Review window expired'));
    if (!crypto.timingSafeEqual(Buffer.from(hash(request.confirmation_token)), Buffer.from(state.tokenHash))) {
      throw Object.assign(new Error('Invalid confirmation token'), { statusCode: 403 });
    }
    try {
      const result = await adapters[state.platform].confirm(state.page);
      assertAllowedUrl(result.post_url);
      this.prepared.delete(request.job_id);
      await state.browser.disconnect();
      return { status: 'published', platform: state.platform, ...result, logs: [{ event: 'publish_verified' }] };
    } catch (error) {
      if (!(error instanceof ManualRequiredError)) throw error;
      return this.dispose(request.job_id, state, error);
    }
  }

  async dispose(jobId, state, error) {
    this.prepared.delete(jobId);
    await state.browser.disconnect();
    throw error;
  }

  withTimeout(promise) {
    return Promise.race([promise, new Promise((_, reject) => setTimeout(() => reject(new ManualRequiredError('timeout', 'Command timed out')), this.timeoutMs))]);
  }
}

function hash(value) { return crypto.createHash('sha256').update(value).digest('hex'); }

module.exports = { SocialExecutor, hash };
