import { GraphConfig } from "@open-swe/shared/open-swe/types";
import { loadModel } from "../llms/index.js";
import { LLMTask } from "@open-swe/shared/open-swe/llm-task";
import { createLogger, LogLevel } from "../logger.js";
import { DOCUMENT_TOC_GENERATION_PROMPT } from "./prompt.js";
import { getMessageContentString } from "@open-swe/shared/messages";
import { truncateOutput } from "../truncate-outputs.js";

const logger = createLogger(LogLevel.INFO, "McpOutputHandler");

export async function handleMcpDocumentationOutput(
  output: string,
  config: GraphConfig,
  options?: {
    maxLength?: number;
    url?: string;
  },
): Promise<string> {
  const { maxLength = 40000, url = "" } = options ?? {};

  // If output is within limits, return as-is
  if (output.length <= maxLength) {
    return output;
  }

  logger.info("MCP output exceeds max length, generating table of contents", {
    outputLength: output.length,
    maxLength,
    url,
  });

  try {
    const model = await loadModel(config, LLMTask.SUMMARIZER);

    const systemPrompt = DOCUMENT_TOC_GENERATION_PROMPT.replace(
      "{DOCUMENT_PAGE_CONTENT}",
      output,
    );

    const response = await model
      .withConfig({ tags: ["nostream"], runName: "mcp-doc-toc-generation" })
      .invoke([
        {
          role: "user",
          content: systemPrompt,
        },
      ]);

    const tableOfContents = getMessageContentString(response.content);

    const explanatoryMessage = createExplanatoryMessage(url, tableOfContents);
    return explanatoryMessage;
  } catch (error) {
    logger.error("Failed to generate MCP documentation summary", {
      ...(error instanceof Error
        ? { name: error.name, message: error.message, stack: error.stack }
        : { error }),
    });

    return truncateOutput(output, {
      numStartCharacters: 20000,
      numEndCharacters: 20000,
    });
  }
}

function createExplanatoryMessage(
  url: string,
  tableOfContents: string,
): string {
  const urlInfo = url ? ` from ${url}` : "";
  const searchInstruction = url
    ? `To get specific information from this document, use: search_document_for("${url}", "your natural language query")`
    : "To get specific information from this document, use the search_document_for tool with the url of the page and the natural language query";

  return `The following output was truncated due to its length exceeding the maximum allowed characters. Content${urlInfo} received ${tableOfContents ? "exceeded" : "exceeds"} 40,000 characters.

The following provides a table of contents of the document content:

${tableOfContents || "Table of contents generation failed"}

${searchInstruction}`;
}

// Keep the original simple function for backwards compatibility
export function handleMcpOutput(
  output: string,
  options?: {
    maxLength?: number;
  },
) {
  const { maxLength = 40000 } = options ?? {};
  if (output.length > maxLength) {
    return "swoosh!!";
  }
  return output;
}
