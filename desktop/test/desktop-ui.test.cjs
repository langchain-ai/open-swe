const test = require("node:test");
const assert = require("node:assert/strict");
const { DesktopUi } = require("../build/desktop-ui.cjs");

const page = () =>
  new Request("open-swe://app/agents/local-thread", {
    headers: { accept: "text/html" },
  });
const asset = () => new Request("open-swe://app/assets/dashboard.js");
const shell = () =>
  new Response("hosted shell", { headers: { "content-type": "text/html" } });

test("loads the deployed shell and assets without sending renderer credentials", async () => {
  const calls = [];
  const ui = new DesktopUi(
    "https://backend.example",
    async (url, init) => {
      calls.push({ url, init });
      return url.endsWith("_shell.html")
        ? shell()
        : new Response("hosted asset");
    },
    () => {
      throw new Error("Bundled UI should not be used");
    },
  );
  assert.equal(await (await ui.serve(page())).text(), "hosted shell");
  assert.equal(await (await ui.serve(asset())).text(), "hosted asset");
  assert.deepEqual(
    calls.map(({ url }) => url),
    [
      "https://backend.example/_shell.html",
      "https://backend.example/assets/dashboard.js",
    ],
  );
  for (const { init } of calls) {
    assert.equal(init.credentials, "omit");
    assert.equal(init.redirect, "error");
    assert.equal(init.headers, undefined);
  }
});

test("offline or incompatible backends use a consistent bundled UI until the next navigation", async () => {
  for (const failure of [
    new Error("offline"),
    new Response("Not found", { status: 404 }),
    new Response("{}", { headers: { "content-type": "application/json" } }),
  ]) {
    let online = false;
    const urls = [];
    const ui = new DesktopUi(
      "https://backend.example",
      async (url) => {
        urls.push(url);
        if (online) return shell();
        if (failure instanceof Error) throw failure;
        return failure;
      },
      async () => new Response("bundled"),
    );
    assert.equal(await (await ui.serve(page())).text(), "bundled");
    online = true;
    assert.equal(await (await ui.serve(asset())).text(), "bundled");
    assert.equal(urls.length, 1);
    assert.equal(await (await ui.serve(page())).text(), "hosted shell");
  }
});

test("strips upstream transport and cookie headers from decoded UI responses", async () => {
  const ui = new DesktopUi(
    "https://backend.example",
    async () =>
      new Response("decoded shell", {
        headers: {
          "content-type": "text/html",
          "content-encoding": "gzip",
          "content-length": "123",
          "set-cookie": "osw_session=untrusted",
        },
      }),
    () => {
      throw new Error("Bundled UI should not be used");
    },
  );
  const response = await ui.serve(page());
  assert.equal(await response.text(), "decoded shell");
  for (const name of ["content-encoding", "content-length", "set-cookie"])
    assert.equal(response.headers.has(name), false);
});

test("never substitutes bundled assets after loading the hosted shell", async () => {
  const ui = new DesktopUi(
    "https://backend.example",
    async (url) => {
      if (url.endsWith("_shell.html")) return shell();
      throw new Error("connection lost");
    },
    () => {
      throw new Error("Must not mix UI versions");
    },
  );
  await ui.serve(page());
  await assert.rejects(ui.serve(asset()), /connection lost/);
});
