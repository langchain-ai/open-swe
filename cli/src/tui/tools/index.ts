import { BashTool } from "./BashTool.js";
import { FileEditTool } from "./FileEditTool.js";
import { FileReadTool } from "./FileReadTool.js";
import { FileWriteTool } from "./FileWriteTool.js";
import { GlobTool } from "./GlobTool.js";
import { GrepTool } from "./GrepTool.js";
import { LSTool } from "./LSTool.js";
import { TaskTool } from "./TaskTool.js";
import { TodoWriteTool } from "./TodoWriteTool.js";
import type { ToolUI } from "./types.js";

const TOOLS: readonly ToolUI[] = [
  BashTool,
  FileReadTool,
  FileWriteTool,
  FileEditTool,
  LSTool,
  GrepTool,
  GlobTool,
  TodoWriteTool,
  TaskTool,
];

const TOOL_BY_NAME: ReadonlyMap<string, ToolUI> = new Map(
  TOOLS.flatMap((tool) =>
    tool.names.map((name): [string, ToolUI] => [name, tool]),
  ),
);

export const findToolByName = (name: string | undefined): ToolUI | null => {
  if (!name) return null;
  return TOOL_BY_NAME.get(name) ?? null;
};

export type { ToolUI } from "./types.js";
