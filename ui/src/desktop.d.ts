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
}

export interface DesktopAcpSession extends DesktopAcpSessionSummary {
  events: Array<DesktopAcpEvent>
}

/** What a local session changed, shaped like the cloud thread's turn diff. */
export interface DesktopAcpDiff {
  status: "ready" | "missing" | "error"
  truncated: boolean
  files: Array<ThreadPrDiffFile>
}

export interface DesktopAcpPromptInput {
  prompt: string
  images: Array<ImageChunk>
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
      startAcpSession: (
        input: DesktopAcpPromptInput & {
          cwd: string
          modelId?: string
          effort?: string
        }
      ) => Promise<DesktopAcpSession>
      promptAcpSession: (
        input: DesktopAcpPromptInput & { sessionId: string }
      ) => Promise<DesktopAcpSession>
      cancelAcpSession: (sessionId: string) => Promise<void>
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
      terminal: {
        create: (id: string, cwd: string) => void
        write: (id: string, data: string) => void
        resize: (id: string, cols: number, rows: number) => void
        destroy: (id: string) => void
        onData: (callback: (id: string, data: string) => void) => () => void
        onError: (callback: (id: string, message: string) => void) => () => void
      }
    }
  }
}

export {}
