export type DesktopCommandId =
  | "new-thread"
  | "show-command-palette"
  | "open-settings"
  | "show-keyboard-shortcuts"
  | "toggle-sidebar"

/** A native menu the shell puts dashboard commands in; `app` is the macOS app menu, File elsewhere. */
export type DesktopMenu =
  | "app"
  | "file"
  | "edit"
  | "view"
  | "go"
  | "thread"
  | "pull-request"
  | "help"

export interface DesktopMenuCommand {
  id: string
  label: string
  group: string
  menu: DesktopMenu
  /** Registered as the item's native accelerator when it has a modifier. */
  shortcut?: string
}

export type DesktopUpdateState = {
  status: "idle" | "downloading" | "ready" | "installing"
  version?: string
}

declare global {
  const __OPEN_SWE_BUNDLE_COMMIT__: string | null
  const __OPEN_SWE_BUNDLE_BUILT_AT__: string

  interface Window {
    /** This bundle's own build identity, stamped by vite.config.ts at build time. */
    __OPEN_SWE_BUNDLE__?: { commit: string | null; built_at: string }
    /** The Navigation API: Chromium, and so the desktop app, has it; TypeScript's DOM types don't yet. */
    navigation?: Pick<
      EventTarget,
      "addEventListener" | "removeEventListener"
    > & {
      readonly canGoBack: boolean
      readonly canGoForward: boolean
    }
    openSweDesktop?: {
      isDesktop: true
      writeClipboard: (value: string) => Promise<void>
      copyPageLink: () => Promise<void>
      /** Missing before desktop builds that open app pages in the system browser. */
      openInBrowser?: (path: string) => Promise<void>
      onCommand: (callback: (commandId: DesktopCommandId) => void) => () => void
      /**
       * Missing before desktop builds that list the dashboard's commands in their
       * menus; resolves to the ids whose shortcut the menu registered.
       */
      setMenuCommands?: (
        commands: ReadonlyArray<DesktopMenuCommand>
      ) => Promise<Array<string>>
      /** `viaShortcut` is true when the item's accelerator, not a click, chose it. */
      onMenuCommand?: (
        callback: (commandId: string, viaShortcut: boolean) => void
      ) => () => void
      getVersion: () => Promise<string>
      getUpdateState: () => Promise<DesktopUpdateState>
      installUpdate: () => Promise<boolean>
      onUpdateState: (
        callback: (state: DesktopUpdateState) => void
      ) => () => void
      openExternal: (url: string) => Promise<boolean>
      connectService: (
        provider: "slack" | "github",
        options?: { recoveryToken: string }
      ) => Promise<boolean | { recoveryToken: string; error?: string }>
      cancelConnectService?: (provider: "slack" | "github") => Promise<void>
      /**
       * Serve this app's approved local actions; resolves to the bridge id a run
       * names as `local_access_bridge_id`.
       */
      ensureLocalAccessBridge?: () => Promise<string>
      /** Folders (`~/…` under home) this thread may act in without asking, and until when (ms). */
      localAccessGrants?: (
        threadId: string
      ) => Promise<Array<{ folder: string; until: number }>>
      revokeLocalAccessGrants?: (threadId: string) => Promise<void>
      onLocalAccessGrantsChanged?: (callback: () => void) => () => void
      /** The thread's local actions waiting on the person's OK. */
      localAccessRequests?: (threadId: string) => Promise<
        Array<{
          id: string
          kind: "read" | "write" | "import" | "execute"
          concern: string | null
          createdAt: number
        }>
      >
      /** Opens the desktop's own approval panel on a request; only it can decide. */
      reviewLocalAccessRequest?: (id: string) => Promise<void>
      onLocalAccessRequestsChanged?: (callback: () => void) => () => void
      setAppearance: (
        appearance: "light" | "dark" | "system"
      ) => Promise<boolean>
    }
  }
}

export {}
