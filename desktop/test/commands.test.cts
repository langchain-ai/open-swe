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

test("dashboard shortcuts become accelerators unless bare or reserved", () => {
  const { MenuCommands } = require("../build/commands.cjs");
  assert.equal(MenuCommands.accelerator("mod+n"), "CmdOrCtrl+N");
  assert.equal(
    MenuCommands.accelerator("shift+mod+alt+b"),
    "CmdOrCtrl+Alt+Shift+B",
  );
  assert.equal(MenuCommands.accelerator("ctrl+`"), "Ctrl+`");
  assert.equal(MenuCommands.accelerator("mod+enter"), "CmdOrCtrl+Enter");
  assert.equal(MenuCommands.accelerator("j"), null);
  assert.equal(MenuCommands.accelerator("shift+r"), null);
  assert.equal(MenuCommands.accelerator("mod+q"), null);
  assert.equal(MenuCommands.accelerator("constructor+n"), null);
  assert.equal(MenuCommands.accelerator("mod+constructor"), null);
});

test("menus show fallback items until the dashboard publishes its commands", () => {
  const { MenuCommands } = require("../build/commands.cjs");
  const ran = [];
  const menu = new MenuCommands((id, viaShortcut) =>
    ran.push([id, viaShortcut]),
  );
  const fallback = [{ label: "New Thread" }];
  assert.deepEqual(menu.items("file", fallback), fallback);

  const commands = [
    {
      id: "new-thread",
      label: "New Thread",
      group: "General",
      menu: "file",
      shortcut: "mod+n",
    },
    {
      id: "add-repository",
      label: "Add Repository",
      group: "Workspace",
      menu: "file",
    },
    {
      id: "pin-thread",
      label: "Pin Thread",
      group: "Thread",
      menu: "thread",
      shortcut: "mod+shift+p",
    },
    {
      id: "other-pin",
      label: "Other",
      group: "Thread",
      menu: "thread",
      shortcut: "mod+shift+p",
    },
    {
      id: "next-file",
      label: "Next File",
      group: "Pull request",
      menu: "pull-request",
      shortcut: "j",
    },
    { id: "bad", label: "Bad", group: "General", menu: "nowhere" },
  ];
  assert.equal(menu.publish(commands), true);
  const file = menu.items("file", fallback);
  assert.deepEqual(
    file.map(({ label, accelerator, type }) => ({ label, accelerator, type })),
    [
      { label: "New Thread", accelerator: "CmdOrCtrl+N", type: undefined },
      { label: undefined, accelerator: undefined, type: "separator" },
      { label: "Add Repository", accelerator: undefined, type: undefined },
    ],
  );
  file[0].click({}, undefined, { triggeredByAccelerator: true });
  file[0].click({}, undefined, {});
  assert.deepEqual(ran, [
    ["new-thread", true],
    ["new-thread", false],
  ]);
  assert.deepEqual(
    menu.commandMenus().map(({ label, submenu }) => ({
      label,
      items: submenu.map((item) => [item.label, item.accelerator]),
    })),
    [
      {
        label: "Thread",
        items: [
          ["Pin Thread", "CmdOrCtrl+Shift+P"],
          ["Other", undefined],
        ],
      },
      { label: "Pull Request", items: [["Next File", undefined]] },
    ],
  );
  assert.deepEqual(menu.nativeShortcutIds(), ["new-thread", "pin-thread"]);
  assert.equal(menu.publish(structuredClone(commands)), false);

  assert.equal(menu.reset(), true);
  assert.deepEqual(menu.items("file", fallback), fallback);
  assert.equal(menu.reset(), false);
});
