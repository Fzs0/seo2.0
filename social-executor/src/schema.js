'use strict';

const { assertAllowedUrl } = require('./security');

const PLATFORMS = new Set(['x', 'reddit']);
const COMMANDS = new Set(['prepare', 'confirm']);

function nonEmpty(value, name, max = 10000) {
  if (typeof value !== 'string' || !value.trim() || value.length > max) {
    throw new Error(`${name} must be a non-empty string up to ${max} characters`);
  }
  return value.trim();
}

function validateCommand(input) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new Error('JSON object required');
  const command = nonEmpty(input.command, 'command', 20);
  if (!COMMANDS.has(command)) throw new Error('Unsupported command');
  const jobId = nonEmpty(input.job_id, 'job_id', 128);
  const containerCode = nonEmpty(input.container_code, 'container_code', 256);

  if (command === 'confirm') {
    nonEmpty(input.confirmation_token, 'confirmation_token', 512);
    return { command, job_id: jobId, container_code: containerCode, confirmation_token: input.confirmation_token };
  }

  const platform = nonEmpty(input.platform, 'platform', 20).toLowerCase();
  if (!PLATFORMS.has(platform)) throw new Error('Unsupported platform');
  const debuggingPort = Number(input.debugging_port);
  if (!Number.isInteger(debuggingPort) || debuggingPort < 1 || debuggingPort > 65535) throw new Error('Invalid debugging_port');
  const content = input.content;
  if (!content || typeof content !== 'object') throw new Error('content object required');
  if (platform === 'x') nonEmpty(content.body, 'content.body', 10000);
  if (platform === 'reddit') {
    nonEmpty(content.title, 'content.title', 300);
    nonEmpty(content.subreddit, 'content.subreddit', 64);
    nonEmpty(content.body, 'content.body', 10000);
    if (content.url) {
      const link = new URL(content.url);
      if (link.protocol !== 'https:') throw new Error('Reddit link must use HTTPS');
    }
  }
  if (input.target_url) assertAllowedUrl(input.target_url);
  return { command, job_id: jobId, container_code: containerCode, platform, debugging_port: debuggingPort, content, target_url: input.target_url };
}

module.exports = { validateCommand };
