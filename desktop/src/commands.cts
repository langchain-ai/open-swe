const DESKTOP_COMMAND_IDS = [
  "new-thread",
  "show-command-palette",
  "open-settings",
  "show-keyboard-shortcuts",
  "toggle-sidebar",
] as const;

const desktopCommandIds = new Set<string>(DESKTOP_COMMAND_IDS);

function isDesktopCommandId(value: unknown) {
  return typeof value === "string" && desktopCommandIds.has(value);
}

module.exports = { DESKTOP_COMMAND_IDS, isDesktopCommandId };
