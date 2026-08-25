const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { randomUUID } = require("node:crypto");

function loadDeviceIdentity(filePath) {
  try {
    const value = JSON.parse(fs.readFileSync(filePath, "utf8"));
    if (typeof value.id === "string" && value.id) {
      return { id: value.id, name: value.name || os.hostname() };
    }
  } catch {}
  const identity = { id: randomUUID(), name: os.hostname() };
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(identity, null, 2)}\n`, {
    mode: 0o600,
  });
  return identity;
}

module.exports = { loadDeviceIdentity };
