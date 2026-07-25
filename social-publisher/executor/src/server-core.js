'use strict';

const crypto = require('node:crypto');
const http = require('node:http');
const { SocialExecutor } = require('./executor');
const { redact, timingSafeSecret } = require('./security');
const { validateCommand } = require('./schema');

function startLocalExecutor(options = {}) {
  const env = options.env || process.env;
  const host = env.SOCIAL_EXECUTOR_HOST || '127.0.0.1';
  if (host !== '127.0.0.1') throw new Error('SOCIAL_EXECUTOR_HOST must be 127.0.0.1');
  const port = Number(env.SOCIAL_EXECUTOR_PORT || 4317);
  const secret = env.SOCIAL_EXECUTOR_SHARED_SECRET;
  if (!secret || secret.length < 32) throw new Error('SOCIAL_EXECUTOR_SHARED_SECRET must be at least 32 characters');
  if (typeof options.connectBrowser !== 'function') throw new TypeError('connectBrowser adapter is required');

  const log = options.log || createLogger(options.stdout || process.stdout, options.stderr || process.stderr);
  const executor = options.executor || new SocialExecutor({
    artifactDir: env.SOCIAL_EXECUTOR_ARTIFACT_DIR,
    timeoutMs: Number(env.SOCIAL_EXECUTOR_COMMAND_TIMEOUT_MS || 45000),
    reviewTtlMs: Number(env.SOCIAL_EXECUTOR_REVIEW_TTL_MS || 1800000),
    log: (event, fields) => log.info(event, fields),
    connectBrowser: options.connectBrowser,
  });

  const server = http.createServer(async (req, res) => {
    res.setHeader('Content-Type', 'application/json; charset=utf-8');
    if (req.method === 'GET' && req.url === '/health') return send(res, 200, { status: 'ok' });
    if (req.method !== 'POST' || req.url !== '/v1/commands') return send(res, 404, { error: 'not_found' });
    if (!timingSafeSecret(req.headers['x-social-executor-secret'], secret)) return send(res, 401, { error: 'unauthorized' });
    try {
      const body = await readJson(req);
      const command = validateCommand(body);
      const result = await executor.run(command);
      return send(res, 200, result);
    } catch (error) {
      const status = error.statusCode || (/required|invalid|unsupported|must/i.test(error.message) ? 400 : 500);
      const traceId = crypto.randomUUID();
      log.error('request_failed', {
        trace_id: traceId,
        status,
        ...error.executorContext,
        error_name: error.name,
        error_code: error.code,
        error_message: redact(error.message),
        error_stack: redact(error.stack),
      });
      return send(res, status, {
        error: status === 500 ? 'executor_error' : 'invalid_request',
        message: redact(error.message),
        trace_id: traceId,
      });
    }
  });

  server.listen(port, host, () => {
    const address = server.address();
    log.info('server_started', {
      host,
      port: typeof address === 'object' && address ? address.port : port,
    });
  });
  return server;
}

function readJson(req) {
  return new Promise((resolve, reject) => {
    let raw = '';
    req.on('data', chunk => {
      raw += chunk;
      if (raw.length > 1024 * 1024) req.destroy(new Error('Request body too large'));
    });
    req.on('end', () => { try { resolve(JSON.parse(raw)); } catch { reject(new Error('Invalid JSON')); } });
    req.on('error', reject);
  });
}

function send(res, status, value) {
  res.statusCode = status;
  res.end(JSON.stringify(value));
}

function createLogger(stdout, stderr) {
  const write = (stream, level, event, fields = {}) => {
    stream.write(`${JSON.stringify(redact({
      timestamp: new Date().toISOString(),
      level,
      service: 'social-executor',
      event,
      ...fields,
    }))}\n`);
  };
  return {
    info: (event, fields) => write(stdout, 'info', event, fields),
    error: (event, fields) => write(stderr, 'error', event, fields),
  };
}

module.exports = { createLogger, startLocalExecutor };
