const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const FILE_NAME = "device-identity.json";

/**
 * A stable id for this installation. Threads record it so a run always reaches
 * the machine that holds the working tree, and so the app can tell the user
 * when a thread belongs to a different computer.
 */
function readDeviceIdentity(userDataPath) {
  const file = path.join(userDataPath, FILE_NAME);
  try {
    const stored = JSON.parse(fs.readFileSync(file, "utf8"));
    if (typeof stored?.deviceId === "string" && /^[a-f0-9]{32}$/.test(stored.deviceId)) {
      return { deviceId: stored.deviceId, deviceName: deviceName(stored.deviceName) };
    }
  } catch {
    /* fall through and mint a new one */
  }
  const identity = { deviceId: crypto.randomBytes(16).toString("hex"), deviceName: deviceName() };
  try {
    fs.mkdirSync(userDataPath, { recursive: true });
    fs.writeFileSync(file, JSON.stringify(identity), { mode: 0o600 });
  } catch {
    /* an unwritable profile still gets a working (if per-launch) identity */
  }
  return identity;
}

function deviceName(stored) {
  if (typeof stored === "string" && stored.trim()) return stored.trim().slice(0, 128);
  return (os.hostname() || "This computer").slice(0, 128);
}

module.exports = { readDeviceIdentity };
