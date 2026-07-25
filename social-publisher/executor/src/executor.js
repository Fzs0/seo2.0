'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs/promises');
const path = require('node:path');
const { getAdapter } = require('./adapters/registry');
const { ManualRequiredError } = require('./errors');
const { assertAllowedUrl, redact } = require('./security');

const MANAGED_PAGE_PREFIX = 'social-executor:';

class SocialExecutor {
  constructor(options = {}) {
    this.artifactDir = path.resolve(options.artifactDir || './artifacts');
    this.timeoutMs = options.timeoutMs || 45000;
    this.reviewTtlMs = options.reviewTtlMs || 1800000;
    this.locks = new Set();
    this.prepared = new Map();
    this.log = options.log || (() => {});
    this.adapterFor = options.adapterFor || getAdapter;
    if (typeof options.connectBrowser !== 'function') {
      throw new TypeError('connectBrowser adapter is required');
    }
    this.connectBrowser = options.connectBrowser;
  }

  async run(request) {
    const context = logContext(request);
    const startedAt = Date.now();
    this.log('command_started', context);
    if (this.locks.has(request.container_code)) {
      this.log('command_rejected', { ...context, reason_code: 'container_busy' });
      throw Object.assign(new Error('Container is busy'), { statusCode: 409 });
    }
    this.locks.add(request.container_code);
    try {
      // Adapter operations already use bounded Puppeteer timeouts. Do not wrap
      // them in Promise.race: a raced timeout leaves the browser operation alive
      // after the container lock is released, so a later tab can be clicked by
      // the old command.
      const result = await (request.command === 'prepare' ? this.prepare(request) : this.confirm(request));
      this.log('command_completed', {
        ...context,
        duration_ms: Date.now() - startedAt,
        result_status: result.status,
      });
      return result;
    } catch (error) {
      if (error instanceof ManualRequiredError) {
        this.log('command_manual_required', {
          ...context,
          duration_ms: Date.now() - startedAt,
          reason_code: error.code,
        });
        return { status: 'manual_required', reason_code: error.code, message: error.message };
      }
      error.executorContext = context;
      this.log('command_failed', {
        ...context,
        duration_ms: Date.now() - startedAt,
        error_name: error.name,
        error_code: error.code,
        error_message: redact(error.message),
      });
      throw error;
    } finally {
      this.locks.delete(request.container_code);
    }
  }

  async prepare(request) {
    const context = logContext(request);
    this.log('prepare_browser_connecting', context);
    const browser = await this.connectBrowser({ debuggingPort: request.debugging_port });
    let page;
    try {
      this.log('prepare_browser_connected', context);
      await this.cleanupOrphanedManagedPages(browser);
      this.log('prepare_orphan_cleanup_completed', context);
      page = await browser.newPage();
      page.setDefaultTimeout(this.timeoutMs);
      this.log('prepare_adapter_started', context);
      const detail = await this.adapterFor(request.platform).prepare(page, request);
      this.log('prepare_adapter_completed', context);
      assertAllowedUrl(detail.target_url);
      const pageMarker = managedPageName(request.job_id);
      await page.evaluate(marker => { window.name = marker; }, pageMarker);
      await fs.mkdir(this.artifactDir, { recursive: true });
      const safeId = crypto.createHash('sha256').update(request.job_id).digest('hex').slice(0, 24);
      const screenshot = path.join(this.artifactDir, `${safeId}-prepared.png`);
      await page.screenshot({ path: screenshot, fullPage: false });
      this.log('prepare_artifact_saved', { ...context, artifact: path.basename(screenshot) });
      const confirmationToken = crypto.randomBytes(32).toString('base64url');
      this.prepared.set(request.job_id, {
        browser, page, platform: request.platform, containerCode: request.container_code,
        content: request.content, detail, tokenHash: hash(confirmationToken),
        targetId: page.target()._targetId,
        pageMarker,
        preparedOrigin: new URL(page.url()).origin,
        expiresAt: Date.now() + this.reviewTtlMs
      });
      return {
        status: 'awaiting_review', confirmation_token: confirmationToken,
        artifact: path.basename(screenshot), detail: redact(detail),
        logs: [{ event: 'content_prepared', platform: request.platform, final_publish_clicked: false }]
      };
    } catch (error) {
      if (page && !page.isClosed()) await page.close({ runBeforeUnload: false }).catch(() => {});
      await browser.disconnect();
      throw error;
    }
  }

  async confirm(request) {
    const context = logContext(request);
    const state = this.prepared.get(request.job_id);
    if (!state || state.containerCode !== request.container_code) throw new ManualRequiredError('preparation_missing', 'Prepared browser session was not found');
    if (Date.now() > state.expiresAt) return this.dispose(request.job_id, state, new ManualRequiredError('review_expired', 'Review window expired'));
    if (!crypto.timingSafeEqual(Buffer.from(hash(request.confirmation_token)), Buffer.from(state.tokenHash))) {
      throw Object.assign(new Error('Invalid confirmation token'), { statusCode: 403 });
    }
    try {
      await state.page.bringToFront();
      this.log('confirm_page_foregrounded', context);
      this.log('confirm_page_validation_started', context);
      if (state.page.isClosed()
          || state.page.target()._targetId !== state.targetId
          || new URL(state.page.url()).origin !== state.preparedOrigin) {
        throw new ManualRequiredError('stale_page', 'The reviewed browser tab was closed or navigated; prepare it again');
      }
      this.log('confirm_adapter_started', context);
      const result = await this.adapterFor(state.platform).confirm(state.page, state.content, state.detail);
      this.log('confirm_adapter_completed', context);
      assertAllowedUrl(result.post_url);
      this.log('confirm_result_verified', context);
      this.prepared.delete(request.job_id);
      if (!state.page.isClosed()) await state.page.close({ runBeforeUnload: false }).catch(() => {});
      await state.browser.disconnect();
      return { status: 'published', platform: state.platform, ...result, logs: [{ event: 'publish_verified' }] };
    } catch (error) {
      if (!(error instanceof ManualRequiredError)) throw error;
      return this.dispose(request.job_id, state, error);
    }
  }

  async dispose(jobId, state, error) {
    this.prepared.delete(jobId);
    if (!state.page.isClosed()) await state.page.close({ runBeforeUnload: false }).catch(() => {});
    await state.browser.disconnect();
    throw error;
  }

  async cleanupOrphanedManagedPages(browser) {
    const activeTargets = new Set([...this.prepared.values()].map(state => state.targetId));
    await Promise.all((await browser.pages()).map(async page => {
      if (activeTargets.has(page.target()._targetId)) return;
      // A suspended background tab must never block preparation for every
      // other platform. Marker inspection is read-only and deliberately
      // bounded; unresponsive tabs are left untouched.
      const marker = await softTimeout(
        page.evaluate(() => window.name).catch(() => ''),
        500,
        '',
      );
      if (typeof marker === 'string' && marker.startsWith(MANAGED_PAGE_PREFIX)) {
        await softTimeout(
          page.close({ runBeforeUnload: false }).catch(() => {}),
          1000,
          undefined,
        );
      }
    }));
  }
}

function hash(value) { return crypto.createHash('sha256').update(value).digest('hex'); }
function logContext(request) {
  return {
    command: request.command,
    platform: request.platform,
    job_id: request.job_id,
    container_code: request.container_code,
  };
}
function managedPageName(jobId) { return `${MANAGED_PAGE_PREFIX}${hash(jobId).slice(0, 24)}`; }
function softTimeout(promise, timeoutMs, fallback) {
  return Promise.race([
    promise,
    new Promise(resolve => setTimeout(() => resolve(fallback), timeoutMs)),
  ]);
}

module.exports = { MANAGED_PAGE_PREFIX, SocialExecutor, hash, logContext, managedPageName, softTimeout };
