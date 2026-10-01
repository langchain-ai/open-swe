const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {
  migrateDesktopConfig,
  readSharedConfig,
  shareSession,
  sharedConfigPath,
  unshareSession,
  updateSharedConfig,
} = require("../build/shared-config.js");

function macHost() {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-home-"));
  return { home, platform: "darwin", env: {} };
}

/** Where a packaged build before the shared file kept its backend. */
function writeLegacy(home, value) {
  const legacy = path.join(
    home,
    "Library",
    "Application Support",
    "open-swe-desktop",
    "desktop-config.json",
  );
  fs.mkdirSync(path.dirname(legacy), { recursive: true });
  fs.writeFileSync(legacy, JSON.stringify(value));
  return legacy;
}

test("copies the desktop backend into the shared config, keeping CLI sessions", () => {
  const host = macHost();
  const shared = sharedConfigPath(host.home);
  updateSharedConfig(shared, (config) => ({
    ...config,
    sessions: { "https://openswe.example.com": "cli-session" },
  }));
  const legacy = writeLegacy(host.home, {
    backendUrl: "https://openswe.example.com/",
  });

  migrateDesktopConfig(host);

  assert.deepEqual(readSharedConfig(shared), {
    backendUrl: "https://openswe.example.com/",
    sessions: { "https://openswe.example.com": "cli-session" },
  });
  assert.equal(fs.statSync(shared).mode & 0o777, 0o600);
  assert.equal(fs.existsSync(legacy), true);
});

test("keeps a backend the shared config already names", () => {
  const host = macHost();
  const shared = sharedConfigPath(host.home);
  updateSharedConfig(shared, (config) => ({
    ...config,
    backendUrl: "https://chosen.example.com/",
  }));
  writeLegacy(host.home, { backendUrl: "https://old.example.com/" });

  migrateDesktopConfig(host);

  assert.equal(
    readSharedConfig(shared).backendUrl,
    "https://chosen.example.com/",
  );
});

test("writes nothing when there is nothing to migrate", () => {
  const host = macHost();

  migrateDesktopConfig(host);

  assert.equal(fs.existsSync(sharedConfigPath(host.home)), false);
});

test("a removed desktop session leaves a newer sign-in in place", () => {
  const host = macHost();
  const shared = sharedConfigPath(host.home);
  shareSession(shared, "https://openswe.example.com/", "desktop");
  shareSession(shared, "https://openswe.example.com", "cli");

  assert.equal(
    unshareSession(shared, "https://openswe.example.com/", "desktop"),
    false,
  );
  assert.deepEqual(readSharedConfig(shared).sessions, {
    "https://openswe.example.com": "cli",
  });
  assert.equal(
    unshareSession(shared, "https://openswe.example.com/", "cli"),
    true,
  );
  assert.deepEqual(readSharedConfig(shared).sessions, {});
});

test("drops keys an older CLI stored", () => {
  const host = macHost();
  const shared = sharedConfigPath(host.home);
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
