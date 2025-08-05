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
 * Format a tool call arguments into a clean, readable string
 */
function formatToolCallArgs(tool: ToolCall): string {
  const toolName = tool.name || "unknown tool";

  if (!tool.args) return toolName;

  switch (toolName.toLowerCase()) {
    case "shell": {
      if (Array.isArray(tool.args.command)) {
        return `${toolName}: ${tool.args.command.join(" ")}`;
      }
      return `${toolName}: ${tool.args.command || ""}`;
    }

    case "grep": {
      const query = tool.args.query || "";
      return `${toolName}: "${query}"`;
    }

    case "view": {
      return `${toolName}: ${tool.args.path || ""}`;
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
          const oldStr = tool.args.old_str || "";
          const newStr = tool.args.new_str || "";
          return `${toolName}: old_str="${oldStr}", new_str="${newStr}"`;
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

    case "search_documents_for": {
      const query = tool.args.query || "";
      const url = tool.args.url || "";
      return `${toolName}: "${query}" in ${url}`;
    }

    case "get_url_content": {
      return `${toolName}: ${tool.args.url || ""}`;
    }

    case "session_plan": {
      const title = tool.args.title || "";
      const planSteps = tool.args.plan || [];
      if (title) {
        return `${toolName}: "${title}" (${planSteps.length} steps)`;
      }
      return `${toolName}: ${planSteps.length} plan steps`;
    }

    case "apply_patch": {
      const filePath = tool.args.file_path || "";
      const diff = tool.args.diff || "";
      const diffLines = diff.split("\n").length;
      return `${toolName}: applied ${diffLines} line diff to ${filePath}`;
    }

    case "install_dependencies": {
      const command = tool.args.command || [];
      if (Array.isArray(command)) {
        return `${toolName}: ${command.join(" ")}`;
      }
      return `${toolName}: ${command}`;
    }

    case "scratchpad": {
      const scratchpad = tool.args.scratchpad || [];
      if (Array.isArray(scratchpad)) {
        return `${toolName}: ${scratchpad.length} notes`;
      }
      return `${toolName}: ${scratchpad}`;
    }

    case "command_safety_evaluator": {
      const command = tool.args.command || "";
      return `${toolName}: evaluating "${command}"`;
    }

    case "respond_and_route": {
      const response = tool.args.response || "";
      const route = tool.args.route || "";
      if (response && route) {
        return `${toolName}: "${response}" → ${route}`;
      } else if (response) {
        return `${toolName}: "${response}"`;
      } else if (route) {
        return `${toolName}: → ${route}`;
      }
      return `${toolName}: routing decision`;
    }

    case "request_human_help": {
      const helpRequest = tool.args.help_request || "";
      return `${toolName}: "${helpRequest}"`;
    }

    case "update_plan": {
      const reasoning = tool.args.update_plan_reasoning || "";
      return `${toolName}: ${reasoning.slice(0, 50)}...`;
    }

    case "mark_task_completed": {
      const summary = tool.args.completed_task_summary || "";
      return `${toolName}: ${summary.slice(0, 50)}...`;
    }

    case "mark_task_not_completed": {
      const reasoning = tool.args.reasoning || "";
      return `${toolName}: ${reasoning.slice(0, 50)}...`;
    }

    case "diagnose_error": {
      const diagnosis = tool.args.diagnosis || "";
      return `${toolName}: ${diagnosis.slice(0, 50)}...`;
    }

    case "write_technical_notes": {
      const notes = tool.args.notes || "";
      return `${toolName}: ${notes.slice(0, 50)}...`;
    }

    case "summarize_conversation_history": {
      const reasoning = tool.args.reasoning || "";
      return `${toolName}: ${reasoning.slice(0, 50)}...`;
    }

    case "code_review_mark_task_completed": {
      const review = tool.args.review || "";
      return `${toolName}: ${review.slice(0, 50)}...`;
    }

    case "code_review_mark_task_not_complete": {
      const review = tool.args.review || "";
      const actions = tool.args.additional_actions || [];
      return `${toolName}: ${review.slice(0, 30)}... (${actions.length} actions)`;
    }

    case "review_started": {
      const started = tool.args.review_started || false;
      return `${toolName}: ${started ? "started" : "not started"}`;
    }
  }
  return "";
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
      return content;

    case "grep": {
      if (content.includes("Exit code 1. No results found.")) {
        return "No results found";
      }
      const lines = content.split("\n").filter((line) => line.trim());
      return `${lines.length} matches found`;
    }

    case "view": {
      const contentLength = content.length;
      return contentLength > 1000
        ? `${contentLength} characters (truncated)`
        : `${contentLength} characters`;
    }

    case "str_replace_based_edit_tool":
      return "File edited successfully";

    case "get_url_content":
      return `${content.length} characters of content`;

    case "apply_patch":
      return "Patch applied successfully";

    case "install_dependencies":
      return "Dependencies installed successfully";

    case "command_safety_evaluator":
      try {
        const evaluation = JSON.parse(content);
        return `Safety: ${evaluation.is_safe ? "SAFE" : "UNSAFE"} (${evaluation.risk_level} risk)`;
      } catch {
        return content;
      }

    default:
      return content.length > 200 ? content.slice(0, 200) + "..." : content;
  }
}

export function formatDisplayLog(chunk: LogChunk | string): string[] {
  if (typeof chunk === "string") {
    if (chunk.startsWith("Human feedback:")) {
      return [
        `[HUMAN FEEDBACK RECEIVED] ${chunk.replace("Human feedback:", "").trim()}`,
      ];
    }
    if (chunk.startsWith("Interrupt:")) {
      const message = chunk.replace("Interrupt:", "").trim();
      return [
        "═══════════════════════════════════════",
        `📤 INTERRUPT: "${message}"`,
        "═══════════════════════════════════════",
      ];
    }
    // Filter out raw file content and object references
    if (
      chunk === "[object Object]" ||
      chunk.includes("total 4") ||
      chunk.includes("drwxr-xr-x") ||
      chunk.includes("Exit code 1") ||
      chunk.startsWith("#") ||
      chunk.startsWith("-") ||
      chunk.startsWith("./")
    ) {
      return [];
    }
    // Single line system messages
    const cleanChunk = chunk.replace(/\s+/g, " ").trim();
    const maxLength = 150;
    const truncated =
      cleanChunk.length > maxLength
        ? cleanChunk.slice(0, maxLength) + "... [trunc]"
        : cleanChunk;
    return [`[SYSTEM] ${truncated}`];
  }

  const data = chunk.data;
  const logs: string[] = [];

  // Handle session events
  if (data.plannerSession) {
    logs.push("[PLANNER SESSION STARTED]");
  }
  if (data.programmerSession) {
    logs.push("[PROGRAMMER SESSION STARTED]");
  }

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
          const result = formatToolResult(message);
          if (result) {
            // Concatenate long tool results to a single line (truncate if too long)
            const maxLength = 500;
            let formattedResult = result.replace(/\s+/g, " ");
            if (formattedResult.length > maxLength) {
              formattedResult =
                formattedResult.slice(0, maxLength) + "... [trunc]";
            }
            logs.push(`[TOOL RESULT] ${toolName}: ${formattedResult}`);
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
            const maxLength = 150;
            const truncated =
              reasoning.length > maxLength
                ? reasoning.slice(0, maxLength) + "... [trunc]"
                : reasoning;
            logs.push(`[REASONING] ${truncated}`);
          }

          // Handle tool calls
          if (message.tool_calls && message.tool_calls.length > 0) {
            const technicalNotesToolName =
              createWriteTechnicalNotesToolFields().name;

            message.tool_calls.forEach((tool) => {
              const formattedArgs = formatToolCallArgs(tool);
              logs.push(`[TOOL CALL] ${formattedArgs}`);

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
            // Always single line, remove newlines and truncate
            const cleanText = text.replace(/\s+/g, " ").trim();
            const maxLength = 200;
            const truncated =
              cleanText.length > maxLength
                ? cleanText.slice(0, maxLength) + "... [trunc]"
                : cleanText;
            logs.push(`[AI] ${truncated}`);
          }
        }

        // Handle human messages
        if (isHumanMessage(message)) {
          const text = getMessageContentString(message.content);
          if (text) {
            // Single line human messages
            const cleanText = text.replace(/\s+/g, " ").trim();
            const maxLength = 150;
            const truncated =
              cleanText.length > maxLength
                ? cleanText.slice(0, maxLength) + "... [trunc]"
                : cleanText;
            logs.push(`[HUMAN] ${truncated}`);
          }
        }
      } catch (error: any) {
        console.error("Error formatting log:", error.message);
        // Fallback to original message if conversion fails
        if (msg.type === "tool") {
          const toolName = msg.name || "tool";
          const content = getMessageContentString(msg.content);
          if (content) {
            logs.push(`[TOOL RESULT] ${toolName}: ${content}`);
          }
        } else if (msg.type === "ai") {
          const text = getMessageContentString(msg.content);
          if (text) {
            const cleanText = text.replace(/\s+/g, " ").trim();
            const maxLength = 200;
            const truncated =
              cleanText.length > maxLength
                ? cleanText.slice(0, maxLength) + "... [trunc]"
                : cleanText;
            logs.push(`[AI] ${truncated}`);
          }
        } else if (msg.type === "human") {
          const text = getMessageContentString(msg.content);
          if (text) {
            const cleanText = text.replace(/\s+/g, " ").trim();
            const maxLength = 150;
            const truncated =
              cleanText.length > maxLength
                ? cleanText.slice(0, maxLength) + "... [trunc]"
                : cleanText;
            logs.push(`[HUMAN] ${truncated}`);
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
    } else {
      logs.push(
        " ", // Blank line for separation
        "⏳ INTERRUPT: Waiting for feedback...",
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
