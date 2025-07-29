import { coerceMessageLikeToMessage, ToolMessage, isAIMessage, isHumanMessage } from "@langchain/core/messages";
import { getMessageContentString } from "@open-swe/shared/messages";
import { createWriteTechnicalNotesToolFields } from "@open-swe/shared/open-swe/tools";

interface LogChunk {
  event: string;
  data: any;
  ops?: Array<{ value: string }>;
}



interface ToolCall {
  type: string;
  args?: string;
  name?: string;
  index?: number;
  id?: string;
}
/**
 * Format a tool result based on its type and content
 */
function formatToolResult(message: ToolMessage): string {
  const content = getMessageContentString(message.content);

  if (!content) return "";

  // For successful tool executions, format nicely
  const toolName = message.name || "tool";
  switch (toolName.toLowerCase()) {
    case "shell":
    case "grep_search":
    case "search":
      return content;
    case "apply_patch":
      return content.includes("Error")
        ? `Error: ${content}`
        : "Patch applied successfully";
    case "install_dependencies":
      return content.includes("Error")
        ? `Error: ${content}`
        : "Dependencies installed successfully";
    default:
      if (content.length > 200) {
        return content.slice(0, 200) + "...";
      }
      return content;
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
        if (message instanceof ToolMessage) {
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
            const technicalNotesToolName = createWriteTechnicalNotesToolFields().name;
            
            message.tool_calls.forEach((tool) => {
              let argsString = "";
              if (typeof tool.args === "string") {
                argsString = tool.args;
              } else if (tool.args !== undefined) {
                try {
                  argsString = JSON.stringify(tool.args, null, 2);
                } catch {
                  argsString = String(tool.args);
                }
              }
              // Truncate the string if too long
              const maxLength = 150;
              const truncatedArgs =
                argsString.length > maxLength
                  ? argsString.slice(0, maxLength) + "... [trunc]"
                  : argsString;
              const toolName = tool.name || "unknown";
              logs.push(`[TOOL CALL] ${toolName}: ${truncatedArgs}`);
              
              // Handle technical notes from tool call
              if (tool.name === technicalNotesToolName && tool.args && typeof tool.args === "object" && "notes" in tool.args) {
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
      } catch (error) {
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
