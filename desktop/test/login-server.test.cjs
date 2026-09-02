const assert = require("node:assert/strict");
const { createHash } = require("node:crypto");
const test = require("node:test");

const { beginLogin } = require("../build/login-server.cjs");

test("receives the handoff code on the loopback listener", async () => {
  const flow = await beginLogin();
  assert.equal(
    createHash("sha256").update(flow.verifier).digest("base64url"),
    flow.challenge,
  );

  const response = await fetch(
    `http://127.0.0.1:${flow.port}/callback?code=handoff-123`,
  );
  assert.equal(response.status, 200);
  assert.match(await response.text(), /You're signed in/);
  assert.equal(await flow.code, "handoff-123");
});

test("yields no code when the callback carries none", async () => {
  const flow = await beginLogin();
  const response = await fetch(`http://127.0.0.1:${flow.port}/callback`);
  assert.equal(response.status, 200);
  assert.match(await response.text(), /Sign-in failed/);
  assert.equal(await flow.code, null);
});

test("ignores other paths and yields no code when cancelled", async () => {
  const flow = await beginLogin();
  const stray = await fetch(`http://127.0.0.1:${flow.port}/favicon.ico`);
  assert.equal(stray.status, 404);

  flow.cancel();
  assert.equal(await flow.code, null);
  await assert.rejects(
    fetch(`http://127.0.0.1:${flow.port}/callback?code=late`),
  );
});

test("uses a fresh port and verifier per login", async () => {
  const first = await beginLogin();
  const second = await beginLogin();
  assert.notEqual(first.port, second.port);
  assert.notEqual(first.verifier, second.verifier);
  first.cancel();
  second.cancel();
});
