import { GraphConfig, GraphState } from "@open-swe/shared/open-swe/types";
import { truncateOutput } from "./truncate-outputs.js";
import { handleMcpDocumentationOutput } from "./mcp-output/index.js";

interface ToolCall {
  name: string;
  args?: Record<string, any>;
}

/**
 * Processes tool call results with appropriate content handling based on tool type.
 * Handles search_document_for, MCP tools, and regular tools with different truncation strategies.
 */
export async function processToolCallContent(
  toolCall: ToolCall,
  result: string,
  config: GraphConfig,
  options: {
    higherContextLimitToolNames: string[];
    state: Pick<GraphState, "documentCache">;
  },
): Promise<string> {
  const { higherContextLimitToolNames, state } = options;

  if (toolCall.name === "search_document_for") {
    return truncateOutput(result, {
      numStartCharacters: 20000,
      numEndCharacters: 20000,
    });
  } else if (higherContextLimitToolNames.includes(toolCall.name)) {
    const url = toolCall.args?.url || toolCall.args?.uri || toolCall.args?.path;
    if (url) {
      state.documentCache[url] = result;
    }
    return await handleMcpDocumentationOutput(result, config, {
      url: typeof url === "string" ? url : undefined,
    });
  } else {
    return truncateOutput(result);
  }
}
