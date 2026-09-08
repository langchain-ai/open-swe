const fs = require("node:fs");
const path = require("node:path");

const UPDATE_CHANNELS = new Set(["stable", "nightly"]);

function isUpdateChannel(value) {
  return typeof value === "string" && UPDATE_CHANNELS.has(value);
}

function defaultUpdateChannel(version) {
  return typeof version === "string" && version.includes("-nightly.")
    ? "nightly"
    : "stable";
}

function readUpdateChannel(filePath, version) {
  try {
    const stored = JSON.parse(fs.readFileSync(filePath, "utf8"));
    if (isUpdateChannel(stored.channel)) return stored.channel;
  } catch {}
  return defaultUpdateChannel(version);
}

function writeUpdateChannel(filePath, channel) {
  if (!isUpdateChannel(channel)) throw new Error("Invalid update channel");
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const temporaryPath = `${filePath}.${process.pid}.tmp`;
  fs.writeFileSync(temporaryPath, `${JSON.stringify({ channel }, null, 2)}\n`);
  fs.renameSync(temporaryPath, filePath);
}

module.exports = {
  defaultUpdateChannel,
  isUpdateChannel,
  readUpdateChannel,
  writeUpdateChannel,
};
