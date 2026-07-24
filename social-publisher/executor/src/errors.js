'use strict';

class ManualRequiredError extends Error {
  constructor(code, message) {
    super(message);
    this.name = 'ManualRequiredError';
    this.code = code;
  }
}

module.exports = { ManualRequiredError };
