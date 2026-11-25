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

/** Where a command the dashboard publishes goes; `app` is the macOS app menu, File elsewhere. */
const MENU_SLOTS = [
  "app",
  "file",
  "edit",
  "view",
  "go",
  "thread",
  "pull-request",
  "help",
] as const;

type MenuSlot = (typeof MENU_SLOTS)[number];

/** Top-level menus that only the dashboard's commands fill. */
const COMMAND_MENUS = {
  go: "Go",
  thread: "Thread",
  "pull-request": "Pull Request",
} as const;

type MenuCommand = {
  id: string;
  label: string;
  group: string;
  menu: MenuSlot;
  accelerator: string | null;
};

type MenuItem = Electron.MenuItemConstructorOptions;

const MAX_COMMANDS = 200;

const MODIFIERS = {
  mod: "CmdOrCtrl",
  meta: "Super",
  ctrl: "Ctrl",
  alt: "Alt",
  shift: "Shift",
} as const;

type Modifier = keyof typeof MODIFIERS;

const MODIFIER_ALIASES = new Map<string, Modifier>([
  ["mod", "mod"],
  ["meta", "meta"],
  ["cmd", "meta"],
  ["command", "meta"],
  ["ctrl", "ctrl"],
  ["control", "ctrl"],
  ["alt", "alt"],
  ["option", "alt"],
  ["shift", "shift"],
]);

const NAMED_KEYS = new Map([
  ["enter", "Enter"],
  ["escape", "Escape"],
  ["space", "Space"],
  ["tab", "Tab"],
  ["backspace", "Backspace"],
  ["delete", "Delete"],
  ["arrowup", "Up"],
  ["arrowdown", "Down"],
  ["arrowleft", "Left"],
  ["arrowright", "Right"],
]);

/** Accelerators the shell's own menu items and roles already answer. */
const RESERVED_ACCELERATORS = new Set([
  "CmdOrCtrl+Q",
  "CmdOrCtrl+W",
  "CmdOrCtrl+Alt+W",
  "CmdOrCtrl+Alt+Left",
  "CmdOrCtrl+Alt+Right",
  "CmdOrCtrl+1",
  "CmdOrCtrl+2",
  "CmdOrCtrl+3",
  "CmdOrCtrl+4",
  "CmdOrCtrl+5",
  "CmdOrCtrl+6",
  "CmdOrCtrl+7",
  "CmdOrCtrl+8",
  "CmdOrCtrl+9",
  "CmdOrCtrl+R",
  "CmdOrCtrl+Z",
  "CmdOrCtrl+Shift+Z",
  "CmdOrCtrl+X",
  "CmdOrCtrl+C",
  "CmdOrCtrl+V",
  "CmdOrCtrl+A",
  "CmdOrCtrl+H",
  "CmdOrCtrl+Alt+H",
  "CmdOrCtrl+M",
  "CmdOrCtrl+Alt+I",
  "CmdOrCtrl+Shift+I",
  "CmdOrCtrl+0",
  "CmdOrCtrl+=",
  "CmdOrCtrl+Shift+=",
  "CmdOrCtrl+-",
  "CmdOrCtrl+Ctrl+F",
]);

/**
 * The dashboard's commands, as it last published them for the page in the main
 * window, laid out as native menu items. Until a page publishes, menus show the
 * shell's own fallback items, which older dashboards rely on.
 */
class MenuCommands {
  private published: MenuCommand[] | null = null;

  constructor(
    private readonly run: (commandId: string, viaShortcut: boolean) => void,
  ) {}

  /** Electron accelerator for an app shortcut such as `mod+shift+p`; null for bare keys. */
  static accelerator(shortcut: unknown): string | null {
    if (typeof shortcut !== "string" || shortcut.length > 40) return null;
    const parts = shortcut.toLowerCase().split("+");
    const key = parts.pop();
    const modifiers = new Set<Modifier>();
    for (const part of parts) {
      const modifier = MODIFIER_ALIASES.get(part);
      if (!modifier) return null;
      modifiers.add(modifier);
    }
    const keyCode =
      NAMED_KEYS.get(key) ??
      (/^(?:[a-z0-9`,./;'[\]\\=-]|f(?:[1-9]|1[0-9]|2[0-4]))$/.test(key)
        ? key.toUpperCase()
        : null);
    if (
      !keyCode ||
      !(["mod", "meta", "ctrl", "alt"] as const).some((modifier) =>
        modifiers.has(modifier),
      )
    )
      return null;
    const accelerator = [
      ...(Object.keys(MODIFIERS) as Modifier[])
        .filter((modifier) => modifiers.has(modifier))
        .map((modifier) => MODIFIERS[modifier]),
      keyCode,
    ].join("+");
    return RESERVED_ACCELERATORS.has(accelerator) ? null : accelerator;
  }

  private static parse(value: unknown): MenuCommand | null {
    if (!value || typeof value !== "object") return null;
    const { id, label, group, menu, shortcut } = value as Record<
      string,
      unknown
    >;
    if (
      typeof id !== "string" ||
      !id ||
      id.length > 100 ||
      typeof label !== "string" ||
      !label.trim() ||
      label.length > 200 ||
      typeof group !== "string" ||
      group.length > 100 ||
      typeof menu !== "string" ||
      !(MENU_SLOTS as readonly string[]).includes(menu)
    )
      return null;
    return {
      id,
      label,
      group,
      menu: menu as MenuSlot,
      accelerator: MenuCommands.accelerator(shortcut),
    };
  }

  /** Replaces the published commands; true when the menu needs rebuilding. */
  publish(value: unknown): boolean {
    if (!Array.isArray(value)) return false;
    const accelerators = new Set<string>();
    const commands = value.slice(0, MAX_COMMANDS).flatMap((entry) => {
      const command = MenuCommands.parse(entry);
      if (!command) return [];
      // Electron fires only one item per accelerator; the dashboard runs the first too.
      if (command.accelerator && accelerators.has(command.accelerator))
        return [{ ...command, accelerator: null }];
      if (command.accelerator) accelerators.add(command.accelerator);
      return [command];
    });
    if (JSON.stringify(commands) === JSON.stringify(this.published))
      return false;
    this.published = commands;
    return true;
  }

  /** Ids of the published commands whose shortcut is a native accelerator. */
  nativeShortcutIds(): string[] {
    return (this.published ?? []).flatMap((command) =>
      command.accelerator ? [command.id] : [],
    );
  }

  /** Forgets the published commands, as when the page that sent them goes away. */
  reset(): boolean {
    if (!this.published) return false;
    this.published = null;
    return true;
  }

  /** One menu's items, with separators between groups; `fallback` until a page publishes. */
  items(slot: MenuSlot, fallback: MenuItem[] = []): MenuItem[] {
    if (!this.published) return fallback;
    const items: MenuItem[] = [];
    let group: string | null = null;
    for (const command of this.published) {
      if (command.menu !== slot) continue;
      if (group !== null && command.group !== group)
        items.push({ type: "separator" });
      group = command.group;
      items.push({
        label: command.label,
        ...(command.accelerator ? { accelerator: command.accelerator } : {}),
        click: (_item, _window, event) =>
          this.run(command.id, event?.triggeredByAccelerator === true),
      });
    }
    return items;
  }

  /** The Go, Thread and Pull Request menus, each only while it has commands. */
  commandMenus(): MenuItem[] {
    return (
      Object.entries(COMMAND_MENUS) as [keyof typeof COMMAND_MENUS, string][]
    ).flatMap(([slot, label]) => {
      const submenu = this.items(slot);
      return submenu.length ? [{ label, submenu }] : [];
    });
  }
}

module.exports = { DESKTOP_COMMAND_IDS, MenuCommands, isDesktopCommandId };
