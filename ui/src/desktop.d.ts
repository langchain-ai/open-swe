import type { ThreadPrDiffFile } from "@/features/agents/lib/api"
import type {
  WorkspaceFileIndex,
  WorkspacePath,
} from "@/features/agents/lib/workspaceFiles"
import type { AgentPullRequest, ImageChunk } from "@/features/agents/lib/types"
import type { Skill } from "@/lib/api"

export type DesktopCommandId =
  | "new-thread"
  | "show-command-palette"
  | "open-settings"
  | "show-keyboard-shortcuts"
  | "toggle-sidebar"

export interface DesktopProject {
  cwd: string
  name: string
  addedAt: number
  /** Terminal/panel scope for the project itself, before a thread exists. */
  scopeId: string
}

export interface DesktopLocalThreadSummary {
  id: string
  cwd: string
  worktreePath: string | null
  /** Worktrees this app created for the thread, removed when it is deleted. */
  ownedWorktrees?: Array<string>
  title: string
  viewed: boolean
  archived?: boolean
  createdAt: number
  updatedAt: number
  modelId: string | null
  effort: string | null
  pending?: DesktopLocalPromptInput | null
}

export type DesktopWorkspaceMode = "local" | "worktree"

export interface DesktopProjectRef {
  name: string
  current: boolean
  isDefault: boolean
  worktreePath: string | null
}

export type DesktopLocalActivity = Record<string, "running" | "error">

export interface DesktopLocalDiff {
  status: "ready" | "missing" | "error"
  truncated: boolean
  files: Array<ThreadPrDiffFile>
  repository?: { branch: string | null; pr: AgentPullRequest | null }
}

export interface DesktopLocalPromptInput {
  prompt: string
  images: Array<ImageChunk>
  skills: Array<Skill>
}

export type DesktopTerminalStatus = "starting" | "running" | "exited" | "error"
export interface DesktopTerminalTarget {
  localSessionId: string
  terminalId: string
}
export interface DesktopTerminalSessionSnapshot extends DesktopTerminalTarget {
  cwd: string
  status: DesktopTerminalStatus
  pid: number | null
  history: string
  exitCode: number | null
  exitSignal: number | null
  hasRunningSubprocess: boolean
  label: string
  updatedAt: string
  sequence: number
}
export interface DesktopTerminalSummary extends DesktopTerminalTarget {
  cwd: string
  status: DesktopTerminalStatus
  pid: number | null
  exitCode: number | null
  exitSignal: number | null
  hasRunningSubprocess: boolean
  label: string
  updatedAt: string
}
export type DesktopTerminalAttachEvent =
  | (DesktopTerminalTarget & {
      type: "started" | "restarted"
      snapshot: DesktopTerminalSessionSnapshot
      sequence: number
    })
  | (DesktopTerminalTarget & { type: "output"; data: string; sequence: number })
  | (DesktopTerminalTarget & {
      type: "exited"
      exitCode: number | null
      exitSignal: number | null
      sequence: number
    })
  | (DesktopTerminalTarget & { type: "closed" | "cleared"; sequence: number })
  | (DesktopTerminalTarget & {
      type: "error"
      message: string
      sequence: number
    })
  | (DesktopTerminalTarget & {
      type: "activity"
      hasRunningSubprocess: boolean
      label: string
      sequence: number
    })
export type DesktopTerminalMetadataEvent =
  | { type: "upsert"; terminal: DesktopTerminalSummary }
  | (DesktopTerminalTarget & { type: "remove" })

export type DesktopBrowserColorScheme = "system" | "light" | "dark"
export type DesktopBrowserNavStatus =
  | { kind: "idle" }
  | { kind: "loading"; url: string; title: string }
  | { kind: "success"; url: string; title: string }
  | {
      kind: "failed"
      url: string
      title: string
      code: number
      description: string
    }
/** Main-process view of one in-app browser tab, pushed on every change. */
export interface DesktopBrowserTabState {
  tabId: string
  webContentsId: number | null
  nav: DesktopBrowserNavStatus
  canGoBack: boolean
  canGoForward: boolean
  zoomFactor: number
  colorScheme: DesktopBrowserColorScheme
  /** PNG data URL captured by the main process, or null. */
  favicon: string | null
  updatedAt: string
}
export interface DesktopBrowserTabDefaults {
  zoomFactor?: number
  colorScheme?: DesktopBrowserColorScheme
}
/** Attributes a `<webview>` needs so the main process accepts it. */
export interface DesktopBrowserConfig {
  partition: string
  webPreferences: string
}
export interface DesktopBrowserBridge {
  getConfig: () => Promise<DesktopBrowserConfig>
  createTab: (
    tabId: string,
    defaults?: DesktopBrowserTabDefaults
  ) => Promise<void>
  closeTab: (tabId: string) => Promise<void>
  registerWebview: (tabId: string, webContentsId: number) => Promise<void>
  navigate: (tabId: string, url: string) => Promise<void>
  goBack: (tabId: string) => Promise<void>
  goForward: (tabId: string) => Promise<void>
  reload: (tabId: string) => Promise<void>
  hardReload: (tabId: string) => Promise<void>
  zoomIn: (tabId: string) => Promise<void>
  zoomOut: (tabId: string) => Promise<void>
  resetZoom: (tabId: string) => Promise<void>
  setColorScheme: (
    tabId: string,
    colorScheme: DesktopBrowserColorScheme
  ) => Promise<void>
  openDevTools: (tabId: string) => Promise<void>
  onStateChange: (
    callback: (state: DesktopBrowserTabState) => void
  ) => () => void
}

export type DesktopUpdateState = {
  status: "idle" | "downloading" | "ready" | "installing"
  version?: string
}

export interface DesktopTerminalBridge {
  attach: (
    input: DesktopTerminalTarget & {
      cwd?: string
      cols?: number
      rows?: number
      restartIfNotRunning?: boolean
    }
  ) => Promise<DesktopTerminalSessionSnapshot>
  open: (
    input: DesktopTerminalTarget & { cwd: string; cols?: number; rows?: number }
  ) => Promise<DesktopTerminalSessionSnapshot>
  write: (input: DesktopTerminalTarget & { data: string }) => Promise<void>
  resize: (
    input: DesktopTerminalTarget & { cols: number; rows: number }
  ) => Promise<void>
  clear: (input: DesktopTerminalTarget) => Promise<void>
  restart: (
    input: DesktopTerminalTarget & { cwd: string; cols?: number; rows?: number }
  ) => Promise<DesktopTerminalSessionSnapshot>
  detach: (input: DesktopTerminalTarget) => Promise<void>
  close: (
    input: DesktopTerminalTarget & { deleteHistory?: boolean }
  ) => Promise<void>
  list: (localSessionId: string) => Promise<Array<DesktopTerminalSummary>>
  subscribeMetadata: (
    localSessionId: string
  ) => Promise<Array<DesktopTerminalSummary>>
  detachMetadata: (localSessionId: string) => Promise<void>
  onEvent: (callback: (event: DesktopTerminalAttachEvent) => void) => () => void
  onMetadata: (
    callback: (event: DesktopTerminalMetadataEvent) => void
  ) => () => void
}

declare global {
  const __OPEN_SWE_BUNDLE_COMMIT__: string | null
  const __OPEN_SWE_BUNDLE_BUILT_AT__: string

  interface Window {
    /** This bundle's own build identity, stamped by vite.config.ts at build time. */
    __OPEN_SWE_BUNDLE__?: { commit: string | null; built_at: string }
    openSweDesktop?: {
      isDesktop: true
      writeClipboard: (value: string) => Promise<void>
      onCommand: (callback: (commandId: DesktopCommandId) => void) => () => void
      listProjects: () => Promise<Array<DesktopProject>>
      getProjectBranches: (cwd: string) => Promise<{
        current: string | null
        branches: Array<DesktopProjectRef>
      }>
      watchProjectHead: (cwd: string | null) => Promise<void>
      onProjectHeadChanged: (callback: (cwd: string) => void) => () => void
      checkoutProjectBranch: (input: {
        cwd: string
        branch: string
      }) => Promise<string>
      setLocalBranch: (input: {
        threadId: string
        branch: string
      }) => Promise<DesktopLocalThreadSummary | null>
      addProject: () => Promise<DesktopProject | null>
      removeProject: (cwd: string) => Promise<boolean>
      getVersion: () => Promise<string>
      getUpdateState: () => Promise<DesktopUpdateState>
      installUpdate: () => Promise<boolean>
      onUpdateState: (
        callback: (state: DesktopUpdateState) => void
      ) => () => void
      onProjectsChanged: (
        callback: (projects: Array<DesktopProject>) => void
      ) => () => void
      openExternal: (url: string) => Promise<boolean>
      connectService: (provider: "slack" | "notion") => Promise<boolean>
      resolveLocalProjectPath: (input: {
        localSessionId: string
        path: string
      }) => Promise<string | null>
      localModelCredentialStatus: (modelId?: string) => Promise<{
        available: boolean
        variable: string | null
        canSignIn?: boolean
      }>
      openLocalTrace: (threadId: string) => Promise<boolean>
      signInLocalOpenAI: () => Promise<{ signedIn: boolean }>
      startLocalThread: (
        input: DesktopLocalPromptInput & {
          cwd: string
          workspaceMode?: DesktopWorkspaceMode
          baseBranch?: string | null
          modelId?: string
          effort?: string
        }
      ) => Promise<DesktopLocalThreadSummary>
      getLocalPrompt: (
        threadId: string
      ) => Promise<DesktopLocalPromptInput | null>
      clearLocalPrompt: (
        threadId: string
      ) => Promise<DesktopLocalThreadSummary | null>
      getLocalThread: (
        threadId: string
      ) => Promise<DesktopLocalThreadSummary | null>
      listLocalThreads: () => Promise<Array<DesktopLocalThreadSummary>>
      setAppearance: (
        appearance: "light" | "dark" | "system"
      ) => Promise<boolean>
      localActivity: () => Promise<DesktopLocalActivity>
      updateLocalThread: (input: {
        threadId: string
        title?: string
        viewed?: boolean
        archived?: boolean
        modelId?: string
        effort?: string
      }) => Promise<DesktopLocalThreadSummary | null>
      deleteLocalThread: (threadId: string) => Promise<boolean>
      getLocalDiff: (threadId: string) => Promise<DesktopLocalDiff>
      getLocalPrDiff: (threadId: string) => Promise<DesktopLocalDiff>
      getProjectDiff: (cwd: string) => Promise<DesktopLocalDiff>
      readWorkspacePath: (input: {
        localSessionId: string
        relativePath: string
      }) => Promise<WorkspacePath>
      listWorkspaceFiles: (
        localSessionId: string
      ) => Promise<WorkspaceFileIndex>
      terminal: DesktopTerminalBridge
      browser: DesktopBrowserBridge
    }
  }
}

export {}
