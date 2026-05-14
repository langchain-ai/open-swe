export type Author = "user" | "agent" | "system" | "tool";
export type ChunkKind = "text" | "code" | "error" | "list" | "tool-execution";
export type SlashCommandName =
  | "help"
  | "quit"
  | "clear"
  | "logs";
export type Provider = "openai" | "anthropic" | "google";
export type Effort = "low" | "medium" | "high" | "xhigh";

export type SlashCommand = {
  name: SlashCommandName;
  description: string;
  aliases?: string[];
};

export type ModelOption = {
  id: number;
  label: string;
  name: string;
  provider: Provider;
  effort: Effort;
  contextWindow: number;
};

export type ModelConfig = {
  name: string;
  provider: Provider;
  effort: Effort;
};

export type ToolExecutionStatus = "running" | "success" | "error";

export type Chunk = {
  kind: ChunkKind;
  text?: string;
  lines?: string[];
  // for 'tool-execution'
  toolCallId?: string;
  toolName?: string;
  toolArgs?: Record<string, any>;
  status?: ToolExecutionStatus;
  output?: string;
};

export type Message = {
  id: string;
  author: Author;
  timestamp?: string;
  chunks: Chunk[];
};

export type TokenUsage = { input: number; output: number; total: number };

export type DiffLine = {
  type: "add" | "remove" | "context";
  oldLine?: number;
  newLine?: number;
  text: string;
};

export type StructuredPatchHunk = {
  oldStart: number;
  oldLines: number;
  newStart: number;
  newLines: number;
  lines: string[];
};

export type StreamProcessorActions = {
  addMessage: (message: Omit<Message, "id">) => void;
  updateToolExecution: (toolExecution: ToolExecution) => void;
  updateTokenUsage: (usage: TokenUsage) => void;
};

export type ToolExecution = {
  toolCallId: string;
  status: ToolExecutionStatus;
  output: string;
};

export type Result<T> = { ok: true; data: T } | { ok: false; error: string };

export * from "./tui.js";
export * from "./text-input.js";
