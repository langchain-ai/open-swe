import type { ThreadPrDiffFile } from "@/features/agents/lib/api"
import type { AgentThread, ImageChunk } from "@/features/agents/lib/types"
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
}

export interface DesktopLocalThreadSummary {
  id: string
  cwd: string
  title: string
  createdAt: number
  updatedAt: number
  modelId: string | null
  effort: string | null
}

export type DesktopLocalActivity = Record<string, "running" | "error">

export interface DesktopLocalDiff {
  status: "ready" | "missing" | "error"
  truncated: boolean
  files: Array<ThreadPrDiffFile>
  repository?: { branch: string | null; pr: AgentThread["pr"] | null }
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
  interface Window {
    openSweDesktop?: {
      isDesktop: true
      onCommand: (callback: (commandId: DesktopCommandId) => void) => () => void
      listProjects: () => Promise<Array<DesktopProject>>
      getProjectBranches: (cwd: string) => Promise<{
        current: string | null
        branches: Array<string>
      }>
      checkoutProjectBranch: (input: {
        cwd: string
        branch: string
        create?: boolean
      }) => Promise<string>
      addProject: () => Promise<DesktopProject | null>
      removeProject: (cwd: string) => Promise<boolean>
      onProjectsChanged: (
        callback: (projects: Array<DesktopProject>) => void
      ) => () => void
      openExternal: (url: string) => Promise<boolean>
      resolveLocalProjectPath: (input: {
        localSessionId: string
        path: string
      }) => Promise<string | null>
      localModelCredentialStatus: (
        modelId?: string
      ) => Promise<{ available: boolean; variable: string | null }>
      startLocalThread: (
        input: DesktopLocalPromptInput & {
          cwd: string
          modelId?: string
          effort?: string
        }
      ) => Promise<DesktopLocalThreadSummary>
      getLocalPrompt: (
        threadId: string
      ) => Promise<DesktopLocalPromptInput | null>
      clearLocalPrompt: (threadId: string) => Promise<boolean>
      getLocalThread: (
        threadId: string
      ) => Promise<DesktopLocalThreadSummary | null>
      listLocalThreads: () => Promise<Array<DesktopLocalThreadSummary>>
      localActivity: () => Promise<DesktopLocalActivity>
      updateLocalThread: (input: {
        threadId: string
        modelId?: string
        effort?: string
      }) => Promise<DesktopLocalThreadSummary | null>
      deleteLocalThread: (threadId: string) => Promise<boolean>
      getLocalDiff: (threadId: string) => Promise<DesktopLocalDiff>
      terminal: DesktopTerminalBridge
    }
  }
}

export {}
