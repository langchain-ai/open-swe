import {
  coerceMessageLikeToMessage,
  ToolMessage,
  isAIMessage,
  isHumanMessage,
  isToolMessage,
} from "@langchain/core/messages";

import { getMessageContentString } from "@open-swe/shared/messages";
import { createWriteTechnicalNotesToolFields } from "@open-swe/shared/open-swe/tools";

export type ToolCall = {
  name: string;
  args: Record<string, any>;
  id?: string;
  type?: "tool_call";
};

interface LogChunk {
  event: string;
  data: any;
  ops?: Array<{ value: string }>;
}

/**
 * Create a simple diff between old and new strings
 */
function createSimpleDiff(oldString: string, newString: string): string[] {
  const logs: string[] = [];

  if (!oldString && newString) {
    const lines = newString.split("\n").slice(0, 10);
    lines.forEach((line) => logs.push(`+ ${line}`));
    if (newString.split("\n").length > 10) {
      logs.push(`+ ... (${newString.split("\n").length - 10} more lines)`);
    }
    return logs;
  }

  if (!newString) {
    oldString.split("\n").forEach((line) => logs.push(`- ${line}`));
    return logs;
  }

  const oldLines = oldString.split("\n");
  const newLines = newString.split("\n");

  const removedLines = oldLines.filter(
    (oldLine) => !newLines.some((newLine) => newLine === oldLine),
  );

  const addedLines = newLines.filter(
    (newLine) => !oldLines.some((oldLine) => oldLine === newLine),
  );

  removedLines.forEach((line) => logs.push(`- ${line}`));
  addedLines.forEach((line) => logs.push(`+ ${line}`));

  return logs;
}

/**
 * Format a tool call arguments into a clean, readable string
 */
function formatToolCallArgs(tool: ToolCall): string {
  const toolName = tool.name || "unknown tool";

  if (!tool.args) return toolName;

  switch (toolName.toLowerCase()) {
    case "shell":
    case "execute_bash": {
      let command = "";
      if (Array.isArray(tool.args.command)) {
        command = tool.args.command.join(" ");
      } else {
        command = tool.args.command || "";
      }

      // Truncate long commands (more than 160 characters)
      if (command.length > 160) {
        return `${toolName}: ${command.substring(0, 160)}...`;
      }
      return `${toolName}: ${command}`;
    }

    case "write_file": {
      const filePath = tool.args.file_path || "";
      const content = tool.args.content || "";
      const lineCount = content.split("\n").length;
      return `${toolName}: ${filePath} (${lineCount} lines)`;
    }

    case "read_file": {
      const filePath = tool.args.file_path || "";
      return `${toolName}: ${filePath}`;
    }

    case "edit_file": {
      const filePath = tool.args.file_path || "";
      return `${toolName}: ${filePath}`;
    }

    case "http_request": {
      const method = tool.args.method || "GET";
      const url = tool.args.url || "";
      return `${toolName}: ${method} ${url}`;
    }

    case "web_search": {
      const query = tool.args.query || "";
      return `${toolName}: "${query}"`;
    }

    case "grep": {
      const pattern = tool.args.pattern || "";
      const path = tool.args.path || "";
      return `${toolName}: "${pattern}"${path ? ` in ${path}` : ""}`;
    }

    case "glob": {
      const pattern = tool.args.pattern || "";
      const path = tool.args.path || "";
      return `${toolName}: ${pattern}${path ? ` in ${path}` : ""}`;
    }

    case "view": {
      return `${toolName}: ${tool.args.path || ""}`;
    }

    case "ls": {
      const path = tool.args.path || "";
      return `${toolName}: ${path}`;
    }

    case "str_replace_based_edit_tool": {
      const command = tool.args.command || "";

      switch (command) {
        case "insert": {
          const insertLine = tool.args.insert_line;
          const newStr = tool.args.new_str || "";
          return `${toolName}: insert_line=${insertLine}, new_str="${newStr}"`;
        }
        case "str_replace": {
          return `${toolName}: string replacement`;
        }
        case "create": {
          const fileText = tool.args.file_text || "";
          return `${toolName}: file_text="${fileText}"`;
        }
        case "view": {
          const viewRange = tool.args.view_range;
          if (viewRange) {
            return `${toolName}: view_range=[${viewRange[0]}, ${viewRange[1]}]`;
          }
          return `${toolName}: view`;
        }
        default:
          return `${toolName}: ${command}`;
      }
    }

    case "write_todos": {
      const todos = tool.args.todos || [];
      if (Array.isArray(todos)) {
        const todoCount = todos.length;
        const statusCounts = todos.reduce((acc: any, todo: any) => {
          acc[todo.status] = (acc[todo.status] || 0) + 1;
          return acc;
        }, {});
        const statusSummary = Object.entries(statusCounts)
          .map(([status, count]) => `${count} ${status}`)
          .join(", ");
        return `${toolName}: Updated ${todoCount} todos (${statusSummary})`;
      }
      return `${toolName}: Updated todos`;
    }
  }

  return toolName;
}

/**
 * Format a tool result based on its type and content
 */
function formatToolResult(message: ToolMessage): string {
  const content = getMessageContentString(message.content);

  if (!content) return "";

  const isError = message.status === "error";
  const toolName = message.name || "tool";

  // If it's an error, return error message immediately
  if (isError) return `Error: ${content}`;

  switch (toolName.toLowerCase()) {
    case "shell":
    case "execute_bash": {
      try {
        const result = JSON.parse(content);
        if (!result.success && result.stderr) {
          return result.stderr;
        }
        if (result.success && result.stdout) {
          return result.stdout;
        }
        return content;
      } catch {
        return content;
      }
    }

    case "write_file":
      if (isError) return content;

      return "File written successfully";

    case "read_file": {
      const contentLength = content.length;
      return `${contentLength} characters`;
    }

    case "edit_file":
      return isError ? content : "File edited successfully";

    case "http_request": {
      try {
        const result = JSON.parse(content);
        return `HTTP ${result.status_code || "unknown"}: ${result.success ? "Success" : "Failed"}`;
      } catch {
        return content.length > 100 ? content.slice(0, 100) + "..." : content;
      }
    }

    case "web_search": {
      try {
        const result = JSON.parse(content);
        if (result.error) {
          return `Search error: ${result.error}`;
        }
        const results = result.results || [];
        return `${results.length} search results found`;
      } catch {
        return content.length > 100 ? content.slice(0, 100) + "..." : content;
      }
    }

    case "grep": {
      if (content.includes("Exit code 1. No results found.")) {
        return "No results found";
      }
      const lines = content.split("\n").filter((line) => line.trim());
      return `${lines.length} matches found`;
    }

    case "view": {
      const contentLength = content.length;
      return `${contentLength} characters`;
    }

    case "str_replace_based_edit_tool":
      return "File edited successfully";

    case "get_url_content":
      return `${content.length} characters of content`;

    case "write_todos":
      if (content.includes("Updated todo list")) {
        return "Todo list updated successfully";
      }
      return content.length > 100 ? content.slice(0, 100) + "..." : content;

    case "ls":
      try {
        const items = JSON.parse(content);
        if (Array.isArray(items)) {
          return `${items.length} items: ${items.slice(0, 8).join(", ")}${items.length > 8 ? "..." : ""}`;
        }
      } catch {
        // fallthrough to default
      }
      return content.length > 100 ? content.slice(0, 100) + "..." : content;

    default:
      return content.length > 200 ? content.slice(0, 200) + "..." : content;
  }
}

export function formatDisplayLog(chunk: LogChunk | string): string[] {
  if (typeof chunk === "string") {
    return [chunk];
  }

  const data = chunk.data;
  const logs: string[] = [];

  // Handle messages
  const nestedDataObj = Object.values(data)[0] as unknown as Record<
    string,
    any
  >;
  if (
    nestedDataObj &&
    typeof nestedDataObj === "object" &&
    "messages" in nestedDataObj
  ) {
    const messages = Array.isArray(nestedDataObj.messages)
      ? nestedDataObj.messages
      : [nestedDataObj.messages];
    for (const msg of messages) {
      try {
        const message = coerceMessageLikeToMessage(msg);

        // Handle tool messages
        if (isToolMessage(message)) {
          const toolName = message.name || "tool";

          // Skip displaying results for todo list tool calls
          if (toolName === "write_todos") {
            continue;
          }

          const result = formatToolResult(message);
          if (result) {
            // Display tool results as indented subsections
            let formattedResult = result.replace(/\s+/g, " ");
            logs.push(`  ↳ ${formattedResult}`);
          }
          continue;
        }

        // Handle AI messages
        if (isAIMessage(message)) {
          // Handle reasoning if present
          if (message.additional_kwargs?.reasoning) {
            const reasoning = String(message.additional_kwargs.reasoning)
              .replace(/\s+/g, " ")
              .trim();
            logs.push(`[REASONING] ${reasoning}`);
          }

          // Handle tool calls
          if (message.tool_calls && message.tool_calls.length > 0) {
            const technicalNotesToolName =
              createWriteTechnicalNotesToolFields().name;

            message.tool_calls.forEach((tool) => {
              const formattedArgs = formatToolCallArgs(tool);
              logs.push(`▸ ${formattedArgs}`);

              // Special handling for write_todos to display the actual todos nicely
              if (
                tool.name === "write_todos" &&
                tool.args &&
                tool.args.todos &&
                Array.isArray(tool.args.todos)
              ) {
                const todos = tool.args.todos;
                logs.push(""); // blank line before todos
                todos.forEach((todo: any) => {
                  const statusIcon =
                    todo.status === "completed"
                      ? "✓"
                      : todo.status === "in_progress"
                        ? "→"
                        : "○";
                  logs.push(`  ${statusIcon} ${todo.content}`);
                });
              }

              // Special handling for edit_file to display the diff
              if (tool.name === "edit_file" && tool.args) {
                const oldString = tool.args.old_string || "";
                const newString = tool.args.new_string || "";
                const diffLines = createSimpleDiff(oldString, newString);
                logs.push(...diffLines);
              }

              // Special handling for write_file to display the new content
              if (tool.name === "write_file" && tool.args) {
                const content = tool.args.content || "";
                const diffLines = createSimpleDiff("", content);
                logs.push(...diffLines);
              }

              // Special handling for str_replace_based_edit_tool to display the diff
              if (tool.name === "str_replace_based_edit_tool" && tool.args) {
                const oldStr = tool.args.old_str || "";
                const newStr = tool.args.new_str || "";
                const diffLines = createSimpleDiff(oldStr, newStr);
                logs.push(...diffLines);
              }

              // Handle technical notes from tool call
              if (
                tool.name === technicalNotesToolName &&
                tool.args &&
                typeof tool.args === "object" &&
                "notes" in tool.args
              ) {
                const notes = (tool.args as any).notes;
                if (Array.isArray(notes)) {
                  logs.push(
                    "[TECHNICAL NOTES]",
                    ...notes.map((note: string) => `  • ${note}`),
                  );
                }
              }
            });
          }

          // Handle regular AI messages
          const text = getMessageContentString(message.content);
          if (text) {
            // Always single line, remove newlines
            const cleanText = text.replace(/\s+/g, " ").trim();
            logs.push(`◆ ${cleanText}`);
          }
        }

        // Handle human messages
        if (isHumanMessage(message)) {
          const text = getMessageContentString(message.content);
          if (text) {
            const cleanText = text.replace(/\s+/g, " ").trim();
            if (cleanText) {
              logs.push(`◉ ${cleanText}`);
            }
          }
        }
      } catch (error: any) {
        console.error("Error formatting log:", error.message);
        // Fallback to original message if conversion fails
        if (msg.type === "tool") {
          const toolName = msg.name || "tool";

          // Skip displaying results for todo list tool calls
          if (toolName === "write_todos") {
            // Skip this tool result
          } else {
            const content = getMessageContentString(msg.content);
            if (content) {
              logs.push(`  ↳ ${content}`);
            }
          }
        } else if (msg.type === "ai") {
          const text = getMessageContentString(msg.content);
          if (text) {
            const cleanText = text.replace(/\s+/g, " ").trim();
            logs.push(`◆ ${cleanText}`);
          }
        } else if (msg.type === "human") {
          const text = getMessageContentString(msg.content);
          if (text) {
            const cleanText = text.replace(/\s+/g, " ").trim();
            if (cleanText) {
              logs.push(`◉ ${cleanText}`);
            }
          }
        }
      }
    }
  }
  // Handle feedback messages
  if (data.command?.resume?.[0]?.type) {
    const type = data.command.resume[0].type;
    logs.push(`[HUMAN FEEDBACK RECEIVED] ${type}`);
  }

  // Handle interrupts and plans
  if (data.__interrupt__) {
    const interrupt = data.__interrupt__[0]?.value;
    if (interrupt?.action_request?.args?.plan) {
      const plan = interrupt.action_request.args.plan;
      const steps = plan
        .split(":::")
        .map((s: string) => s.trim())
        .filter(Boolean);

      // Add clear visual separation and format nicely
      logs.push(
        " ", // Blank line for separation

        "🎯 PROPOSED PLAN",
        ...steps.map((step: string, idx: number) => `  ${idx + 1}. ${step}`),

        " ", // Blank line after
      );
    }
  }

  return logs;
}

/**
 * Formats a log chunk for debug purposes, showing all raw data.
 * This should only be used during development.
 */
export function formatDebugLog(chunk: LogChunk | string): string {
  if (typeof chunk === "string") return chunk;
  return JSON.stringify(chunk, null, 2);
}
