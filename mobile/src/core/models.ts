/** JSON as the server sends it, for payloads whose shape it does not pin down. */
export type JSONValue =
  | string
  | number
  | boolean
  | null
  | JSONValue[]
  | { [key: string]: JSONValue }

export type JSONObject = { [key: string]: JSONValue }

export function isObject(value: unknown): value is JSONObject {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

export function stringAt(value: unknown, key: string): string | undefined {
  if (!isObject(value)) return undefined
  const found = value[key]
  return typeof found === "string" ? found : undefined
}

/** Text of a LangChain message `content`: a string, or the text blocks of a list. */
export function messageText(content: unknown): string {
  if (typeof content === "string") return content
  if (!Array.isArray(content)) return ""
  return content
    .map((block) => {
      if (typeof block === "string") return block
      if (
        isObject(block) &&
        block.type === "text" &&
        typeof block.text === "string"
      )
        return block.text
      return null
    })
    .filter((text): text is string => text !== null)
    .join("\n")
}

export type AgentStatus =
  | "running"
  | "idle"
  | "finished"
  | "interrupted"
  | "error"
export const AGENT_STATUSES: AgentStatus[] = [
  "running",
  "idle",
  "finished",
  "interrupted",
  "error",
]

export const STATUS_TITLES: Record<AgentStatus, string> = {
  running: "Running",
  idle: "Idle",
  finished: "Finished",
  interrupted: "Stopped",
  error: "Error",
}

export type PullRequestState = "draft" | "open" | "merged" | "closed"

export interface PullRequestSummary {
  number: number
  title: string
  state: PullRequestState
  headRef: string
  baseRef: string
  url: string
}

export interface DiffStats {
  files: number
  additions: number
  deletions: number
}

/** Where a thread stands in the inbox. */
export type InboxCategory = "needsAttention" | "working" | "inReview" | "older"
export const INBOX_CATEGORIES: InboxCategory[] = [
  "needsAttention",
  "working",
  "inReview",
  "older",
]

export const CATEGORY_TITLES: Record<InboxCategory, string> = {
  needsAttention: "Needs Attention",
  working: "Working",
  inReview: "In Review",
  older: "Older",
}

/** The git state a row shows: its pull request's, or whether it changed anything. */
export type GitState =
  | "open"
  | "draft"
  | "merged"
  | "closed"
  | "changes"
  | "noChanges"
export const GIT_STATES: GitState[] = [
  "open",
  "draft",
  "merged",
  "closed",
  "changes",
  "noChanges",
]

export const GIT_STATE_TITLES: Record<GitState, string> = {
  open: "Open",
  draft: "Draft",
  merged: "Merged",
  closed: "Closed",
  changes: "Changes",
  noChanges: "No Changes",
}

export interface AgentThread {
  id: string
  title: string
  repo: string
  repoFullName: string
  branch: string
  model?: string
  status: AgentStatus
  viewed: boolean
  resolved: boolean
  attentionReason?: string
  createdAt: Date
  updatedAt: Date
  source?: string
  threadCategory?: string
  ownerLogin?: string
  sandboxBridgeClient?: string
  pr?: PullRequestSummary
  diffStats?: DiffStats
}

const PR_STATES: PullRequestState[] = ["draft", "open", "merged", "closed"]

function decodePullRequest(value: unknown): PullRequestSummary | undefined {
  if (!isObject(value) || typeof value.number !== "number") return undefined
  const state = PR_STATES.includes(value.state as PullRequestState)
    ? (value.state as PullRequestState)
    : "open"
  return {
    number: value.number,
    title: stringAt(value, "title") ?? "",
    state,
    headRef: stringAt(value, "headRef") ?? "",
    baseRef: stringAt(value, "baseRef") ?? "",
    url: stringAt(value, "url") ?? "",
  }
}

function decodeDiffStats(value: unknown): DiffStats | undefined {
  if (!isObject(value)) return undefined
  return {
    files: typeof value.files === "number" ? value.files : 0,
    additions: typeof value.additions === "number" ? value.additions : 0,
    deletions: typeof value.deletions === "number" ? value.deletions : 0,
  }
}

function dateFromMilliseconds(value: unknown): Date {
  return typeof value === "number" ? new Date(value) : new Date()
}

/** A thread summary from the dashboard API, with the defaults the app relies on. */
export function decodeThread(value: unknown): AgentThread {
  if (!isObject(value) || typeof value.id !== "string")
    throw new Error("malformed thread")
  const status = AGENT_STATUSES.includes(value.status as AgentStatus)
    ? (value.status as AgentStatus)
    : "idle"
  return {
    id: value.id,
    title: stringAt(value, "title") ?? "Untitled",
    repo: stringAt(value, "repo") ?? "",
    repoFullName: stringAt(value, "repoFullName") ?? "",
    branch: stringAt(value, "branch") ?? "",
    model: stringAt(value, "model"),
    status,
    viewed: typeof value.viewed === "boolean" ? value.viewed : true,
    resolved: value.resolved === true,
    attentionReason: stringAt(value, "attentionReason"),
    createdAt: dateFromMilliseconds(value.createdAt),
    updatedAt: dateFromMilliseconds(value.updatedAt),
    source: stringAt(value, "source"),
    threadCategory: stringAt(value, "threadCategory"),
    ownerLogin: stringAt(value, "ownerLogin"),
    sandboxBridgeClient: stringAt(value, "sandboxBridgeClient"),
    pr: decodePullRequest(value.pr),
    diffStats: decodeDiffStats(value.diffStats),
  }
}

/** A thread built in the app, with the repository's short name worked out. */
export function makeThread(
  fields: Partial<AgentThread> &
    Pick<
      AgentThread,
      | "id"
      | "title"
      | "repoFullName"
      | "status"
      | "viewed"
      | "createdAt"
      | "updatedAt"
    >
): AgentThread {
  return {
    repo: fields.repoFullName.split("/").pop() ?? fields.repoFullName,
    branch: "",
    resolved: false,
    source: "dashboard",
    threadCategory: "interactive",
    ...fields,
  }
}

export function threadCategory(thread: AgentThread): InboxCategory {
  if (thread.status === "running") return "working"
  if (thread.pr && (thread.pr.state === "open" || thread.pr.state === "draft"))
    return "inReview"
  if (
    !thread.viewed ||
    thread.attentionReason ||
    thread.status === "error" ||
    thread.status === "interrupted"
  ) {
    return "needsAttention"
  }
  return "older"
}

export function gitState(thread: AgentThread): GitState {
  if (thread.pr) return thread.pr.state
  const stats = thread.diffStats
  return !stats || (stats.additions === 0 && stats.deletions === 0)
    ? "noChanges"
    : "changes"
}

export function isEmptyDiff(stats: DiffStats | undefined): boolean {
  return !stats || (stats.additions === 0 && stats.deletions === 0)
}

/** The repository a thread is filed under; empty for threads with no repository. */
export function workspaceKey(thread: AgentThread): string {
  return thread.repoFullName
}

export function workspaceName(thread: AgentThread): string {
  if (thread.repo) return thread.repo
  return thread.repoFullName || "No Repo"
}

export function isLocal(thread: AgentThread): boolean {
  return thread.sandboxBridgeClient !== undefined
}

export const AGENT_SOURCES = [
  "dashboard",
  "slack",
  "github",
  "linear",
  "schedule",
  "claude_code",
]

export function sourceTitle(source: string | undefined): string {
  switch (source) {
    case undefined:
    case "dashboard":
      return "Web"
    case "slack":
      return "Slack"
    case "github":
      return "GitHub"
    case "linear":
      return "Linear"
    case "schedule":
      return "Automation"
    case "claude_code":
      return "Claude Code"
    default:
      return source.charAt(0).toUpperCase() + source.slice(1)
  }
}

export interface Identity {
  login: string
  email?: string
  avatarURL?: string
}

export function decodeIdentity(value: unknown): Identity {
  const login = stringAt(value, "login")
  if (!login) throw new Error("malformed identity")
  return {
    login,
    email: stringAt(value, "email"),
    avatarURL: stringAt(value, "avatar_url"),
  }
}

export function identityInitial(identity: Identity | undefined): string {
  return identity?.login.charAt(0).toUpperCase() ?? "?"
}

export interface DiffFile {
  path: string
  previousPath?: string
  status: string
  additions: number
  deletions: number
  originalContent?: string
  modifiedContent?: string
  patch?: string
  unrenderable: boolean
}

export function fileName(path: string): string {
  return path.split("/").pop() ?? path
}

export function fileDirectory(path: string): string {
  const parts = path.split("/")
  return parts.length > 1 ? parts[parts.length - 2] : ""
}

export function decodeDiffFile(value: unknown): DiffFile {
  const path = stringAt(value, "path")
  if (!isObject(value) || !path) throw new Error("malformed diff file")
  return {
    path,
    previousPath: stringAt(value, "previousPath"),
    status: stringAt(value, "status") ?? "modified",
    additions: typeof value.additions === "number" ? value.additions : 0,
    deletions: typeof value.deletions === "number" ? value.deletions : 0,
    originalContent: stringAt(value, "originalContent"),
    modifiedContent: stringAt(value, "modifiedContent"),
    patch: stringAt(value, "patch"),
    unrenderable: value.unrenderable === true,
  }
}

export interface BranchDiff {
  prNumber?: number
  baseRef: string
  headRef?: string
  truncated: boolean
  files: DiffFile[]
}

export function decodeBranchDiff(value: unknown): BranchDiff {
  if (!isObject(value)) throw new Error("malformed branch diff")
  return {
    prNumber: typeof value.prNumber === "number" ? value.prNumber : undefined,
    baseRef: stringAt(value, "baseRef") ?? "main",
    headRef: stringAt(value, "headRef"),
    truncated: value.truncated === true,
    files: Array.isArray(value.files) ? value.files.map(decodeDiffFile) : [],
  }
}

export function diffTotals(diff: BranchDiff): {
  additions: number
  deletions: number
} {
  return diff.files.reduce(
    (sum, file) => ({
      additions: sum.additions + file.additions,
      deletions: sum.deletions + file.deletions,
    }),
    { additions: 0, deletions: 0 }
  )
}

export interface PullRequestHealth {
  repoFullName?: string
  number?: number
  url?: string
  state?: string
  mergeConflictState?: string
  checksAvailable: boolean
  failingChecks: { name: string; conclusion?: string; url?: string }[]
  pendingCheckCount?: number
  unresolvedReviewThreadCount?: number
}

export function decodePullRequestHealth(value: unknown): PullRequestHealth {
  if (!isObject(value)) throw new Error("malformed pull request status")
  const failing = Array.isArray(value.failingChecks) ? value.failingChecks : []
  return {
    repoFullName: stringAt(value, "repoFullName"),
    number: typeof value.number === "number" ? value.number : undefined,
    url: stringAt(value, "url"),
    state: stringAt(value, "state"),
    mergeConflictState: stringAt(value, "mergeConflictState"),
    checksAvailable: value.checksAvailable === true,
    failingChecks: failing.filter(isObject).map((check) => ({
      name: stringAt(check, "name") ?? "check",
      conclusion: stringAt(check, "conclusion"),
      url: stringAt(check, "url"),
    })),
    pendingCheckCount:
      typeof value.pendingCheckCount === "number"
        ? value.pendingCheckCount
        : undefined,
    unresolvedReviewThreadCount:
      typeof value.unresolvedReviewThreadCount === "number"
        ? value.unresolvedReviewThreadCount
        : undefined,
  }
}

/**
 * Whether `health` is the status of `pr`: same number, and the same repository
 * when both name one (its URL is `https://github.com/<owner>/<repo>/pull/<n>`).
 */
export function healthDescribes(
  health: PullRequestHealth,
  pr: PullRequestSummary
): boolean {
  if (health.number !== pr.number) return false
  if (!health.repoFullName) return true
  return pr.url
    .toLowerCase()
    .includes(`/${health.repoFullName.toLowerCase()}/pull/`)
}

/** "now", "5m", "3h", "2d", "4w". */
export function compactAge(date: Date): string {
  const seconds = Math.max(0, (Date.now() - date.getTime()) / 1000)
  if (seconds < 60) return "now"
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)}h`
  if (seconds < 604_800) return `${Math.floor(seconds / 86_400)}d`
  return `${Math.floor(seconds / 604_800)}w`
}
