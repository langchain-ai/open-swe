const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {
  migrateDesktopConfig,
  readSharedConfig,
  sharedConfigPath,
  updateSharedConfig,
} = require("../build/shared-config.js");

function tempHome() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-home-"));
}

function writeLegacy(home, value) {
  const legacy = path.join(home, "profile", "desktop-config.json");
  fs.mkdirSync(path.dirname(legacy), { recursive: true });
  fs.writeFileSync(legacy, JSON.stringify(value));
  return legacy;
}

test("moves the desktop backend into the shared config, keeping CLI sessions", () => {
  const home = tempHome();
  const shared = sharedConfigPath(home);
  updateSharedConfig(shared, (config) => ({
    ...config,
    sessions: { "https://openswe.example.com": "cli-session" },
  }));
  const legacy = writeLegacy(home, {
    backendUrl: "https://openswe.example.com/",
  });

  migrateDesktopConfig(legacy, shared);

  assert.deepEqual(readSharedConfig(shared), {
    backendUrl: "https://openswe.example.com/",
    sessions: { "https://openswe.example.com": "cli-session" },
  });
  assert.equal(fs.existsSync(legacy), false);
  assert.equal(fs.statSync(shared).mode & 0o777, 0o600);
});

test("keeps a backend the shared config already names", () => {
  const home = tempHome();
  const shared = sharedConfigPath(home);
  updateSharedConfig(shared, (config) => ({
    ...config,
    backendUrl: "https://chosen.example.com/",
  }));
  const legacy = writeLegacy(home, { backendUrl: "https://old.example.com/" });

  migrateDesktopConfig(legacy, shared);

  assert.equal(
    readSharedConfig(shared).backendUrl,
    "https://chosen.example.com/",
  );
  assert.equal(fs.existsSync(legacy), false);
});

test("drops keys an older CLI stored", () => {
  const home = tempHome();
  const shared = sharedConfigPath(home);
  fs.mkdirSync(path.dirname(shared), { recursive: true });
  fs.writeFileSync(
    shared,
    JSON.stringify({ backend: "http://127.0.0.1:2027", session: "stale" }),
  );

  assert.deepEqual(readSharedConfig(shared), {
    backendUrl: null,
    sessions: {},
  });
});
