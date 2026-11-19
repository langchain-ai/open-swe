const test = require("node:test");
const assert = require("node:assert/strict");

const { FramePolicy } = require("../build/frame-policy.cjs");

test("the proxy gives the session only to the app's own page", () => {
  const raw = "/dashboard/api/threads/t/files/raw/token/index.html";
  const runs = "/dashboard/api/threads/t/runs";
  const cases = [
    ["GET", raw, "null", "anonymous"],
    ["POST", raw, "open-swe://app", "method-not-allowed"],
    ["POST", runs, "open-swe://app", "session"],
    ["POST", runs, "null", "forbidden"],
    ["POST", runs, "https://evil.example", "forbidden"],
    ["DELETE", runs, undefined, "forbidden"],
    ["GET", "/dashboard/api/me", "null", "session"],
  ];
  for (const [method, pathname, origin, expected] of cases)
    assert.equal(
      FramePolicy.proxyCredentials(method, pathname, origin),
      expected,
      `${method} ${pathname} ${origin}`,
    );
});

test("a sandboxed or preview frame, or one inside it, may not navigate itself away", () => {
  const app = { url: "open-swe://app/threads/t", origin: "open-swe://app" };
  const raw =
    "open-swe://app/dashboard/api/threads/t/files/raw/token/index.html";
  const evil = "https://evil.example/";
  const sandboxed = (url) => ({ url, origin: "null" });
  const blocks = (frames, target, byApp = false) =>
    FramePolicy.blocksNavigation([...frames, app], target, byApp);

  assert.equal(blocks([sandboxed(raw)], evil), true);
  assert.equal(blocks([sandboxed("about:srcdoc#escaped")], evil), true);
  assert.equal(
    blocks([{ url: "about:srcdoc#escaped", origin: app.origin }], evil),
    true,
  );
  assert.equal(
    blocks([sandboxed("https://sandbox.example/artifact.html")], evil),
    true,
  );
  assert.equal(
    blocks([{ url: "about:blank", origin: "null" }, sandboxed(raw)], evil),
    true,
  );
  assert.equal(blocks([sandboxed(raw)], raw.replace("token", "next")), false);
  assert.equal(
    blocks(
      [sandboxed(raw)],
      "chrome-extension://mhjfbmdgcfjbbpaeojofohoefgiehjai/x",
    ),
    false,
  );
  assert.equal(blocks([sandboxed(raw)], evil, true), false);
  const browser = "https://smith.langchain.com/browser";
  assert.equal(
    blocks([{ url: browser, origin: "https://smith.langchain.com" }], evil),
    false,
  );
});
