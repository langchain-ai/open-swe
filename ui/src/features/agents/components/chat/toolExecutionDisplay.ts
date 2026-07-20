import { humanizeToolName } from "@/features/agents/lib/toolNames";
import type { AcpToolKind } from "@/features/agents/lib/types";

function stripProjectPath(path: string, projectPath?: string): string {
  if (!projectPath || !path.startsWith(projectPath)) return path;
  const relative = path.slice(projectPath.length);
  return relative.replace(/^\/+/, "") || ".";
}

function firstStringArg(
  input: Record<string, unknown> | undefined,
  keys: Array<string>,
): string | undefined {
  if (!input) return undefined;
  for (const key of keys) {
    const value = input[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return undefined;
}

function truncateMiddle(value: string, maxLength: number): string {
  return value.length > maxLength ? `${value.slice(0, maxLength)}...` : value;
}

function normalizedToolName(title: string): string {
  return title.trim().split(/\s+/, 1)[0]?.toLowerCase() ?? "";
}

function humanizeToolTitle(title: string): string {
  const trimmed = title.trim();
  if (!trimmed) return "Tool";

  const [name, ...rest] = trimmed.split(/\s+/);
  const suffix = rest.join(" ");
  if (name && suffix && /^(?:[./~]|[a-z]+:\/\/)/i.test(suffix)) {
    return `${humanizeToolName(name)} ${suffix}`;
  }
  return humanizeToolName(trimmed);
}

export function formatToolDisplay(
  title: string,
  toolKind: AcpToolKind,
  input: Record<string, unknown> | undefined,
  projectPath?: string,
): string {
  const toolName = normalizedToolName(title);
  const path = firstStringArg(input, ["path", "file_path", "target_file"]);
  const pattern = firstStringArg(input, ["pattern"]);
  const query = firstStringArg(input, ["query"]);
  const url = firstStringArg(input, ["url"]);
  const command = firstStringArg(input, ["command"]);

  switch (toolKind) {
    case "read": {
      if (path) {
        const displayPath = stripProjectPath(path, projectPath);
        return toolName === "ls" ? `List ${displayPath}` : `Read ${displayPath}`;
      }
      return humanizeToolTitle(title);
    }
    case "search": {
      if (pattern) return `Search "${truncateMiddle(pattern, 40)}"`;
      if (query) return `Search "${truncateMiddle(query, 40)}"`;
      if (path) return `Search ${stripProjectPath(path, projectPath)}`;
      return humanizeToolTitle(title);
    }
    case "fetch": {
      if (url) return `Fetch ${truncateMiddle(url, 50)}`;
      return humanizeToolTitle(title);
    }
    case "execute": {
      if (command) return `Shell ${truncateMiddle(command, 60)}`;
      return humanizeToolTitle(title);
    }
    case "edit":
    case "delete":
    case "move":
      return humanizeToolTitle(title);
    case "think":
      return "Thinking...";
    default: {
      if (toolName === "write_todos" || title.toLowerCase().startsWith("write todos")) {
        return "Update todos";
      }
      if (toolName === "ls" && path) return `List ${stripProjectPath(path, projectPath)}`;
      return humanizeToolTitle(title);
    }
  }
}
