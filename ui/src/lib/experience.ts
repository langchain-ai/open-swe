import { useCallback, useSyncExternalStore } from "react"
import { useSession } from "./session"
import type { AgentThread } from "@/features/agents/lib/types"

export type Experience = "coding" | "agent_workspace"

class ExperienceStore {
  private listeners = new Set<() => void>()
  private selections = new Map<string, Experience>()
  private locations = new Map<string, string>()

  rememberLocation(userId: string, experience: Experience, href: string) {
    if (
      userId &&
      (href === "/agents" ||
        href.startsWith("/agents/") ||
        href.startsWith("/agents?"))
    ) {
      this.locations.set(`${userId}:${experience}`, href)
    }
  }

  location(userId: string, experience: Experience): string {
    return this.locations.get(`${userId}:${experience}`) ?? "/agents"
  }

  key(login: string) {
    return `open-swe.desktop.experience:${login}`
  }

  read(login: string): Experience {
    try {
      const stored = window.localStorage.getItem(this.key(login))
      if (stored === "coding" || stored === "agent_workspace") return stored
    } catch {
      return this.selections.get(login) ?? "agent_workspace"
    }
    return "agent_workspace"
  }

  write(login: string, experience: Experience) {
    this.selections.set(login, experience)
    try {
      window.localStorage.setItem(this.key(login), experience)
    } catch (error) {
      console.warn("Could not save the desktop experience", error)
    }
    this.listeners.forEach((listener) => listener())
  }

  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    window.addEventListener("storage", listener)
    return () => {
      this.listeners.delete(listener)
      window.removeEventListener("storage", listener)
    }
  }

  thread(thread: Pick<AgentThread, "experience">): Experience {
    return thread.experience === "agent_workspace"
      ? "agent_workspace"
      : "coding"
  }

  includes(thread: AgentThread, experience?: Experience): boolean {
    return !experience || this.thread(thread) === experience
  }

  filter(
    threads: Array<AgentThread>,
    experience?: Experience
  ): Array<AgentThread> {
    if (!experience) return threads
    return threads
      .filter((thread) => this.includes(thread, experience))
      .map((thread) =>
        thread.taskWorkers
          ? {
              ...thread,
              taskWorkers: this.filter(thread.taskWorkers, experience),
            }
          : thread
      )
  }
}

export const experienceStore = new ExperienceStore()

export function useExperience() {
  const session = useSession()
  const login = session.data?.login ?? ""
  const selected = useSyncExternalStore(
    experienceStore.subscribe,
    () => experienceStore.read(login),
    () => "agent_workspace" as const
  )
  const select = useCallback(
    (experience: Experience) => experienceStore.write(login, experience),
    [login]
  )
  return {
    experience: selected,
    select,
  }
}
