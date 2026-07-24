const assert = require("node:assert/strict");
const test = require("node:test");

require("../response-json.js");

test("turns an HTML backend response into an actionable extension error", async () => {
  const response = new Response("<!DOCTYPE html><html></html>", {
    status: 200,
    headers: { "Content-Type": "text/html; charset=utf-8" }
  });

  await assert.rejects(
    globalThis.parseExdivoJsonResponse(
      response,
      "http://127.0.0.1:5173/api/v1/social/extension/heartbeat"
    ),
    /连接的是 8000 端口的本地后端/
  );
});
