const fs = require("node:fs");
const path = require("node:path");

function readDocument(configPath) {
  try {
    const value = JSON.parse(fs.readFileSync(configPath, "utf8"));
    if (
      !value ||
      typeof value !== "object" ||
      Array.isArray(value) ||
      !value.mcpServers ||
      typeof value.mcpServers !== "object" ||
      Array.isArray(value.mcpServers)
    )
      throw new Error(
        "The MCP configuration must contain an mcpServers object.",
      );
    return value;
  } catch (error) {
    if (error?.code === "ENOENT") return { mcpServers: {} };
    if (error instanceof SyntaxError)
      throw new Error("The MCP configuration contains invalid JSON.");
    throw error;
  }
}

function writeDocument(configPath, value) {
  fs.mkdirSync(path.dirname(configPath), { recursive: true });
  const temporaryPath = `${configPath}.${process.pid}.tmp`;
  fs.writeFileSync(temporaryPath, `${JSON.stringify(value, null, 2)}\n`, {
    encoding: "utf8",
    mode: 0o600,
  });
  fs.renameSync(temporaryPath, configPath);
  fs.chmodSync(configPath, 0o600);
}

function publicConnection(name, settings) {
  const isStdio = typeof settings.command === "string";
  return {
    name,
    url: isStdio ? settings.command : settings.url || "",
    transport:
      settings.transport === "sse" || settings.type === "sse"
        ? "sse"
        : "streamable_http",
    enabled: settings.enabled !== false,
    allowed_tools: settings.allowed_tools || [],
    header_names: Object.keys(settings.headers || {}),
    revision: "local",
    updated_at: "",
    local_command: isStdio,
  };
}

function listMcpConnections(configPath) {
  const { mcpServers } = readDocument(configPath);
  return Object.entries(mcpServers).map(([name, settings]) =>
    publicConnection(name, settings),
  );
}

function saveMcpConnection(configPath, update) {
  const name = update?.name;
  if (typeof name !== "string" || !/^[a-z][a-z0-9_-]{0,31}$/.test(name))
    throw new Error("Invalid MCP connection name.");
  if (!update.url || !["streamable_http", "sse"].includes(update.transport))
    throw new Error("Local MCP connections require a URL and valid transport.");
  const document = readDocument(configPath);
  const previous = document.mcpServers[name] || {};
  if (typeof previous.command === "string")
    throw new Error("Command-based MCPs must still be edited in mcp.json.");
  const settings = {
    ...previous,
    url: update.url,
    transport: update.transport,
    enabled: update.enabled !== false,
    ...(update.headers == null ? {} : { headers: update.headers }),
  };
  delete settings.type;
  delete settings.command;
  delete settings.args;
  delete settings.env;
  delete settings.oauth;
  document.mcpServers[name] = settings;
  writeDocument(configPath, document);
  return publicConnection(name, settings);
}

function deleteMcpConnection(configPath, name) {
  const document = readDocument(configPath);
  delete document.mcpServers[name];
  writeDocument(configPath, document);
}

function revealMcpHeaders(configPath, name) {
  return readDocument(configPath).mcpServers[name]?.headers || {};
}

module.exports = {
  deleteMcpConnection,
  listMcpConnections,
  revealMcpHeaders,
  saveMcpConnection,
};
