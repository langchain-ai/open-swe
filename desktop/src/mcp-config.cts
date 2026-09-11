const fs = require("node:fs");
const path = require("node:path");

async function readDocument(configPath) {
  let handle;
  try {
    const linkInfo = await fs.promises.lstat(configPath);
    handle = await fs.promises.open(
      configPath,
      fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0),
    );
    const info = await handle.stat();
    if (
      linkInfo.dev !== info.dev ||
      linkInfo.ino !== info.ino ||
      !info.isFile()
    )
      throw new Error("The MCP configuration must be a file.");
    if (
      process.platform !== "win32" &&
      ((typeof process.getuid === "function" &&
        info.uid !== process.getuid()) ||
        (info.mode & 0o077) !== 0)
    )
      throw new Error(
        "The MCP configuration must be owned by this user and readable only by them.",
      );
    const value = JSON.parse(await handle.readFile("utf8"));
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
  } finally {
    await handle?.close();
  }
}

async function writeDocument(configPath, value) {
  await fs.promises.mkdir(path.dirname(configPath), { recursive: true });
  const temporaryPath = `${configPath}.${process.pid}.tmp`;
  await fs.promises.writeFile(
    temporaryPath,
    `${JSON.stringify(value, null, 2)}\n`,
    { encoding: "utf8", mode: 0o600 },
  );
  await fs.promises.rename(temporaryPath, configPath);
  await fs.promises.chmod(configPath, 0o600);
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

async function listMcpConnections(configPath) {
  const { mcpServers } = await readDocument(configPath);
  return Object.entries(mcpServers).map(([name, settings]) =>
    publicConnection(name, settings),
  );
}

async function saveMcpConnection(configPath, update) {
  const name = update?.name;
  if (typeof name !== "string" || !/^[a-z][a-z0-9_-]{0,31}$/.test(name))
    throw new Error("Invalid MCP connection name.");
  if (!update.url || !["streamable_http", "sse"].includes(update.transport))
    throw new Error("Local MCP connections require a URL and valid transport.");
  const document = await readDocument(configPath);
  const previous = document.mcpServers[name] || {};
  if (typeof previous.command === "string")
    throw new Error("Command-based MCPs must still be edited in mcp.json.");
  if (
    previous.url &&
    previous.url !== update.url &&
    Object.keys(previous.headers || {}).length &&
    update.headers == null
  )
    throw new Error(
      "Replace or clear authentication headers when changing the server URL.",
    );
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
  await writeDocument(configPath, document);
  return publicConnection(name, settings);
}

async function deleteMcpConnection(configPath, name) {
  const document = await readDocument(configPath);
  if (typeof document.mcpServers[name]?.command === "string")
    throw new Error("Command-based MCPs must still be edited in mcp.json.");
  delete document.mcpServers[name];
  await writeDocument(configPath, document);
}

async function revealMcpHeaders(configPath, name) {
  return (await readDocument(configPath)).mcpServers[name]?.headers || {};
}

module.exports = {
  deleteMcpConnection,
  listMcpConnections,
  revealMcpHeaders,
  saveMcpConnection,
};
