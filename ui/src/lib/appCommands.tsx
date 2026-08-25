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

export interface AppCommand {
  id: string
  label: string
  aliases?: ReadonlyArray<string>
  shortcuts?: ReadonlyArray<string>
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
    shortcuts: ["c"],
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
      createNewThreadCommand(() => void navigate({ to: "/agents/new" })),
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
    [navigate, openPalette, openShortcutReference]
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
      if (shouldIgnoreHotkey(event)) return
      const desktop = Boolean(window.openSweDesktop)
      const command = commandsRef.current.find(
        (candidate) =>
          candidate.run &&
          candidate.shortcuts?.some(
            (shortcut) =>
              !(desktop && candidate.desktopShortcuts?.includes(shortcut)) &&
              eventMatchesShortcut(event, shortcut)
          )
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
