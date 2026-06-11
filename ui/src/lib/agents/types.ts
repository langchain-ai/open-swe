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

export interface ToolExecutionChunk {
  kind: "tool-execution"
  toolCallId: string
  title: string
  toolKind: AcpToolKind
  input?: Record<string, unknown>
  status: AcpToolStatus
  output?: string
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

export interface AgentSchedule {
  id: string
  name: string
  prompt: string
  schedule: string
  repo: string | null
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

export interface AgentThread {
  id: string
  title: string
  repo: string
  repoFullName: string
  branch: string
  model: string
  effort?: string | null
  source?: AgentSource
  status: AgentStatus
  viewed: boolean
  viewedAt?: number | null
  createdAt: number
  updatedAt: number
  traceUrl?: string | null
  messages: Array<Message>
  pr?: {
    number: number
    title: string
    state: "draft" | "open" | "merged" | "closed"
    headRef: string
    baseRef: string
    url: string
  }
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
  | "deleted-by-them";

export interface GitStatusEntry {
  path: string
  status: GitFileStatus
  staged: boolean
  originalPath?: string
}
