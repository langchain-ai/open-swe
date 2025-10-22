import { GraphConfig, GraphState } from "@openswe/shared/open-swe/types";
import { handleMcpDocumentationOutput } from "./mcp-output/index.js";
import { createLogger, LogLevel } from "./logger.js";
import { parseUrl } from "./url-parser.js";
import { truncateOutput } from "./truncate-outputs.js";

export const toolOutputProcessingLogger = createLogger(
  LogLevel.INFO,
  "ToolOutputProcessing",
);

export const DOCUMENT_CACHE_CHARACTER_BUDGET = 40_000;
/**
 * The document cache feeds entire pages back into planner and programmer prompts.
 * Capping entries keeps serialized graph state lightweight and protects downstream
 * models from receiving multi-hundred kilobyte payloads that would otherwise
 * exhaust context budgets.
 */
export function enforceDocumentCacheBudget(content: string): {
  content: string;
  truncated: boolean;
} {
  if (content.length <= DOCUMENT_CACHE_CHARACTER_BUDGET) {
    return { content, truncated: false };
  }

  const halfBudget = Math.floor(DOCUMENT_CACHE_CHARACTER_BUDGET / 2);
  const marker = "\n\n... [document cache truncated] ...\n\n";
  const truncatedContent =
    content.slice(0, halfBudget) + marker + content.slice(-halfBudget);
  const cappedContent = truncatedContent.slice(0, DOCUMENT_CACHE_CHARACTER_BUDGET);

  toolOutputProcessingLogger.warn(
    "Document cache entry truncated to respect character budget.",
    {
      budget: DOCUMENT_CACHE_CHARACTER_BUDGET,
      originalLength: content.length,
      finalLength: cappedContent.length,
    },
  );

  return { content: cappedContent, truncated: true };
}

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
      ? (() => {
          const { content: budgetedContent } = enforceDocumentCacheBudget(result);

          return {
            documentCache: {
              ...state.documentCache,
              [parsedUrl]: budgetedContent,
            },
          };
        })()
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
