import { GraphConfig } from "@open-swe/shared/open-swe/types";
import { truncateOutput } from "./truncate-outputs.js";
import { handleMcpDocumentationOutput } from "./mcp-output/index.js";

interface ToolCall {
  name: string;
  args?: Record<string, any>;
}

/**
 * Processes tool call results with appropriate content handling based on tool type.
 * Handles MCP tools, URL content tools, and regular tools with different truncation strategies.
 */
export async function processToolCallContent(
  toolCall: ToolCall,
  result: string,
  config: GraphConfig,
  options: {
    mcpToolNames: string[];
    urlContentToolName: string;
  },
): Promise<string> {
  const { mcpToolNames, urlContentToolName } = options;

  if (mcpToolNames.includes(toolCall.name)) {
    const url = toolCall.args?.url || toolCall.args?.uri || toolCall.args?.path;
    return await handleMcpDocumentationOutput(result, config, {
      url: typeof url === "string" ? url : undefined,
    });
  } else if (toolCall.name === urlContentToolName) {
    return truncateOutput(result, {
      numStartCharacters: 20000,
      numEndCharacters: 20000,
    });
  } else {
    return truncateOutput(result);
  }
}
