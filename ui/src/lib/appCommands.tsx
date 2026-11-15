import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import { useNavigate } from "@tanstack/react-router"
import type {
  DesktopCommandId,
  DesktopMenu,
  DesktopMenuCommand,
} from "@/desktop"

import { AppCommandPalette } from "@/components/AppCommandPalette"
import { AppShortcutReference } from "@/components/AppShortcutReference"
import { useSession } from "@/lib/session"
import {
  eventMatchesShortcut,
  isHotkeySuppressed,
  isTypingContext,
  isTypingSafeShortcut,
} from "@/lib/hotkeys"
import { useTheme } from "@/lib/theme"

export interface AppCommand {
  id: string
  label: string
  aliases?: ReadonlyArray<string>
  shortcuts?: ReadonlyArray<string>
  enableInFormFields?: boolean
  group: string
  run?: () => void | Promise<void>
  available?: boolean
  showInPalette?: boolean
  /** The desktop shell's built-in menu item, shown until the page publishes its commands. */
  desktopId?: DesktopCommandId
  /** Shortcuts desktop builds without `setMenuCommands` register on those items. */
  desktopShortcuts?: ReadonlyArray<string>
  /** Native menu placement; defaults by group. */
  menu?: DesktopMenu
  /** Native menu label; defaults to the title-cased label. */
  menuLabel?: string
}

export function createNewThreadCommand(run: () => void): AppCommand {
  return {
    id: "new-thread",
    label: "New thread",
    aliases: ["new chat", "start thread"],
    shortcuts: ["mod+n", "mod+shift+o"],
    enableInFormFields: true,
    group: "General",
    run,
    desktopId: "new-thread",
  }
}

const DESKTOP_MENU_BY_GROUP: Record<string, DesktopMenu> = {
  General: "file",
  Navigation: "go",
  Workspace: "view",
  Thread: "thread",
  Conversation: "edit",
  Composer: "thread",
  "Pull request": "pull-request",
}

const MENU_LOWERCASE_WORDS = new Set([
  "a",
  "an",
  "and",
  "as",
  "at",
  "but",
  "by",
  "for",
  "in",
  "nor",
  "of",
  "on",
  "or",
  "the",
  "to",
  "via",
  "with",
])

/**
 * The commands the desktop shell lists in its native menus, and which of their
 * shortcuts it confirmed registering there, so the page leaves those to the menu.
 */
export class DesktopMenuCommands {
  private nativeIds = new Set<string>()

  /** The shortcut the shell registers natively for a command: its first modifier combo. */
  static shortcut(command: AppCommand): string | undefined {
    return command.shortcuts?.find(isTypingSafeShortcut)
  }

  /** Every runnable command, as the shell lists it. */
  static entries(
    commands: ReadonlyArray<AppCommand>
  ): Array<DesktopMenuCommand> {
    return commands.flatMap((command) => {
      if (!command.run) return []
      const shortcut = DesktopMenuCommands.shortcut(command)
      return [
        {
          id: command.id,
          label: command.menuLabel ?? DesktopMenuCommands.title(command.label),
          group: command.group,
          menu: command.menu ?? DESKTOP_MENU_BY_GROUP[command.group] ?? "view",
          ...(shortcut ? { shortcut } : {}),
        },
      ]
    })
  }

  private static title(label: string): string {
    return label
      .split(" ")
      .map((word, index) =>
        index > 0 && MENU_LOWERCASE_WORDS.has(word)
          ? word
          : word.charAt(0).toUpperCase() + word.slice(1)
      )
      .join(" ")
  }

  async publish(entries: ReadonlyArray<DesktopMenuCommand>): Promise<void> {
    const setMenuCommands = window.openSweDesktop?.setMenuCommands
    if (!setMenuCommands) return
    try {
      this.nativeIds = new Set(await setMenuCommands(entries))
    } catch {
      this.nativeIds = new Set()
    }
  }

  /**
   * Whether `shortcut` may run `command` with focus on `target`: text fields
   * only take modifier shortcuts of commands that opt in, and ignore zones such
   * as the terminal take none unless an app dialog is open.
   */
  static allows(
    command: AppCommand,
    shortcut: string,
    target: EventTarget | null,
    dialogOpen: boolean
  ): boolean {
    if (!dialogOpen && isHotkeySuppressed(target)) return false
    const typing = isTypingContext(target)
    if (typing && !command.enableInFormFields) return false
    return !(typing || dialogOpen) || isTypingSafeShortcut(shortcut)
  }

  /** Whether the shell's menu, not the page, answers `shortcut` for `command`. */
  handles(command: AppCommand, shortcut: string): boolean {
    const desktop = window.openSweDesktop
    if (!desktop) return false
    if (!desktop.setMenuCommands)
      return Boolean(command.desktopShortcuts?.includes(shortcut))
    return (
      this.nativeIds.has(command.id) &&
      DesktopMenuCommands.shortcut(command) === shortcut
    )
  }
}

interface CommandRegistration {
  key: number
  commands: ReadonlyArray<AppCommand>
}

interface AppCommandsContextValue {
  commands: ReadonlyArray<AppCommand>
  openPalette: () => void
  openShortcutReference: () => void
  register: (commands: ReadonlyArray<AppCommand>) => () => void
}

const AppCommandsContext = createContext<AppCommandsContextValue | null>(null)

export function resolveAppCommands(
  globalCommands: ReadonlyArray<AppCommand>,
  registrations: ReadonlyArray<CommandRegistration>
): Array<AppCommand> {
  const resolved = new Map<string, AppCommand>()
  for (const command of globalCommands) resolved.set(command.id, command)
  for (const registration of registrations) {
    for (const command of registration.commands)
      resolved.set(command.id, command)
  }
  return [...resolved.values()].filter((command) => command.available !== false)
}

export function AppCommandProvider({
  children,
}: {
  children: React.ReactNode
}) {
  const navigate = useNavigate()
  const session = useSession()
  const { toggleTheme } = useTheme()
  const enabled = Boolean(session.data)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [shortcutReferenceOpen, setShortcutReferenceOpen] = useState(false)
  const [registrations, setRegistrations] = useState<
    Array<CommandRegistration>
  >([])
  const nextRegistrationKey = useRef(0)

  const openPalette = useCallback(() => setPaletteOpen(true), [])
  const openShortcutReference = useCallback(
    () => setShortcutReferenceOpen(true),
    []
  )

  const globalCommands = useMemo<ReadonlyArray<AppCommand>>(
    () => [
      createNewThreadCommand(() => void navigate({ to: "/agents" })),
      {
        id: "search-commands",
        label: "Search commands and threads",
        aliases: ["command palette", "quick switcher"],
        shortcuts: ["mod+k"],
        group: "General",
        run: openPalette,
        showInPalette: false,
        desktopId: "show-command-palette",
        desktopShortcuts: ["mod+k"],
        enableInFormFields: true,
        menuLabel: "Search Commands and Threads…",
      },
      {
        id: "open-threads",
        label: "Open threads",
        aliases: ["home", "chats", "agents"],
        group: "Navigation",
        run: () => void navigate({ to: "/agents" }),
      },
      {
        id: "open-skills",
        label: "Open skills",
        group: "Navigation",
        run: () => void navigate({ to: "/agents/skills" }),
      },
      {
        id: "open-automations",
        label: "Open automations",
        aliases: ["schedules"],
        group: "Navigation",
        run: () => void navigate({ to: "/agents/automations" }),
      },
      {
        id: "open-reviews",
        label: "Open reviews",
        aliases: ["pull requests", "prs"],
        group: "Navigation",
        run: () => void navigate({ to: "/agents/reviews" }),
      },
      {
        id: "toggle-theme",
        label: "Toggle color theme",
        aliases: ["dark mode", "light mode", "appearance"],
        group: "General",
        run: toggleTheme,
        menu: "view",
      },
      {
        id: "open-settings",
        label: "Open settings",
        aliases: ["preferences", "dashboard"],
        shortcuts: ["mod+,"],
        group: "Navigation",
        run: () => void navigate({ to: "/my-settings" }),
        desktopId: "open-settings",
        desktopShortcuts: ["mod+,"],
        enableInFormFields: true,
        menu: "app",
        menuLabel: "Settings…",
      },
      {
        id: "show-keyboard-shortcuts",
        label: "Keyboard shortcuts",
        aliases: ["shortcut reference", "help"],
        shortcuts: ["mod+/", "?"],
        group: "General",
        run: openShortcutReference,
        desktopId: "show-keyboard-shortcuts",
        desktopShortcuts: ["mod+/"],
        enableInFormFields: true,
        menu: "help",
      },
    ],
    [navigate, openPalette, openShortcutReference, toggleTheme]
  )

  const register = useCallback((commands: ReadonlyArray<AppCommand>) => {
    const key = nextRegistrationKey.current++
    setRegistrations((current) => [...current, { key, commands }])
    return () => {
      setRegistrations((current) =>
        current.filter((registration) => registration.key !== key)
      )
    }
  }, [])

  const commands = useMemo(
    () => resolveAppCommands(globalCommands, registrations),
    [globalCommands, registrations]
  )
  const dialogOpen = paletteOpen || shortcutReferenceOpen
  const commandsRef = useRef(commands)
  const enabledRef = useRef(enabled)
  const dialogOpenRef = useRef(dialogOpen)
  useEffect(() => {
    commandsRef.current = commands
    enabledRef.current = enabled
    dialogOpenRef.current = dialogOpen
  }, [commands, dialogOpen, enabled])

  const [desktopMenu] = useState(() => new DesktopMenuCommands())
  const runCommand = useCallback((command: AppCommand | undefined) => {
    if (!command?.run) return
    setPaletteOpen(false)
    setShortcutReferenceOpen(false)
    void command.run()
  }, [])

  useEffect(() => {
    if (!enabled) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing || event.repeat) return
      let command: AppCommand | undefined
      let shortcut: string | undefined
      for (const candidate of commandsRef.current) {
        if (!candidate.run) continue
        shortcut = candidate.shortcuts?.find(
          (value) =>
            !desktopMenu.handles(candidate, value) &&
            eventMatchesShortcut(event, value)
        )
        if (shortcut) {
          command = candidate
          break
        }
      }
      if (!command || !shortcut) return
      if (
        !DesktopMenuCommands.allows(command, shortcut, event.target, dialogOpen)
      )
        return
      event.preventDefault()
      runCommand(command)
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [desktopMenu, dialogOpen, enabled, runCommand])

  const menuCommands = JSON.stringify(
    enabled ? DesktopMenuCommands.entries(commands) : []
  )
  useEffect(() => {
    void desktopMenu.publish(
      JSON.parse(menuCommands) as Array<DesktopMenuCommand>
    )
  }, [desktopMenu, menuCommands])

  useEffect(() => {
    const desktop = window.openSweDesktop
    if (!desktop) return
    const stopShellCommands = desktop.onCommand((commandId) => {
      if (!enabledRef.current) return
      runCommand(
        commandsRef.current.find(
          (candidate) =>
            candidate.desktopId === commandId && candidate.run !== undefined
        )
      )
    })
    const stopMenuCommands = desktop.onMenuCommand?.(
      (commandId, viaShortcut) => {
        if (!enabledRef.current) return
        const command = commandsRef.current.find(
          (candidate) => candidate.id === commandId
        )
        const shortcut = command && DesktopMenuCommands.shortcut(command)
        if (
          viaShortcut &&
          command &&
          shortcut &&
          !DesktopMenuCommands.allows(
            command,
            shortcut,
            document.activeElement,
            dialogOpenRef.current
          )
        )
          return
        runCommand(command)
      }
    )
    return () => {
      stopShellCommands()
      stopMenuCommands?.()
    }
  }, [runCommand])

  const context = useMemo<AppCommandsContextValue>(
    () => ({ commands, openPalette, openShortcutReference, register }),
    [commands, openPalette, openShortcutReference, register]
  )

  return (
    <AppCommandsContext.Provider value={context}>
      {children}
      {enabled && (
        <>
          <AppCommandPalette
            commands={commands}
            open={paletteOpen}
            onOpenChange={setPaletteOpen}
          />
          <AppShortcutReference
            commands={commands}
            open={shortcutReferenceOpen}
            onOpenChange={setShortcutReferenceOpen}
          />
        </>
      )}
    </AppCommandsContext.Provider>
  )
}

export function useAppCommandControls() {
  const context = useContext(AppCommandsContext)
  if (!context) throw new Error("AppCommandProvider is missing")
  return {
    openPalette: context.openPalette,
    openShortcutReference: context.openShortcutReference,
  }
}

export function useAppCommand(commandId: string) {
  const context = useContext(AppCommandsContext)
  if (!context) throw new Error("AppCommandProvider is missing")
  return context.commands.find((command) => command.id === commandId)
}

export function useRegisterAppCommands(commands: ReadonlyArray<AppCommand>) {
  const context = useContext(AppCommandsContext)
  if (!context) throw new Error("AppCommandProvider is missing")
  const { register } = context
  useEffect(() => register(commands), [commands, register])
}
