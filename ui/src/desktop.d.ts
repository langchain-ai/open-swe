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
  model: string | null
  effort: string | null
  status: "starting" | "idle" | "running" | "error"
  createdAt: number
  updatedAt: number
}

export interface DesktopAcpSession extends DesktopAcpSessionSummary {
  events: Array<DesktopAcpEvent>
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
          model: string | null
          effort: string | null
        }
      ) => Promise<DesktopAcpSession>
      promptAcpSession: (
        input: DesktopAcpPromptInput & { sessionId: string }
      ) => Promise<DesktopAcpSession>
      cancelAcpSession: (sessionId: string) => Promise<void>
      getAcpSession: (sessionId: string) => Promise<DesktopAcpSession | null>
      listAcpSessions: () => Promise<Array<DesktopAcpSessionSummary>>
      onAcpEvent: (
        callback: (payload: {
          sessionId: string
          event: DesktopAcpEvent
          session: DesktopAcpSessionSummary
        }) => void
      ) => () => void
    }
  }
}

export {}
