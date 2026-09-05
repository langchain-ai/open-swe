const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { DesktopMcp, resolveLoginEnvironment } = require("../build/mcp.cjs");

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "desktop-mcp-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const manager = new DesktopMcp({
    configPath: path.join(root, "mcp.json"),
    togglesPath: path.join(root, "enabled.json"),
    credentialsDir: path.join(root, "credentials"),
    loginEnv: { PATH: "/login/bin", LOCAL_SECRET: "local-value" },
    cloudRuntime: async () => ({
      backend_url: "https://backend.example",
      session_token: "session-only",
      cookie_name: "osw_session",
    }),
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
          env_vars: ["PATH"],
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
  assert.equal(runtime.cloud.session_token, "session-only");
  assert.equal(runtime.env.PATH, "/login/bin");
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
  assert.equal(row.oauth_redirect_uri, input.oauth_redirect_uri);
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
