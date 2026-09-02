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
import type { DesktopCommandId } from "@/desktop"

import { AppCommandPalette } from "@/components/AppCommandPalette"
import { AppShortcutReference } from "@/components/AppShortcutReference"
import { useSession } from "@/lib/session"
import { eventMatchesShortcut, shouldIgnoreHotkey } from "@/lib/hotkeys"
import { useTheme } from "@/lib/theme"

export interface AppCommand {
  id: string
  label: string
  aliases?: ReadonlyArray<string>
  shortcuts?: ReadonlyArray<string>
  allowRepeat?: boolean
  group: string
  run?: () => void | Promise<void>
  available?: boolean
  showInPalette?: boolean
  desktopId?: DesktopCommandId
  desktopShortcuts?: ReadonlyArray<string>
}

export function createNewThreadCommand(run: () => void): AppCommand {
  return {
    id: "new-thread",
    label: "New thread",
    aliases: ["new chat", "start thread"],
    shortcuts: ["c", "mod+n", "mod+shift+o"],
    group: "General",
    run,
    desktopId: "new-thread",
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

export function commandAcceptsKeyboardEvent(
  command: AppCommand,
  event: KeyboardEvent,
  desktop = false
): boolean {
  return Boolean(
    command.run &&
    (!event.repeat || command.allowRepeat) &&
    command.shortcuts?.some(
      (shortcut) =>
        !(desktop && command.desktopShortcuts?.includes(shortcut)) &&
        eventMatchesShortcut(event, shortcut)
    )
  )
}

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
      },
      createNewThreadCommand(() => void navigate({ to: "/agents" })),
      {
        id: "open-threads",
        label: "Open threads",
        aliases: ["home", "chats", "agents"],
        group: "Navigation",
        run: () => void navigate({ to: "/agents" }),
      },
      {
        id: "open-kanban",
        label: "Open Kanban",
        aliases: ["board", "all threads"],
        group: "Navigation",
        run: () =>
          void navigate({
            to: "/agents/threads",
            search: { page: 1, layout: "board", group: "focus" },
          }),
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
  const commandsRef = useRef(commands)
  const enabledRef = useRef(enabled)
  useEffect(() => {
    commandsRef.current = commands
    enabledRef.current = enabled
  }, [commands, enabled])

  useEffect(() => {
    if (!enabled || paletteOpen || shortcutReferenceOpen) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (shouldIgnoreHotkey(event, false, false)) return
      const desktop = Boolean(window.openSweDesktop)
      const command = commandsRef.current.find((candidate) =>
        commandAcceptsKeyboardEvent(candidate, event, desktop)
      )
      if (!command?.run) return
      event.preventDefault()
      void command.run()
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [enabled, paletteOpen, shortcutReferenceOpen])

  useEffect(() => {
    const desktop = window.openSweDesktop
    if (!desktop) return
    return desktop.onCommand((commandId) => {
      if (!enabledRef.current) return
      const command = commandsRef.current.find(
        (candidate) =>
          candidate.desktopId === commandId && candidate.run !== undefined
      )
      if (command?.run) void command.run()
    })
  }, [])

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
