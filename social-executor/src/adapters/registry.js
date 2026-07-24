'use strict';

const x = require('./x');
const reddit = require('./reddit');
const quora = require('./quora');
const tiktok = require('./tiktok');
const youtube = require('./youtube');
const instagram = require('./instagram');
const facebook = require('./facebook');

const adapters = new Map(Object.entries({ x, reddit, quora, tiktok, youtube, instagram, facebook }));

function getAdapter(platform) {
  const adapter = adapters.get(platform);
  if (!adapter) throw new Error(`Unsupported platform: ${platform}`);
  return adapter;
}

function platforms() {
  return new Set(adapters.keys());
}

module.exports = { getAdapter, platforms };
