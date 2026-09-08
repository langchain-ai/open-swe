const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {
  defaultUpdateChannel,
  isUpdateChannel,
  readUpdateChannel,
  writeUpdateChannel,
} = require("../build/update-channel.cjs");

test("defaults to the channel matching the installed version", () => {
  assert.equal(defaultUpdateChannel("0.2.6"), "stable");
  assert.equal(defaultUpdateChannel("0.2.6-nightly.20260904082449"), "nightly");
});

test("persists only supported update channels", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-updates-"));
  const filePath = path.join(directory, "channel.json");

  assert.equal(readUpdateChannel(filePath, "0.2.6"), "stable");
  writeUpdateChannel(filePath, "nightly");
  assert.equal(readUpdateChannel(filePath, "0.2.6"), "nightly");
  assert.equal(isUpdateChannel("preview"), false);
  assert.throws(() => writeUpdateChannel(filePath, "preview"), /Invalid/);

  fs.rmSync(directory, { recursive: true, force: true });
});

test("ignores invalid stored channel state", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-updates-"));
  const filePath = path.join(directory, "channel.json");
  fs.writeFileSync(filePath, JSON.stringify({ channel: "preview" }));

  assert.equal(
    readUpdateChannel(filePath, "0.2.6-nightly.20260904082449"),
    "nightly",
  );

  fs.rmSync(directory, { recursive: true, force: true });
});
