import { createContext, useContext } from "react"

import type { AgentVisibility } from "../core/agentService"
import { APIError } from "../core/apiClient"
import type { AgentStore } from "../state/agentStore"
import { Observable, useObserve } from "../state/observable"

/** What the New Agent sheet is putting together, shared by its prompt and repository screens. */
export class NewAgentDraft extends Observable {
  prompt = ""
  repo: string | null = null
  visibility: AgentVisibility = "private"
  repositories: string[] | null = null
  repositoriesError: string | null = null
  isStarting = false
  error: string | null = null
  /** Set once the person picks a visibility, so the saved default arriving later leaves it alone. */
  private visibilityChosen = false

  constructor(private readonly store: AgentStore) {
    super()
    this.repo = store.lastRepository
    this.load()
  }

  private async load() {
    const preferred = this.store.service.defaultVisibility()
    try {
      const list = await this.store.service.repositories()
      this.repositories = list
      if (this.repo && !list.includes(this.repo)) this.repo = null
    } catch (error) {
      this.repositories = []
      this.repositoriesError = APIError.describe(error)
    }
    const visibility = await preferred
    if (!this.visibilityChosen) this.visibility = visibility
    this.changed()
  }

  set(patch: Partial<Pick<NewAgentDraft, "prompt" | "repo" | "visibility">>) {
    Object.assign(this, patch)
    if (patch.visibility !== undefined) this.visibilityChosen = true
    this.changed()
  }

  get canStart(): boolean {
    return !this.isStarting && this.prompt.trim().length > 0
  }

  /** Start the agent; resolves with its id, or null when it failed. */
  async start(): Promise<string | null> {
    const text = this.prompt.trim()
    if (!text) return null
    this.isStarting = true
    this.error = null
    this.changed()
    try {
      return await this.store.startAgent(text, this.repo, this.visibility)
    } catch (error) {
      this.error = APIError.describe(error)
      return null
    } finally {
      this.isStarting = false
      this.changed()
    }
  }
}

export const NewAgentDraftContext = createContext<NewAgentDraft | null>(null)

export function useNewAgentDraft(): NewAgentDraft {
  const draft = useContext(NewAgentDraftContext)
  if (!draft) throw new Error("useNewAgentDraft outside the New Agent sheet")
  return useObserve(draft)
}
