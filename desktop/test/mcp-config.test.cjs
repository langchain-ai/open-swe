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

test("local MCP connections are managed through structured config", (t) => {
  const configPath = temporaryConfig(t);
  assert.deepEqual(listMcpConnections(configPath), []);

  saveMcpConnection(configPath, {
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
  assert.deepEqual(revealMcpHeaders(configPath, "local"), {
    Authorization: "Bearer secret",
  });
  if (process.platform !== "win32")
    assert.equal(fs.statSync(configPath).mode & 0o777, 0o600);

  deleteMcpConnection(configPath, "local");
  assert.deepEqual(listMcpConnections(configPath), []);
});

for (const allowedTools of [undefined, [], ["search"]]) {
  test(`edits and toggles preserve local tool policy ${JSON.stringify(allowedTools)} and headers`, (t) => {
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
    );

    for (const update of [
      { headers: null, enabled: true },
      { enabled: false },
      { enabled: true },
    ]) {
      saveMcpConnection(configPath, {
        ...listMcpConnections(configPath)[0],
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

    saveMcpConnection(configPath, {
      ...listMcpConnections(configPath)[0],
      headers: {},
    });
    assert.deepEqual(revealMcpHeaders(configPath, "local"), {});
  });
}

test("command MCPs remain visible but cannot be overwritten as URLs", (t) => {
  const configPath = temporaryConfig(t);
  fs.writeFileSync(
    configPath,
    JSON.stringify({
      mcpServers: { files: { command: "node", args: ["server.js"] } },
    }),
  );

  assert.equal(listMcpConnections(configPath)[0].local_command, true);
  assert.throws(
    () =>
      saveMcpConnection(configPath, {
        name: "files",
        url: "http://localhost:8080/mcp",
        transport: "streamable_http",
      }),
    /Command-based MCPs/,
  );
});
