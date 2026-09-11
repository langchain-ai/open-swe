const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { readMcpConfig, writeMcpConfig } = require("../build/mcp-config.cjs");

test("MCP config defaults to an empty server list and persists formatted JSON", (t) => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-mcp-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const configPath = path.join(directory, "mcp.json");

  assert.deepEqual(readMcpConfig(configPath), {
    path: configPath,
    text: '{\n  "mcpServers": {}\n}\n',
  });

  const saved = writeMcpConfig(
    configPath,
    '{"mcpServers":{"local":{"command":"node","args":["server.js"]}}}',
  );
  assert.equal(saved.text, fs.readFileSync(configPath, "utf8"));
  assert.deepEqual(JSON.parse(saved.text), {
    mcpServers: { local: { command: "node", args: ["server.js"] } },
  });
  if (process.platform !== "win32")
    assert.equal(fs.statSync(configPath).mode & 0o777, 0o600);
});

test("MCP config rejects invalid content without replacing the file", (t) => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-mcp-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const configPath = path.join(directory, "mcp.json");
  fs.writeFileSync(configPath, '{"mcpServers":{}}\n');

  assert.throws(
    () => writeMcpConfig(configPath, '{"other":{}}'),
    /mcpServers object/,
  );
  assert.equal(fs.readFileSync(configPath, "utf8"), '{"mcpServers":{}}\n');
});
