'use strict';

const http = require('node:http');
const { SocialExecutor } = require('./executor');
const { redact, timingSafeSecret } = require('./security');
const { validateCommand } = require('./schema');

const host = process.env.SOCIAL_EXECUTOR_HOST || '127.0.0.1';
if (host !== '127.0.0.1') throw new Error('SOCIAL_EXECUTOR_HOST must be 127.0.0.1');
const port = Number(process.env.SOCIAL_EXECUTOR_PORT || 4317);
const secret = process.env.SOCIAL_EXECUTOR_SHARED_SECRET;
if (!secret || secret.length < 32) throw new Error('SOCIAL_EXECUTOR_SHARED_SECRET must be at least 32 characters');
const executor = new SocialExecutor({
  artifactDir: process.env.SOCIAL_EXECUTOR_ARTIFACT_DIR,
  timeoutMs: Number(process.env.SOCIAL_EXECUTOR_COMMAND_TIMEOUT_MS || 45000),
  reviewTtlMs: Number(process.env.SOCIAL_EXECUTOR_REVIEW_TTL_MS || 1800000)
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
    return send(res, status, { error: status === 500 ? 'executor_error' : 'invalid_request', message: redact(error.message) });
  }
});

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
function send(res, status, value) { res.statusCode = status; res.end(JSON.stringify(value)); }

server.listen(port, host, () => process.stdout.write(`social-executor listening on http://${host}:${port}\n`));
