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
  "idle" | "running" | "finished" | "interrupted" | "error"

export type AgentSource =
  "dashboard" | "github" | "slack" | "linear" | "schedule"

export type AgentThreadCategory =
  "interactive" | "issue" | "pull_request" | "automation" | "review" | "system"

export type AgentTriggerKind =
  | "user"
  | "schedule"
  | "schedule_test"
  | "wakeup"
  | "reviewer"
  | "analyzer"
  | "ci_autofix"

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
  /** deepagents `task` tool — spawns a subagent; rendered as a subagent card. */
  | "task"
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

export interface ImageChunk {
  kind: "image"
  base64: string
  mimeType: string
  fileName?: string
}

export type Chunk =
  | TextChunk
  | ReasoningChunk
  | CodeChunk
  | ErrorChunk
  | ListChunk
  | ToolExecutionChunk
  | TodoChunk
  | ImageChunk

export interface Message {
  id: string
  author: Author
  timestamp: string
  structuredSenderId?: string
  structuredSenderKind?: "person" | "system"
  structuredSenderName?: string
  structuredSenderNote?: string
  structuredSurface?: string
  /** Id of the user message that opened this agent run and keys its diff artifact. */
  turnKey?: string
  /** Timestamp of the first message in an agent turn; used to derive work duration. */
  startedAt?: string
  timestampIsFallback?: boolean
  chunks: Array<Chunk>
  hidden?: boolean
}

export interface Project {
  id: string
  path: string
  name: string
  createdAt: number
  lastOpenedAt: number
  gitBranch?: string
}

export type SlackNotificationMode = "always" | "on_action"

export interface AgentSchedule {
  id: string
  name: string
  prompt: string
  schedule: string
  repo: string | null
  slackChannelId?: string | null
  slackNotificationMode: SlackNotificationMode
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
  createdAt?: string | null
  updatedAt?: string | null
}

export interface QueuedThreadMessage {
  id: string
  content: string
  images?: Array<ImageChunk>
  createdAt: number
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
  approvalUrl: string | null
  requestedAt: string | null
  decidedAt: string | null
  decidedBy: string | null
}

export interface WorkflowPushApprovalsResponse {
  threadId: string
  isOwner: boolean
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

export interface AgentThread {
  id: string
  title: string
  repo: string
  repoFullName: string
  branch: string
  model: string
  effort?: string | null
  planMode?: boolean
  planStatus?: string | null
  adminThread?: boolean
  source?: AgentSource
  origin?: AgentSource | string
  threadCategory?: AgentThreadCategory | string
  triggerKind?: AgentTriggerKind | string
  automationId?: string | null
  automationName?: string | null
  status: AgentStatus
  viewed: boolean
  viewedAt?: number | null
  resolved?: boolean
  resolvedAt?: number | null
  isOwner?: boolean
  createdAt: number
  updatedAt: number
  traceUrl?: string | null
  sourceUrl?: string | null
  sandboxId?: string | null
  messages: Array<Message>
  queuedMessages?: Array<QueuedThreadMessage>
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
