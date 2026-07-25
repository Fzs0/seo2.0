'use strict';

const puppeteer = require('puppeteer-core');
const { startLocalExecutor } = require('../../social-publisher/executor/src/server-core');

function connectBrowser({ debuggingPort }) {
  return puppeteer.connect({
    browserURL: `http://127.0.0.1:${debuggingPort}`,
    defaultViewport: null,
  });
}

function startMainExecutor(options = {}) {
  return startLocalExecutor({ ...options, connectBrowser: options.connectBrowser || connectBrowser });
}

if (require.main === module) startMainExecutor();

module.exports = { connectBrowser, startMainExecutor };
