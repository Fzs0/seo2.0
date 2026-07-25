'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { once } = require('node:events');
const { startMainExecutor } = require('../src/server');

function requestJson(url) {
  return new Promise((resolve, reject) => {
    http.get(url, response => {
      let raw = '';
      response.on('data', chunk => { raw += chunk; });
      response.on('end', () => {
        try {
          resolve({ status: response.statusCode, body: JSON.parse(raw) });
        } catch (error) {
          reject(error);
        }
      });
    }).on('error', reject);
  });
}

test('main package contains only the launcher, not a copied executor implementation', () => {
  const sourceRoot = path.resolve(__dirname, '../src');
  const sourceFiles = [];
  const visit = directory => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const absolute = path.join(directory, entry.name);
      if (entry.isDirectory()) visit(absolute);
      if (entry.isFile() && entry.name.endsWith('.js')) {
        sourceFiles.push(path.relative(sourceRoot, absolute).replaceAll('\\', '/'));
      }
    }
  };
  visit(sourceRoot);
  assert.deepEqual(sourceFiles.sort(), ['server.js']);
});

test('main launcher serves the canonical executor health interface', async t => {
  const sink = { write: () => {} };
  const server = startMainExecutor({
    env: {
      SOCIAL_EXECUTOR_HOST: '127.0.0.1',
      SOCIAL_EXECUTOR_PORT: '0',
      SOCIAL_EXECUTOR_SHARED_SECRET: 'x'.repeat(32),
    },
    stdout: sink,
    stderr: sink,
    connectBrowser: async () => {
      throw new Error('browser connection is not expected in the health test');
    },
  });
  t.after(() => new Promise(resolve => server.close(resolve)));
  await once(server, 'listening');

  const address = server.address();
  const response = await requestJson(`http://127.0.0.1:${address.port}/health`);

  assert.equal(response.status, 200);
  assert.deepEqual(response.body, { status: 'ok' });
});
