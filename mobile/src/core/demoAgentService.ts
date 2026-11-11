import * as Crypto from "expo-crypto"

import type { AgentService, AgentVisibility } from "./agentService"
import { APIError, type StreamEnd } from "./apiClient"
import {
  fileName,
  isEmptyDiff,
  makeThread,
  type AgentThread,
  type BranchDiff,
  type DiffFile,
  type DiffStats,
  type Identity,
  type PullRequestHealth,
  type PullRequestState,
  type PullRequestSummary,
} from "./models"
import { toolActivity, type TranscriptItem } from "./transcript"
import type {
  TranscriptLog,
  TranscriptStreamEvent,
  TurnPage,
} from "./transcriptLog"

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

const DEMO_REPLY_START =
  "This is a demo workspace, so no agent picked this up. Sign in to a Robin backend to start real agents."
const DEMO_REPLY_FOLLOW_UP =
  "This is a demo workspace, so no agent picked that up. Sign in to a Robin backend to work with real agents."

/** Sample agents held in memory, for trying the app without a backend. */
export class DemoAgentService implements AgentService {
  private stored = sampleThreads()
  private pinned = new Set<string>()
  private extraMessages = new Map<string, TranscriptItem[]>()
  private startedIDs = new Set<string>()

  readonly threadPageBase = "https://robin.example.com/agents"

  async me(): Promise<Identity> {
    return { login: "jane", email: "jane@example.com" }
  }

  async threads(): Promise<AgentThread[]> {
    await sleep(250)
    return this.stored
      .filter((thread) => !thread.resolved)
      .map((thread) => ({ ...thread }))
  }

  async pinnedThreadIDs(): Promise<Set<string>> {
    return new Set(this.pinned)
  }

  async thread(id: string): Promise<AgentThread> {
    const thread = this.stored.find((candidate) => candidate.id === id)
    if (!thread) throw new APIError(404, "thread not found")
    if (thread.status !== "running") thread.viewed = true
    return { ...thread }
  }

  async transcriptLog(): Promise<TranscriptLog | null> {
    return null
  }

  async transcriptEvents(
    _id: string,
    _after: number,
    _onEvent: (event: TranscriptStreamEvent) => void
  ): Promise<StreamEnd> {
    return "closed"
  }

  async olderTurns(): Promise<TurnPage> {
    return { turns: [], messages: [], tool_calls: [], older_cursor: null }
  }

  async legacyTranscript(id: string): Promise<TranscriptItem[]> {
    await sleep(200)
    const thread = await this.thread(id)
    // Agents started in the demo have only their own messages.
    const sample = this.startedIDs.has(id) ? [] : sampleTranscript(thread)
    return [...sample, ...(this.extraMessages.get(id) ?? [])]
  }

  async branchDiff(id: string): Promise<BranchDiff> {
    await sleep(300)
    const thread = await this.thread(id)
    return {
      prNumber: thread.pr?.number,
      baseRef: "main",
      headRef: thread.branch,
      truncated: false,
      files: isEmptyDiff(thread.diffStats)
        ? []
        : sampleFiles(thread, thread.diffStats),
    }
  }

  async pullRequestHealth(id: string): Promise<PullRequestHealth | null> {
    await sleep(700)
    return (await this.thread(id)).pr
      ? { checksAvailable: true, failingChecks: [], pendingCheckCount: 0 }
      : null
  }

  async repositories(): Promise<string[]> {
    return ["acme/api-gateway", "acme/billing-service", "acme/web"]
  }

  async defaultVisibility(): Promise<AgentVisibility> {
    return "private"
  }

  async startAgent(prompt: string, repo: string | null): Promise<string> {
    await sleep(400)
    const id = `demo-${Crypto.randomUUID().slice(0, 8).toLowerCase()}`
    const title = prompt.split("\n")[0].slice(0, 80) || "New agent"
    this.startedIDs.add(id)
    this.stored.unshift(
      makeThread({
        id,
        title,
        repoFullName: repo ?? "",
        status: "finished",
        viewed: true,
        createdAt: new Date(),
        updatedAt: new Date(),
      })
    )
    this.extraMessages.set(id, [
      { id: Crypto.randomUUID(), kind: "user", text: prompt },
      { id: Crypto.randomUUID(), kind: "assistant", text: DEMO_REPLY_START },
    ])
    return id
  }

  async signOut(): Promise<void> {}

  async rename(id: string, title: string): Promise<void> {
    const thread = this.stored.find((candidate) => candidate.id === id)
    if (thread) thread.title = title
  }

  async setPinned(id: string, pinned: boolean): Promise<void> {
    if (pinned) this.pinned.add(id)
    else this.pinned.delete(id)
  }

  async setResolved(id: string, resolved: boolean): Promise<void> {
    const thread = this.stored.find((candidate) => candidate.id === id)
    if (thread) thread.resolved = resolved
  }

  async sendMessage(id: string, text: string): Promise<void> {
    await sleep(300)
    const items = this.extraMessages.get(id) ?? []
    items.push({ id: Crypto.randomUUID(), kind: "user", text })
    items.push({
      id: Crypto.randomUUID(),
      kind: "assistant",
      text: DEMO_REPLY_FOLLOW_UP,
    })
    this.extraMessages.set(id, items)
    const thread = this.stored.find((candidate) => candidate.id === id)
    if (thread) thread.updatedAt = new Date()
  }
}

function sampleThreads(): AgentThread[] {
  const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000)
  const repo = "acme/billing-service"
  const pr = (
    number: number,
    title: string,
    state: PullRequestState,
    branch: string
  ): PullRequestSummary => ({
    number,
    title,
    state,
    headRef: branch,
    baseRef: "main",
    url: `https://github.com/acme/billing-service/pull/${number}`,
  })
  const stats = (
    files: number,
    additions: number,
    deletions: number
  ): DiffStats => ({ files, additions, deletions })
  return [
    makeThread({
      id: "demo-1",
      title: "Add retry budget to webhook delivery",
      repoFullName: "acme/api-gateway",
      branch: "open-swe/webhook-retry-budget",
      status: "running",
      viewed: false,
      createdAt: ago(12),
      updatedAt: ago(1),
      diffStats: stats(3, 48, 9),
    }),
    makeThread({
      id: "demo-2",
      title: "Fix INC-1033: billing/invoices.py",
      repoFullName: repo,
      branch: "open-swe/fix-inc-1033-invoices",
      status: "finished",
      viewed: false,
      createdAt: ago(90),
      updatedAt: ago(35),
      source: "slack",
      pr: pr(
        14,
        "Fix INC-1033: billing/invoices.py",
        "merged",
        "open-swe/fix-inc-1033-invoices"
      ),
      diffStats: stats(2, 12, 1),
    }),
    makeThread({
      id: "demo-3",
      title: "RCA INC-1033",
      repoFullName: repo,
      status: "finished",
      viewed: false,
      createdAt: ago(120),
      updatedAt: ago(80),
      source: "slack",
    }),
    makeThread({
      id: "demo-4",
      title: "Fix INC-1031: billing/payments.py",
      repoFullName: repo,
      branch: "open-swe/fix-inc-1031-payments",
      status: "finished",
      viewed: false,
      createdAt: ago(300),
      updatedAt: ago(200),
      source: "slack",
      pr: pr(
        11,
        "Fix INC-1031: billing/payments.py",
        "merged",
        "open-swe/fix-inc-1031-payments"
      ),
      diffStats: stats(2, 6, 1),
    }),
    makeThread({
      id: "demo-5",
      title: "Review INC-1026",
      repoFullName: repo,
      status: "error",
      viewed: true,
      attentionReason: "The run failed",
      createdAt: ago(600),
      updatedAt: ago(500),
      pr: pr(9, "Review INC-1026", "closed", "open-swe/review-inc-1026"),
    }),
    makeThread({
      id: "demo-6",
      title: "Bump ruff and fix new lint findings",
      repoFullName: "acme/api-gateway",
      branch: "open-swe/bump-ruff",
      status: "finished",
      viewed: true,
      createdAt: ago(800),
      updatedAt: ago(700),
      source: "github",
      pr: pr(
        212,
        "Bump ruff and fix new lint findings",
        "open",
        "open-swe/bump-ruff"
      ),
      diffStats: stats(2, 31, 27),
    }),
    makeThread({
      id: "demo-7",
      title: "Fix INC-1028: billing/devices.py",
      repoFullName: repo,
      branch: "open-swe/fix-inc-1028-devices",
      status: "finished",
      viewed: true,
      createdAt: ago(1600),
      updatedAt: ago(1500),
      source: "slack",
      sandboxBridgeClient: "desktop",
      pr: pr(
        8,
        "Fix INC-1028: billing/devices.py",
        "closed",
        "open-swe/fix-inc-1028-devices"
      ),
      diffStats: stats(1, 7, 1),
    }),
    makeThread({
      id: "demo-8",
      title: "Summarise this week's recruiter emails",
      repoFullName: "",
      status: "finished",
      viewed: true,
      createdAt: ago(3000),
      updatedAt: ago(2900),
    }),
    makeThread({
      id: "demo-9",
      title: "RCA INC-1029",
      repoFullName: repo,
      status: "finished",
      viewed: true,
      createdAt: ago(4000),
      updatedAt: ago(3900),
      source: "slack",
    }),
  ]
}

/** Two files whose counts add up to the thread's own, so the row and the diff agree. */
function sampleFiles(
  thread: AgentThread,
  stats: DiffStats | undefined
): DiffFile[] {
  const testAdditions = 5
  const sourceAdditions = Math.max(1, (stats?.additions ?? 6) - testAdditions)
  const sourceDeletions = Math.max(0, stats?.deletions ?? 1)
  const module = thread.title.split(": ").pop() ?? "billing/payments.py"
  return [
    {
      path: module,
      status: "modified",
      additions: sourceAdditions,
      deletions: sourceDeletions,
      unrenderable: false,
      patch: [
        "@@ -17,7 +17,7 @@ class CardExpiration:",
        " def is_card_expired(expiration: CardExpiration, processed_on: date) -> bool:",
        "     if expiration.year != processed_on.year:",
        "         return expiration.year < processed_on.year",
        "-    return expiration.month <= processed_on.month",
        "+    return expiration.month < processed_on.month",
        "",
        "",
        " def payment_status(expiration: CardExpiration, processed_on: date) -> str:",
      ].join("\n"),
    },
    {
      path: `tests/test_${fileName(module)}`,
      status: "modified",
      additions: testAdditions,
      deletions: 0,
      unrenderable: false,
      patch: [
        "@@ -40,3 +40,8 @@ def test_expired_last_year():",
        "     assert is_card_expired(CardExpiration(2023, 12), date(2024, 1, 3))",
        "+",
        "+",
        "+def test_same_month_is_still_valid():",
        "+    expiration = CardExpiration(2024, 5)",
        "+    assert not is_card_expired(expiration, date(2024, 5, 31))",
      ].join("\n"),
    },
  ]
}

function sampleTranscript(thread: AgentThread): TranscriptItem[] {
  const done = thread.status !== "running"
  const items: TranscriptItem[] = [
    {
      id: "u1",
      kind: "user",
      text: `${thread.title}\n\nInvestigate the incident, make the smallest fix that resolves it, add a regression test, and open a pull request. Keep the final message brief.`,
    },
    {
      id: "a1",
      kind: "activity",
      group: {
        tools: [
          toolActivity("read_file", "billing/payments.py"),
          toolActivity("read_file", "tests/test_payments.py"),
          toolActivity("grep", "is_card_expired"),
          toolActivity("grep", "processed_on.month"),
          toolActivity("glob", "tests/**/*.py"),
          toolActivity("read_file", "billing/models.py"),
          toolActivity("task_context"),
        ],
      },
    },
    {
      id: "t1",
      kind: "todos",
      todos: [
        {
          content: "Add same-month card expiration regression coverage",
          status: "completed",
        },
        {
          content:
            "Change same-year expiration comparison to strict month check",
          status: "completed",
        },
        {
          content: "Run full unittest suite and inspect results",
          status: done ? "completed" : "in_progress",
        },
        {
          content: "Commit, push, open PR, and submit fix report",
          status: done ? "completed" : "pending",
        },
      ],
    },
  ]
  if (!isEmptyDiff(thread.diffStats)) {
    items.push({
      id: "a2",
      kind: "activity",
      group: {
        tools: [
          toolActivity("edit_file", "billing/payments.py"),
          toolActivity("edit_file", "tests/test_payments.py"),
          toolActivity("grep", "def test_"),
          toolActivity("execute", "python -m unittest"),
          toolActivity("execute", "git push"),
        ],
      },
    })
  }
  if (done) {
    const prLine = thread.pr
      ? `\n- PR: [${thread.pr.title}](${thread.pr.url})`
      : ""
    items.push({
      id: "m1",
      kind: "assistant",
      text: `Implemented and pushed the fix.\n\n- Same-month expirations remain valid.\n- Added regression coverage.\n- All 13 tests pass.${prLine}`,
    })
  }
  return items
}
