import AsyncStorage from "@react-native-async-storage/async-storage"

import type { AgentService, AgentVisibility } from "../core/agentService"
import { APIError } from "../core/apiClient"
import {
  makeThread,
  sourceTitle,
  threadCategory,
  workspaceKey,
  workspaceName,
  type AgentThread,
  type Identity,
  type InboxCategory,
} from "../core/models"
import {
  DEFAULT_PREFERENCES,
  loadPreferences,
  savePreferences,
  type ListPreferences,
} from "./listPreferences"
import { Observable } from "./observable"

export interface Workspace {
  key: string
  name: string
  count: number
}

const LAST_REPOSITORY_KEY = "lastRepository"

/**
 * The signed-in person's agents, as the inbox and lists show them.
 *
 * Changes are optimistic: the list updates at once and rolls back if the
 * server refuses.
 */
export class AgentStore extends Observable {
  identity: Identity | undefined
  threads: AgentThread[] = []
  pinnedIDs = new Set<string>()
  hasLoaded = false
  isLoading = false
  errorMessage: string | null = null
  preferences: ListPreferences = DEFAULT_PREFERENCES
  /** The repository a new agent starts in, remembered from the last one. */
  lastRepository: string | null = null
  onUnauthorized: (() => void) | null = null

  constructor(readonly service: AgentService) {
    super()
    loadPreferences().then((preferences) => {
      this.preferences = preferences
      this.changed()
    })
    AsyncStorage.getItem(LAST_REPOSITORY_KEY).then((repo) => {
      this.lastRepository = repo
    })
  }

  setPreferences(preferences: ListPreferences) {
    this.preferences = preferences
    savePreferences(preferences)
    this.changed()
  }

  async refresh() {
    if (this.isLoading) return
    this.isLoading = true
    try {
      const [threads, identity, pinned] = await Promise.all([
        this.service.threads(),
        this.service.me().catch(() => this.identity),
        this.service.pinnedThreadIDs().catch(() => this.pinnedIDs),
      ])
      this.threads = threads
      this.identity = identity
      this.pinnedIDs = pinned
      this.hasLoaded = true
      this.errorMessage = null
    } catch (error) {
      this.report(error)
    } finally {
      this.isLoading = false
      this.changed()
    }
  }

  // Derived views

  threadsIn(category: InboxCategory): AgentThread[] {
    return this.threads.filter((thread) => threadCategory(thread) === category)
  }

  count(category: InboxCategory): number {
    return this.threadsIn(category).length
  }

  /** Repositories with threads, most recently active first; threads with no repository last. */
  get workspaces(): Workspace[] {
    const groups = new Map<string, AgentThread[]>()
    for (const thread of this.threads) {
      const key = workspaceKey(thread)
      groups.set(key, [...(groups.get(key) ?? []), thread])
    }
    const latest = (threads: AgentThread[]) =>
      Math.max(...threads.map((thread) => thread.updatedAt.getTime()))
    const named = [...groups.entries()]
      .filter(([key]) => key !== "")
      .sort((a, b) => latest(b[1]) - latest(a[1]))
      .map(([key, threads]) => ({
        key,
        name: workspaceName(threads[0]),
        count: threads.length,
      }))
    const none = groups.get("")
    return none
      ? [...named, { key: "", name: "No Repo", count: none.length }]
      : named
  }

  get allSources(): string[] {
    return [
      ...new Set(this.threads.map((thread) => thread.source ?? "dashboard")),
    ].sort((a, b) => sourceTitle(a).localeCompare(sourceTitle(b)))
  }

  thread(id: string): AgentThread | undefined {
    return this.threads.find((thread) => thread.id === id)
  }

  isPinned(id: string): boolean {
    return this.pinnedIDs.has(id)
  }

  // Changes

  upsert(thread: AgentThread) {
    const index = this.threads.findIndex(
      (candidate) => candidate.id === thread.id
    )
    this.threads =
      index === -1
        ? [thread, ...this.threads]
        : this.threads.map((candidate, i) => (i === index ? thread : candidate))
    this.changed()
  }

  markViewed(id: string) {
    const thread = this.thread(id)
    if (!thread || thread.status === "running" || thread.viewed) return
    this.upsert({ ...thread, viewed: true })
  }

  async rename(id: string, rawTitle: string) {
    const title = rawTitle.trim().slice(0, 80)
    const thread = this.thread(id)
    if (!title || !thread) return
    const previous = thread.title
    this.upsert({ ...thread, title })
    try {
      await this.service.rename(id, title)
    } catch (error) {
      const current = this.thread(id)
      if (current) this.upsert({ ...current, title: previous })
      this.report(error)
    }
  }

  async togglePin(id: string) {
    const pin = !this.pinnedIDs.has(id)
    const flip = (on: boolean) => {
      const next = new Set(this.pinnedIDs)
      if (on) next.add(id)
      else next.delete(id)
      this.pinnedIDs = next
      this.changed()
    }
    flip(pin)
    try {
      await this.service.setPinned(id, pin)
    } catch (error) {
      flip(!pin)
      this.report(error)
    }
  }

  async archive(id: string) {
    const index = this.threads.findIndex((thread) => thread.id === id)
    if (index === -1) return
    const removed = this.threads[index]
    this.threads = this.threads.filter((thread) => thread.id !== id)
    this.changed()
    try {
      await this.service.setResolved(id, true)
    } catch (error) {
      const next = [...this.threads]
      next.splice(Math.min(index, next.length), 0, removed)
      this.threads = next
      this.report(error)
    }
  }

  /**
   * Start an agent; resolves with its id. Its row is seeded so the inbox and the
   * thread view show it before the next list refresh.
   */
  async startAgent(
    prompt: string,
    repo: string | null,
    visibility: AgentVisibility
  ): Promise<string> {
    const id = await this.service.startAgent(prompt, repo, visibility)
    this.lastRepository = repo
    if (repo) AsyncStorage.setItem(LAST_REPOSITORY_KEY, repo).catch(() => {})
    else AsyncStorage.removeItem(LAST_REPOSITORY_KEY).catch(() => {})
    if (!this.thread(id)) {
      const title = prompt.split("\n")[0].slice(0, 80) || "New agent"
      this.upsert(
        makeThread({
          id,
          title,
          repoFullName: repo ?? "",
          status: "running",
          viewed: true,
          createdAt: new Date(),
          updatedAt: new Date(),
          ownerLogin: this.identity?.login,
        })
      )
    }
    return id
  }

  dismissError() {
    this.errorMessage = null
    this.changed()
  }

  report(error: unknown) {
    if (error instanceof APIError && error.isUnauthorized) {
      this.onUnauthorized?.()
      return
    }
    this.errorMessage = APIError.describe(error)
    this.changed()
  }
}
