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

/**
 * What this Mac knows about one of its "This Mac" threads: the checkout the
 * cloud agent works in, through `bridgeId`. `id` is the cloud thread id.
 */
export interface DesktopLocalThread {
  id: string
  cwd: string
  worktreePath: string | null
  /** Worktrees this app created for the thread, removed when it is discarded. */
  ownedWorktrees: Array<string>
  bridgeId: string | null
  createdAt: number
  updatedAt: number
}

/** A thread the retired local LangGraph server ran, before "This Mac" moved to the cloud. */
export interface DesktopLegacyLocalThread {
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
  pending?: DesktopLegacyLocalPromptInput | null
}

export type DesktopLegacyLocalActivity = Record<string, "running" | "error">

export interface DesktopLegacyLocalPromptInput {
  prompt: string
  images: Array<ImageChunk>
  skills: Array<Skill>
}

export type DesktopWorkspaceMode = "local" | "worktree"

export interface DesktopProjectRef {
  name: string
  current: boolean
  isDefault: boolean
  worktreePath: string | null
}

export interface DesktopLocalDiff {
  status: "ready" | "missing" | "error"
  truncated: boolean
  files: Array<ThreadPrDiffFile>
  repository?: { branch: string | null; pr: AgentPullRequest | null }
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
      }) => Promise<DesktopLocalThread | null>
      addProject: () => Promise<DesktopProject | null>
      removeProject: (cwd: string) => Promise<boolean>
      getVersion: () => Promise<string>
      getUpdateState: () => Promise<DesktopUpdateState>
      installUpdate: () => Promise<boolean>
      onUpdateState: (
        callback: (state: DesktopUpdateState) => void
      ) => () => void
      onLocalThreadsChanged: (callback: () => void) => () => void
      onProjectsChanged: (
        callback: (projects: Array<DesktopProject>) => void
      ) => () => void
      openExternal: (url: string) => Promise<boolean>
      connectService: (provider: "slack" | "langsmith") => Promise<boolean>
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
      getLegacyLocalPrompt: (
        threadId: string
      ) => Promise<DesktopLegacyLocalPromptInput | null>
      clearLegacyLocalPrompt: (
        threadId: string
      ) => Promise<DesktopLegacyLocalThread | null>
      getLegacyLocalThread: (
        threadId: string
      ) => Promise<DesktopLegacyLocalThread | null>
      listLegacyLocalThreads: () => Promise<Array<DesktopLegacyLocalThread>>
      legacyLocalActivity: () => Promise<DesktopLegacyLocalActivity>
      updateLegacyLocalThread: (input: {
        threadId: string
        title?: string
        viewed?: boolean
        archived?: boolean
        modelId?: string
        effort?: string
      }) => Promise<DesktopLegacyLocalThread | null>
      deleteLegacyLocalThread: (threadId: string) => Promise<boolean>
      /**
       * Set up a thread the renderer is about to start in the cloud: its
       * checkout or a new worktree, and the bridge its agent runs through.
       */
      prepareLocalThread: (input: {
        threadId: string
        cwd: string
        workspaceMode?: DesktopWorkspaceMode
        baseBranch?: string | null
      }) => Promise<{ bridgeId: string; repo: string | null }>
      /** Move a cloud thread onto a new worktree here; resolves to its bridge id. */
      takeOverThread: (input: {
        threadId: string
        repo: string
      }) => Promise<string>
      /** Serve the thread's checkout again before its next run. */
      ensureLocalBridge: (threadId: string) => Promise<boolean>
      getLocalThread: (threadId: string) => Promise<DesktopLocalThread | null>
      listLocalThreads: () => Promise<Array<DesktopLocalThread>>
      /** Forget a deleted thread and remove the worktrees this app made for it. */
      discardLocalThread: (threadId: string) => Promise<boolean>
      setAppearance: (
        appearance: "light" | "dark" | "system"
      ) => Promise<boolean>
      getLocalDiff: (threadId: string) => Promise<DesktopLocalDiff>
      getLocalPrDiff: (threadId: string) => Promise<DesktopLocalDiff>
      getLocalPr: (threadId: string) => Promise<AgentPullRequest | null>
      getProjectDiff: (cwd: string) => Promise<DesktopLocalDiff>
      readWorkspacePath: (input: {
        localSessionId: string
        relativePath: string
      }) => Promise<WorkspacePath>
      listWorkspaceFiles: (
        localSessionId: string
      ) => Promise<WorkspaceFileIndex>
      terminal: DesktopTerminalBridge
    }
  }
}

export {}
