import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  mkdir,
  mkdtemp,
  readdir,
  readFile,
  rm,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { gzipSync } from "node:zlib";
import { DesktopUi } from "../build/desktop-ui.cjs";

type Failure = "interrupted" | "corrupt";
type Fetch = (url: string, init: RequestInit) => Promise<Response>;

const page = () =>
  new Request("open-swe://app/agents/local-thread", {
    headers: { accept: "text/html" },
  });
const asset = () => new Request("open-swe://app/assets/lazy.js");
const serveFile = async (filename: string) =>
  new Response(await readFile(filename));

async function fixture(t: test.TestContext) {
  const root = await mkdtemp(path.join(tmpdir(), "desktop-ui-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  return root;
}

function archiveOf(bodies: Record<string, string>) {
  return gzipSync(
    JSON.stringify({
      files: Object.entries(bodies).map(([assetPath, body]) => ({
        path: assetPath,
        body: Buffer.from(body).toString("base64"),
      })),
    }),
  );
}

function server(
  version: string,
  failure?: Failure,
  bodies: Record<string, string> = {
    "/_shell.html": `shell ${version}`,
    "/assets/lazy.js": `lazy ${version}`,
  },
): Fetch {
  const archive = archiveOf(bodies);
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
  throw new TypeError("fetch failed");
};

test("persists a complete server UI across restarts, including unopened lazy routes", async (t) => {
  const root = await fixture(t);
  const ui = new DesktopUi(
    "https://backend.example",
    server("v1"),
    root,
    serveFile,
  );
  const response = await ui.serve(page());
  assert.equal(await response.text(), "shell v1");
  assert.equal(response.headers.get("set-cookie"), null);
  assert.equal(response.headers.get("content-encoding"), null);
  const restarted = new DesktopUi(
    "https://backend.example",
    offline,
    root,
    serveFile,
  );
  assert.equal(await (await restarted.serve(page())).text(), "shell v1");
  assert.equal(await (await restarted.serve(asset())).text(), "lazy v1");
  await assert.rejects(
    new DesktopUi("https://other.example", offline, root, serveFile).serve(
      page(),
    ),
    /Connect to the internet/,
  );
});

test("interrupted or corrupt updates never replace the last complete version", async (t) => {
  const root = await fixture(t);
  await new DesktopUi(
    "https://backend.example",
    server("v1"),
    root,
    serveFile,
  ).serve(page());
  for (const fetchUi of [
    server("v2", "interrupted"),
    server("v3", "corrupt"),
  ]) {
    const ui = new DesktopUi(
      "https://backend.example",
      fetchUi,
      root,
      serveFile,
    );
    assert.equal(await (await ui.serve(page())).text(), "shell v1");
    await assert.rejects(
      ui.refresh(),
      /interrupted download|checksum mismatch/,
    );
    const restarted = new DesktopUi(
      "https://backend.example",
      offline,
      root,
      serveFile,
    );
    assert.equal(await (await restarted.serve(page())).text(), "shell v1");
  }
});

test("promotes complete updates on next navigation without changing active page assets", async (t) => {
  const root = await fixture(t);
  await new DesktopUi(
    "https://backend.example",
    server("v1"),
    root,
    serveFile,
  ).serve(page());
  const ui = new DesktopUi(
    "https://backend.example",
    server("v2"),
    root,
    serveFile,
  );
  assert.equal(await (await ui.serve(page())).text(), "shell v1");
  await ui.refresh();
  assert.equal(await (await ui.serve(asset())).text(), "lazy v1");
  assert.equal(await (await ui.serve(page())).text(), "shell v2");
  await ui.refresh();
  assert.equal(await (await ui.serve(asset())).text(), "lazy v2");
});

test("rejects untrusted archive URLs", async (t) => {
  const root = await fixture(t);
  for (const descriptor of [
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
      serveFile,
    );
    await assert.rejects(ui.serve(page()), /Could not load the Open SWE UI/);
  }
});

test("reports a bridge mismatch as a desktop update, on first install and over a cached UI", async (t) => {
  const root = await fixture(t);
  const incompatible: Fetch = async () =>
    Response.json({ bridge_version: 2, url: "", sha256: "" });
  await assert.rejects(
    new DesktopUi(
      "https://backend.example",
      incompatible,
      root,
      serveFile,
    ).serve(page()),
    /Update the Open SWE desktop app/,
  );
  await new DesktopUi(
    "https://backend.example",
    server("v1"),
    root,
    serveFile,
  ).serve(page());
  const warnings: Error[] = [];
  const ui = new DesktopUi(
    "https://backend.example",
    incompatible,
    root,
    serveFile,
    null,
    (error) => warnings.push(error),
  );
  for (let load = 0; load < 2; load++) {
    assert.equal(await (await ui.serve(page())).text(), "shell v1");
    await ui.refresh().catch(() => {});
  }
  assert.equal(warnings.length, 1);
  assert.match(warnings[0].message, /bridge version 2/);
});

test("rejects archive traversal before extracting a checksum-valid download", async (t) => {
  const root = await fixture(t);
  const ui = new DesktopUi(
    "https://backend.example",
    server("bad", undefined, {
      "/_shell.html": "shell",
      "/assets/../../outside": "escape",
    }),
    root,
    serveFile,
  );
  await assert.rejects(ui.serve(page()), /Could not load the Open SWE UI/);
  assert.deepEqual(await readdir(root), []);
});

test("installs a local archive on every load instead of contacting the backend", async (t) => {
  const root = await fixture(t);
  const archive = path.join(root, "ui.json.gz");
  const ui = new DesktopUi(
    "https://backend.example",
    offline,
    path.join(root, "cache"),
    serveFile,
    archive,
  );
  await writeFile(archive, archiveOf({ "/_shell.html": "local v1" }));
  assert.equal(await (await ui.serve(page())).text(), "local v1");
  await writeFile(archive, archiveOf({ "/_shell.html": "local v2" }));
  assert.equal(await (await ui.serve(page())).text(), "local v2");
});

test("prunes superseded versions and crashed installs, keeping the one being served", async (t) => {
  const root = await fixture(t);
  const cache = path.join(root, "cache");
  const backend = path.join(
    cache,
    createHash("sha256").update("https://backend.example").digest("hex"),
  );
  for (const version of ["v1", "v2", "v3"]) {
    const ui = new DesktopUi(
      "https://backend.example",
      server(version),
      cache,
      serveFile,
    );
    await ui.serve(page()).catch(() => {});
    await ui.refresh();
    await mkdir(path.join(backend, "staging-crashed"), { recursive: true });
  }
  const entries = await readdir(backend);
  assert.equal(
    entries.filter((entry) => entry.startsWith("staging-")).length,
    1,
  );
  assert.equal(
    entries.filter((entry) => /^[a-f0-9]{64}$/.test(entry)).length,
    2,
  );
});
