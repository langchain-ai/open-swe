const fs = require("node:fs");
const path = require("node:path");

function parseMcpConfig(text) {
  let value;
  try {
    value = JSON.parse(text);
  } catch {
    throw new Error("Enter valid JSON.");
  }
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    !value.mcpServers ||
    typeof value.mcpServers !== "object" ||
    Array.isArray(value.mcpServers)
  )
    throw new Error("The JSON must contain an mcpServers object.");
  return value;
}

function readMcpConfig(configPath) {
  try {
    const text = fs.readFileSync(configPath, "utf8");
    return {
      path: configPath,
      text: `${JSON.stringify(parseMcpConfig(text), null, 2)}\n`,
    };
  } catch (error) {
    if (error?.code === "ENOENT")
      return { path: configPath, text: '{\n  "mcpServers": {}\n}\n' };
    throw error;
  }
}

function writeMcpConfig(configPath, text) {
  const value = parseMcpConfig(text);
  fs.mkdirSync(path.dirname(configPath), { recursive: true });
  const temporaryPath = `${configPath}.${process.pid}.tmp`;
  fs.writeFileSync(temporaryPath, `${JSON.stringify(value, null, 2)}\n`, {
    encoding: "utf8",
    mode: 0o600,
  });
  fs.renameSync(temporaryPath, configPath);
  fs.chmodSync(configPath, 0o600);
  return readMcpConfig(configPath);
}

module.exports = { parseMcpConfig, readMcpConfig, writeMcpConfig };
