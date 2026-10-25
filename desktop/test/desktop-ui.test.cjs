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
const bundled = async () => new Response("bundled");

async function fixture(t) {
  const root = await mkdtemp(path.join(tmpdir(), "desktop-ui-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  return root;
}

function server(version, failedAsset) {
  const bodies = {
    "/_shell.html": `shell ${version}`,
    "/assets/lazy.js": `lazy ${version}`,
  };
  return async (url, init) => {
    assert.equal(init.credentials, "omit");
    assert.equal(init.redirect, "error");
    assert.equal(init.headers, undefined);
    const pathname = new URL(url).pathname;
    if (pathname === "/desktop-ui-manifest.json")
      return Response.json({
        files: Object.entries(bodies).map(([assetPath, body]) => ({
          path: assetPath,
          sha256: createHash("sha256").update(body).digest("hex"),
        })),
      });
    if (pathname === failedAsset) throw new Error("interrupted download");
    return new Response(bodies[pathname], {
      headers: { "set-cookie": "untrusted=value", "content-encoding": "gzip" },
    });
  };
}

const offline = async () => {
  throw new Error("offline");
};

test("persists a complete server UI across restarts, including unopened lazy routes", async (t) => {
  const root = await fixture(t);
  const ui = new DesktopUi(
    "https://backend.example",
    server("v1"),
    bundled,
    root,
  );
  const response = await ui.serve(page());
  assert.equal(await response.text(), "shell v1");
  assert.equal(response.headers.get("set-cookie"), null);
  assert.equal(response.headers.get("content-encoding"), null);
  const restarted = new DesktopUi(
    "https://backend.example",
    offline,
    bundled,
    root,
  );
  assert.equal(await (await restarted.serve(page())).text(), "shell v1");
  assert.equal(await (await restarted.serve(asset())).text(), "lazy v1");
  assert.equal(
    await (
      await new DesktopUi(
        "https://other.example",
        offline,
        bundled,
        root,
      ).serve(page())
    ).text(),
    "bundled",
  );
});

test("interrupted or corrupt updates never replace the last complete version", async (t) => {
  const root = await fixture(t);
  await new DesktopUi(
    "https://backend.example",
    server("v1"),
    bundled,
    root,
  ).serve(page());
  for (const fetchUi of [
    server("v2", "/assets/lazy.js"),
    async (url, init) => {
      const response = await server("v3")(url, init);
      return url.endsWith("lazy.js") ? new Response("wrong hash") : response;
    },
  ]) {
    const ui = new DesktopUi("https://backend.example", fetchUi, bundled, root);
    assert.equal(await (await ui.serve(page())).text(), "shell v1");
    await assert.rejects(
      ui.refresh(),
      /interrupted download|changed during download/,
    );
    const restarted = new DesktopUi(
      "https://backend.example",
      offline,
      bundled,
      root,
    );
    assert.equal(await (await restarted.serve(page())).text(), "shell v1");
  }
});

test("promotes complete updates on next navigation without changing active page assets", async (t) => {
  const root = await fixture(t);
  await new DesktopUi(
    "https://backend.example",
    server("v1"),
    bundled,
    root,
  ).serve(page());
  const ui = new DesktopUi(
    "https://backend.example",
    server("v2"),
    bundled,
    root,
  );
  assert.equal(await (await ui.serve(page())).text(), "shell v1");
  await ui.refresh();
  assert.equal(await (await ui.serve(asset())).text(), "lazy v1");
  assert.equal(await (await ui.serve(page())).text(), "shell v2");
  await ui.refresh();
  assert.equal(await (await ui.serve(asset())).text(), "lazy v2");
});

test("rejects manifest paths that could fetch APIs or escape the cache", async (t) => {
  const root = await fixture(t);
  const ui = new DesktopUi(
    "https://backend.example",
    async () =>
      Response.json({
        files: [{ path: "/dashboard/api/session", sha256: "a".repeat(64) }],
      }),
    bundled,
    root,
  );
  assert.equal(await (await ui.serve(page())).text(), "bundled");
});
