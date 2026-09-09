const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const http = require("node:http");
const { DesktopMcp, resolveLoginEnvironment } = require("../build/mcp.cjs");

function fixture(t, cloudRuntime) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "desktop-mcp-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const manager = new DesktopMcp({
    configPath: path.join(root, "mcp.json"),
    togglesPath: path.join(root, "enabled.json"),
    credentialsDir: path.join(root, "credentials"),
    cloudRuntime:
      cloudRuntime ??
      (async () => ({
        backend_url: "https://backend.example",
        session_token: "session-only",
        cookie_name: "osw_session",
      })),
    encryptString: (value) => Buffer.from(value).map((byte) => byte ^ 73),
    decryptString: (value) =>
      Buffer.from(value)
        .map((byte) => byte ^ 73)
        .toString(),
    openExternal: async () => {},
  });
  return manager;
}

test("MCP CRUD preserves external configs, keeps toggles separate and rereads", (t) => {
  const manager = fixture(t);
  fs.writeFileSync(
    manager.options.configPath,
    JSON.stringify({
      other: true,
      mcpServers: {
        external: {
          command: "node",
          args: ["a; not a shell"],
          env_passthrough: ["PATH"],
        },
      },
    }),
  );
  manager.save({
    name: "web",
    transport: "streamable_http",
    enabled: false,
    url: "http://localhost:9000/mcp",
  });
  const doc = JSON.parse(fs.readFileSync(manager.options.configPath));
  assert.equal(doc.other, true);
  assert.deepEqual(doc.mcpServers.external.args, ["a; not a shell"]);
  assert.equal(doc.mcpServers.web.enabled, undefined);
  assert.equal(manager.servers().find((s) => s.name === "web").enabled, false);
  doc.mcpServers.external.command = "python";
  fs.writeFileSync(manager.options.configPath, JSON.stringify(doc));
  assert.equal(manager.servers()[0].command, "python");
  assert.equal(manager.delete("web"), true);
  assert.equal(manager.servers().length, 1);
  assert.throws(() =>
    manager.save({ name: "bad", command: "node", cloud: true }),
  );
  assert.throws(() => manager.save({ name: "bad", url: "file:///secret" }));
  assert.equal(fs.statSync(manager.options.configPath).mode & 0o777, 0o600);
  fs.writeFileSync(manager.options.configPath, "not json");
  assert.throws(() => manager.save({ name: "valid", command: "node" }));
  assert.equal(fs.readFileSync(manager.options.configPath, "utf8"), "not json");
});

test("MCP broker requires capability, rejects browser origins, and scopes encrypted credentials", async (t) => {
  const manager = fixture(t);
  manager.save({
    name: "web",
    url: "http://localhost:9000/mcp",
    enabled: true,
  });
  await manager.start();
  t.after(() => manager.close());
  const auth = { Authorization: `Bearer ${manager.secret}` };
  assert.equal((await fetch(`${manager.url}/runtime`)).status, 403);
  assert.equal(
    (
      await fetch(`${manager.url}/runtime`, {
        headers: { ...auth, Origin: "https://evil.example" },
      })
    ).status,
    403,
  );
  const runtime = await (
    await fetch(`${manager.url}/runtime`, { headers: auth })
  ).json();
  const key = runtime.servers[0].credential_key;
  const request = (data) =>
    fetch(`${manager.url}/credentials`, {
      method: "POST",
      headers: { ...auth, "Content-Type": "application/json" },
      body: JSON.stringify({ name: "web", key, ...data }),
    });
  assert.equal(
    (await request({ value: { tokens: { access_token: "private-token" } } }))
      .status,
    200,
  );
  const file = manager.credentialPath("web", manager.servers()[0]);
  assert.equal(
    fs.readFileSync(file).includes(Buffer.from("private-token")),
    false,
  );
  assert.equal(
    (await (await request({})).json()).tokens.access_token,
    "private-token",
  );
  assert.equal(
    JSON.stringify(manager.servers()).includes("private-token"),
    false,
  );
  manager.save({
    name: "web",
    url: "http://localhost:9001/mcp",
    enabled: true,
  });
  assert.equal((await request({ value: { tokens: {} } })).status, 400);
  assert.equal(fs.existsSync(file), false);
});

async function fakeBackend(t, handler) {
  const seen = [];
  const server = http.createServer((request, response) => {
    seen.push(request.headers);
    handler(request, response);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => {
    server.closeAllConnections();
    server.close();
  });
  return { url: `http://127.0.0.1:${server.address().port}`, seen };
}

async function brokerFixture(t, backend) {
  const manager = fixture(
    t,
    async () =>
      backend && {
        backend_url: backend.url,
        session_token: "session-only",
        cookie_name: "osw_session",
      },
  );
  await manager.start();
  t.after(() => manager.close());
  return manager;
}

test("cloud connections are listed through Electron with the session cookie", async (t) => {
  const backend = await fakeBackend(t, (request, response) => {
    assert.equal(request.url, "/dashboard/api/mcp-connections");
    response
      .writeHead(201, { "Content-Type": "application/json" })
      .end(JSON.stringify({ connections: [{ id: "a".repeat(32) }] }));
  });
  const manager = await brokerFixture(t, backend);
  const auth = { Authorization: `Bearer ${manager.secret}` };
  const response = await fetch(`${manager.url}/cloud/connections`, {
    headers: auth,
  });
  assert.equal(response.status, 201);
  assert.deepEqual(await response.json(), {
    connections: [{ id: "a".repeat(32) }],
  });
  assert.equal(backend.seen.length, 1);
  assert.equal(backend.seen[0].cookie, "osw_session=session-only");
  assert.equal(backend.seen[0].origin, "open-swe://app");
  assert.equal(backend.seen[0].authorization, undefined);
});

test("cloud proxy streams SSE, forwards MCP headers both ways and validates paths", async (t) => {
  const id = "0123456789abcdef0123456789abcdef";
  let release;
  const gate = new Promise((resolve) => (release = resolve));
  const backend = await fakeBackend(t, async (request, response) => {
    assert.equal(request.url, `/dashboard/api/mcp-connections/${id}/proxy`);
    let body = "";
    for await (const chunk of request) body += chunk;
    assert.equal(body, request.method === "POST" ? '{"jsonrpc":"2.0"}' : "");
    response.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Mcp-Session-Id": "upstream-session",
      "Set-Cookie": "leak=1",
    });
    response.write("data: first\n\n");
    await gate;
    response.end("data: second\n\n");
  });
  const manager = await brokerFixture(t, backend);
  const auth = { Authorization: `Bearer ${manager.secret}` };
  const response = await fetch(`${manager.url}/cloud/connections/${id}/proxy`, {
    method: "POST",
    headers: {
      ...auth,
      Accept: "text/event-stream",
      "Content-Type": "application/json",
      "Mcp-Session-Id": "client-session",
      "X-Forwarded-Secret": "nope",
    },
    body: '{"jsonrpc":"2.0"}',
  });
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("content-type"), "text/event-stream");
  assert.equal(response.headers.get("mcp-session-id"), "upstream-session");
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.equal(response.headers.get("set-cookie"), null);
  const reader = response.body.getReader();
  const first = await reader.read();
  assert.equal(Buffer.from(first.value).toString(), "data: first\n\n");
  release();
  let rest = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    rest += Buffer.from(value).toString();
  }
  assert.equal(rest, "data: second\n\n");
  const [headers] = backend.seen;
  assert.equal(headers.cookie, "osw_session=session-only");
  assert.equal(headers.origin, "open-swe://app");
  assert.equal(headers["mcp-session-id"], "client-session");
  assert.equal(headers.accept, "text/event-stream");
  assert.equal(headers.authorization, undefined);
  assert.equal(headers["x-forwarded-secret"], undefined);
  assert.equal(
    (
      await fetch(`${manager.url}/cloud/connections/not-an-id/proxy`, {
        headers: auth,
      })
    ).status,
    404,
  );
  assert.equal(
    (
      await fetch(`${manager.url}/cloud/connections/${id}/proxy`, {
        method: "PUT",
        headers: auth,
      })
    ).status,
    404,
  );
  assert.equal(backend.seen.length, 1);
});

test("cloud endpoints answer 503 without a backend session", async (t) => {
  const manager = await brokerFixture(t, null);
  const auth = { Authorization: `Bearer ${manager.secret}` };
  const list = await fetch(`${manager.url}/cloud/connections`, {
    headers: auth,
  });
  assert.equal(list.status, 503);
  assert.equal(await list.json(), null);
  const proxy = await fetch(
    `${manager.url}/cloud/connections/${"b".repeat(32)}/proxy`,
    { headers: auth },
  );
  assert.equal(proxy.status, 503);
});

test("credential storage IDs persist and invalidate for each public OAuth setting", (t) => {
  const manager = fixture(t);
  const input = {
    name: "manual",
    url: "http://localhost:9000/mcp",
    auth_type: "oauth",
    oauth_client_id: "client",
    oauth_scope: "read",
    oauth_redirect_uri: "http://127.0.0.1:12345/callback",
    oauth_token_endpoint_auth_method: "none",
  };
  manager.save(input);
  const file = manager.credentialPath(input.name, input);
  const key = path.basename(file);
  assert.match(key, /^[a-f0-9]{64}\.bin$/);
  const restarted = new DesktopMcp(manager.options);
  assert.equal(restarted.credentialPath(input.name, input), file);
  manager.credentials(input.name, key, {
    tokens: { access_token: "private-token" },
  });
  const indexFile = path.join(manager.options.credentialsDir, "index.json");
  assert.equal(
    fs.readFileSync(indexFile, "utf8").includes("private-token"),
    false,
  );
  for (const [field, value] of Object.entries({
    url: "http://localhost:9001/mcp",
    oauth_client_id: "another-client",
    oauth_scope: "read write",
    oauth_redirect_uri: "http://127.0.0.1:12346/callback",
    oauth_token_endpoint_auth_method: "client_secret_post",
  })) {
    const { name, ...changed } = { ...input, [field]: value };
    const document = { mcpServers: { [name]: changed } };
    fs.writeFileSync(manager.options.configPath, JSON.stringify(document));
    assert.notEqual(manager.credentialPath(input.name, changed), file);
    assert.throws(() => manager.credentials(input.name, key), /changed/);
  }
  const index = JSON.parse(fs.readFileSync(indexFile, "utf8"));
  for (const configuration of Object.keys(index))
    index[configuration] = "../../outside";
  fs.writeFileSync(indexFile, JSON.stringify(index));
  assert.throws(() => manager.servers(), /Invalid MCP credential storage ID/);
});

test("login shell resolution parses null-delimited values without interpolating config", async () => {
  let invocation;
  const result = await resolveLoginEnvironment(
    { SHELL: "/bin/zsh", ORIGINAL: "yes" },
    async (...args) => {
      invocation = args;
      return {
        stdout:
          "banner\nOPEN_SWE_LOGIN_ENV_START\0PATH=/custom/bin\0MULTILINE=a\nb=c\0",
      };
    },
  );
  assert.equal(invocation[0], "/bin/zsh");
  assert.deepEqual(invocation[1].slice(0, 1), ["-ilc"]);
  assert.equal(result.PATH, "/custom/bin");
  assert.equal(result.MULTILINE, "a\nb=c");
  assert.equal(result.ORIGINAL, "yes");
});

test("manual OAuth roundtrips public config and encrypts secrets without renderer disclosure", (t) => {
  const manager = fixture(t);
  const input = {
    name: "manual",
    url: "http://localhost:9000/mcp",
    auth_type: "oauth",
    oauth_client_id: "manual-client",
    oauth_scope: "read",
    oauth_redirect_uri: "http://127.0.0.1:12345/callback",
    oauth_token_endpoint_auth_method: "client_secret_basic",
    oauth_client_secret: "manual-private-secret",
  };
  manager.save(input);
  const row = manager.servers()[0];
  assert.equal(row.oauth_client_secret_configured, true);
  assert.equal(row.oauth_client_secret, undefined);
  const file = manager.credentialPath(row.name, row);
  assert.equal(
    fs
      .readFileSync(manager.options.configPath, "utf8")
      .includes(input.oauth_client_secret),
    false,
  );
  assert.equal(
    fs
      .readFileSync(`${file}.secret`)
      .includes(Buffer.from(input.oauth_client_secret)),
    false,
  );
  manager.credentials(row.name, path.basename(file), {
    tokens: { access_token: "keep-on-equivalent-edit" },
  });
  manager.save(Object.fromEntries(Object.entries(row).reverse()));
  assert.equal(
    manager.credentials(row.name, path.basename(file)).tokens.access_token,
    "keep-on-equivalent-edit",
  );
  manager.save({ ...row, enabled: false });
  manager.save({ ...row, oauth_scope: "read write" });
  const updated = manager.servers()[0];
  assert.equal(
    manager.credentials(
      updated.name,
      path.basename(manager.credentialPath(updated.name, updated)),
    ).client_secret,
    input.oauth_client_secret,
  );
  manager.save({ ...updated, oauth_client_secret: "replacement" });
  assert.equal(
    manager.credentials(
      updated.name,
      path.basename(manager.credentialPath(updated.name, updated)),
    ).client_secret,
    "replacement",
  );
  manager.save({ ...updated, url: "http://localhost:9001/mcp" });
  assert.equal(manager.servers()[0].oauth_client_secret_configured, false);
  assert.throws(() =>
    manager.save({
      ...input,
      oauth_redirect_uri: "https://evil.example/callback",
    }),
  );
  const before = fs.readFileSync(manager.options.configPath, "utf8");
  manager.options.encryptString = () => {
    throw new Error("OS keychain unavailable");
  };
  assert.throws(() => manager.save(input), /OS keychain unavailable/);
  assert.equal(fs.readFileSync(manager.options.configPath, "utf8"), before);
});
