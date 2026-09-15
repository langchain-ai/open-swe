const test = require("node:test");
const assert = require("node:assert/strict");
const { redactSecrets } = require("../build/diagnostics.cjs");

test("redacts sessions, bearer tokens, and provider keys", () => {
  const text = [
    "cookie: osw_session=abc.def.ghi; theme=dark",
    "Authorization: Bearer eyJhbGciOi.payload.sig",
    "GET /callback?code=1234&state=xyz&other=1",
    "token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123 and sk-abcdefghijklmnopqrstuvwxyz",
  ].join("\n");
  const redacted = redactSecrets(text);
  assert.doesNotMatch(
    redacted,
    /abc\.def\.ghi|eyJhbGciOi|1234|xyz|ghp_ABCDEF|sk-abcdef/,
  );
  assert.match(redacted, /osw_session=\[redacted\]; theme=dark/);
  assert.match(redacted, /code=\[redacted\]&state=\[redacted\]&other=1/);
});
