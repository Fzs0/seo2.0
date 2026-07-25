'use strict';

const puppeteer = require('puppeteer-core');
const { startLocalExecutor } = require('./server-core');

function connectBrowser({ debuggingPort }) {
  return puppeteer.connect({
    browserURL: `http://127.0.0.1:${debuggingPort}`,
    defaultViewport: null,
  });
}

function startStandaloneExecutor(options = {}) {
  return startLocalExecutor({ ...options, connectBrowser: options.connectBrowser || connectBrowser });
}

if (require.main === module) startStandaloneExecutor();

module.exports = { connectBrowser, startStandaloneExecutor };
