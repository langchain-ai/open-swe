import type { ThreadPrDiffFile } from "@/features/agents/lib/api"
import type { ImageChunk } from "@/features/agents/lib/types"

export interface DesktopProject {
  cwd: string
  name: string
  addedAt: number
}

export interface DesktopAcpEvent {
  sequence: number
  timestamp: string
  type: string
  [key: string]: unknown
}

export interface DesktopAcpSessionSummary {
  id: string
  cwd: string
  title: string
  status: "starting" | "idle" | "running" | "error"
  createdAt: number
  updatedAt: number
  modelId?: string
  effort?: string
}

export interface DesktopAcpSession extends DesktopAcpSessionSummary {
  events: Array<DesktopAcpEvent>
}

export interface DesktopAcpDraftSession {
  id: string
  cwd: string
}

export interface DesktopAcpDiff {
  status: "ready" | "missing" | "error"
  truncated: boolean
  files: Array<ThreadPrDiffFile>
}

export interface DesktopAcpPromptInput {
  prompt: string
  images: Array<ImageChunk>
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
  | (DesktopTerminalTarget & {
      type: "output"
      data: string
      sequence: number
    })
  | (DesktopTerminalTarget & {
      type: "exited"
      exitCode: number | null
      exitSignal: number | null
      sequence: number
    })
  | (DesktopTerminalTarget & {
      type: "closed" | "cleared"
      sequence: number
    })
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
    input: DesktopTerminalTarget & {
      cwd: string
      cols?: number
      rows?: number
    }
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
      listProjects: () => Promise<Array<DesktopProject>>
      addProject: () => Promise<DesktopProject | null>
      removeProject: (cwd: string) => Promise<boolean>
      onProjectsChanged: (
        callback: (projects: Array<DesktopProject>) => void
      ) => () => void
      openExternal: (url: string) => Promise<boolean>
      resolveAcpProjectPath: (input: {
        localSessionId: string
        path: string
      }) => Promise<string | null>
      createAcpDraftSession?: (cwd: string) => Promise<DesktopAcpDraftSession>
      deleteAcpDraftSession?: (sessionId: string) => Promise<boolean>
      startAcpSession: (
        input: DesktopAcpPromptInput & {
          cwd: string
          draftSessionId?: string
          modelId?: string
          effort?: string
        }
      ) => Promise<DesktopAcpSession>
      promptAcpSession: (
        input: DesktopAcpPromptInput & {
          sessionId: string
          modelId?: string
          effort?: string
        }
      ) => Promise<DesktopAcpSession>
      cancelAcpSession: (sessionId: string) => Promise<void>
      deleteAcpSession: (sessionId: string) => Promise<boolean>
      getAcpSession: (sessionId: string) => Promise<DesktopAcpSession | null>
      listAcpSessions: () => Promise<Array<DesktopAcpSessionSummary>>
      getAcpDiff: (sessionId: string) => Promise<DesktopAcpDiff>
      onAcpEvent: (
        callback: (payload: {
          sessionId: string
          event: DesktopAcpEvent
          session: DesktopAcpSessionSummary
        }) => void
      ) => () => void
      terminal: DesktopTerminalBridge
    }
  }
}

export {}
