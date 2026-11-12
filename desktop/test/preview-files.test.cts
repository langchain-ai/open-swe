const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { PreviewFiles } = require("../build/preview-files.cjs");

function workspace(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-preview-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const root = path.join(dir, "repo");
  fs.mkdirSync(path.join(root, "site", "css"), { recursive: true });
  fs.mkdirSync(path.join(root, "site", ".hidden"));
  fs.mkdirSync(path.join(root, ".config"));
  fs.writeFileSync(path.join(root, "site", "index.html"), "<h1>deck</h1>");
  fs.writeFileSync(path.join(root, "site", "css", "deck.css"), "h1{}");
  fs.writeFileSync(path.join(root, "site", ".hidden", "x.css"), "x{}");
  fs.writeFileSync(path.join(root, "secret.js"), "secret()");
  fs.writeFileSync(path.join(root, "site", "data.json"), "{}");
  fs.writeFileSync(path.join(root, ".config", "token.css"), "token{}");
  fs.symlinkSync(
    path.join(root, "secret.js"),
    path.join(root, "site", "leak.js"),
  );
  fs.symlinkSync(path.join(root, ".config"), path.join(root, "site", "alias"));
  fs.writeFileSync(path.join(root, "archive.zip"), "PK");
  fs.writeFileSync(path.join(root, "clip.mp4"), Buffer.alloc(1024, 7));
  return root;
}

function previews(root, clock = { now: 1_000 }) {
  return new PreviewFiles(
    (id) => {
      if (id !== "session-1") throw new Error("Local workspace not found");
      return root;
    },
    () => clock.now,
  );
}

const get = (files, url, headers = {}) =>
  files.serve(new Request(url, { headers }));

test("an HTML page reaches its own folder and nothing beside it", async (t) => {
  const files = previews(workspace(t));
  const { url } = await files.mint("session-1", "site/index.html");
  const folder = url.slice(0, url.lastIndexOf("/"));

  const page = await get(files, url);
  assert.equal(page.status, 200);
  assert.equal(await page.text(), "<h1>deck</h1>");
  const csp = page.headers.get("content-security-policy");
  assert.match(csp, /^sandbox allow-scripts allow-downloads;/);
  assert.doesNotMatch(csp, /allow-same-origin/);
  assert.equal(page.headers.get("access-control-allow-origin"), "*");
  const css = await get(files, `${folder}/css/deck.css`);
  assert.match(css.headers.get("content-type"), /^text\/css/);

  // data.json is in the folder, but a page could send its own link out: data needs its own link.
  for (const escape of [
    "data.json",
    "leak.js",
    "alias/token.css",
    ".hidden/x.css",
    "missing.css",
  ]) {
    const refused = await get(files, `${folder}/${escape}`);
    assert.equal(refused.status, 404, escape);
    assert.equal(
      refused.headers.get("content-security-policy"),
      "default-src 'none'; sandbox",
    );
  }
  // URL parsing turns `%2E%2E` into a dot segment, which eats the token itself.
  assert.equal((await get(files, `${folder}/%2E%2E/secret.js`)).status, 401);
});

test("a folder swapped for a symlink takes no grant with it", async (t) => {
  const root = workspace(t);
  const files = previews(root);
  const { url } = await files.mint("session-1", "site/index.html");
  fs.mkdirSync(path.join(root, "private"));
  fs.writeFileSync(path.join(root, "private", "secret.js"), "secret()");
  fs.renameSync(path.join(root, "site"), path.join(root, "site-moved"));
  fs.symlinkSync(path.join(root, "private"), path.join(root, "site"));

  const folder = url.slice(0, url.lastIndexOf("/"));
  assert.equal((await get(files, `${folder}/secret.js`)).status, 404);
});

test("a file link serves only its own file, unknown types as downloads", async (t) => {
  const files = previews(workspace(t));
  const { url } = await files.mint("session-1", "archive.zip");
  const archive = await get(files, url);
  assert.equal(archive.headers.get("content-type"), "application/octet-stream");
  assert.equal(
    archive.headers.get("content-disposition"),
    'attachment; filename="archive.zip"',
  );
  const sibling = url.slice(0, url.lastIndexOf("/")) + "/clip.mp4";
  assert.equal((await get(files, sibling)).status, 404);
  const again = await files.mint("session-1", "archive.zip");
  assert.notEqual(again.url, url);
  await assert.rejects(files.mint("session-1", ".git/config"));
  await assert.rejects(files.mint("session-1", "../outside.txt"));
});

test("a link stops working once tampered with, expired, or its session is gone", async (t) => {
  const clock = { now: 1_000 };
  const files = previews(workspace(t), clock);
  const { url } = await files.mint("session-1", "clip.mp4");
  const token = url.split("/")[3];
  const [payload, signature] = token.split(".");
  const forged = Buffer.from(
    JSON.stringify({
      ...JSON.parse(Buffer.from(payload, "base64url").toString()),
      path: "secret.json",
    }),
  ).toString("base64url");

  assert.equal((await get(files, url.replace(payload, forged))).status, 401);
  assert.equal((await get(files, url.replace(signature, "x"))).status, 401);
  const other = previews(workspace(t), clock);
  assert.equal((await get(other, url)).status, 401);
  clock.now += 60 * 60 * 1000;
  assert.equal((await get(files, url)).status, 401);
});

test("video seeks by range", async (t) => {
  const files = previews(workspace(t));
  const { url } = await files.mint("session-1", "clip.mp4");

  const partial = await get(files, url, { range: "bytes=10-19" });
  assert.equal(partial.status, 206);
  assert.equal(partial.headers.get("content-range"), "bytes 10-19/1024");
  assert.equal((await partial.arrayBuffer()).byteLength, 10);
  assert.equal((await get(files, url, { range: "bytes=4096-" })).status, 416);
});

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
      PreviewFiles.proxyCredentials(method, pathname, origin),
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
    PreviewFiles.blocksNavigation([...frames, app], target, byApp);

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
