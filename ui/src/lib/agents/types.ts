export type Author = "user" | "agent" | "system" | "tool";

export type ChunkKind =
  | "text"
  | "code"
  | "error"
  | "list"
  | "tool-execution"
  | "todo"
  | "image";

export type TodoStatus = "pending" | "in_progress" | "completed";

export type AgentStatus = "idle" | "running" | "finished" | "interrupted" | "error";

export interface TodoItem {
  content: string;
  status: TodoStatus;
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
  | "other";

export type AcpToolStatus = "pending" | "in_progress" | "completed" | "error";

export interface AcpToolLocation {
  path: string;
  line?: number;
}

export interface DiffData {
  originalContent: string | null;
  newContent: string;
  filePath: string;
  isNewFile: boolean;
  isBinary: boolean;
  isTruncated: boolean;
  totalLines: number;
}

export interface ToolExecutionChunk {
  kind: "tool-execution";
  toolCallId: string;
  title: string;
  toolKind: AcpToolKind;
  input?: Record<string, unknown>;
  status: AcpToolStatus;
  output?: string;
  elapsedMs?: number;
  approvalRequestId?: string;
  diffData?: DiffData;
  diffs?: DiffData[];
  locations?: AcpToolLocation[];
}

export interface TextChunk {
  kind: "text";
  text: string;
}

export interface CodeChunk {
  kind: "code";
  text: string;
  language?: string;
}

export interface ErrorChunk {
  kind: "error";
  text: string;
}

export interface ListChunk {
  kind: "list";
  lines: string[];
}

export interface TodoChunk {
  kind: "todo";
  todos: TodoItem[];
}

export interface ImageChunk {
  kind: "image";
  base64: string;
  mimeType: string;
  fileName?: string;
}

export type Chunk =
  | TextChunk
  | CodeChunk
  | ErrorChunk
  | ListChunk
  | ToolExecutionChunk
  | TodoChunk
  | ImageChunk;

export interface Message {
  id: string;
  author: Author;
  timestamp: string;
  chunks: Chunk[];
  hidden?: boolean;
}

export interface Project {
  id: string;
  path: string;
  name: string;
  createdAt: number;
  lastOpenedAt: number;
  gitBranch?: string;
}

export interface AgentThread {
  id: string;
  title: string;
  repo: string;
  repoFullName: string;
  branch: string;
  model: string;
  status: AgentStatus;
  createdAt: number;
  updatedAt: number;
  messages: Message[];
  pr?: {
    number: number;
    title: string;
    state: "draft" | "open" | "merged";
    headRef: string;
    baseRef: string;
    url: string;
  };
  diffStats?: {
    files: number;
    additions: number;
    deletions: number;
  };
  changedFiles?: Array<{
    path: string;
    additions: number;
    deletions: number;
    patch?: string;
  }>;
}

export type GitFileStatus =
  | "index-modified"
  | "index-added"
  | "index-deleted"
  | "modified"
  | "deleted"
  | "untracked";

export interface GitStatusEntry {
  path: string;
  status: GitFileStatus;
  staged: boolean;
  originalPath?: string;
}
