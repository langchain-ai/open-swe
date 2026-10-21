import type { TaskEventMetadata } from "./structuredInputMessages"

export type Author = "user" | "agent" | "system" | "tool"

export type ChunkKind =
  | "text"
  | "reasoning"
  | "code"
  | "error"
  | "list"
  | "tool-execution"
  | "todo"
  | "image"

export type TodoStatus = "pending" | "in_progress" | "completed"

export type AgentStatus =
  | "idle"
  | "running"
  | "finished"
  | "interrupted"
  | "error"

export type AgentSource =
  | "dashboard"
  | "github"
  | "slack"
  | "linear"
  | "schedule"

export type AgentThreadCategory =
  | "interactive"
  | "issue"
  | "pull_request"
  | "automation"
  | "review"
  | "system"

export type AgentTriggerKind =
  | "user"
  | "schedule"
  | "schedule_test"
  | "wakeup"
  | "reviewer"
  | "ci_autofix"
  | "slack_bot"

export interface TodoItem {
  content: string
  status: TodoStatus
}

export type AcpToolKind =
  | "read"
  | "edit"
  | "delete"
  | "move"
  | "search"
  | "execute"
  | "think"
  | "fetch"
  | "slack"
  | "linear"
  | "sql"
  | "managed-tools"
  /** deepagents `task` tool — spawns a subagent; rendered as a subagent card. */
  | "task"
  /** Code mode's `eval` tool — runs code that may call other tools. */
  | "code"
  | "other"

export type AcpToolStatus = "pending" | "in_progress" | "completed" | "error"

export interface AcpToolLocation {
  path: string
  line?: number
}

export interface DiffData {
  originalContent: string | null
  newContent: string
  filePath: string
  isNewFile: boolean
  isBinary: boolean
  isTruncated: boolean
  totalLines: number
}

export type OutputIframeDisplay =
  | {
      type: "output_iframe"
      previewUrl: string
      downloadUrl: string
      title: string
      filename: string
    }
  | {
      type: "output_iframe"
      html: string
      title: string
      filename: string
    }

export interface ToolExecutionChunk {
  kind: "tool-execution"
  toolCallId: string
  /** Stable arrival time for the tool call, shown on hover. */
  timestamp?: string
  title: string
  toolKind: AcpToolKind
  input?: Record<string, unknown>
  status: AcpToolStatus
  output?: string
  /**
   * Fetches the call's full output, for sources that only hold a preview (the
   * transcript log keeps large outputs out of its snapshot). Present only when
   * there is more output than {@link output} already shows.
   */
  loadOutput?: () => Promise<string>
  display?: OutputIframeDisplay
  elapsedMs?: number
  approvalRequestId?: string
  diffData?: DiffData
  diffs?: Array<DiffData>
  locations?: Array<AcpToolLocation>
  /**
   * Namespace of the subagent this `task` call spawned, from the SDK's
   * `stream.subagents` discovery map (correlated by tool-call id). Present only
   * for `toolKind: "task"` chunks whose subagent the SDK has discovered; lets
   * the UI open a scoped `useToolCalls(stream, { namespace })` subscription to
   * show the subagent's nested activity.
   */
  subagentNamespace?: Array<string>
  /**
   * The tool calls a `code` chunk's code made, in order. When present the row
   * shows them instead of the code's raw output.
   */
  calls?: Array<ToolExecutionChunk>
}

export interface TextChunk {
  kind: "text"
  text: string
}

export interface ReasoningChunk {
  kind: "reasoning"
  text: string
}

export interface CodeChunk {
  kind: "code"
  text: string
  language?: string
}

export interface ErrorChunk {
  kind: "error"
  text: string
}

export interface ListChunk {
  kind: "list"
  lines: Array<string>
}

export interface TodoChunk {
  kind: "todo"
  todos: Array<TodoItem>
}

/** An inline image or video produced by a composer upload. */
export interface MediaChunk {
  kind: "image" | "video"
  base64: string
  mimeType: string
  fileName?: string
}

/** A transcript attachment fetched with the session, or a public media URL. */
export interface RemoteMediaChunk {
  kind: "image" | "video"
  url: string
  credentials: "session" | "none"
  mimeType?: string
  fileName?: string
}

export type AnyMediaChunk = MediaChunk | RemoteMediaChunk

export type Chunk =
  | TextChunk
  | ReasoningChunk
  | CodeChunk
  | ErrorChunk
  | ListChunk
  | ToolExecutionChunk
  | TodoChunk
  | AnyMediaChunk

export interface Message {
  id: string
  author: Author
  timestamp: string
  deliveryStatus?: "sending" | "failed"
  deliveryError?: string
  optimistic?: boolean
  structuredSenderId?: string
  structuredSenderKind?: "person" | "system"
  structuredSenderName?: string
  structuredSenderNote?: string
  structuredSenderIsBot?: boolean
  structuredSurface?: string
  taskEvent?: TaskEventMetadata
  /** Id of the user message that opened this agent run and keys its diff artifact. */
  turnKey?: string
  /** Invocations an agent turn's model calls ran under; key their costs in `AgentThread.runCosts`. */
  invocationIds?: Array<string>
  /** Timestamp of the first message in an agent turn; used to derive work duration. */
  startedAt?: string
  timestampIsFallback?: boolean
  chunks: Array<Chunk>
  hidden?: boolean
}

export interface LocalRepo {
  id: string
  path: string
  name: string
  createdAt: number
  lastOpenedAt: number
  gitBranch?: string
}

export type GitHubTriggerEvent =
  | "issues.opened"
  | "pull_request.opened"
  | "pull_request.closed"
  | "pull_request.merged"

export type SlackTriggerEvent = "message.posted"
export type SlackTriggerSenders = "anyone" | "people" | "bots"
export type LinearTriggerEvent = "issue.created" | "issue.labeled"

/** One way an automation fires; `kind` names the provider, the rest are its filters. */
export type AutomationTriggerConfig =
  | { kind: "schedule"; cron: string }
  | { kind: "github"; repo: string; events: Array<GitHubTriggerEvent> }
  | {
      kind: "slack"
      channel: string
      events: Array<SlackTriggerEvent>
      senders?: SlackTriggerSenders
      /** Case-insensitive regular expression the message text must match. */
      match?: string | null
      max_runs_per_hour?: number | null
    }
  | {
      kind: "linear"
      /** Team key, e.g. ENG. */
      team: string
      events: Array<LinearTriggerEvent>
      labels?: Array<string>
      project?: string | null
      max_runs_per_hour?: number | null
    }

export type AutomationTriggerProvider = AutomationTriggerConfig["kind"]

/** The event providers an automation can run on, each with its events' labels. */
export const AUTOMATION_EVENT_PROVIDERS = {
  github: {
    label: "GitHub",
    events: {
      "issues.opened": "Issue opened",
      "pull_request.opened": "PR opened",
      "pull_request.closed": "PR closed",
      "pull_request.merged": "PR merged",
    } satisfies Record<GitHubTriggerEvent, string>,
  },
  slack: {
    label: "Slack",
    events: {
      "message.posted": "Message posted",
    } satisfies Record<SlackTriggerEvent, string>,
  },
  linear: {
    label: "Linear",
    events: {
      "issue.created": "Issue created",
      "issue.labeled": "Label added",
    } satisfies Record<LinearTriggerEvent, string>,
  },
} as const

export interface AgentSchedule {
  id: string
  name: string
  prompt: string
  schedule: string | null
  triggers: Array<AutomationTriggerConfig & { id: string }>
  scope: "workspace"
  /** Slug of the workspace every run launches in. */
  workspace: string
  adminThread: boolean
  model: string
  effort?: string | null
  enabled: boolean
  cronId?: string | null
  lastThreadId?: string | null
  lastRunId?: string | null
  lastTriggeredAt?: string | null
  lastError?: string | null
  lastErrorAt?: string | null
  createdBy?: string | null
  updatedBy?: string | null
  createdAt?: string | null
  updatedAt?: string | null
}

export interface QueuedThreadMessage {
  id: string
  content: string
  attachments?: Array<AnyMediaChunk>
  createdAt: number
  /** The server has not acknowledged it yet, so it cannot be sent now or cancelled. */
  pending?: boolean
  /** False when someone else sent it: only its sender may send it now or cancel it. */
  mine?: boolean
  /** Held in the thread's queue for the agent's next model call, with no run of its own. */
  waitsForAgent?: boolean
  sender?: string | null
}

/** A message in the thread's queue, as `/threads/{id}/queued-messages` returns it. */
export interface AgentBackgroundCommand {
  task_id: string
  command: string | null
  status: string
  started_at: number | null
}

export interface AgentThreadMonitor {
  monitor_id: string
  description: string
  command: string
  filter: string
  status: string
  started_at: number | null
  timeout: number | null
  events: number
  raw_lines: number
}

/** What a thread's monitors used of the allowance its last human message opened. */
export interface AgentMonitorBudget {
  events: number
  max_events: number
  starts: number
  max_starts: number
  window_ends_at: number
}

/** The sandbox commands and monitors running for a thread. */
export interface AgentBackgroundWork {
  commands: Array<AgentBackgroundCommand>
  monitors: Array<AgentThreadMonitor>
  monitor_budget: AgentMonitorBudget | null
}

export interface AgentQueuedMessage {
  id: string
  text: string
  sender: string | null
  platform: string | null
  queued_at: string | null
}

export interface PendingThreadMessage extends Omit<
  QueuedThreadMessage,
  "attachments" | "pending" | "mine"
> {
  attachments?: Array<MediaChunk>
  status: "sending" | "failed"
  /** Sent to queue behind the live run, so it renders as a queued row. */
  queued?: boolean
  /** Why delivery failed, e.g. `503 Service Unavailable`. */
  error?: string
}

export type WorkflowApprovalStatus = "pending" | "approved" | "rejected"

export interface WorkflowDiffStats {
  files: number
  additions: number
  deletions: number
}

export interface WorkflowPushApproval {
  fingerprint: string
  status: WorkflowApprovalStatus
  repo: string
  branch: string
  baseSha: string
  headSha: string
  files: Array<string>
  diffStats: WorkflowDiffStats
  diffPreview: string
  diffPreviewTruncated: boolean
  inheritedFrom: string | null
  approvalUrl: string | null
  requestedAt: string | null
  decidedAt: string | null
  decidedBy: string | null
}

export interface WorkflowPushApprovalsResponse {
  threadId: string
  approvals: Array<WorkflowPushApproval>
}

export interface AgentPullRequestSummary {
  number: number
  title: string
  state: "draft" | "open" | "merged" | "closed"
  headRef: string
  baseRef: string
  url: string
}

export interface AgentPullRequest extends AgentPullRequestSummary {
  repoFullName: string
  author: string | null
  authorAvatarUrl: string | null
  createdAt: string | null
  diffStats: {
    files: number
    additions: number
    deletions: number
  }
}

export interface AgentPullRequestHealth {
  repoFullName: string | null
  number: number | null
  url: string | null
  statusAvailable: boolean
  state: "open" | "merged" | "closed" | null
  isDraft: boolean | null
  mergeConflictState: "mergeable" | "conflicting" | "unknown" | null
  checksAvailable: boolean
  failingChecks: Array<{
    name: string
    conclusion: string | null
    url: string | null
  }>
  pendingCheckCount: number | null
  inconclusiveCheckCount: number | null
  commentsAvailable: boolean
  unresolvedReviewThreadCount: number | null
  unresolvedReviewThreads: Array<{
    author: string | null
    body: string
    path: string
    line: number | null
    url: string | null
  }>
}

export interface AgentPullRequestStatusResponse {
  pullRequests: Array<AgentPullRequestHealth>
}

/** One kind of PR problem a thread-view fix prompt covers, and nothing else. */
export type ThreadFixScope = "conflicts" | "checks" | "comments"

export interface AgentPullRequestContextResponse {
  context: {
    repoFullName: string
    number: number
    url: string
    headSha: string | null
    mergeState: string | null
    reviewDecision: string | null
    checksAvailable: boolean
    checks: Array<{
      name: string
      status: string
      conclusion: string | null
      required: boolean | null
      url: string | null
    }>
    reviewsAvailable: boolean
    changesRequestedReviews: Array<{
      author: string
      body: string
      url: string | null
    }>
    unresolvedReviewThreads: Array<{
      path: string
      line: number | null
      isOutdated: boolean
      commentsTruncated: boolean
      comments: Array<{
        author: string
        body: string
        url: string | null
      }>
    }>
    truncated: boolean
  }
  prompt: string
}

export interface ReviewPageRef {
  owner: string
  repo: string
  number: number
}

export type AgentSubagentStatus = "in_progress" | "completed" | "error"

/**
 * A subagent the thread spawned with the `task` tool, as the sidebar lists it
 * under the thread. `toolCallId` is the `task` call's id, which is also the
 * last segment of the namespace every event the subagent emitted carries.
 */
export interface AgentSubagentSummary {
  toolCallId: string
  /** The first line of the task description. */
  title: string
  subagentType: string
  status: AgentSubagentStatus
  startedAt: number
  endedAt: number | null
}

export type TaskMembership =
  | { role: "coordinator"; taskId: string }
  | { role: "worker"; taskId: string; coordinatorThreadId: string | null }

export interface AgentThread {
  taskMembership?: TaskMembership
  taskWorkers?: Array<AgentThread>
  ownerLogin?: string | null
  visibility?: "public" | "private"
  id: string
  /** Set on a PR review listed in the sidebar: its row opens this review page. */
  reviewPage?: ReviewPageRef
  /** Root-level subagents, oldest first; only transcript-log threads report them. */
  subagents?: Array<AgentSubagentSummary>
  /**
   * Transcript source for the thread, from its LangGraph metadata. `"v2"` means
   * the append-only event log serves it; absent means the SDK stream does.
   */
  transcript?: "v2"

  title: string
  repo: string
  repoFullName: string
  branch: string
  model: string
  effort?: string | null
  modelSelection?: "auto" | "explicit" | null
  planStatus?: string | null
  adminThread?: boolean
  source?: AgentSource
  origin?: AgentSource | string
  threadCategory?: AgentThreadCategory | string
  triggerKind?: AgentTriggerKind | string
  /** The allowed Slack bot that started the thread; the dashboard cannot steer it. */
  triggeringBot?: { key: string; name: string } | null
  automationId?: string | null
  automationName?: string | null
  automationActionPosted?: boolean
  status: AgentStatus
  viewed: boolean
  viewedAt?: number | null
  resolved?: boolean
  resolvedAt?: number | null
  attentionReason?: string | null
  createdAt: number
  updatedAt: number
  traceUrl?: string | null
  /** LangSmith cost of the thread's finished runs so far, in USD. */
  costUsd?: number | null
  /** LangSmith cost of each finished run, in USD, by invocation id. */
  runCosts?: Record<string, number>
  sourceUrl?: string | null
  sourceAppUrl?: string | null
  codeChannelUrl?: string | null
  sandboxId?: string | null
  /** For a thread bridged to someone's machine: which app serves it. */
  sandboxBridgeClient?: "cli" | "desktop" | null
  /** Whether that machine is serving the thread's checkout right now; null in lists. */
  sandboxBridgeOnline?: boolean | null
  messages: Array<Message>
  pendingMessages?: Array<PendingThreadMessage>
  pr?: AgentPullRequestSummary
  pullRequests?: Array<AgentPullRequest>
  diffStats?: {
    files: number
    additions: number
    deletions: number
  }
  changedFiles?: Array<{
    path: string
    additions: number
    deletions: number
    patch?: string
  }>
}

export type GitFileStatus =
  | "index-modified"
  | "index-added"
  | "index-deleted"
  | "index-renamed"
  | "index-copied"
  | "modified"
  | "deleted"
  | "untracked"
  | "ignored"
  | "type-changed"
  | "intent-to-add"
  | "both-modified"
  | "both-added"
  | "both-deleted"
  | "added-by-us"
  | "added-by-them"
  | "deleted-by-us"
  | "deleted-by-them"

export interface GitStatusEntry {
  path: string
  status: GitFileStatus
  staged: boolean
  originalPath?: string
}
