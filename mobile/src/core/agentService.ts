import * as Crypto from "expo-crypto"

import { APIClient, APIError, type StreamEnd } from "./apiClient"
import {
  decodeBranchDiff,
  decodeIdentity,
  decodePullRequestHealth,
  decodeThread,
  healthDescribes,
  isObject,
  stringAt,
  type AgentThread,
  type BranchDiff,
  type Identity,
  type PullRequestHealth,
  type PullRequestSummary,
} from "./models"
import { transcriptFromState, type TranscriptItem } from "./transcript"
import {
  TranscriptLog,
  type LogEvent,
  type Snapshot,
  type TranscriptStreamEvent,
  type TurnPage,
} from "./transcriptLog"

/** Who can see a new agent: just its owner, or everyone in the workspace. */
export type AgentVisibility = "private" | "workspace"

export const VISIBILITY_TITLES: Record<AgentVisibility, string> = {
  private: "Private",
  workspace: "Shared",
}

/** Everything the app reads and changes about the signed-in person's agents. */
export interface AgentService {
  readonly threadPageBase: string | null

  me(): Promise<Identity>
  threads(): Promise<AgentThread[]>
  pinnedThreadIDs(): Promise<Set<string>>
  thread(id: string): Promise<AgentThread>
  /** The thread's transcript event log, or null for a thread that predates it. */
  transcriptLog(id: string): Promise<TranscriptLog | null>
  /** Events after `version`, until the stream ends or `signal` aborts. */
  transcriptEvents(
    id: string,
    after: number,
    onEvent: (event: TranscriptStreamEvent) => void,
    signal: AbortSignal
  ): Promise<StreamEnd>
  /** The page of turns before `cursor`, for scrolling back past the snapshot's window. */
  olderTurns(id: string, before: string): Promise<TurnPage>
  /** The transcript of a thread without an event log, read from its graph state. */
  legacyTranscript(id: string): Promise<TranscriptItem[]>
  branchDiff(id: string): Promise<BranchDiff>
  /** Live GitHub health of `pr`, one of the pull requests the thread tracks. */
  pullRequestHealth(
    id: string,
    pr: PullRequestSummary
  ): Promise<PullRequestHealth | null>

  /** Repositories the person can start agents in, as `owner/name`. */
  repositories(): Promise<string[]>
  defaultVisibility(): Promise<AgentVisibility>
  /** Start a new agent; resolves with its thread id. */
  startAgent(
    prompt: string,
    repo: string | null,
    visibility: AgentVisibility
  ): Promise<string>

  /** End the session on the server, revoking what it holds for it. */
  signOut(): Promise<void>

  rename(id: string, title: string): Promise<void>
  setPinned(id: string, pinned: boolean): Promise<void>
  setResolved(id: string, resolved: boolean): Promise<void>
  /** Send a follow-up; while the agent works, it queues behind the live run. */
  sendMessage(id: string, text: string, enqueue: boolean): Promise<void>
}

/** The `run.start` command the dashboard posts to `/threads/{id}/commands`. */
export function runCommand(
  threadID: string,
  text: string,
  configurable: Record<string, string | boolean>,
  enqueue: boolean
) {
  return {
    id: 1,
    method: "run.start",
    params: {
      ...(enqueue ? { multitask_strategy: "enqueue" } : {}),
      input: {
        messages: [
          {
            // Client-minted, so the graph's message and the transcript row share it.
            id: Crypto.randomUUID().toLowerCase(),
            type: "human",
            content: [{ type: "text", text: text.trim() }],
          },
        ],
      },
      config: { configurable: { ...configurable, thread_id: threadID } },
      assistant_id: "agent",
      // The app reads the transcript log, so the server need keep no replay of the run.
      stream_resumable: false,
    },
  }
}

/** The Robin dashboard API, called the way the web dashboard calls it. */
export class LiveAgentService implements AgentService {
  constructor(readonly client: APIClient) {}

  get threadPageBase(): string {
    return `${this.client.backend}/agents`
  }

  private threadPath(id: string, suffix = ""): string {
    return `/threads/${encodeURIComponent(id)}${suffix}`
  }

  async me(): Promise<Identity> {
    return decodeIdentity(await this.client.get("/me"))
  }

  /** Every unresolved thread, newest activity first; resolving is how the app archives. */
  async threads(): Promise<AgentThread[]> {
    const all: AgentThread[] = []
    while (true) {
      const page = await this.client.get("/threads/page", {
        limit: "100",
        offset: String(all.length),
        resolved: "false",
        sort_by: "updated_at",
      })
      const items =
        isObject(page) && Array.isArray(page.items)
          ? page.items.map(decodeThread)
          : []
      all.push(...items)
      if (!isObject(page) || page.hasMore !== true || items.length === 0)
        return all
    }
  }

  async pinnedThreadIDs(): Promise<Set<string>> {
    const pinned = await this.client.get("/threads/pinned")
    return new Set(
      Array.isArray(pinned) ? pinned.map((item) => decodeThread(item).id) : []
    )
  }

  async thread(id: string): Promise<AgentThread> {
    return decodeThread(await this.client.get(this.threadPath(id)))
  }

  async transcriptLog(id: string): Promise<TranscriptLog | null> {
    try {
      return new TranscriptLog(
        (await this.client.get(this.threadPath(id, "/transcript"))) as Snapshot
      )
    } catch (error) {
      if (error instanceof APIError && error.status === 404) return null
      throw error
    }
  }

  transcriptEvents(
    id: string,
    after: number,
    onEvent: (event: TranscriptStreamEvent) => void,
    signal: AbortSignal
  ) {
    return this.client.events(
      this.threadPath(id, "/transcript/events"),
      { after: String(after) },
      (frame) => {
        switch (frame.name) {
          case "transcript":
            onEvent({
              kind: "event",
              event: JSON.parse(frame.data) as LogEvent,
            })
            break
          case "snapshot":
            onEvent({
              kind: "snapshot",
              snapshot: JSON.parse(frame.data) as Snapshot,
            })
            break
          case "synchronized":
            onEvent({ kind: "synchronized" })
            break
          case "deleted":
          case "revoked":
            onEvent({ kind: "ended" })
            break
        }
      },
      signal
    )
  }

  async olderTurns(id: string, before: string): Promise<TurnPage> {
    return (await this.client.get(this.threadPath(id, "/transcript/turns"), {
      before,
    })) as TurnPage
  }

  async legacyTranscript(id: string): Promise<TranscriptItem[]> {
    return transcriptFromState(
      await this.client.get(this.threadPath(id, "/state"))
    )
  }

  async branchDiff(id: string): Promise<BranchDiff> {
    return decodeBranchDiff(
      await this.client.get(this.threadPath(id, "/branch-diff"))
    )
  }

  async pullRequestHealth(
    id: string,
    pr: PullRequestSummary
  ): Promise<PullRequestHealth | null> {
    const response = await this.client.get(
      this.threadPath(id, "/pull-request-status")
    )
    const all =
      isObject(response) && Array.isArray(response.pullRequests)
        ? response.pullRequests.map(decodePullRequestHealth)
        : []
    return all.find((health) => healthDescribes(health, pr)) ?? null
  }

  async repositories(): Promise<string[]> {
    const payload = await this.client.get("/repos")
    const repositories =
      isObject(payload) && Array.isArray(payload.repositories)
        ? payload.repositories
        : []
    return repositories
      .filter((repo) => isObject(repo) && repo.archived !== true)
      .map((repo) => stringAt(repo, "full_name"))
      .filter((name): name is string => !!name)
      .sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }))
  }

  async defaultVisibility(): Promise<AgentVisibility> {
    try {
      const preferences = await this.client.get("/me/preferences")
      return stringAt(preferences, "default_visibility") === "public"
        ? "workspace"
        : "private"
    } catch {
      return "private"
    }
  }

  /**
   * The dashboard's own creation path: the first `run.start` on a client-minted
   * thread id creates the thread.
   */
  async startAgent(
    prompt: string,
    repo: string | null,
    visibility: AgentVisibility
  ): Promise<string> {
    const threadID = Crypto.randomUUID().toLowerCase()
    const configurable: Record<string, string | boolean> = {
      model_selection: "auto",
      thread_type: visibility === "private" ? "private" : "workspace",
      ...(repo ? { repo } : { repo_explicitly_none: true }),
    }
    await this.run(threadID, runCommand(threadID, prompt, configurable, false))
    return threadID
  }

  /** Best effort: the app forgets the session either way. */
  async signOut(): Promise<void> {
    try {
      await this.client.send("POST", "/auth/logout")
    } catch {
      // The session is gone from the app regardless; nothing else to do.
    }
  }

  async rename(id: string, title: string): Promise<void> {
    await this.client.send("PATCH", this.threadPath(id), { json: { title } })
  }

  async setPinned(id: string, pinned: boolean): Promise<void> {
    await this.client.send(
      pinned ? "POST" : "DELETE",
      this.threadPath(id, "/pin")
    )
  }

  async setResolved(id: string, resolved: boolean): Promise<void> {
    await this.client.send("POST", this.threadPath(id, "/resolve"), {
      json: { resolved },
    })
  }

  async sendMessage(id: string, text: string, enqueue: boolean): Promise<void> {
    await this.run(id, runCommand(id, text, {}, enqueue))
  }

  private async run(threadID: string, command: ReturnType<typeof runCommand>) {
    const text = await this.client.send(
      "POST",
      this.threadPath(threadID, "/commands"),
      { json: command }
    )
    let reply: unknown = null
    try {
      reply = JSON.parse(text)
    } catch {
      return
    }
    if (stringAt(reply, "type") === "error") {
      throw new APIError(
        400,
        stringAt(reply, "message") ??
          stringAt(reply, "error") ??
          "The agent did not accept the message."
      )
    }
  }
}
