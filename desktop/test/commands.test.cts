const test = require("node:test");
const assert = require("node:assert/strict");

const {
  DESKTOP_COMMAND_IDS,
  isDesktopCommandId,
} = require("../build/commands.cjs");

test("desktop commands use a fixed allowlist", () => {
  assert.deepEqual(DESKTOP_COMMAND_IDS, [
    "new-thread",
    "show-command-palette",
    "open-settings",
    "show-keyboard-shortcuts",
    "toggle-sidebar",
  ]);
  for (const commandId of DESKTOP_COMMAND_IDS) {
    assert.equal(isDesktopCommandId(commandId), true);
  }
  assert.equal(isDesktopCommandId("open-url"), false);
  assert.equal(isDesktopCommandId({ id: "new-thread" }), false);
});
