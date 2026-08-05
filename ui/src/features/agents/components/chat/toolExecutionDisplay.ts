import type { AcpToolKind } from "@/features/agents/lib/types";
import { humanizeToolName } from "@/features/agents/lib/toolNames";

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

/**
 * A tool call split into the verb ("Read", "Shell") and its argument
 * ("src/app.ts", "pnpm test"). The timeline styles the two differently — the
 * heading carries weight, the preview dims out — so they have to stay separable
 * rather than being pre-joined into one string.
 */
export interface ToolDisplayParts {
  heading: string;
  preview: string | null;
}

export function formatToolDisplayParts(
  title: string,
  toolKind: AcpToolKind,
  input: Record<string, unknown> | undefined,
  projectPath?: string,
): ToolDisplayParts {
  const toolName = normalizedToolName(title);
  const path = firstStringArg(input, ["path", "file_path", "target_file"]);
  const pattern = firstStringArg(input, ["pattern"]);
  const query = firstStringArg(input, ["query"]);
  const url = firstStringArg(input, ["url"]);
  const command = firstStringArg(input, ["command"]);
  const plain = (heading: string): ToolDisplayParts => ({ heading, preview: null });

  switch (toolKind) {
    case "read": {
      if (path) {
        const displayPath = stripProjectPath(path, projectPath);
        return { heading: toolName === "ls" ? "List" : "Read", preview: displayPath };
      }
      return plain(humanizeToolTitle(title));
    }
    case "search": {
      if (pattern) return { heading: "Search", preview: `"${truncateMiddle(pattern, 40)}"` };
      if (query) return { heading: "Search", preview: `"${truncateMiddle(query, 40)}"` };
      if (path) return { heading: "Search", preview: stripProjectPath(path, projectPath) };
      return plain(humanizeToolTitle(title));
    }
    case "fetch": {
      if (url) return { heading: "Fetch", preview: truncateMiddle(url, 50) };
      return plain(humanizeToolTitle(title));
    }
    case "execute": {
      if (command) return { heading: "Shell", preview: truncateMiddle(command, 60) };
      return plain(humanizeToolTitle(title));
    }
    case "edit":
    case "delete":
    case "move":
      return plain(humanizeToolTitle(title));
    case "think":
      return plain("Thinking...");
    default: {
      if (toolName === "write_todos" || title.toLowerCase().startsWith("write todos")) {
        return plain("Update todos");
      }
      if (toolName === "ls" && path) {
        return { heading: "List", preview: stripProjectPath(path, projectPath) };
      }
      return plain(humanizeToolTitle(title));
    }
  }
}

export function formatToolDisplay(
  title: string,
  toolKind: AcpToolKind,
  input: Record<string, unknown> | undefined,
  projectPath?: string,
): string {
  const { heading, preview } = formatToolDisplayParts(title, toolKind, input, projectPath);
  return preview ? `${heading} ${preview}` : heading;
}
