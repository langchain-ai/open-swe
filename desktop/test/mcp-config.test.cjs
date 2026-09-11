const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const {
  deleteMcpConnection,
  listMcpConnections,
  revealMcpHeaders,
  saveMcpConnection,
} = require("../build/mcp-config.cjs");

function temporaryConfig(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-mcp-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  return path.join(directory, "mcp.json");
}

test("local MCP connections are managed through structured config", async (t) => {
  const configPath = temporaryConfig(t);
  assert.deepEqual(await listMcpConnections(configPath), []);

  await saveMcpConnection(configPath, {
    name: "local",
    url: "http://127.0.0.1:8080/mcp",
    transport: "streamable_http",
    enabled: true,
    allowed_tools: [],
    headers: { Authorization: "Bearer secret" },
  });
  assert.equal(
    Object.hasOwn(
      JSON.parse(fs.readFileSync(configPath, "utf8")).mcpServers.local,
      "allowed_tools",
    ),
    false,
  );
  assert.deepEqual(await revealMcpHeaders(configPath, "local"), {
    Authorization: "Bearer secret",
  });
  if (process.platform !== "win32")
    assert.equal(fs.statSync(configPath).mode & 0o777, 0o600);

  await deleteMcpConnection(configPath, "local");
  assert.deepEqual(await listMcpConnections(configPath), []);
});

for (const allowedTools of [undefined, [], ["search"]]) {
  test(`edits and toggles preserve local tool policy ${JSON.stringify(allowedTools)} and headers`, async (t) => {
    const configPath = temporaryConfig(t);
    const headers = { Authorization: "Bearer secret" };
    fs.writeFileSync(
      configPath,
      JSON.stringify({
        mcpServers: {
          local: {
            url: "http://localhost:8080/mcp",
            transport: "streamable_http",
            allowed_tools: allowedTools,
            headers,
          },
        },
      }),
      { mode: 0o600 },
    );

    for (const update of [
      { headers: null, enabled: true },
      { enabled: false },
      { enabled: true },
    ]) {
      await saveMcpConnection(configPath, {
        ...(await listMcpConnections(configPath))[0],
        url: "http://localhost:9090/mcp",
        ...update,
      });
      const saved = JSON.parse(fs.readFileSync(configPath, "utf8")).mcpServers
        .local;
      assert.deepEqual(saved.allowed_tools, allowedTools);
      assert.equal(
        Object.hasOwn(saved, "allowed_tools"),
        allowedTools !== undefined,
      );
      assert.deepEqual(saved.headers, headers);
      assert.equal(saved.url, "http://localhost:9090/mcp");
      assert.equal(saved.enabled, update.enabled);
    }

    await saveMcpConnection(configPath, {
      ...(await listMcpConnections(configPath))[0],
      headers: {},
    });
    assert.deepEqual(await revealMcpHeaders(configPath, "local"), {});
  });
}

test("command MCPs remain visible but cannot be overwritten as URLs", async (t) => {
  const configPath = temporaryConfig(t);
  fs.writeFileSync(
    configPath,
    JSON.stringify({
      mcpServers: { files: { command: "node", args: ["server.js"] } },
    }),
    { mode: 0o600 },
  );

  assert.equal((await listMcpConnections(configPath))[0].local_command, true);
  await assert.rejects(
    saveMcpConnection(configPath, {
      name: "files",
      url: "http://localhost:8080/mcp",
      transport: "streamable_http",
    }),
    /Command-based MCPs/,
  );
  await assert.rejects(
    deleteMcpConnection(configPath, "files"),
    /Command-based MCPs/,
  );
});

if (process.platform !== "win32") {
  test("local MCP configuration rejects unsafe files", async (t) => {
    const configPath = temporaryConfig(t);
    fs.writeFileSync(configPath, '{"mcpServers":{}}', { mode: 0o644 });
    await assert.rejects(listMcpConnections(configPath), /readable only/);

    fs.unlinkSync(configPath);
    const target = `${configPath}.target`;
    fs.writeFileSync(target, '{"mcpServers":{}}', { mode: 0o600 });
    fs.symlinkSync(target, configPath);
    await assert.rejects(listMcpConnections(configPath));
  });
}
