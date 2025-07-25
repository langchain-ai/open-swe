import { GraphConfig, GraphState } from "@open-swe/shared/open-swe/types";
import { truncateOutput } from "./truncate-outputs.js";
import { handleMcpDocumentationOutput } from "./mcp-output/index.js";
import { parseUrl } from "./url-parser.js";

interface ToolCall {
  name: string;
  args?: Record<string, any>;
}

/**
 * Processes tool call results with appropriate content handling based on tool type.
 * Handles search_document_for, MCP tools, and regular tools with different truncation strategies.
 * Returns a new state object with the updated document cache if the tool is a higher context limit tool.
 */
export async function processToolCallContent(
  toolCall: ToolCall,
  result: string,
  options: {
    higherContextLimitToolNames: string[];
    state: Pick<GraphState, "documentCache">;
    config: GraphConfig;
  },
): Promise<{
  content: string;
  stateUpdates?: Partial<Pick<GraphState, "documentCache">>;
}> {
  const { higherContextLimitToolNames, state, config } = options;

  if (toolCall.name === "search_document_for") {
    return {
      content: truncateOutput(result, {
        numStartCharacters: 20000,
        numEndCharacters: 20000,
      }),
    };
  } else if (higherContextLimitToolNames.includes(toolCall.name)) {
    const url = toolCall.args?.url || toolCall.args?.uri || toolCall.args?.path;
    const parsedResult = typeof url === "string" ? parseUrl(url) : null;
    const parsedUrl = parsedResult?.success ? parsedResult.url.href : undefined;

    // avoid generating TOC again if it's already in the cache
    if (parsedUrl && state.documentCache[parsedUrl]) {
      return {
        content: state.documentCache[parsedUrl],
      };
    }

    const processedContent = await handleMcpDocumentationOutput(
      result,
      config,
      {
        url: parsedUrl,
      },
    );

    const stateUpdates = parsedUrl
      ? {
          documentCache: {
            ...state.documentCache,
            [parsedUrl]: result,
          },
        }
      : undefined;

    return {
      content: processedContent,
      stateUpdates,
    };
  } else {
    return {
      content: truncateOutput(result),
    };
  }
}
