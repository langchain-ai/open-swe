const test = require("node:test");
const assert = require("node:assert/strict");
const { mkdtemp, rm } = require("node:fs/promises");
const { tmpdir } = require("node:os");
const path = require("node:path");
const { createHash } = require("node:crypto");
const { DesktopUi } = require("../build/desktop-ui.cjs");

const page = () =>
  new Request("open-swe://app/agents/local-thread", {
    headers: { accept: "text/html" },
  });
const asset = () => new Request("open-swe://app/assets/lazy.js");
const { gzipSync } = require("node:zlib");

async function fixture(t) {
  const root = await mkdtemp(path.join(tmpdir(), "desktop-ui-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  return root;
}

function server(version, failure) {
  const bodies = {
    "/_shell.html": `shell ${version}`,
    "/assets/lazy.js": `lazy ${version}`,
  };
  const archive = gzipSync(
    JSON.stringify({
      files: Object.entries(bodies).map(([assetPath, body]) => ({
        path: assetPath,
        sha256: createHash("sha256").update(body).digest("hex"),
        body: Buffer.from(body).toString("base64"),
      })),
    }),
  );
  return async (url, init) => {
    assert.equal(init.credentials, "omit");
    assert.equal(init.redirect, "manual");
    assert.equal(init.headers, undefined);
    if (url.endsWith("/desktop-ui.json"))
      return Response.json({
        bridge_version: 1,
        url: `https://github.com/langchain-ai/open-swe/releases/download/desktop-ui-${"a".repeat(40)}/ui.json.gz`,
        sha256: createHash("sha256").update(archive).digest("hex"),
      });
    if (failure === "interrupted") throw new Error("interrupted download");
    return new Response(
      failure === "corrupt" ? Buffer.from("corrupt") : archive,
    );
  };
}

const offline = async () => {
  throw new Error("offline");
};

test("persists a complete server UI across restarts, including unopened lazy routes", async (t) => {
  const root = await fixture(t);
  const ui = new DesktopUi("https://backend.example", server("v1"), root);
  const response = await ui.serve(page());
  assert.equal(await response.text(), "shell v1");
  assert.equal(response.headers.get("set-cookie"), null);
  assert.equal(response.headers.get("content-encoding"), null);
  const restarted = new DesktopUi("https://backend.example", offline, root);
  assert.equal(await (await restarted.serve(page())).text(), "shell v1");
  assert.equal(await (await restarted.serve(asset())).text(), "lazy v1");
  await assert.rejects(
    new DesktopUi("https://other.example", offline, root).serve(page()),
    /Connect to the internet/,
  );
});

test("interrupted or corrupt updates never replace the last complete version", async (t) => {
  const root = await fixture(t);
  await new DesktopUi("https://backend.example", server("v1"), root).serve(
    page(),
  );
  for (const fetchUi of [
    server("v2", "interrupted"),
    server("v3", "corrupt"),
  ]) {
    const ui = new DesktopUi("https://backend.example", fetchUi, root);
    assert.equal(await (await ui.serve(page())).text(), "shell v1");
    await assert.rejects(
      ui.refresh(),
      /interrupted download|checksum mismatch/,
    );
    const restarted = new DesktopUi("https://backend.example", offline, root);
    assert.equal(await (await restarted.serve(page())).text(), "shell v1");
  }
});

test("promotes complete updates on next navigation without changing active page assets", async (t) => {
  const root = await fixture(t);
  await new DesktopUi("https://backend.example", server("v1"), root).serve(
    page(),
  );
  const ui = new DesktopUi("https://backend.example", server("v2"), root);
  assert.equal(await (await ui.serve(page())).text(), "shell v1");
  await ui.refresh();
  assert.equal(await (await ui.serve(asset())).text(), "lazy v1");
  assert.equal(await (await ui.serve(page())).text(), "shell v2");
  await ui.refresh();
  assert.equal(await (await ui.serve(asset())).text(), "lazy v2");
});

test("rejects untrusted archive URLs and incompatible bridges", async (t) => {
  const root = await fixture(t);
  for (const descriptor of [
    {
      bridge_version: 2,
      url: "https://github.com/example",
      sha256: "a".repeat(64),
    },
    {
      bridge_version: 1,
      url: "https://evil.example/ui.json.gz",
      sha256: "a".repeat(64),
    },
  ]) {
    const ui = new DesktopUi(
      "https://backend.example",
      async () => Response.json(descriptor),
      root,
    );
    await assert.rejects(ui.serve(page()), /Connect to the internet/);
  }
});
