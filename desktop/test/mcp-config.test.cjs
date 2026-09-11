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

  saveMcpConnection(configPath, "local", {
    name: "local",
    url: "http://127.0.0.1:8080/mcp",
    transport: "streamable_http",
    enabled: true,
    allowed_tools: [],
    headers: { Authorization: "Bearer secret" },
  });
  assert.deepEqual(revealMcpHeaders(configPath, "local"), {
    Authorization: "Bearer secret",
  });
  assert.deepEqual(listMcpConnections(configPath), [
    {
      name: "local",
      url: "http://127.0.0.1:8080/mcp",
      transport: "streamable_http",
      enabled: true,
      allowed_tools: [],
      header_names: ["Authorization"],
      revision: "local",
      updated_at: "",
      local_command: false,
    },
  ]);
  if (process.platform !== "win32")
    assert.equal(fs.statSync(configPath).mode & 0o777, 0o600);

  deleteMcpConnection(configPath, "local");
  assert.deepEqual(listMcpConnections(configPath), []);
});

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
      saveMcpConnection(configPath, "files", {
        name: "files",
        url: "http://localhost:8080/mcp",
        transport: "streamable_http",
      }),
    /Command-based MCPs/,
  );
});
